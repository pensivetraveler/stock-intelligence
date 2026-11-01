//! The resident runner as a Linux systemd user service (ADR 0045).
//!
//! Behaves like the macOS LaunchAgent: it starts when the user logs in, restarts after a
//! crash, and stops with the session. Lingering is deliberately not enabled, so the runner
//! only runs while the session that unlocks the keyring exists. The unit names the
//! repository and the runner command only; no keys are written.
#![cfg_attr(test, allow(dead_code))]

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::{home, RunnerServiceState, RUNNER_LABEL};

fn unit_name() -> String {
    format!("{RUNNER_LABEL}.service")
}

/// An XDG base directory: the variable when it is an absolute path, else the default.
fn xdg_dir(variable: &str, default: &str) -> PathBuf {
    std::env::var_os(variable)
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .unwrap_or_else(|| home().join(default))
}

/// The file `repo_dir` reads the repository's location from.
pub fn settings_path() -> PathBuf {
    xdg_dir("XDG_CONFIG_HOME", ".config")
        .join("com.pensivetraveler.siw.agent/settings.json")
}

pub fn log_dir() -> PathBuf {
    xdg_dir("XDG_STATE_HOME", ".local/state").join("siw-agent")
}

fn unit_path() -> PathBuf {
    xdg_dir("XDG_CONFIG_HOME", ".config").join("systemd/user").join(unit_name())
}

/// A whole-line unit value: only `%` specifiers are expanded there.
fn escape_line(text: &str) -> String {
    text.replace('%', "%%")
}

/// One quoted word of `ExecStart=`, where `$` expands variables as well.
fn quote_exec_word(text: &str) -> String {
    let escaped = text
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('%', "%%")
        .replace('$', "$$");
    format!("\"{escaped}\"")
}

/// The quoted value of `Environment=`, which does not expand `$`.
fn quote_environment(text: &str) -> String {
    let escaped = text.replace('\\', "\\\\").replace('"', "\\\"").replace('%', "%%");
    format!("\"{escaped}\"")
}

/// The runner's user service: started at login, restarted after a crash, never given keys.
pub fn runner_unit(uv: &Path, repo: &Path, log_dir: &Path) -> String {
    let program = crate::runner_arguments(uv, repo)
        .iter()
        .map(|argument| quote_exec_word(argument))
        .collect::<Vec<_>>()
        .join(" ");
    let repo_text = escape_line(&repo.display().to_string());
    let cache = quote_environment(&format!("UV_CACHE_DIR={}", repo.join(".uv-cache").display()));
    let log = escape_line(&log_dir.join("runner.log").display().to_string());
    format!(
        "[Unit]
Description=SIW Agent paper-order runner

[Service]
Type=simple
WorkingDirectory={repo_text}
Environment={cache}
ExecStart={program}
Restart=on-failure
RestartSec=30
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"
    )
}

fn systemctl(arguments: &[&str]) -> (bool, String) {
    crate::run_tool(Command::new("/usr/bin/systemctl").arg("--user").args(arguments))
}

/// `systemctl is-active` prints `activating` while a crashed runner waits for its restart.
fn reads_as_running(printed: &str) -> bool {
    printed.trim() == "active"
}

pub fn state(detail: String) -> RunnerServiceState {
    let (_, printed) = systemctl(&["is-active", &unit_name()]);
    RunnerServiceState {
        installed: unit_path().is_file(),
        running: reads_as_running(&printed),
        detail,
    }
}

pub fn install(uv: &Path, repo: &Path, logs: &Path) -> Result<RunnerServiceState, String> {
    // A line break would end the value and start another unit setting.
    if [uv, repo, logs].iter().any(|path| path.display().to_string().contains('\n')) {
        return Err("a path contains a line break".into());
    }
    let unit = unit_path();
    if let Some(parent) = unit.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&unit, runner_unit(uv, repo, logs)).map_err(|e| e.to_string())?;
    let name = unit_name();
    for arguments in [&["daemon-reload"][..], &["enable", &name][..], &["restart", &name][..]] {
        let (ok, output) = systemctl(arguments);
        if !ok {
            return Ok(state(output));
        }
    }
    Ok(state(String::new()))
}

