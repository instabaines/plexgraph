//! Phase A of plexgraph's desktop app: spawn the Python sidecar (plexgraph_bridge's WebSocket
//! server, started with a hardcoded demo graph for now -- see
//! `packages/bridge/plexgraph_bridge/desktop_sidecar.py`), own its lifecycle (killed on app
//! exit, no orphaned process left running), and point the webview at the existing web frontend
//! with the sidecar's fixed port and a freshly-generated per-launch token appended as query
//! params -- the exact mechanism `packages/app/src/main.ts` already reads from `window.location
//! .search`, so the frontend needs no changes at all.
//!
//! The sidecar is invoked as a plain `python3 -m plexgraph_bridge.desktop_sidecar` for now
//! (override the interpreter via `PLEXGRAPH_SIDECAR_PYTHON` for local dev against a non-default
//! venv). Phase C replaces this invocation with Tauri's real sidecar `Command::sidecar()` API
//! against a PyInstaller-frozen binary; the spawn-and-own lifecycle built here is deliberately
//! kept stable across that swap.

use rand::Rng;
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use tauri::{RunEvent, WebviewUrl, WebviewWindowBuilder};

/// The sidecar's fixed, pre-agreed port -- reasonable for a single-user local desktop app (see
/// the module docstring on `desktop_sidecar.py`), must match its own `DEFAULT_PORT`.
const SIDECAR_PORT: u16 = 47300;

type SharedChild = Arc<Mutex<Option<Child>>>;

fn kill_sidecar(sidecar: &SharedChild) {
    if let Ok(mut guard) = sidecar.lock()
        && let Some(mut child) = guard.take()
    {
        let _ = child.kill();
    }
}

fn generate_token() -> String {
    let bytes: [u8; 16] = rand::thread_rng().r#gen();
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn spawn_sidecar(token: &str) -> Child {
    let python =
        std::env::var("PLEXGRAPH_SIDECAR_PYTHON").unwrap_or_else(|_| "python3".to_string());
    Command::new(&python)
        .args([
            "-m",
            "plexgraph_bridge.desktop_sidecar",
            "--port",
            &SIDECAR_PORT.to_string(),
            "--token",
            token,
        ])
        .spawn()
        .unwrap_or_else(|e| panic!("failed to start the plexgraph Python sidecar ({python}): {e}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Shared between the normal RunEvent::ExitRequested path (fired on a graceful app quit --
    // window close, Cmd+Q, the OS's own quit action) and a direct OS signal handler below: a raw
    // SIGTERM/SIGINT (e.g. `kill`, a session manager logging the user out, or Ctrl-C against
    // `cargo tauri dev`) does NOT go through Tauri's event loop at all, so relying on
    // RunEvent::ExitRequested alone leaves the sidecar orphaned in that case -- confirmed directly
    // while verifying this phase (`kill -TERM` on the app left the Python sidecar still running).
    let sidecar: SharedChild = Arc::new(Mutex::new(None));

    {
        let sidecar = sidecar.clone();
        ctrlc::set_handler(move || {
            kill_sidecar(&sidecar);
            std::process::exit(0);
        })
        .expect("failed to register the Ctrl-C/SIGTERM handler");
    }

    let sidecar_for_setup = sidecar.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            let token = generate_token();
            let child = spawn_sidecar(&token);
            *sidecar_for_setup.lock().unwrap() = Some(child);

            let dev_url = std::env::var("PLEXGRAPH_FRONTEND_URL")
                .unwrap_or_else(|_| "http://localhost:5173".to_string());
            let url = format!("{dev_url}/?ws={SIDECAR_PORT}&host=localhost&token={token}");
            let url: tauri::Url = url
                .parse()
                .expect("the constructed viewer URL is always valid");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("plexgraph")
                .inner_size(1000.0, 700.0)
                .build()?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the tauri application");

    app.run(move |_app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            kill_sidecar(&sidecar);
        }
    });
}
