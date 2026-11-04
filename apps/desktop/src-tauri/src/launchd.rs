//! The resident runner as a macOS user LaunchAgent (ADR 0037 §6).
//!
//! The plist names the repository and the runner command only; no keys are written.
#![cfg_attr(test, allow(dead_code))]

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::{home, RunnerKind, RunnerServiceState};

/// The file `repo_dir` reads the repository's location from.
pub fn settings_path() -> PathBuf {
    home().join("Library/Application Support/com.pensivetraveler.siw.agent/settings.json")
}

pub fn log_dir() -> PathBuf {
    home().join("Library/Logs/siw-agent")
}

fn plist_path(kind: RunnerKind) -> PathBuf {
    home().join(format!("Library/LaunchAgents/{}.plist", kind.label()))
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// The runner's LaunchAgent: started at login, restarted after a crash, never given keys.
pub fn runner_plist(kind: RunnerKind, uv: &Path, repo: &Path, log_dir: &Path) -> String {
    let label = kind.label();
    let program = crate::runner_arguments(uv, repo, kind)
        .iter()
        .map(|argument| format!("    <string>{}</string>\n", xml_escape(argument)))
        .collect::<String>();
    let repo_text = xml_escape(&repo.display().to_string());
    let cache = xml_escape(&repo.join(".uv-cache").display().to_string());
    let log = xml_escape(&log_dir.join(kind.log_name()).display().to_string());
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{program}  </array>
  <key>WorkingDirectory</key>
  <string>{repo_text}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>UV_CACHE_DIR</key>
    <string>{cache}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"#
    )
}

fn gui_domain() -> String {
    let uid = Command::new("id")
        .arg("-u")
        .output()
        .ok()
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|text| text.trim().to_string())
        .unwrap_or_default();
    format!("gui/{uid}")
}

fn launchctl(arguments: &[&str]) -> (bool, String) {
    crate::run_tool(Command::new("/bin/launchctl").args(arguments))
}

pub fn state(kind: RunnerKind, detail: String) -> RunnerServiceState {
    let target = format!("{}/{}", gui_domain(), kind.label());
    let (loaded, printed) = launchctl(&["print", &target]);
    RunnerServiceState {
        installed: plist_path(kind).is_file(),
        running: loaded && printed.contains("state = running"),
        detail,
    }
}

pub fn install(
    kind: RunnerKind,
    uv: &Path,
    repo: &Path,
    logs: &Path,
) -> Result<RunnerServiceState, String> {
    let plist = plist_path(kind);
    if let Some(parent) = plist.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&plist, runner_plist(kind, uv, repo, logs)).map_err(|e| e.to_string())?;
    let domain = gui_domain();
    let _ = launchctl(&["bootout", &format!("{domain}/{}", kind.label())]);
    let (ok, output) = launchctl(&["bootstrap", &domain, &plist.display().to_string()]);
    Ok(state(kind, if ok { String::new() } else { output }))
}

pub fn stop(kind: RunnerKind) -> RunnerServiceState {
    let (_, output) = launchctl(&["bootout", &format!("{}/{}", gui_domain(), kind.label())]);
    let current = state(kind, String::new());
    if current.running {
        state(kind, output)
    } else {
        current
    }
}

pub fn uninstall(kind: RunnerKind) -> Result<RunnerServiceState, String> {
    let _ = launchctl(&["bootout", &format!("{}/{}", gui_domain(), kind.label())]);
    let plist = plist_path(kind);
    if plist.is_file() {
        std::fs::remove_file(&plist).map_err(|e| e.to_string())?;
    }
    Ok(state(kind, String::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plist_runs_the_runner_without_keys() {
        let repo = std::env::temp_dir().join("siw-plist-test");
        let _ = std::fs::create_dir_all(&repo);
        let text = runner_plist(
            RunnerKind::Paper,
            Path::new("/opt/homebrew/bin/uv"),
            &repo,
            Path::new("/tmp/logs"),
        );
        assert!(text.contains("<string>services.paper_trading.service</string>"));
        assert!(text.contains(&format!("<string>{}</string>", crate::RUNNER_LABEL)));
        assert!(text.contains("<key>KeepAlive</key>"));
        for secret in ["APP_KEY", "SECRET", "app_key", "secretkey"] {
            assert!(!text.contains(secret), "{secret}");
        }
    }

    #[test]
    fn the_live_runner_is_its_own_service() {
        let repo = std::env::temp_dir().join("siw-plist-test");
        let _ = std::fs::create_dir_all(&repo);
        let text = runner_plist(
            RunnerKind::Live,
            Path::new("/opt/homebrew/bin/uv"),
            &repo,
            Path::new("/tmp/logs"),
        );
        assert!(text.contains("<string>services.live_trading.service</string>"));
        assert!(text.contains(&format!("<string>{}</string>", crate::LIVE_RUNNER_LABEL)));
        assert!(text.contains("/tmp/logs/live-runner.log"));
        assert!(!text.contains("services.paper_trading.service"));
        assert_ne!(plist_path(RunnerKind::Live), plist_path(RunnerKind::Paper));
    }

    #[test]
    fn xml_is_escaped() {
        assert_eq!(xml_escape("a&b<c>\"d\""), "a&amp;b&lt;c&gt;&quot;d&quot;");
    }
}
