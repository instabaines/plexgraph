import { describe, expect, it } from "vitest";
import type { WireConnector, WireNode } from "../ir/types";
import { mergeStyle, StyleManager, validateStyle, type StyleBase } from "./manager";
import type { StyleEnv } from "./engine";

const nodes: WireNode[] = [0, 1, 2].map(id => ({ id, key: `n${id}`, attrs: { team: id === 2 ? "b" : "a", v: id } }));
const connectors: WireConnector[] = [0, 1].map(id => ({ id, endpoints: [id, id + 1], directed: false, layer_id: null, t_start: null, t_end: null, weight: id + 1, attrs: {} }));
const env: StyleEnv = { nodes, connectors, timeConnectors: connectors, degree: () => 1, nodeActivity: () => ({ first: new Float64Array(3).fill(NaN), last: new Float64Array(3).fill(NaN) }), timeDomain: null };
const base: StyleBase = { nodeColor: [0.1, 0.2, 0.3, 1], nodeRadiusPx: 5, edgeColor: [0.6, 0.6, 0.6, 0.5], edgeWidthPx: 1.5, backgroundColor: [1, 1, 1, 1] };
const px = (a: Float32Array, i: number) => Array.from(a.slice(i * 4, i * 4 + 4)).map(v => Number(v.toFixed(3)));

describe("validateStyle", () => {
  it("catches typos and names the allowed fields", () => {
    expect(() => validateStyle({ node: { colour: "red" } as never })).toThrow(/unknown style field "colour" in node; allowed: color, size/);
    expect(() => validateStyle({ nodes: {} } as never)).toThrow(/unknown style field "nodes"/);
    expect(() => validateStyle({ node: { label: { size: 3 } as never } })).toThrow(/node.label/);
  });
  it("rejects out-of-range values", () => {
    expect(() => validateStyle({ node: { opacity: 1.5 } })).toThrow(/node.opacity must be a number from 0 to 1/);
    expect(() => validateStyle({ edge: { curvature: 9 } })).toThrow(/between -2 and 2/);
    expect(() => validateStyle({ edge: { arrowScale: 0 } })).toThrow(/positive/);
    expect(() => validateStyle({ node: { label: { mode: "some" as never } } })).toThrow(/"hover", "all" or "none"/);
    expect(() => validateStyle({ node: { outline: { width: -1 } } })).toThrow(/non-negative/);
    expect(() => validateStyle({ node: { label: { fontSize: 2 } } })).toThrow(/between 4 and 72/);
  });
  it("rejects a malformed dash pattern", () => {
    expect(() => validateStyle({ edge: { dash: [1, 2, 3] as never } })).toThrow(/edge.dash must be an array of 4 non-negative numbers/);
    expect(() => validateStyle({ edge: { dash: [1, -2, 0, 0] } })).toThrow(/edge.dash must be an array of 4 non-negative numbers/);
    expect(() => validateStyle({ edge: { dash: "dashed" as never } })).toThrow(/edge.dash must be an array of 4 non-negative numbers/);
  });
  it("accepts a full valid style", () => {
    expect(() => validateStyle({ node: { color: "#f00", size: 12, shape: "square", opacity: 0.5, outline: { color: "#000", width: 1 }, label: { mode: "all", fontSize: 12, halo: true } }, edge: { color: "#ccc", width: 2, opacity: 0.4, curvature: 0.2, arrowScale: 1.5, dash: [8, 5, 0, 0] }, background: "#fff" })).not.toThrow();
  });
});

describe("mergeStyle", () => {
  it("keeps fields you omit and replaces the ones you give", () => {
    const a = mergeStyle({}, { node: { color: "#f00", size: 10 } });
    const b = mergeStyle(a, { node: { size: 20 }, edge: { width: 3 } });
    expect(b).toEqual({ node: { color: "#f00", size: 20 }, edge: { width: 3 } });
  });
  it("clears a field with null, and a whole group with null", () => {
    const a = mergeStyle({}, { node: { color: "#f00", size: 10 }, edge: { width: 3 } });
    expect(mergeStyle(a, { node: { color: null } })).toEqual({ node: { size: 10 }, edge: { width: 3 } });
    expect(mergeStyle(a, { node: null })).toEqual({ edge: { width: 3 } });
    expect(mergeStyle(a, { node: { color: null, size: null } })).toEqual({ edge: { width: 3 } });
  });
  it("merges label fields instead of replacing the label", () => {
    const a = mergeStyle({}, { node: { label: { mode: "all", fontSize: 14 } } });
    expect(mergeStyle(a, { node: { label: { halo: true } } }).node?.label).toEqual({ mode: "all", fontSize: 14, halo: true });
  });
  it("does not modify its inputs", () => {
    const a = { node: { size: 10 } };
    mergeStyle(a, { node: { size: 20 } });
    expect(a).toEqual({ node: { size: 10 } });
  });
});

