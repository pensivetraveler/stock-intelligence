//! The resident runner as a macOS user LaunchAgent (ADR 0037 §6).
//!
//! The plist names the repository and the runner command only; no keys are written.
#![cfg_attr(test, allow(dead_code))]

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::{home, RunnerServiceState, RUNNER_LABEL};

/// The file `repo_dir` reads the repository's location from.
pub fn settings_path() -> PathBuf {
    home().join("Library/Application Support/com.pensivetraveler.siw.agent/settings.json")
}

pub fn log_dir() -> PathBuf {
    home().join("Library/Logs/siw-agent")
}

fn plist_path() -> PathBuf {
    home().join(format!("Library/LaunchAgents/{RUNNER_LABEL}.plist"))
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// The runner's LaunchAgent: started at login, restarted after a crash, never given keys.
pub fn runner_plist(uv: &Path, repo: &Path, log_dir: &Path) -> String {
    let program = crate::runner_arguments(uv, repo)
        .iter()
        .map(|argument| format!("    <string>{}</string>\n", xml_escape(argument)))
        .collect::<String>();
    let repo_text = xml_escape(&repo.display().to_string());
    let cache = xml_escape(&repo.join(".uv-cache").display().to_string());
    let log = xml_escape(&log_dir.join("runner.log").display().to_string());
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{RUNNER_LABEL}</string>
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

pub fn state(detail: String) -> RunnerServiceState {
    let target = format!("{}/{RUNNER_LABEL}", gui_domain());
    let (loaded, printed) = launchctl(&["print", &target]);
    RunnerServiceState {
        installed: plist_path().is_file(),
        running: loaded && printed.contains("state = running"),
        detail,
    }
}

pub fn install(uv: &Path, repo: &Path, logs: &Path) -> Result<RunnerServiceState, String> {
    let plist = plist_path();
    if let Some(parent) = plist.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&plist, runner_plist(uv, repo, logs)).map_err(|e| e.to_string())?;
    let domain = gui_domain();
    let _ = launchctl(&["bootout", &format!("{domain}/{RUNNER_LABEL}")]);
    let (ok, output) = launchctl(&["bootstrap", &domain, &plist.display().to_string()]);
    Ok(state(if ok { String::new() } else { output }))
}

pub fn stop() -> RunnerServiceState {
    let (_, output) = launchctl(&["bootout", &format!("{}/{RUNNER_LABEL}", gui_domain())]);
    let current = state(String::new());
    if current.running {
        state(output)
    } else {
        current
    }
}

pub fn uninstall() -> Result<RunnerServiceState, String> {
    let _ = launchctl(&["bootout", &format!("{}/{RUNNER_LABEL}", gui_domain())]);
    let plist = plist_path();
    if plist.is_file() {
        std::fs::remove_file(&plist).map_err(|e| e.to_string())?;
    }
    Ok(state(String::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plist_runs_the_runner_without_keys() {
        let repo = std::env::temp_dir().join("siw-plist-test");
        let _ = std::fs::create_dir_all(&repo);
        let text = runner_plist(Path::new("/opt/homebrew/bin/uv"), &repo, Path::new("/tmp/logs"));
        assert!(text.contains("<string>services.paper_trading.service</string>"));
        assert!(text.contains(&format!("<string>{RUNNER_LABEL}</string>")));
        assert!(text.contains("<key>KeepAlive</key>"));
        for secret in ["APP_KEY", "SECRET", "app_key", "secretkey"] {
            assert!(!text.contains(secret), "{secret}");
        }
    }

    #[test]
    fn xml_is_escaped() {
        assert_eq!(xml_escape("a&b<c>\"d\""), "a&amp;b&lt;c&gt;&quot;d&quot;");
    }
}
