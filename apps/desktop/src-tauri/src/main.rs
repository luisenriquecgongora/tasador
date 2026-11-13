// Prevents an extra console window on Windows in release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Tasador desktop: a small window around the engine running on this computer.
//!
//! - Starts the Python engine hidden (no console window) on the chosen port, with the
//!   settings page switched on, and stops it when the app quits.
//! - Keeps the port in `.env` (`TASADOR_PORT`). Changing it restarts the engine on the new
//!   port; a busy port is refused with a suggestion of the next free one.
//! - Starts local search (SearXNG in Docker) when Docker is running and no other search
//!   provider is configured, then restarts the engine so it uses it.
//! - Closing the window hides it; the tray icon opens it again or quits.
//!
//! Two ways to run:
//! - Installed: the installer puts the engine (with its own Python) in `engine/` next to
//!   the app. Settings live in the user's app-config folder, SearXNG runs as the
//!   `tasador-searxng` container started with `docker run`.
//! - From the repository (developers): no bundled engine, so the checkout's virtualenv
//!   (installed by Tasador.bat) runs it, settings in the repository's `.env`, SearXNG via
//!   `docker compose`. TASADOR_HOME points to another checkout.

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};

const DEFAULT_PORT: u16 = 8000;
const SEARXNG_PORT: u16 = 8888;
const PORT_KEY: &str = "TASADOR_PORT";

const SEARXNG_CONTAINER: &str = "tasador-searxng";
const SEARXNG_IMAGE: &str = "searxng/searxng:latest";
/// The same SearXNG settings the repository uses with docker compose.
const SEARXNG_SETTINGS: &str = include_str!("../../../../deploy/searxng/settings.yml");

/// How the engine is started.
enum Program {
    /// Installed: `engine/tasador-engine.exe` from the installer.
    Bundled(PathBuf),
    /// From the repository: the checkout's virtualenv.
    Checkout { python: PathBuf, src: PathBuf },
}

/// Where things live.
struct Paths {
    program: Program,
    workdir: PathBuf,
    env_file: PathBuf,
    log_file: PathBuf,
    /// The repository, when running from it (SearXNG then starts with docker compose).
    compose_dir: Option<PathBuf>,
    /// Folder for the SearXNG settings when the app starts the container itself.
    searxng_dir: PathBuf,
}

impl Paths {
    fn resolve(app: &tauri::App) -> tauri::Result<Self> {
        let log_dir = app.path().app_log_dir()?;
        let data_dir = app.path().app_data_dir()?;
        let config_dir = app.path().app_config_dir()?;
        for dir in [&log_dir, &data_dir, &config_dir] {
            fs::create_dir_all(dir)?;
        }
        let log_file = log_dir.join("engine.log");
        let searxng_dir = data_dir.join("searxng");
        let exe = if cfg!(windows) { "tasador-engine.exe" } else { "tasador-engine" };
        let bundled = app
            .path()
            .resource_dir()
            .ok()
            .map(|dir| dir.join("engine").join(exe))
            .filter(|path| path.exists() && std::env::var_os("TASADOR_HOME").is_none());
        if let Some(exe) = bundled {
            return Ok(Paths {
                program: Program::Bundled(exe),
                workdir: data_dir,
                env_file: config_dir.join(".env"),
                log_file,
                compose_dir: None,
                searxng_dir,
            });
        }
        let root = std::env::var_os("TASADOR_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| normalize(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")));
        let engine = root.join("services").join("tasador-engine");
        let python = if cfg!(windows) {
            engine.join(".venv").join("Scripts").join("python.exe")
        } else {
            engine.join(".venv").join("bin").join("python")
        };
        Ok(Paths {
            program: Program::Checkout { python, src: engine.join("src") },
            workdir: engine,
            env_file: root.join(".env"),
            log_file,
            compose_dir: Some(root),
            searxng_dir,
        })
    }
}

/// `a/b/../c` -> `a/c` without touching the disk (canonicalize would add `\\?\` on Windows).
fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

