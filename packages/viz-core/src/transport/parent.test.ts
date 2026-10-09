import { describe, expect, it, vi } from "vitest";
import { encode } from "@msgpack/msgpack";
import { ParentTransport } from "./parent";

function setup() {
  const posted: unknown[] = [];
  const host = { postMessage: (message: unknown, origin: string) => posted.push([message, origin]) };
  let listener: ((event: MessageEvent) => void) | null = null;
  const self = {
    addEventListener: (_type: "message", fn: (event: MessageEvent) => void) => { listener = fn; },
    removeEventListener: (_type: "message", fn: (event: MessageEvent) => void) => { if (listener === fn) listener = null; },
  };
  const handlers = { onMessage: vi.fn(), onOpen: vi.fn(), onClose: vi.fn(), onError: vi.fn() };
  const transport = new ParentTransport(handlers, host, self);
  const deliver = (data: unknown, source: unknown = host) => listener?.({ data, source } as unknown as MessageEvent);
  const frame = (message: unknown) => encode(message).slice().buffer;
  return { transport, handlers, posted, deliver, frame, host, isListening: () => listener !== null };
}

describe("ParentTransport", () => {
  it("says hello to the widget once it is listening, and reports itself open", () => {
    const { handlers, posted } = setup();
    expect(posted).toEqual([[{ plexgraph: "hello" }, "*"]]);
    expect(handlers.onOpen).toHaveBeenCalledOnce();
  });

  it("decodes the frames the widget sends", () => {
    const { handlers, deliver, frame } = setup();
    deliver({ plexgraph: "frame", data: frame({ type: "graph", nodes: [1, 2] }) });
    expect(handlers.onMessage).toHaveBeenCalledWith({ type: "graph", nodes: [1, 2] });
  });

  it("ignores messages from any window other than the widget's", () => {
    const { handlers, deliver, frame } = setup();
    deliver({ plexgraph: "frame", data: frame({ type: "graph" }) }, { some: "other window" });
    expect(handlers.onMessage).not.toHaveBeenCalled();
  });

  it("ignores messages that are not frames, and frames without data", () => {
    const { handlers, deliver } = setup();
    deliver("hello");
    deliver(null);
    deliver({ plexgraph: "frame" });
    deliver({ plexgraph: "frame", data: "not a buffer" });
    deliver({ other: 1 });
    expect(handlers.onMessage).not.toHaveBeenCalled();
    expect(handlers.onError).not.toHaveBeenCalled();
  });

  it("reports a frame it cannot decode instead of throwing", () => {
    const { handlers, deliver } = setup();
    deliver({ plexgraph: "frame", data: new Uint8Array([0xc1]).buffer }); // 0xc1 is never valid msgpack
    expect(handlers.onError).toHaveBeenCalledOnce();
    expect(handlers.onMessage).not.toHaveBeenCalled();
  });

  it("reports the widget going away, and stops listening once closed", () => {
    const { transport, handlers, deliver, isListening } = setup();
    deliver({ plexgraph: "closed" });
    expect(handlers.onClose).toHaveBeenCalledOnce();
    transport.close();
    expect(isListening()).toBe(false);
  });

  it("posts an export result as a distinct message, transferring the buffer", () => {
    const { transport, posted } = setup();
    const data = new Uint8Array([1, 2, 3]);
    transport.sendExport("req-1", "png", data, null);
    expect(posted).toHaveLength(2); // the initial "hello", then this
    const [message] = posted[1] as [Record<string, unknown>, string];
    expect(message).toEqual({ plexgraph: "export", id: "req-1", format: "png", error: null, data: data.buffer });
  });

  it("posts a failed export with no data", () => {
    const { transport, posted } = setup();
    transport.sendExport("req-2", "svg", null, "boom");
    const [message] = posted[1] as [Record<string, unknown>, string];
    expect(message).toEqual({ plexgraph: "export", id: "req-2", format: "svg", error: "boom", data: null });
  });
});
