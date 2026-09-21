import { describe, expect, it } from "vitest";
import { applyStyleMessage, reviveArrays, type StyleTarget } from "./messages";

function recorder() {
  const calls: [string, unknown][] = [];
  const target: StyleTarget = {
    setStyle: (u) => calls.push(["set", u]),
    resetStyle: () => calls.push(["reset", null]),
    paintNodes: (ids, color) => calls.push(["paint", { ids, color }]),
    clearPaint: () => calls.push(["clear", null]),
  };
  return { calls, target };
}
const wrap = (dtype: string, arr: ArrayBufferView) => ({ $dtype: dtype, $data: new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength) });

describe("reviveArrays", () => {
  it("turns wrapped bytes into typed arrays at any depth, including misaligned bytes", () => {
    const f64 = new Float64Array([1.5, NaN, -2]);
    // place the bytes at an odd offset, like a decoder slicing into a larger message
    const big = new Uint8Array(f64.byteLength + 3); big.set(new Uint8Array(f64.buffer), 3);
    const out = reviveArrays({ node: { color: { kind: "values", values: { $dtype: "f64", $data: big.subarray(3) } } }, list: [wrap("u8", new Uint8Array([1, 2]))] }) as any;
    expect(out.node.color.values).toBeInstanceOf(Float64Array);
    expect(Array.from(out.node.color.values as Float64Array).map(String)).toEqual(["1.5", "NaN", "-2"]);
    expect(Array.from(out.list[0])).toEqual([1, 2]);
  });
  it("leaves plain values alone and rejects a malformed array", () => {
    expect(reviveArrays({ a: 1, b: "x", c: [1, { d: null }] })).toEqual({ a: 1, b: "x", c: [1, { d: null }] });
    expect(() => reviveArrays({ $dtype: "f16", $data: new Uint8Array(2) })).toThrow(/bad array/);
    expect(() => reviveArrays({ $dtype: "f32", $data: [1, 2] })).toThrow(/bad array/);
  });
});

describe("applyStyleMessage", () => {
  it("merges with set, and resets before applying with replace", () => {
    const { calls, target } = recorder();
    applyStyleMessage(target, { type: "style", op: "set", spec: { node: { size: 12 } } });
    applyStyleMessage(target, { type: "style", op: "replace", spec: { edge: { width: 2 } } });
    expect(calls.map(c => c[0])).toEqual(["set", "reset", "set"]);
    expect(calls[2][1]).toEqual({ edge: { width: 2 } });
  });
  it("revives arrays inside the spec before applying it", () => {
    const { calls, target } = recorder();
    applyStyleMessage(target, { type: "style", op: "set", spec: { node: { size: { kind: "pixels", values: wrap("f32", new Float32Array([4, 8])) } } } });
    const values = (calls[0][1] as any).node.size.values;
    expect(values).toBeInstanceOf(Float32Array); expect(Array.from(values)).toEqual([4, 8]);
  });
  it("paints ids sent as raw uint32 bytes or as a plain list, and clears", () => {
    const { calls, target } = recorder();
    const ids = new Uint32Array([3, 7, 900]);
    applyStyleMessage(target, { type: "style", op: "paint", ids: new Uint8Array(ids.buffer), color: [1, 0, 0, 1] });
    applyStyleMessage(target, { type: "style", op: "paint", ids: [1, 2], color: null });
    applyStyleMessage(target, { type: "style", op: "clear_paint" });
    applyStyleMessage(target, { type: "style", op: "reset" });
    expect(calls).toEqual([["paint", { ids: [3, 7, 900], color: [1, 0, 0, 1] }], ["paint", { ids: [1, 2], color: null }], ["clear", null], ["reset", null]]);
  });
  it("rejects an unknown operation", () => {
    expect(() => applyStyleMessage(recorder().target, { type: "style", op: "explode" as never })).toThrow(/unknown style operation/);
  });
});