describe("StyleManager", () => {
  it("falls back to the base defaults when nothing is styled", () => {
    const r = new StyleManager().resolve(env, base);
    expect(px(r.nodeColors, 1)).toEqual([0.1, 0.2, 0.3, 1]);
    expect(Array.from(r.nodeSizes)).toEqual([10, 10, 10]);
    expect(Array.from(r.edgeWidths)).toEqual([1.5, 1.5]);
    expect([r.nodeOpacity, r.edgeOpacity, r.curvature, r.arrowScale]).toEqual([1, 1, 0, 1]);
    expect(r.dash).toBeNull();
    expect(r.label).toMatchObject({ mode: "hover", fontSize: 11, halo: false });
    expect(r.background).toEqual(base.backgroundColor);
  });

  it("applies encodings, opacity, outline, shape and labels", () => {
    const m = new StyleManager();
    const r = m.apply({
      node: { color: { kind: "attribute", attribute: "team", palette: "tab10" }, size: { kind: "attribute", attribute: "v", range: [6, 18] }, shape: "diamond", opacity: 0.5, outline: { color: "#000000", width: 2 }, label: { mode: "all", fontSize: 13, color: "#112233", halo: true } },
      edge: { color: "#ff0000", width: { kind: "weight", range: [1, 5] }, opacity: 0.4, curvature: 0.25, arrowScale: 2, dash: [8, 5, 0, 0] },
      background: "#101010",
    }, env, base);
    expect(px(r.nodeColors, 0)).toEqual(px(r.nodeColors, 1));
    expect(px(r.nodeColors, 0)).not.toEqual(px(r.nodeColors, 2));
    expect(Array.from(r.nodeSizes)).toEqual([6, 12, 18]);
    expect(Array.from(r.nodeShapes)).toEqual([3, 3, 3]);
    expect(r.nodeOpacity).toBe(0.5);
    expect(r.outline.width).toBe(2);
    expect(r.nodeLegend).toMatchObject({ type: "categorical", title: "team" });
    expect(px(r.edgeColors, 0)).toEqual([1, 0, 0, 1]);
    expect(Array.from(r.edgeWidths)).toEqual([1, 5]);
    expect([r.edgeOpacity, r.curvature, r.arrowScale]).toEqual([0.4, 0.25, 2]);
    expect(r.dash).toEqual([8, 5, 0, 0]);
    expect(r.label).toMatchObject({ mode: "all", fontSize: 13, halo: true });
    expect(r.background[0]).toBeCloseTo(16 / 255, 3);
  });

  it("layers colors: encoding, then option overrides, then painted nodes", () => {
    const m = new StyleManager();
    const withOverride: StyleBase = { ...base, nodeOverride: i => (i === 1 ? [0, 1, 0, 1] : undefined) };
    m.apply({ node: { color: "#ff0000" } }, env, withOverride);
    m.paint([0, 1], [0, 0, 1, 1]);
    const r = m.resolve(env, withOverride);
    expect(px(r.nodeColors, 0)).toEqual([0, 0, 1, 1]);   // painted beats the encoding
    expect(px(r.nodeColors, 1)).toEqual([0, 0, 1, 1]);   // painted beats the override too
    expect(px(r.nodeColors, 2)).toEqual([1, 0, 0, 1]);   // untouched node keeps the encoding
    m.paint([0], null);
    expect(px(m.resolve(env, withOverride).nodeColors, 0)).toEqual([1, 0, 0, 1]);
    m.clearPaint();
    expect(px(m.resolve(env, withOverride).nodeColors, 1)).toEqual([0, 1, 0, 1]);
  });

  it("ignores painted ids that are not in the graph and rejects invalid ids", () => {
    const m = new StyleManager();
    m.paint([99], [1, 1, 1, 1]);
    expect(() => m.resolve(env, base)).not.toThrow();
    expect(() => m.paint([-1], [1, 1, 1, 1])).toThrow(/non-negative integer/);
  });

  it("uses each connector's own default color where an edge encoding has no value", () => {
    const layered: StyleBase = { ...base, edgeBaseColor: i => (i === 0 ? [0, 1, 0, 1] : [0, 0, 1, 1]) };
    const m = new StyleManager();
    expect(px(m.resolve(env, layered).edgeColors, 0)).toEqual([0, 1, 0, 1]);
    const r = m.apply({ edge: { color: { kind: "attribute", attribute: "nothing", palette: ["#ffffff"] } } }, env, layered);
    expect(px(r.edgeColors, 1)).toEqual([0, 0, 1, 1]);
  });

  it("is transactional: an update that fails to resolve leaves the style unchanged", () => {
    const m = new StyleManager();
    m.apply({ node: { size: 14 } }, env, base);
    expect(() => m.apply({ node: { color: { kind: "degree", colormap: "nope" } } }, env, base)).toThrow(/unknown colormap/);
    expect(() => m.apply({ node: { size: 3 }, edge: { color: { kind: "time" } } }, env, base)).toThrow(/no time information/);
    expect(m.get()).toEqual({ node: { size: 14 } });
    expect(Array.from(m.resolve(env, base).nodeSizes)).toEqual([14, 14, 14]);
  });

  it("returns a plain copy of the current style, and resets everything", () => {
    const m = new StyleManager({ node: { size: 9 } });
    const copy = m.get(); (copy.node as { size: number }).size = 1;
    expect(m.get()).toEqual({ node: { size: 9 } });
    m.paint([0], [1, 0, 0, 1]);
    m.reset();
    expect(m.get()).toEqual({}); expect(m.paintedCount()).toBe(0);
  });
});