/// Stops the runner until the next login, like `launchctl bootout`.
pub fn stop() -> RunnerServiceState {
    let (_, output) = systemctl(&["stop", &unit_name()]);
    let current = state(String::new());
    if current.running {
        state(output)
    } else {
        current
    }
}

pub fn uninstall() -> Result<RunnerServiceState, String> {
    let _ = systemctl(&["disable", "--now", &unit_name()]);
    let unit = unit_path();
    if unit.is_file() {
        std::fs::remove_file(&unit).map_err(|e| e.to_string())?;
    }
    let _ = systemctl(&["daemon-reload"]);
    Ok(state(String::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit_for(repo: &Path) -> String {
        runner_unit(Path::new("/home/me/.local/bin/uv"), repo, Path::new("/home/me/logs"))
    }

    #[test]
    fn unit_runs_the_runner_without_keys() {
        let repo = std::env::temp_dir().join("siw-unit-test");
        let _ = std::fs::create_dir_all(&repo);
        let text = unit_for(&repo);
        assert!(text.contains(
            "ExecStart=\"/home/me/.local/bin/uv\" \"run\" \"python\" \"-m\" \"services.paper_trading.service\""
        ));
        assert!(text.contains("Restart=on-failure\nRestartSec=30\n"));
        assert!(text.contains("StandardOutput=append:/home/me/logs/runner.log\n"));
        assert!(text.contains("WantedBy=default.target\n"));
        for secret in ["APP_KEY", "SECRET", "app_key", "secretkey"] {
            assert!(!text.contains(secret), "{secret}");
        }
    }

    #[test]
    fn unit_values_are_escaped() {
        let text = unit_for(Path::new("/data/100% \"siw\" $HOME"));
        assert!(text.contains("WorkingDirectory=/data/100%% \"siw\" $HOME\n"));
        assert!(text.contains("Environment=\"UV_CACHE_DIR=/data/100%% \\\"siw\\\" $HOME/.uv-cache\"\n"));
        assert_eq!(quote_exec_word("a$b%c\"d\\e"), "\"a$$b%%c\\\"d\\\\e\"");
    }

    #[test]
    fn only_active_reads_as_running() {
        assert!(reads_as_running("active\n"));
        for printed in ["inactive\n", "activating\n", "failed\n", "unknown\n", ""] {
            assert!(!reads_as_running(printed), "{printed}");
        }
    }

    /// Installs, stops and removes a real user service. Needs a systemd user manager and a
    /// throwaway home: `cargo test -- --ignored`, as the Linux CI job does.
    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "changes the user's systemd services"]
    fn service_lifecycle_on_a_real_user_manager() {
        use std::os::unix::fs::PermissionsExt;
        use std::time::{Duration, Instant};

        let root = std::env::temp_dir().join(format!("siw unit {}", std::process::id()));
        let logs = root.join("logs");
        std::fs::create_dir_all(&logs).expect("directories");
        let uv = root.join("fake-uv");
        std::fs::write(&uv, "#!/bin/sh\necho started\nexec sleep 300\n").expect("script");
        std::fs::set_permissions(&uv, std::fs::Permissions::from_mode(0o755)).expect("mode");

        let wait_for = |running: bool| {
            let deadline = Instant::now() + Duration::from_secs(10);
            loop {
                let current = state(String::new());
                if current.running == running || Instant::now() >= deadline {
                    return current;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        };

        let installed = install(&uv, &root, &logs).expect("install");
        assert_eq!(installed.detail, "");
        assert!(installed.installed);
        assert!(wait_for(true).running);
        assert!(systemctl(&["is-enabled", &unit_name()]).0, "starts at the next login");

        let stopped = stop();
        assert!(stopped.installed);
        assert!(!wait_for(false).running);
        assert!(std::fs::read_to_string(logs.join("runner.log")).expect("log").contains("started"));

        assert!(install(&uv, &root, &logs).expect("reinstall").installed);
        assert!(wait_for(true).running);

        let removed = uninstall().expect("uninstall");
        assert!(!removed.installed);
        assert!(!wait_for(false).running);
        assert!(!unit_path().exists());
        let _ = std::fs::remove_dir_all(&root);
    }
}
