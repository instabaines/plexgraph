// WebSocket transport shim. Per the plan (section 3), viz-core exposes one
// renderer consumed by two transport shims — this WebSocket client for the
// standalone browser app, and (later) an anywidget comm-channel client for
// Jupyter — both decoding the same wire protocol.

import { decode, encode } from "@msgpack/msgpack";
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

  /** Answer an ExportRequestMessage: `data` is null (with `error` set) when the export itself failed. The
   * WebSocket carries this the same way every other message travels — one msgpack-encoded binary frame. */
  sendExport(id: string, format: string, data: Uint8Array | null, error: string | null): void {
    this.socket.send(encode({ type: "export_response", id, format, data, error }));
  }

  close(): void {
    this.socket.close();
  }
}
