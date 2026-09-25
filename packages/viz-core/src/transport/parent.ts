// Notebook transport. Inside a notebook the viewer runs in an iframe that the widget hosts, and the widget hands it
// the bridge's frames with postMessage: nothing is served on a port, so there is no address to reach or to guard.
// The frames are the same msgpack messages the WebSocket carries; only the way they arrive differs.

import { decode } from "@msgpack/msgpack";
import type { WireMessage } from "../ir/types";
import type { TransportHandlers } from "./websocket";

/** What `mountViewer` is given instead of a WebSocket address to use this transport. */
export const PARENT_TRANSPORT = "parent";

interface MessageTarget {
  postMessage(message: unknown, targetOrigin: string, transfer?: Transferable[]): void;
}

interface MessageSource {
  addEventListener(type: "message", listener: (event: MessageEvent) => void): void;
  removeEventListener(type: "message", listener: (event: MessageEvent) => void): void;
}

export class ParentTransport {
  private readonly listener: (event: MessageEvent) => void;

  /** `host` and `self` are the page around this iframe and this page; they are parameters so tests can supply fakes. */
  constructor(
    private readonly handlers: TransportHandlers,
    private readonly host: MessageTarget = window.parent,
    private readonly self: MessageSource = window,
  ) {
    this.listener = (event) => {
      // Only what the hosting widget sent: any other window can post to this one.
      if (event.source !== this.host) return;
      const data = event.data as { plexgraph?: string; data?: ArrayBuffer } | null;
      if (data?.plexgraph === "closed") {
        handlers.onClose?.();
      } else if (data?.plexgraph === "frame" && data.data instanceof ArrayBuffer) {
        try {
          handlers.onMessage(decode(new Uint8Array(data.data)) as WireMessage);
        } catch (err) {
          handlers.onError?.(err);
        }
      }
    };
    self.addEventListener("message", this.listener);
    // Tell the widget this viewer is listening, so it starts (or restarts) the stream. It is the last thing done here,
    // after the listener exists, so the first frame cannot arrive before anything is ready to take it.
    host.postMessage({ plexgraph: "hello" }, "*");
    handlers.onOpen?.();
  }

  /** Answer an ExportRequestMessage: relayed to Python by the widget host script (widget.js), which is listening
   * for this exact shape, as a "export" custom message with `data` as its binary buffer -- not wrapped as a
   * generic "frame" (that channel only carries Python -> viewer traffic; see ClientHub.request_export). */
  sendExport(id: string, format: string, data: Uint8Array | null, error: string | null): void {
    const buffer = data ? data.buffer as ArrayBuffer : null;
    this.host.postMessage({ plexgraph: "export", id, format, error, data: buffer }, "*", buffer ? [buffer] : []);
  }

  close(): void {
    this.self.removeEventListener("message", this.listener);
  }
}