describe("StyleManager.get", () => {
  it("copies the structure but shares large arrays instead of converting them", () => {
    const big = new Float64Array(200_000).fill(1);
    const m = new StyleManager({ node: { color: { kind: "values", values: big } } });
    const copy = m.get() as unknown as { node: { color: { values: Float64Array } } };
    expect(copy.node.color.values).toBe(big);          // same array, not a 200,000-element conversion
    (copy.node as { size?: number }).size = 5;
    expect(m.get()).not.toHaveProperty("node.size");   // but editing the copy never changes the manager's style
  });
});

describe("StyleManager edge cache", () => {
  it("reuses the edge result across node-only updates, and recomputes when edges or the graph change", () => {
    const m = new StyleManager();
    const keyed: StyleEnv = { ...env, cacheKey: 1 };
    const first = m.apply({ edge: { color: "#ff0000", width: 3 } }, keyed, base);
    const nodeOnly = m.apply({ node: { size: 20 } }, keyed, base);
    expect(nodeOnly.edgeColors).toBe(first.edgeColors);            // same array: not recomputed
    expect(nodeOnly.edgeWidths).toBe(first.edgeWidths);
    expect(Array.from(nodeOnly.nodeSizes)).toEqual([20, 20, 20]);  // but the node change did apply
    const changed = m.apply({ edge: { width: 5 } }, keyed, base);
    expect(changed.edgeColors).not.toBe(first.edgeColors);
    expect(Array.from(changed.edgeWidths)).toEqual([5, 5]);
    expect(px(changed.edgeColors, 0)).toEqual([1, 0, 0, 1]);       // merged, so the colour stayed
    const newGraph = m.resolve({ ...env, cacheKey: 2 }, base);
    expect(newGraph.edgeColors).not.toBe(changed.edgeColors);
  });
  it("does not cache anything without a graph key, and a failed update leaves the cache valid", () => {
    const m = new StyleManager();
    const a = m.apply({ edge: { width: 2 } }, env, base), b = m.apply({ node: { size: 9 } }, env, base);
    expect(b.edgeWidths).not.toBe(a.edgeWidths);
    const keyed: StyleEnv = { ...env, cacheKey: 7 };
    const good = m.apply({ edge: { width: 4 } }, keyed, base);
    expect(() => m.apply({ edge: { color: { kind: "time" } } }, keyed, base)).toThrow(/no time information/);
    expect(m.resolve(keyed, base).edgeWidths).toBe(good.edgeWidths);
  });
});
