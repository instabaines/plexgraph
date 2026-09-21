import { describe, expect, it } from "vitest";
import type { WireConnector, WireNode } from "../ir/types";
import { computeTimeDomain } from "../render/time";
import { parseColor, rgbaToHex } from "./colors";
import { colormapNames, paletteColors, paletteNames, sampleColormap } from "./colormaps";
import { resolveColors, resolveShapes, resolveSizes, type StyleEnv } from "./engine";

const node = (id: number, attrs: Record<string, unknown> = {}): WireNode => ({ id, key: `n${id}`, attrs });
const edge = (id: number, a: number, b: number, extra: Partial<WireConnector> = {}): WireConnector => ({
  id, endpoints: [a, b], directed: false, layer_id: null, t_start: null, t_end: null, weight: null, attrs: {}, ...extra,
});

function env(nodes: WireNode[], connectors: WireConnector[]): StyleEnv {
  const degrees = nodes.map(n => new Set(connectors.filter(c => c.endpoints.includes(n.id)).flatMap(c => c.endpoints).filter(x => x !== n.id)).size);
  return {
    nodes, connectors, timeConnectors: connectors,
    degree: i => degrees[i],
    nodeActivity: () => {
      const first = new Float64Array(nodes.length).fill(NaN), last = new Float64Array(nodes.length).fill(NaN);
      for (const c of connectors) for (const n of c.endpoints) {
        if (c.t_start !== null) first[n] = Number.isNaN(first[n]) ? c.t_start : Math.min(first[n], c.t_start);
        if (c.t_end !== null) last[n] = Number.isNaN(last[n]) ? c.t_end : Math.max(last[n], c.t_end);
      }
      return { first, last };
    },
    timeDomain: computeTimeDomain(connectors),
  };
}
const rgba = (out: Float32Array, i: number) => Array.from(out.slice(i * 4, i * 4 + 4)).map(v => Number(v.toFixed(3)));
const BASE: [number, number, number, number] = [0.5, 0.5, 0.5, 1];

describe("parseColor", () => {
  it("reads hex in every length", () => {
    expect(parseColor("#f00")).toEqual([1, 0, 0, 1]);
    expect(parseColor("#f008")).toEqual([1, 0, 0, 136 / 255]);
    expect(rgbaToHex(parseColor("#1f77b4"))).toBe("#1f77b4");
    expect(parseColor("#00000080")[3]).toBeCloseTo(0.502, 2);
  });
  it("reads [r, g, b] and [r, g, b, a] in 0-1", () => {
    expect(parseColor([0.1, 0.2, 0.3])).toEqual([0.1, 0.2, 0.3, 1]);
    expect(parseColor(new Float32Array([1, 0, 0, 0.5]))[3]).toBe(0.5);
  });
  it("explains what it could not read", () => {
    expect(() => parseColor([255, 0, 0])).toThrow(/0-1.*hex/);
    expect(() => parseColor([1, 2])).toThrow(/3 or 4 numbers/);
    expect(() => parseColor("crimson-ish")).toThrow(/cannot read color/); // no browser here, so names need Python or hex
  });
});

describe("colormaps and palettes", () => {
  it("hit their published end colors and can be reversed", () => {
    expect(rgbaToHex(sampleColormap("viridis", 0))).toBe("#440154");
    expect(rgbaToHex(sampleColormap("viridis", 1))).toBe("#fde725");
    expect(rgbaToHex(sampleColormap("viridis", 0, true))).toBe("#fde725");
    expect(rgbaToHex(sampleColormap("Blues", 1))).toBe("#08306b");
  });
  it("clamp out-of-range and non-finite positions", () => {
    expect(sampleColormap("plasma", -5)).toEqual(sampleColormap("plasma", 0));
    expect(sampleColormap("plasma", 9)).toEqual(sampleColormap("plasma", 1));
    expect(sampleColormap("plasma", NaN)).toEqual(sampleColormap("plasma", 0));
  });
  it("interpolate smoothly between control colors", () => {
    const mid = sampleColormap("coolwarm", 0.5);
    expect(mid[0]).toBeGreaterThan(0.7); expect(mid[2]).toBeGreaterThan(0.7); // the pale middle of a diverging map
  });
  it("list their names and refuse unknown ones with the choices", () => {
    expect(colormapNames()).toEqual(expect.arrayContaining(["viridis", "plasma", "coolwarm", "Blues"]));
    expect(paletteNames()).toEqual(expect.arrayContaining(["default", "tab10", "Set2"]));
    expect(() => sampleColormap("rainbowz", 0.5)).toThrow(/unknown colormap.*viridis/);
    expect(() => paletteColors("nope")).toThrow(/unknown palette.*tab10/);
    expect(paletteColors("tab10")).toHaveLength(10);
  });
});

