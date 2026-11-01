//! SIW Agent shell (ADR 0036, ADR 0037 §6).
//!
//! - Starts the local API from this repository when nothing answers on 127.0.0.1:8765,
//!   and stops only an API it started itself. `uv run` keeps uvicorn as a child process,
//!   so the API runs in its own process group and quitting stops the whole group.
//! - Installs, stops and removes the resident paper-order runner as a user service: a
//!   LaunchAgent on macOS (`launchd`), a systemd user service on Linux (`systemd`, ADR 0045).
//!
//! The shell never sends orders and holds no keys: the runner reads its demo key from
//! the keychain itself.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Manager, RunEvent};

#[cfg(any(target_os = "macos", test))]
mod launchd;
#[cfg(any(target_os = "linux", test))]
mod systemd;

#[cfg(target_os = "macos")]
use launchd as service;
#[cfg(target_os = "linux")]
use systemd as service;

const API_ADDR: &str = "127.0.0.1:8765";
pub const RUNNER_LABEL: &str = "com.pensivetraveler.siw.agent.runner";

/// The API process group this shell started, if any; the child is the group leader.
struct ApiChild(Mutex<Option<Child>>);

#[derive(Serialize, Debug, PartialEq)]
pub struct RunnerServiceState {
    pub(crate) installed: bool,
    pub(crate) running: bool,
    pub(crate) detail: String,
}

pub(crate) fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
}

fn is_repo(path: &Path) -> bool {
    path.join("services/api/main.py").is_file()
}

/// SIW_REPO_DIR, then the app's settings file, then the repository this app was built in.
pub fn repo_dir() -> Option<PathBuf> {
    if let Ok(value) = std::env::var("SIW_REPO_DIR") {
        let path = PathBuf::from(value);
        return is_repo(&path).then_some(path);
    }
    if let Ok(text) = std::fs::read_to_string(service::settings_path()) {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(dir) = value.get("repo_dir").and_then(|v| v.as_str()) {
                let path = PathBuf::from(dir);
                if is_repo(&path) {
                    return Some(path);
                }
            }
        }
    }
    let built = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let built = built.canonicalize().ok()?;
    is_repo(&built).then_some(built)
}

/// uv in the usual places: apps opened from Finder or a Linux launcher do not get the
/// shell's PATH.
fn find_uv() -> Option<PathBuf> {
    [".local/bin/uv", ".cargo/bin/uv"]
        .iter()
        .map(|relative| home().join(relative))
        .chain(["/opt/homebrew/bin/uv", "/usr/local/bin/uv"].iter().map(PathBuf::from))
        .find(|path| path.is_file())
}

