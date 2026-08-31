// WebSocket transport shim. Per the plan (section 3), viz-core exposes one
// renderer consumed by two transport shims — this WebSocket client for the
// standalone browser app, and (later) an anywidget comm-channel client for
// Jupyter — both decoding the same wire protocol.

import { decode } from "@msgpack/msgpack";
import type { WireMessage } from "../ir/types";

export interface TransportHandlers {
  onMessage: (msg: WireMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: unknown) => void;
}

export class WebSocketTransport {
  private socket: WebSocket;

  constructor(url: string, handlers: TransportHandlers) {
    this.socket = new WebSocket(url);
    this.socket.binaryType = "arraybuffer";

    this.socket.addEventListener("open", () => handlers.onOpen?.());
    this.socket.addEventListener("close", () => handlers.onClose?.());
    this.socket.addEventListener("error", (e) => handlers.onError?.(e));
    this.socket.addEventListener("message", (event) => {
      const bytes = new Uint8Array(event.data as ArrayBuffer);
      const msg = decode(bytes) as WireMessage;
      handlers.onMessage(msg);
    });
  }

  close(): void {
    this.socket.close();
  }
}