describe("resolveColors: constants and explicit colors", () => {
  const e = env([node(0), node(1), node(2)], [edge(0, 0, 1), edge(1, 1, 2)]);
  it("leaves the base color when nothing is specified", () => {
    expect(rgba(resolveColors("node", undefined, e, BASE).colors, 2)).toEqual(BASE);
  });
  it("fills a constant given as a string, an array or {kind:'constant'}", () => {
    expect(rgba(resolveColors("node", "#ff0000", e, BASE).colors, 1)).toEqual([1, 0, 0, 1]);
    expect(rgba(resolveColors("edge", [0, 1, 0, 0.5], e, BASE).colors, 1)).toEqual([0, 1, 0, 0.5]);
    expect(rgba(resolveColors("node", { kind: "constant", color: "#0000ff" }, e, BASE).colors, 0)).toEqual([0, 0, 1, 1]);
  });
  it("takes one color per element, flat or as a list, with null meaning missing", () => {
    const flat = resolveColors("node", { kind: "colors", colors: [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1] }, e, BASE).colors;
    expect(rgba(flat, 1)).toEqual([0, 1, 0, 1]);
    const list = resolveColors("node", { kind: "colors", colors: ["#ff0000", null as unknown as string, [0, 0, 1]] }, e, BASE).colors;
    expect(rgba(list, 0)).toEqual([1, 0, 0, 1]); expect(rgba(list, 1)).toEqual(BASE); expect(rgba(list, 2)).toEqual([0, 0, 1, 1]);
    expect(() => resolveColors("node", { kind: "colors", colors: ["#fff"] }, e, BASE)).toThrow(/one entry per node \(3\)/);
  });
});

describe("resolveColors: attributes", () => {
  const nodes = [node(0, { team: "red" }), node(1, { team: "blue" }), node(2, { team: "red" }), node(3, {})];
  const e = env(nodes, []);
  it("gives each distinct value a palette color, cycling and reporting counts, and leaves missing values on the base color", () => {
    const { colors, legend } = resolveColors("node", { kind: "attribute", attribute: "team", palette: "tab10" }, e, BASE);
    expect(rgbaToHex([colors[0], colors[1], colors[2], 1])).toBe("#1f77b4");
    expect(rgba(colors, 0)).toEqual(rgba(colors, 2));
    expect(rgba(colors, 0)).not.toEqual(rgba(colors, 1));
    expect(rgba(colors, 3)).toEqual(BASE);
    expect(legend).toMatchObject({ type: "categorical", title: "team" });
    expect(legend?.type === "categorical" && legend.entries.map(x => [x.value, x.count])).toEqual([["red", 2], ["blue", 1]]);
  });
  it("accepts an explicit list of colors and a missing color", () => {
    const { colors } = resolveColors("node", { kind: "attribute", attribute: "team", palette: ["#000000", "#ffffff"], missing: "#ff00ff" }, e, BASE);
    expect(rgba(colors, 0)).toEqual([0, 0, 0, 1]); expect(rgba(colors, 1)).toEqual([1, 1, 1, 1]); expect(rgba(colors, 3)).toEqual([1, 0, 1, 1]);
  });
  it("switches to a colormap for a numeric attribute with many values, and honours an explicit scale", () => {
    const many = env(Array.from({ length: 20 }, (_, i) => node(i, { score: i })), []);
    const auto = resolveColors("node", { kind: "attribute", attribute: "score", colormap: "viridis" }, many, BASE);
    expect(auto.legend).toMatchObject({ type: "continuous", min: 0, max: 19, colormap: "viridis" });
    expect(rgbaToHex([auto.colors[0], auto.colors[1], auto.colors[2], 1])).toBe("#440154");
    expect(rgbaToHex([auto.colors[19 * 4], auto.colors[19 * 4 + 1], auto.colors[19 * 4 + 2], 1])).toBe("#fde725");
    const few = env([node(0, { s: 1 }), node(1, { s: 2 }), node(2, { s: 1 })], []);
    expect(resolveColors("node", { kind: "attribute", attribute: "s" }, few, BASE).legend?.type).toBe("categorical");
    expect(resolveColors("node", { kind: "attribute", attribute: "s", scale: "continuous" }, few, BASE).legend?.type).toBe("continuous");
  });
  it("respects a fixed domain, reverse, and treats non-numeric values as missing", () => {
    const e2 = env([node(0, { v: 0 }), node(1, { v: 100 }), node(2, { v: "n/a" })], []);
    const { colors, legend } = resolveColors("node", { kind: "attribute", attribute: "v", scale: "continuous", domain: [0, 50], reverse: true, missing: "#ff0000" }, e2, BASE);
    expect(rgbaToHex([colors[0], colors[1], colors[2], 1])).toBe("#fde725"); // reversed: 0 is the top of the map
    expect(rgbaToHex([colors[4], colors[5], colors[6], 1])).toBe("#440154"); // 100 clamps to the end of the domain
    expect(rgba(colors, 2)).toEqual([1, 0, 0, 1]);
    expect(legend).toMatchObject({ min: 0, max: 50, reverse: true });
  });
});

