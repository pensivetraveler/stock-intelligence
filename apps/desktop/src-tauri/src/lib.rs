//! SIW Agent shell (ADR 0036, ADR 0037 §6).
//!
//! - Starts the local API from this repository when nothing answers on 127.0.0.1:8765,
//!   and stops only an API it started itself. When it cannot (the port is held by
//!   another program, no repository, no uv, the API exits), it keeps the reason so the
//!   screen can say what blocked it (`api_start_problem`). `uv run` keeps uvicorn as a child process,
//!   so the API runs in its own process group and quitting stops the whole group.
//! - Installs, stops and removes the resident order runners as user services: a
//!   LaunchAgent on macOS (`launchd`), a systemd user service on Linux (`systemd`, ADR 0045).
//!   The paper runner and the live runner (ADR 0047) are separate services.
//!
//! The shell never sends orders and holds no keys: each runner reads its key from the
//! keychain itself.

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
pub const LIVE_RUNNER_LABEL: &str = "com.pensivetraveler.siw.agent.live-runner";

/// Which resident runner a service command is about.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum RunnerKind {
    Paper,
    Live,
}

impl RunnerKind {
    pub fn label(self) -> &'static str {
        match self {
            RunnerKind::Paper => RUNNER_LABEL,
            RunnerKind::Live => LIVE_RUNNER_LABEL,
        }
    }

    pub fn module(self) -> &'static str {
        match self {
            RunnerKind::Paper => "services.paper_trading.service",
            RunnerKind::Live => "services.live_trading.service",
        }
    }

    pub fn log_name(self) -> &'static str {
        match self {
            RunnerKind::Paper => "runner.log",
            RunnerKind::Live => "live-runner.log",
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            RunnerKind::Paper => "SIW Agent paper-order runner",
            RunnerKind::Live => "SIW Agent live-order runner",
        }
    }
}

/// The API process group this shell started, if any; the child is the group leader.
struct ApiChild(Mutex<Option<Child>>);

/// Why the shell could not start the local API, for the screen to show.
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct ApiStartProblem {
    /// `port_in_use`, `no_repo`, `no_uv`, `spawn_failed`, `exited` or `timeout`.
    pub(crate) kind: &'static str,
    pub(crate) port: u16,
    /// The program listening on the port (`port_in_use`), the exit status (`exited`) or
    /// the OS error (`spawn_failed`); empty otherwise.
    pub(crate) detail: String,
}

struct ApiProblem(Mutex<Option<ApiStartProblem>>);

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

fn api_port() -> u16 {
    API_ADDR.rsplit(':').next().and_then(|p| p.parse().ok()).unwrap_or(8765)
}

