// Wires the desktop app's native File menu ("Open Graph…"/"Save Project As…", built in Rust —
// see src-tauri/src/lib.rs) to the viewer. Only active when this page runs inside the Tauri shell
// (packages/desktop): a plain browser tab has no such menu and never emits these events, so
// mounting this in every build (rather than a desktop-only bundle) is harmless there.
//
// The native dialog itself only ever runs in Rust. This module receives the path the user picked,
// via a Tauri event, and does the actual work — sending the graph's "load_file"/"save_project"
// wire message to the Python sidecar through the viewer's existing WebSocket connection
// (ViewerHandle.loadFile/.saveProject) — the same request/response shape the "Export ▾" button's
// round trip already uses.

import type { ViewerHandle } from "@plexgraph/viz-core";

interface AckMessage {
  type: "ack";
  of: string;
  ok: boolean;
  error: string | null;
}

export interface DesktopIntegration {
  /** Feed an AckMessage the viewer received in (main.ts's MountViewerOptions.onFileAck); a no-op
   * outside Tauri, since nothing here is listening for load_file/save_project acks otherwise. */
  handleAck(ack: AckMessage): void;
}

const NOOP: DesktopIntegration = { handleAck: () => {} };

export function mountDesktopIntegration(handle: ViewerHandle, onStatus: (text: string) => void): DesktopIntegration {
  if (!("__TAURI_INTERNALS__" in window)) return NOOP;

  let pendingLoad = false;

  void (async () => {
    const { listen } = await import("@tauri-apps/api/event");
    await listen<string>("plexgraph://open-file", (event) => {
      pendingLoad = true;
      onStatus(`opening ${event.payload}…`);
      handle.loadFile(event.payload);
    });
    await listen<string>("plexgraph://save-project", (event) => {
      onStatus(`saving project to ${event.payload}…`);
      handle.saveProject(event.payload);
    });
  })().catch((err) => console.error("[plexgraph] desktop integration failed to start", err));

  return {
    handleAck(ack) {
      if (ack.of === "load_file") {
        if (!ack.ok) {
          pendingLoad = false;
          onStatus(`could not open file: ${ack.error}`);
          return;
        }
        // The sidecar already swapped its live graph in on this same connection; reloading opens
        // a fresh WebSocket (same ?ws=&token= URL) that streams it through the ordinary
        // just-connected path (BridgeServer._handle_connection), rather than teaching this page a
        // second, parallel way to receive a graph.
        if (pendingLoad) window.location.reload();
      } else if (ack.of === "save_project") {
        onStatus(ack.ok ? "project saved" : `could not save project: ${ack.error}`);
      }
    },
  };
}