describe("resolveColors: degree, weight and explicit values", () => {
  const nodes = [node(0), node(1), node(2), node(3)];
  const connectors = [edge(0, 0, 1, { weight: 1 }), edge(1, 0, 2, { weight: 5 }), edge(2, 0, 3)];
  const e = env(nodes, connectors);
  it("colors nodes by degree, and edges by weight with unweighted edges left on the base color", () => {
    const byDegree = resolveColors("node", { kind: "degree", colormap: "Reds" }, e, BASE);
    expect(byDegree.legend).toMatchObject({ min: 1, max: 3, title: "degree" });
    expect(rgbaToHex([byDegree.colors[0], byDegree.colors[1], byDegree.colors[2], 1])).toBe("#67000d"); // node 0 has degree 3
    const byWeight = resolveColors("edge", { kind: "weight", colormap: "Blues" }, e, BASE);
    expect(rgba(byWeight.colors, 2)).toEqual(BASE);
    expect(byWeight.legend).toMatchObject({ min: 1, max: 5 });
  });
  it("puts each property on the right kind of element", () => {
    expect(() => resolveColors("edge", { kind: "degree" }, e, BASE)).toThrow(/node property/);
    expect(() => resolveColors("node", { kind: "weight" }, e, BASE)).toThrow(/edge property/);
    expect(() => resolveColors("node", { kind: "time" }, e, BASE)).toThrow(/no time information/);
  });
  it("maps one number per element through a colormap and rejects the wrong length", () => {
    const { colors } = resolveColors("node", { kind: "values", values: [0, 1, 2, 3], colormap: "Greys" }, e, BASE);
    expect(rgbaToHex([colors[0], colors[1], colors[2], 1])).toBe("#ffffff");
    expect(rgbaToHex([colors[12], colors[13], colors[14], 1])).toBe("#000000");
    expect(() => resolveColors("node", { kind: "values", values: [1, 2] }, e, BASE)).toThrow(/one number per node \(4\)/);
  });
});

