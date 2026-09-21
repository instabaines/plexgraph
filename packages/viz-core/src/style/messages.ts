import type { StyleMessage } from "../ir/types";
import type { StyleSpec } from "./spec";

/** The renderer methods a style message can call (kept narrow so messages can be tested without WebGL). */
export interface StyleTarget {
  setStyle(update: StyleSpec): void;
  resetStyle(): void;
  paintNodes(ids: number[], color: number[] | null): void;
  clearPaint(): void;
}

const DTYPES: Record<string, (buffer: ArrayBuffer) => ArrayLike<number>> = {
  f32: (b) => new Float32Array(b),
  f64: (b) => new Float64Array(b),
  i32: (b) => new Int32Array(b),
  u32: (b) => new Uint32Array(b),
  u8: (b) => new Uint8Array(b),
};

/** Turn `{ "$dtype", "$data" }` wrappers back into typed arrays, anywhere inside a decoded message. */
export function reviveArrays(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(reviveArrays);
  if (value === null || typeof value !== "object" || ArrayBuffer.isView(value)) return value;
  const obj = value as Record<string, unknown>;
  if ("$dtype" in obj) {
    const make = DTYPES[String(obj["$dtype"])];
    const data = obj["$data"];
    if (!make || !(data instanceof Uint8Array)) throw new TypeError(`bad array in style message: ${JSON.stringify(obj["$dtype"])}`);
    // Copy to an aligned buffer: the bytes arrive at whatever offset the decoder left them.
    const aligned = new Uint8Array(data.byteLength);
    aligned.set(data);
    return make(aligned.buffer);
  }
  return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, reviveArrays(v)]));
}

/** Apply a decoded style message. Throws (leaving the current style unchanged) if the style is invalid. */
export function applyStyleMessage(target: StyleTarget, msg: StyleMessage): void {
  switch (msg.op) {
    case "set":
      target.setStyle(reviveArrays(msg.spec) as StyleSpec);
      return;
    case "replace":
      target.resetStyle();
      target.setStyle(reviveArrays(msg.spec) as StyleSpec);
      return;
    case "reset":
      target.resetStyle();
      return;
    case "clear_paint":
      target.clearPaint();
      return;
    case "paint": {
      const ids = msg.ids instanceof Uint8Array ? Array.from(reviveArrays({ $dtype: "u32", $data: msg.ids }) as ArrayLike<number>) : (msg.ids ?? []);
      target.paintNodes(ids, msg.color ?? null);
      return;
    }
  }
  throw new RangeError(`unknown style operation ${JSON.stringify((msg as { op?: string }).op)}`);
}