fn api_healthy() -> bool {
    let address: SocketAddr = API_ADDR.parse().expect("valid address");
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(800)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let request = format!("GET /health HTTP/1.1\r\nHost: {API_ADDR}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut reply = String::new();
    let _ = stream.read_to_string(&mut reply);
    reply.starts_with("HTTP/1.1 200")
}

/// Starts the API from the repository unless one already answers. Never fatal: the
/// screen shows the connection error if the API cannot be started, and keeps retrying.
fn ensure_api() -> Option<Child> {
    if api_healthy() {
        return None;
    }
    let repo = repo_dir()?;
    let uv = find_uv()?;
    let logs = service::log_dir();
    let _ = std::fs::create_dir_all(&logs);
    let log = std::fs::File::create(logs.join("api.log")).ok()?;
    let mut command = Command::new(uv);
    command
        .args(["run", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", "8765"])
        .current_dir(&repo)
        .env("UV_CACHE_DIR", repo.join(".uv-cache"))
        // The API stops itself if this app is killed before it can stop the group.
        .env("SIW_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().ok()?))
        .stderr(Stdio::from(log));
    if repo.join(".env").is_file() {
        command.args(["--env-file", ".env"]);
    }
    spawn_group(&mut command).ok()
}

/// Spawns `command` as the leader of a new process group. Everything it starts (uv's
/// uvicorn child, any reloader or worker) joins that group unless it leaves on purpose.
fn spawn_group(command: &mut Command) -> std::io::Result<Child> {
    command.process_group(0).spawn()
}

/// Stops the process group led by `child`: SIGTERM so uvicorn shuts down cleanly, then
/// SIGKILL for whatever is left after `grace`. Only that group is signalled, so an API
/// the shell did not start and the resident runner service are never touched.
fn stop_group(child: &mut Child, grace: Duration) {
    let Ok(pgid) = libc::pid_t::try_from(child.id()) else {
        return;
    };
    if pgid <= 1 {
        return;
    }
    // SAFETY: killpg only sends a signal; pgid is our own child's group (> 1).
    unsafe { libc::killpg(pgid, libc::SIGTERM) };
    let deadline = Instant::now() + grace;
    loop {
        // Reap the leader so an empty group reads as gone.
        let _ = child.try_wait();
        if unsafe { libc::killpg(pgid, 0) } != 0 {
            return;
        }
        if Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    unsafe { libc::killpg(pgid, libc::SIGKILL) };
    let _ = child.wait();
}

/// What `uv` runs for the resident runner, from the repository, on either service manager.
pub(crate) fn runner_arguments(uv: &Path, repo: &Path) -> Vec<String> {
    let mut arguments = vec![
        uv.display().to_string(),
        "run".into(),
        "python".into(),
        "-m".into(),
        "services.paper_trading.service".into(),
    ];
    if repo.join(".env").is_file() {
        arguments.extend(["--env-file".into(), ".env".into()]);
    }
    arguments
}

/// Runs a service-manager tool: whether it succeeded, and what it printed.
pub(crate) fn run_tool(command: &mut Command) -> (bool, String) {
    match command.output() {
        Ok(output) => (
            output.status.success(),
            String::from_utf8_lossy(&output.stdout).into_owned()
                + &String::from_utf8_lossy(&output.stderr),
        ),
        Err(error) => (false, error.to_string()),
    }
}

#[tauri::command]
fn runner_state() -> RunnerServiceState {
    service::state(String::new())
}

#[tauri::command]
fn runner_install() -> Result<RunnerServiceState, String> {
    let repo = repo_dir().ok_or("the repository was not found (set SIW_REPO_DIR)")?;
    let uv = find_uv().ok_or("uv was not found")?;
    let logs = service::log_dir();
    std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    service::install(&uv, &repo, &logs)
}

#[tauri::command]
fn runner_stop() -> RunnerServiceState {
    service::stop()
}

#[tauri::command]
fn runner_uninstall() -> Result<RunnerServiceState, String> {
    service::uninstall()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(ApiChild(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let child = ensure_api();
                *handle.state::<ApiChild>().0.lock().expect("lock") = child;
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runner_state,
            runner_install,
            runner_stop,
            runner_uninstall
        ])
        .build(tauri::generate_context!())
        .expect("error while building Tauri application");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            // Only the API this shell started; the resident runner keeps running.
            if let Some(mut child) = handle.state::<ApiChild>().0.lock().expect("lock").take() {
                stop_group(&mut child, Duration::from_secs(5));
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn alive(pid: libc::pid_t) -> bool {
        unsafe { libc::kill(pid, 0) == 0 }
    }

    /// The defect: killing only the leader left its child running with PPID 1.
    #[test]
    fn stopping_the_group_stops_grandchildren() {
        use std::io::BufRead;
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "sleep 60 & echo $!; wait"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped());
        let mut child = spawn_group(&mut command).expect("spawn");
        let mut line = String::new();
        std::io::BufReader::new(child.stdout.take().expect("stdout"))
            .read_line(&mut line)
            .expect("read");
        let grandchild: libc::pid_t = line.trim().parse().expect("pid");
        assert!(alive(grandchild));
        assert_eq!(unsafe { libc::getpgid(grandchild) }, child.id() as libc::pid_t);

        stop_group(&mut child, Duration::from_secs(5));

        let deadline = Instant::now() + Duration::from_secs(5);
        while alive(grandchild) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert!(!alive(grandchild), "grandchild {grandchild} survived");
        assert!(child.try_wait().expect("wait").is_some());
    }

    /// A child that ignores SIGTERM is killed once the grace period ends.
    #[test]
    fn stopping_the_group_kills_after_grace() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "trap '' TERM; sleep 60 & wait"]).stdin(Stdio::null());
        let mut child = spawn_group(&mut command).expect("spawn");
        std::thread::sleep(Duration::from_millis(200));
        let pgid = child.id() as libc::pid_t;
        let started = Instant::now();
        stop_group(&mut child, Duration::from_millis(500));
        assert!(started.elapsed() >= Duration::from_millis(500));
        assert!(child.try_wait().expect("wait").is_some());
        let deadline = Instant::now() + Duration::from_secs(5);
        while unsafe { libc::killpg(pgid, 0) } == 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert_ne!(unsafe { libc::killpg(pgid, 0) }, 0, "group {pgid} survived");
    }
}