describe("resolveColors: time buckets", () => {
  // events at t = 0..9, one per unit; node k touches events k and k+1
  const nodes = Array.from({ length: 11 }, (_, i) => node(i));
  const connectors = Array.from({ length: 10 }, (_, i) => edge(i, i, i + 1, { t_start: i, t_end: i }));
  const e = env(nodes, connectors);

  it("colors each edge by the bucket its time falls in, and reports how many edges each bucket holds", () => {
    const { colors, legend } = resolveColors("edge", { kind: "timeBucket", buckets: 3 }, e, BASE);
    const ids = Array.from({ length: 10 }, (_, i) => rgbaToHex([colors[i * 4], colors[i * 4 + 1], colors[i * 4 + 2], 1]));
    expect(new Set(ids).size).toBe(3);
    expect(ids[0]).toBe(ids[2]); expect(ids[3]).toBe(ids[5]); expect(ids[6]).toBe(ids[9]); // [0,3) [3,6) [6,9]
    expect(ids[2]).not.toBe(ids[3]);
    expect(legend?.type === "categorical" && legend.entries.map(x => x.count)).toEqual([3, 3, 4]);
  });
  it("uses the same colors as the ribbon by default, or a colormap when asked", () => {
    const ribbon = resolveColors("edge", { kind: "timeBucket", buckets: 2 }, e, BASE).colors;
    const map = resolveColors("edge", { kind: "timeBucket", buckets: 2, colormap: "viridis" }, e, BASE).colors;
    expect(rgba(ribbon, 0)).not.toEqual(rgba(map, 0));
    expect(rgbaToHex([map[0], map[1], map[2], 1])).not.toBe(rgbaToHex([map[36], map[37], map[38], 1]));
  });
  it("can split by equal numbers of events", () => {
    const skewed = Array.from({ length: 12 }, (_, i) => edge(i, 0, 1, { t_start: i < 9 ? i / 9 : 10 * (i - 8), t_end: i < 9 ? i / 9 : 10 * (i - 8) }));
    const es = env([node(0), node(1)], skewed);
    const time = resolveColors("edge", { kind: "timeBucket", buckets: 3 }, es, BASE).legend;
    const events = resolveColors("edge", { kind: "timeBucket", buckets: 3, split: "events" }, es, BASE).legend;
    expect(time?.type === "categorical" && time.entries.map(x => x.count)).toEqual([9, 1, 2]); // t=10 sits on a boundary: second bucket only
    expect(events?.type === "categorical" && events.entries.every(x => (x.count ?? 0) >= 3)).toBe(true);
  });
  it("colors nodes by the bucket of their first or last activity and leaves untimed nodes on the base color", () => {
    const withLoner = env([...nodes, node(11)], connectors);
    const first = resolveColors("node", { kind: "timeBucket", buckets: 2, nodeTime: "first" }, withLoner, BASE).colors;
    const last = resolveColors("node", { kind: "timeBucket", buckets: 2, nodeTime: "last" }, withLoner, BASE).colors;
    expect(rgba(first, 11)).toEqual(BASE);
    expect(rgba(first, 4)).not.toEqual(rgba(first, 9));       // first activity early vs late
    expect(rgba(first, 5)).not.toEqual(rgba(last, 5));         // node 5: first activity at t=4 (early bucket), last at t=5 (later bucket)
  });
  it("refuses a graph without time", () => {
    expect(() => resolveColors("edge", { kind: "timeBucket" }, env([node(0), node(1)], [edge(0, 0, 1)]), BASE)).toThrow(/no time information/);
  });
});