struct Engine {
    child: Option<Child>,
    port: u16,
    phase: &'static str, // starting | running | stopped | error
    message: String,
    search: &'static str, // checking | starting | running | unavailable | failed | configured
    generation: u64,      // bumps on every (re)start, so old watcher threads stop
}

struct Shared {
    paths: Paths,
    engine: Mutex<Engine>,
}

type Handle = Arc<Shared>;

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Engine> {
        self.engine.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

#[derive(Serialize)]
struct Status {
    phase: String,
    port: u16,
    url: String,
    message: String,
    search: String,
    log_file: String,
    env_file: String,
}

fn snapshot(shared: &Shared) -> Status {
    let engine = shared.lock();
    Status {
        phase: engine.phase.to_string(),
        port: engine.port,
        url: format!("http://localhost:{}/", engine.port),
        message: engine.message.clone(),
        search: engine.search.to_string(),
        log_file: shared.paths.log_file.display().to_string(),
        env_file: shared.paths.env_file.display().to_string(),
    }
}

// ---- .env -------------------------------------------------------------------------------

fn read_env_value(path: &Path, key: &str) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    text.lines()
        .filter(|line| !line.trim_start().starts_with('#'))
        .filter_map(|line| line.split_once('='))
        .filter(|(k, v)| k.trim() == key && !v.trim().is_empty())
        .map(|(_, v)| v.trim().to_string())
        .last()
}

/// Update `key=value` in place, keep every other line, append when missing.
fn write_env_value(path: &Path, key: &str, value: &str) -> std::io::Result<()> {
    let text = fs::read_to_string(path).unwrap_or_default();
    let mut found = false;
    let mut lines: Vec<String> = text
        .lines()
        .map(|line| {
            let is_key = !line.trim_start().starts_with('#')
                && line.split_once('=').is_some_and(|(k, _)| k.trim() == key);
            if is_key {
                found = true;
                format!("{key}={value}")
            } else {
                line.to_string()
            }
        })
        .collect();
    if !found {
        if lines.last().is_some_and(|line| !line.trim().is_empty()) {
            lines.push(String::new());
        }
        lines.push(format!("{key}={value}"));
    }
    fs::write(path, lines.join("\n") + "\n")
}

// ---- ports and processes ------------------------------------------------------------------

fn answers(port: u16) -> bool {
    TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], port)), Duration::from_millis(400)).is_ok()
}

fn port_free(port: u16) -> bool {
    !answers(port) && TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn next_free(after: u16) -> Option<u16> {
    (after.saturating_add(1)..=after.saturating_add(50)).find(|port| port_free(*port))
}

fn healthy(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], port)), Duration::from_millis(500))
    else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /health HTTP/1.0\r\nHost: localhost:{port}\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buffer = [0u8; 64];
    matches!(stream.read(&mut buffer), Ok(n) if String::from_utf8_lossy(&buffer[..n]).contains(" 200 "))
}

