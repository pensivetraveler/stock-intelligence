//! The resident runner as a Linux systemd user service (ADR 0045).
//!
//! Behaves like the macOS LaunchAgent: it starts when the user logs in, restarts after a
//! crash, and stops with the session. Lingering is deliberately not enabled, so the runner
//! only runs while the session that unlocks the keyring exists. The unit names the
//! repository and the runner command only; no keys are written.
#![cfg_attr(test, allow(dead_code))]

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::{home, RunnerKind, RunnerServiceState};

fn unit_name(kind: RunnerKind) -> String {
    format!("{}.service", kind.label())
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

fn unit_path(kind: RunnerKind) -> PathBuf {
    xdg_dir("XDG_CONFIG_HOME", ".config").join("systemd/user").join(unit_name(kind))
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
pub fn runner_unit(kind: RunnerKind, uv: &Path, repo: &Path, log_dir: &Path) -> String {
    let description = kind.description();
    let program = crate::runner_arguments(uv, repo, kind)
        .iter()
        .map(|argument| quote_exec_word(argument))
        .collect::<Vec<_>>()
        .join(" ");
    let repo_text = escape_line(&repo.display().to_string());
    let cache = quote_environment(&format!("UV_CACHE_DIR={}", repo.join(".uv-cache").display()));
    let log = escape_line(&log_dir.join(kind.log_name()).display().to_string());
    format!(
        "[Unit]
Description={description}

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

pub fn state(kind: RunnerKind, detail: String) -> RunnerServiceState {
    let (_, printed) = systemctl(&["is-active", &unit_name(kind)]);
    RunnerServiceState {
        installed: unit_path(kind).is_file(),
        running: reads_as_running(&printed),
        detail,
    }
}

pub fn install(
    kind: RunnerKind,
    uv: &Path,
    repo: &Path,
    logs: &Path,
) -> Result<RunnerServiceState, String> {
    // A line break would end the value and start another unit setting.
    if [uv, repo, logs].iter().any(|path| path.display().to_string().contains('\n')) {
        return Err("a path contains a line break".into());
    }
    let unit = unit_path(kind);
    if let Some(parent) = unit.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&unit, runner_unit(kind, uv, repo, logs)).map_err(|e| e.to_string())?;
    let name = unit_name(kind);
    for arguments in [&["daemon-reload"][..], &["enable", &name][..], &["restart", &name][..]] {
        let (ok, output) = systemctl(arguments);
        if !ok {
            return Ok(state(kind, output));
        }
    }
    Ok(state(kind, String::new()))
}

/// Stops the runner until the next login, like `launchctl bootout`.
pub fn stop(kind: RunnerKind) -> RunnerServiceState {
    let (_, output) = systemctl(&["stop", &unit_name(kind)]);
    let current = state(kind, String::new());
    if current.running {
        state(kind, output)
    } else {
        current
    }
}

pub fn uninstall(kind: RunnerKind) -> Result<RunnerServiceState, String> {
    let _ = systemctl(&["disable", "--now", &unit_name(kind)]);
    let unit = unit_path(kind);
    if unit.is_file() {
        std::fs::remove_file(&unit).map_err(|e| e.to_string())?;
    }
    let _ = systemctl(&["daemon-reload"]);
    Ok(state(kind, String::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit_for(repo: &Path) -> String {
        runner_unit(
            RunnerKind::Paper,
            Path::new("/home/me/.local/bin/uv"),
            repo,
            Path::new("/home/me/logs"),
        )
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
                let current = state(RunnerKind::Paper, String::new());
                if current.running == running || Instant::now() >= deadline {
                    return current;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        };

        let installed = install(RunnerKind::Paper, &uv, &root, &logs).expect("install");
        assert_eq!(installed.detail, "");
        assert!(installed.installed);
        assert!(wait_for(true).running);
        assert!(
            systemctl(&["is-enabled", &unit_name(RunnerKind::Paper)]).0,
            "starts at the next login"
        );

        let stopped = stop(RunnerKind::Paper);
        assert!(stopped.installed);
        assert!(!wait_for(false).running);
        assert!(std::fs::read_to_string(logs.join("runner.log")).expect("log").contains("started"));

        assert!(install(RunnerKind::Paper, &uv, &root, &logs).expect("reinstall").installed);
        assert!(wait_for(true).running);

        let removed = uninstall(RunnerKind::Paper).expect("uninstall");
        assert!(!removed.installed);
        assert!(!wait_for(false).running);
        assert!(!unit_path(RunnerKind::Paper).exists());
        let _ = std::fs::remove_dir_all(&root);
    }
}