describe("resolveSizes", () => {
  const nodes = [node(0, { v: 0 }), node(1, { v: 50 }), node(2, { v: 100 }), node(3, {})];
  const e = env(nodes, [edge(0, 0, 1), edge(1, 0, 2), edge(2, 0, 3)]);
  it("fills constants and the base size", () => {
    expect(Array.from(resolveSizes("node", 12, e, 8))).toEqual([12, 12, 12, 12]);
    expect(Array.from(resolveSizes("node", undefined, e, 8))).toEqual([8, 8, 8, 8]);
    expect(Array.from(resolveSizes("node", { kind: "constant", value: 3 }, e, 8))).toEqual([3, 3, 3, 3]);
  });
  it("maps a numeric attribute onto a pixel range, sending missing values to the base size", () => {
    const s = resolveSizes("node", { kind: "attribute", attribute: "v", range: [4, 24] }, e, 9);
    expect(Array.from(s)).toEqual([4, 14, 24, 9]);
  });
  it("supports sqrt and log scales, a fixed domain and degree", () => {
    const sqrt = resolveSizes("node", { kind: "attribute", attribute: "v", range: [0, 10], scale: "sqrt" }, e, 0);
    expect(sqrt[1]).toBeCloseTo(10 * Math.sqrt(0.5), 5);
    const log = resolveSizes("node", { kind: "attribute", attribute: "v", range: [0, 10], scale: "log" }, e, 0);
    expect(log[1]).toBeGreaterThan(5); // log scales lift the middle
    const clamped = resolveSizes("node", { kind: "attribute", attribute: "v", range: [0, 10], domain: [0, 50] }, e, 0);
    expect(clamped[2]).toBe(10);
    const deg = resolveSizes("node", { kind: "degree", range: [5, 15] }, e, 0);
    expect(deg[0]).toBe(15); expect(deg[1]).toBe(5);
  });
  it("sizes edges by weight and rejects bad input", () => {
    const w = env([node(0), node(1)], [edge(0, 0, 1, { weight: 1 }), edge(1, 0, 1, { weight: 3 }), edge(2, 0, 1)]);
    expect(Array.from(resolveSizes("edge", { kind: "weight", range: [1, 5] }, w, 2))).toEqual([1, 5, 2]);
    expect(() => resolveSizes("node", -3, e, 1)).toThrow(/non-negative/);
    expect(() => resolveSizes("node", { kind: "values", values: [1], range: [1, 2] }, e, 1)).toThrow(/one number per node/);
    expect(() => resolveSizes("node", { kind: "attribute", attribute: "v", range: [NaN, 2] }, e, 1)).toThrow(/range\[0\]/);
  });
});

describe("resolveShapes", () => {
  const e = env([node(0, { k: "a" }), node(1, { k: "b" }), node(2, { k: "a" }), node(3)], []);
  it("defaults to circles and fills a constant", () => {
    expect(Array.from(resolveShapes(undefined, e))).toEqual([0, 0, 0, 0]);
    expect(Array.from(resolveShapes("square", e))).toEqual([1, 1, 1, 1]);
  });
  it("assigns shapes by attribute value in first-seen order, leaving missing values as circles", () => {
    expect(Array.from(resolveShapes({ kind: "attribute", attribute: "k", shapes: ["triangle", "diamond"] }, e))).toEqual([2, 3, 2, 0]);
  });
  it("takes explicit per-node shapes and rejects unknown ones", () => {
    expect(Array.from(resolveShapes({ kind: "values", values: ["cross", 0, "square", "circle"] }, e))).toEqual([4, 0, 1, 0]);
    expect(() => resolveShapes("hexagon" as never, e)).toThrow(/unknown shape.*circle/);
    expect(() => resolveShapes({ kind: "values", values: ["circle"] }, e)).toThrow(/one entry per node/);
  });
});

describe("partial and explicit encodings (used by the Python API)", () => {
  const e = env([node(0), node(1), node(2)], [edge(0, 0, 1), edge(1, 1, 2)]);
  it("leaves elements with present=0 on their base color", () => {
    const flat = [1, 0, 0, 1, 9, 9, 9, 9, 0, 0, 1, 1];
    const { colors } = resolveColors("node", { kind: "colors", colors: flat, present: [1, 0, 1] }, e, BASE);
    expect(rgba(colors, 0)).toEqual([1, 0, 0, 1]); expect(rgba(colors, 1)).toEqual(BASE); expect(rgba(colors, 2)).toEqual([0, 0, 1, 1]);
    expect(() => resolveColors("node", { kind: "colors", colors: flat, present: [1] }, e, BASE)).toThrow(/present must have one entry/);
  });
  it("takes pixel sizes as given, with NaN meaning the base size", () => {
    expect(Array.from(resolveSizes("node", { kind: "pixels", values: [4, NaN, 20] }, e, 9))).toEqual([4, 9, 20]);
    expect(Array.from(resolveSizes("edge", { kind: "pixels", values: [1, 3] }, e, 2))).toEqual([1, 3]);
    expect(() => resolveSizes("node", { kind: "pixels", values: [1, 2] }, e, 9)).toThrow(/one number per node \(3\)/);
    expect(() => resolveSizes("node", { kind: "pixels", values: [1, -2, 3] }, e, 9)).toThrow(/non-negative/);
  });
});