fn hidden(command: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

fn tail(path: &Path, lines: usize) -> String {
    let text = fs::read_to_string(path).unwrap_or_default();
    let all: Vec<&str> = text.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

/// Stops the engine and anything it started (on Windows the whole process tree).
fn stop_child(engine: &mut Engine) {
    if let Some(mut child) = engine.child.take() {
        #[cfg(windows)]
        {
            let _ = hidden(Command::new("taskkill").args(["/F", "/T", "/PID", &child.id().to_string()]))
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
    engine.phase = "stopped";
}

// ---- engine lifecycle ---------------------------------------------------------------------

fn start_engine(shared: &Handle) {
    let paths = &shared.paths;
    let mut engine = shared.lock();
    stop_child(&mut engine);
    engine.generation += 1;
    let (generation, port) = (engine.generation, engine.port);

    if let Program::Checkout { python, .. } = &paths.program {
        if !python.exists() {
            engine.phase = "error";
            engine.message = format!(
                "The engine is not installed yet. Run Tasador.bat once (it installs everything), then open this app again.\n\nLooked for: {}",
                python.display()
            );
            return;
        }
    }
    // The port the engine just released can take a moment to free up.
    let deadline = Instant::now() + Duration::from_secs(3);
    while !port_free(port) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(200));
    }
    if !port_free(port) {
        let hint = next_free(port).map(|p| format!(" Port {p} is free.")).unwrap_or_default();
        engine.phase = "error";
        engine.message = format!("Port {port} is already used by another program. Choose another port above.{hint}");
        return;
    }
    let log = match OpenOptions::new().create(true).append(true).open(&paths.log_file) {
        Ok(file) => file,
        Err(err) => {
            engine.phase = "error";
            engine.message = format!("Cannot write the log file {}: {err}", paths.log_file.display());
            return;
        }
    };
    let _ = writeln!(&log, "\n--- starting the engine on port {port} ---");
    let mut command = match &paths.program {
        Program::Bundled(exe) => {
            let mut command = Command::new(exe);
            command.args(["--host", "127.0.0.1", "--port"]).arg(port.to_string());
            command
        }
        Program::Checkout { python, src } => {
            let mut command = Command::new(python);
            command
                .args(["-m", "uvicorn", "tasador_engine.main:app_from_env", "--factory", "--app-dir"])
                .arg(src)
                .args(["--host", "127.0.0.1", "--port"])
                .arg(port.to_string());
            command
        }
    };
    command
        .current_dir(&paths.workdir)
        .env("TASADOR_DEPLOYMENT", "local")
        .env("TASADOR_SETTINGS_UI", "1")
        .env("TASADOR_AUTODETECT", "1")
        .env("TASADOR_ENV_FILE", &paths.env_file)
        .env("PYTHONUTF8", "1")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null());
    if let Ok(copy) = log.try_clone() {
        command.stdout(copy);
    }
    command.stderr(log);
    match hidden(&mut command).spawn() {
        Ok(child) => {
            engine.child = Some(child);
            engine.phase = "starting";
            engine.message = format!("Starting the engine on port {port}…");
            drop(engine);
            watch(shared.clone(), generation, port);
        }
        Err(err) => {
            engine.phase = "error";
            engine.message = format!("Could not start the engine: {err}");
        }
    }
}

/// Marks the engine running once /health answers, and reports it if it stops.
fn watch(shared: Handle, generation: u64, port: u16) {
    thread::spawn(move || {
        let started = Instant::now();
        loop {
            thread::sleep(Duration::from_millis(500));
            {
                let mut engine = shared.lock();
                if engine.generation != generation {
                    return;
                }
                let exited = engine.child.as_mut().and_then(|child| child.try_wait().ok().flatten());
                if let Some(status) = exited {
                    engine.child = None;
                    engine.phase = "error";
                    engine.message = format!(
                        "The engine stopped ({status}). Last lines of {}:\n\n{}",
                        shared.paths.log_file.display(),
                        tail(&shared.paths.log_file, 12)
                    );
                    return;
                }
                if engine.phase == "running" {
                    continue;
                }
            }
            if healthy(port) {
                let mut engine = shared.lock();
                if engine.generation == generation {
                    engine.phase = "running";
                    engine.message = String::new();
                }
            } else if started.elapsed() > Duration::from_secs(180) {
                let mut engine = shared.lock();
                if engine.generation == generation {
                    engine.phase = "error";
                    engine.message = format!("The engine did not answer within 3 minutes. See {}", shared.paths.log_file.display());
                }
                return;
            }
        }
    });
}

/// Local search: start SearXNG in Docker unless it already runs or another search is chosen.
fn ensure_search(shared: Handle) {
    thread::spawn(move || {
        let set = |state: &'static str| shared.lock().search = state;
        let configured = read_env_value(&shared.paths.env_file, "TASADOR_SEARCH_PROVIDER");
        if configured.as_deref().is_some_and(|p| p != "searxng" && p != "auto") {
            set("configured");
            return;
        }
        if answers(SEARXNG_PORT) {
            set("running");
            return;
        }
        if !docker(&["info"], None) {
            set("unavailable");
            return;
        }
        set("starting");
        let up = match &shared.paths.compose_dir {
            Some(root) => docker(&["compose", "--profile", "local", "up", "-d", "searxng"], Some(root)),
            None => run_searxng_container(&shared.paths),
        };
        let deadline = Instant::now() + Duration::from_secs(180);
        while up && !answers(SEARXNG_PORT) && Instant::now() < deadline {
            thread::sleep(Duration::from_secs(2));
        }
        if up && answers(SEARXNG_PORT) {
            thread::sleep(Duration::from_secs(3)); // let its engines warm up
            set("running");
            start_engine(&shared); // the engine picks up local search when it starts
        } else {
            set("failed");
        }
    });
}

/// Folders where Docker Desktop and Homebrew put `docker` on macOS. An app opened from Finder
/// gets a minimal PATH without them, so `docker` would not be found.
const MACOS_DOCKER_DIRS: [&str; 3] = ["/usr/local/bin", "/opt/homebrew/bin", "/Applications/Docker.app/Contents/Resources/bin"];

/// `current` PATH with the macOS Docker folders appended (each only once).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn path_with_docker_dirs(current: &str) -> String {
    let mut parts: Vec<&str> = current.split(':').filter(|part| !part.is_empty()).collect();
    for dir in MACOS_DOCKER_DIRS {
        if !parts.contains(&dir) {
            parts.push(dir);
        }
    }
    parts.join(":")
}