fn api_healthy(addr: &str) -> bool {
    let Ok(address) = addr.parse::<SocketAddr>() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(800)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let request = format!("GET /health HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut reply = String::new();
    let _ = stream.read_to_string(&mut reply);
    reply.starts_with("HTTP/1.1 200")
}

/// Whether anything accepts connections on `addr`, API or not.
fn port_in_use(addr: &str) -> bool {
    addr.parse::<SocketAddr>()
        .map(|address| TcpStream::connect_timeout(&address, Duration::from_millis(800)).is_ok())
        .unwrap_or(false)
}

/// The program listening on `port`, as `name (pid N)`, when `lsof` can tell.
fn port_holder(port: u16) -> String {
    let output = Command::new("lsof")
        .args(["-nP", &format!("-iTCP:{port}"), "-sTCP:LISTEN", "-Fpc"])
        .stdin(Stdio::null())
        .output();
    let Ok(output) = output else {
        return String::new();
    };
    let text = String::from_utf8_lossy(&output.stdout);
    let pid = text.lines().find_map(|line| line.strip_prefix('p'));
    let name = text.lines().find_map(|line| line.strip_prefix('c'));
    match (name, pid) {
        (Some(name), Some(pid)) => format!("{name} (pid {pid})"),
        (None, Some(pid)) => format!("pid {pid}"),
        _ => String::new(),
    }
}

fn problem(kind: &'static str, port: u16, detail: String) -> ApiStartProblem {
    ApiStartProblem { kind, port, detail }
}

/// Something answers on the port but not as the API. A starting API (e.g. the resident
/// service restarting) answers within a few seconds, so ask a few times first.
fn held_by_other(addr: &str, attempts: u32, pause: Duration) -> bool {
    for attempt in 0..attempts {
        if !port_in_use(addr) || api_healthy(addr) {
            return false;
        }
        if attempt + 1 < attempts {
            std::thread::sleep(pause);
        }
    }
    true
}

/// Before starting anything: why starting the API on `addr` cannot work, if it cannot.
fn precheck(addr: &str, port: u16, attempts: u32, pause: Duration) -> Option<ApiStartProblem> {
    held_by_other(addr, attempts, pause).then(|| problem("port_in_use", port, port_holder(port)))
}

/// Waits for the API `child` started to answer on `addr`. On failure the child is
/// stopped and the reason returned; the port held by something else comes first, since
/// that is the one the user can fix.
fn await_start(child: &mut Child, addr: &str, port: u16, limit: Duration) -> Option<ApiStartProblem> {
    let deadline = Instant::now() + limit;
    loop {
        if let Ok(Some(status)) = child.try_wait() {
            if port_in_use(addr) && !api_healthy(addr) {
                return Some(problem("port_in_use", port, port_holder(port)));
            }
            return Some(problem("exited", port, status.to_string()));
        }
        if api_healthy(addr) {
            return None;
        }
        if Instant::now() >= deadline {
            stop_group(child, Duration::from_secs(5));
            return Some(problem("timeout", port, String::new()));
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// Starts the API from the repository unless one already answers. Never fatal: the
/// screen shows the reason (`api_start_problem`) and the connection error, and keeps
/// retrying. Returns the child it started, if it is running, and why it could not.
fn ensure_api() -> (Option<Child>, Option<ApiStartProblem>) {
    let port = api_port();
    if api_healthy(API_ADDR) {
        return (None, None);
    }
    if let Some(found) = precheck(API_ADDR, port, 3, Duration::from_secs(1)) {
        return (None, Some(found));
    }
    let Some(repo) = repo_dir() else {
        return (None, Some(problem("no_repo", port, String::new())));
    };
    let Some(uv) = find_uv() else {
        return (None, Some(problem("no_uv", port, String::new())));
    };
    let logs = service::log_dir();
    let _ = std::fs::create_dir_all(&logs);
    let opened = std::fs::File::create(logs.join("api.log"))
        .and_then(|log| Ok((log.try_clone()?, log)));
    let (out, err) = match opened {
        Ok(pair) => pair,
        Err(error) => return (None, Some(problem("spawn_failed", port, error.to_string()))),
    };
    let mut command = Command::new(uv);
    command
        .args(["run", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port"])
        .arg(port.to_string())
        .current_dir(&repo)
        .env("UV_CACHE_DIR", repo.join(".uv-cache"))
        // The API stops itself if this app is killed before it can stop the group.
        .env("SIW_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(out))
        .stderr(Stdio::from(err));
    if repo.join(".env").is_file() {
        command.args(["--env-file", ".env"]);
    }
    let mut child = match spawn_group(&mut command) {
        Ok(child) => child,
        Err(error) => return (None, Some(problem("spawn_failed", port, error.to_string()))),
    };
    match await_start(&mut child, API_ADDR, port, Duration::from_secs(60)) {
        None => (Some(child), None),
        Some(found) => (None, Some(found)),
    }
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
pub(crate) fn runner_arguments(uv: &Path, repo: &Path, kind: RunnerKind) -> Vec<String> {
    let mut arguments = vec![
        uv.display().to_string(),
        "run".into(),
        "python".into(),
        "-m".into(),
        kind.module().into(),
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

fn install(kind: RunnerKind) -> Result<RunnerServiceState, String> {
    let repo = repo_dir().ok_or("the repository was not found (set SIW_REPO_DIR)")?;
    let uv = find_uv().ok_or("uv was not found")?;
    let logs = service::log_dir();
    std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    service::install(kind, &uv, &repo, &logs)
}

#[tauri::command]
fn runner_state() -> RunnerServiceState {
    service::state(RunnerKind::Paper, String::new())
}

#[tauri::command]
fn runner_install() -> Result<RunnerServiceState, String> {
    install(RunnerKind::Paper)
}

#[tauri::command]
fn runner_stop() -> RunnerServiceState {
    service::stop(RunnerKind::Paper)
}

#[tauri::command]
fn runner_uninstall() -> Result<RunnerServiceState, String> {
    service::uninstall(RunnerKind::Paper)
}

#[tauri::command]
fn live_runner_state() -> RunnerServiceState {
    service::state(RunnerKind::Live, String::new())
}

#[tauri::command]
fn live_runner_install() -> Result<RunnerServiceState, String> {
    install(RunnerKind::Live)
}

#[tauri::command]
fn live_runner_stop() -> RunnerServiceState {
    service::stop(RunnerKind::Live)
}

#[tauri::command]
fn live_runner_uninstall() -> Result<RunnerServiceState, String> {
    service::uninstall(RunnerKind::Live)
}

/// Why the shell could not start the local API, or nothing once the API answers (the
/// user freed the port and started it, or the resident service came up).
#[tauri::command]
async fn api_start_problem(
    state: tauri::State<'_, ApiProblem>,
) -> Result<Option<ApiStartProblem>, String> {
    let current = state.0.lock().map_err(|e| e.to_string())?.clone();
    let Some(current) = current else {
        return Ok(None);
    };
    let healthy = tauri::async_runtime::spawn_blocking(|| api_healthy(API_ADDR))
        .await
        .unwrap_or(false);
    if healthy {
        *state.0.lock().map_err(|e| e.to_string())? = None;
        return Ok(None);
    }
    Ok(Some(current))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(ApiChild(Mutex::new(None)))
        .manage(ApiProblem(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let (child, found) = ensure_api();
                *handle.state::<ApiChild>().0.lock().expect("lock") = child;
                *handle.state::<ApiProblem>().0.lock().expect("lock") = found;
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            api_start_problem,
            runner_state,
            runner_install,
            runner_stop,
            runner_uninstall,
            live_runner_state,
            live_runner_install,
            live_runner_stop,
            live_runner_uninstall
        ])
        .build(tauri::generate_context!())
        .expect("error while building Tauri application");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            // Only the API this shell started; the resident runners keep running.
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

    /// Something that is not the API holds a port: it accepts connections and never
    /// answers HTTP.
    fn hold_port() -> (std::net::TcpListener, String, u16) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().expect("addr").port();
        (listener, format!("127.0.0.1:{port}"), port)
    }

    /// The defect (dashboard item 24): with the port held, the shell started nothing and
    /// said nothing. Now it reports the port and does not try to start the API.
    #[test]
    fn a_port_held_by_another_program_is_reported() {
        let (_listener, addr, port) = hold_port();
        let found = precheck(&addr, port, 2, Duration::from_millis(50)).expect("problem");
        assert_eq!(found.kind, "port_in_use");
        assert_eq!(found.port, port);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn the_holder_is_named() {
        let (_listener, _addr, port) = hold_port();
        let holder = port_holder(port);
        assert!(
            holder.contains(&format!("pid {}", std::process::id())),
            "holder was {holder:?}"
        );
    }

    #[test]
    fn a_free_port_is_not_a_problem() {
        let (listener, addr, port) = hold_port();
        drop(listener);
        assert_eq!(precheck(&addr, port, 2, Duration::from_millis(50)), None);
    }

    /// The API child exits because the port was taken while it started.
    #[test]
    fn an_api_that_exits_on_a_held_port_reports_the_port() {
        let (_listener, addr, port) = hold_port();
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exit 1"]).stdin(Stdio::null());
        let mut child = spawn_group(&mut command).expect("spawn");
        let found = await_start(&mut child, &addr, port, Duration::from_secs(5)).expect("problem");
        assert_eq!(found.kind, "port_in_use");
    }

    #[test]
    fn an_api_that_exits_on_a_free_port_reports_the_exit() {
        let (listener, addr, port) = hold_port();
        drop(listener);
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exit 3"]).stdin(Stdio::null());
        let mut child = spawn_group(&mut command).expect("spawn");
        let found = await_start(&mut child, &addr, port, Duration::from_secs(5)).expect("problem");
        assert_eq!(found.kind, "exited");
        assert!(found.detail.contains('3'), "detail was {:?}", found.detail);
    }

    #[test]
    fn an_api_that_never_answers_times_out_and_is_stopped() {
        let (listener, addr, port) = hold_port();
        drop(listener);
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "sleep 60"]).stdin(Stdio::null());
        let mut child = spawn_group(&mut command).expect("spawn");
        let found =
            await_start(&mut child, &addr, port, Duration::from_millis(300)).expect("problem");
        assert_eq!(found.kind, "timeout");
        assert!(child.try_wait().expect("wait").is_some());
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