fn docker(args: &[&str], dir: Option<&Path>) -> bool {
    let mut command = Command::new("docker");
    #[cfg(target_os = "macos")]
    command.env("PATH", path_with_docker_dirs(&std::env::var("PATH").unwrap_or_default()));
    command.args(args).stdout(Stdio::null()).stderr(Stdio::null());
    if let Some(dir) = dir {
        command.current_dir(dir);
    }
    hidden(&mut command).status().is_ok_and(|status| status.success())
}

fn random_hex() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    (0..2)
        .map(|_| {
            let mut hasher = RandomState::new().build_hasher();
            hasher.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos());
            format!("{:016x}", hasher.finish())
        })
        .collect()
}

/// Installed mode: start (or create) the `tasador-searxng` container, listening on this
/// computer only. Public DNS servers are passed because Docker Desktop's own DNS fails
/// on some Windows machines; TASADOR_SEARXNG_DNS=system turns that off.
fn run_searxng_container(paths: &Paths) -> bool {
    if docker(&["start", SEARXNG_CONTAINER], None) {
        return true;
    }
    if fs::create_dir_all(&paths.searxng_dir).is_err() {
        return false;
    }
    let settings = paths.searxng_dir.join("settings.yml");
    if !settings.exists() {
        let text = SEARXNG_SETTINGS.replace("change-me-before-exposing-this-instance", &random_hex());
        if fs::write(&settings, text).is_err() {
            return false;
        }
    }
    let volume = format!("{}:/etc/searxng", paths.searxng_dir.display());
    let port = format!("127.0.0.1:{SEARXNG_PORT}:8080");
    let mut args = vec!["run", "-d", "--name", SEARXNG_CONTAINER, "--restart", "unless-stopped", "-p", &port, "-v", &volume];
    let dns = read_env_value(&paths.env_file, "TASADOR_SEARXNG_DNS").unwrap_or_else(|| "1.1.1.1,8.8.8.8".into());
    let servers: Vec<String> = if dns == "system" { Vec::new() } else { dns.split(',').map(|s| s.trim().to_string()).collect() };
    for server in &servers {
        args.push("--dns");
        args.push(server);
    }
    args.push(SEARXNG_IMAGE);
    docker(&args, None)
}

// ---- commands for the window ----------------------------------------------------------------

#[tauri::command]
fn status(state: tauri::State<'_, Handle>) -> Status {
    snapshot(&state)
}

#[tauri::command]
fn set_port(state: tauri::State<'_, Handle>, port: u16) -> Result<Status, String> {
    if port < 1024 {
        return Err("Choose a port from 1024 to 65535.".into());
    }
    let current = {
        let engine = state.lock();
        (engine.port, engine.phase)
    };
    if current == (port, "running") {
        return Ok(snapshot(&state));
    }
    if port != current.0 && !port_free(port) {
        let hint = next_free(port).map(|p| format!(" Port {p} is free.")).unwrap_or_default();
        return Err(format!("Port {port} is already used by another program.{hint}"));
    }
    write_env_value(&state.paths.env_file, PORT_KEY, &port.to_string())
        .map_err(|err| format!("Could not save the port to {}: {err}", state.paths.env_file.display()))?;
    state.lock().port = port;
    start_engine(&state);
    Ok(snapshot(&state))
}

#[tauri::command]
fn restart_engine(state: tauri::State<'_, Handle>) -> Status {
    start_engine(&state);
    snapshot(&state)
}

#[tauri::command]
fn open_in_browser(url: String) -> Result<(), String> {
    if !(url.starts_with("http://localhost:") || url.starts_with("http://127.0.0.1:")) {
        return Err("Only local addresses can be opened from here.".into());
    }
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", &url]);
        command
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg(&url);
        command
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(&url);
        command
    };
    hidden(&mut command).spawn().map(|_| ()).map_err(|err| err.to_string())
}

// ---- window and tray --------------------------------------------------------------------

fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Tasador", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart engine", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit (stops the engine)", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &restart, &quit])?;
    let mut tray = TrayIconBuilder::with_id("tasador")
        .tooltip("Tasador")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_window(app),
            "restart" => start_engine(&app.state::<Handle>()),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_window(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![status, set_port, restart_engine, open_in_browser])
        // Closing the window keeps Tasador in the tray; "Quit" in the tray stops everything.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(|app| {
            let paths = Paths::resolve(app)?;
            let port = read_env_value(&paths.env_file, PORT_KEY)
                .and_then(|value| value.parse::<u16>().ok())
                .filter(|port| *port >= 1024)
                .unwrap_or(DEFAULT_PORT);
            let shared: Handle = Arc::new(Shared {
                paths,
                engine: Mutex::new(Engine {
                    child: None,
                    port,
                    phase: "stopped",
                    message: String::new(),
                    search: "checking",
                    generation: 0,
                }),
            });
            app.manage(shared.clone());
            start_engine(&shared);
            ensure_search(shared);
            build_tray(app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Tasador")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let shared = app.state::<Handle>();
                stop_child(&mut shared.lock());
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn docker_dirs_are_appended_once() {
        assert_eq!(
            path_with_docker_dirs("/usr/bin:/bin:/usr/local/bin"),
            "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin"
        );
        assert_eq!(path_with_docker_dirs(""), MACOS_DOCKER_DIRS.join(":"));
    }

    fn temp_file(name: &str, text: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("tasador-desktop-test-{}-{name}", std::process::id()));
        fs::write(&path, text).unwrap();
        path
    }

    #[test]
    fn reads_the_last_non_empty_value_and_skips_comments() {
        let path = temp_file("read", "# TASADOR_PORT=1\nTASADOR_PORT=\nTASADOR_PORT=9000\nOTHER=x\n");
        assert_eq!(read_env_value(&path, "TASADOR_PORT").as_deref(), Some("9000"));
        assert_eq!(read_env_value(&path, "MISSING"), None);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn writes_in_place_and_keeps_other_lines() {
        let path = temp_file("write", "# notes\nTASADOR_PORT=8000\nOTHER=x\n");
        write_env_value(&path, "TASADOR_PORT", "9001").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "# notes\nTASADOR_PORT=9001\nOTHER=x\n");
        write_env_value(&path, "NEW", "1").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "# notes\nTASADOR_PORT=9001\nOTHER=x\n\nNEW=1\n");
        let _ = fs::remove_file(path);
    }

    #[test]
    fn normalizes_parent_segments() {
        assert_eq!(normalize(Path::new("a/b/../c/./d")), PathBuf::from("a/c/d"));
    }

    #[test]
    fn a_port_in_use_is_not_free() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(!port_free(port));
        assert!(next_free(port).is_some_and(|free| free > port));
    }
}
