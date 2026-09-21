import type { WireConnector, WireNode } from "../ir/types";
import { formatTime, timeBucketEdges, timeSliceColor, type TimeDomain } from "../render/time";
import { parseColor, type ColorInput, type RGBA } from "./colors";
import { DEFAULT_PALETTE, paletteColors, sampleColormap } from "./colormaps";
import { NODE_SHAPES, type ColorEncoding, type NodeShape, type ShapeEncoding, type SizeEncoding } from "./spec";

export type Target = "node" | "edge";

/** What the engine needs to know about the graph; the renderer supplies it. */
export interface StyleEnv {
  nodes: WireNode[];
  /** The connectors edge styles apply to, in a fixed order. */
  connectors: WireConnector[];
  /** Every connector, used to place time-bucket boundaries. */
  timeConnectors: WireConnector[];
  degree(nodeIndex: number): number;
  /** Earliest start and latest end among each node's time-bounded connectors; NaN where there are none. */
  nodeActivity(): { first: Float64Array; last: Float64Array };
  timeDomain: TimeDomain | null;
  /** Changes whenever the graph changes; lets results for an unchanged graph be reused. */
  cacheKey?: number;
}

export type ColorLegend =
  | { type: "categorical"; title: string; entries: { value: string; color: RGBA; count?: number }[] }
  | { type: "continuous"; title: string; colormap: string; reverse: boolean; min: number; max: number };

export interface ColorResult { colors: Float32Array; legend: ColorLegend | null }

/** A color for every element, or a function giving each element its own (edges default to their layer color). */
export type BaseColor = RGBA | ((index: number) => RGBA);
const baseAt = (base: BaseColor, i: number): RGBA => (typeof base === "function" ? base(i) : base);

const MANY_DISTINCT = 12; // numeric attributes with more distinct values than this are treated as continuous

const countOf = (target: Target, env: StyleEnv): number => (target === "node" ? env.nodes.length : env.connectors.length);
const attrsOf = (target: Target, env: StyleEnv, i: number): Record<string, unknown> => (target === "node" ? env.nodes[i].attrs : env.connectors[i].attrs);

function isPlainColor(enc: unknown): enc is ColorInput {
  return typeof enc === "string" || Array.isArray(enc) || ArrayBuffer.isView(enc);
}

function fill(n: number, color: BaseColor): Float32Array {
  const out = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) out.set(baseAt(color, i), i * 4);
  return out;
}

/** Numbers for each element, NaN where missing, plus a title for legends. */
function sourceValues(target: Target, kind: string, env: StyleEnv, spec: { attribute?: string; values?: ArrayLike<number> }): { values: Float64Array; title: string } {
  const n = countOf(target, env);
  const out = new Float64Array(n).fill(NaN);
  switch (kind) {
    case "attribute": {
      if (!spec.attribute) throw new TypeError("an attribute encoding needs an attribute name");
      for (let i = 0; i < n; i++) {
        const v = attrsOf(target, env, i)[spec.attribute];
        if (typeof v === "number" && Number.isFinite(v)) out[i] = v;
      }
      return { values: out, title: spec.attribute };
    }
    case "degree":
      if (target !== "node") throw new RangeError("degree is a node property; edges can use weight, time or an attribute");
      for (let i = 0; i < n; i++) out[i] = env.degree(i);
      return { values: out, title: "degree" };
    case "weight":
      if (target !== "edge") throw new RangeError("weight is an edge property; nodes can use degree, time or an attribute");
      for (let i = 0; i < n; i++) out[i] = env.connectors[i].weight ?? NaN;
      return { values: out, title: "weight" };
    case "time": {
      if (env.timeDomain === null) throw new RangeError("the graph has no time information");
      if (target === "node") out.set(env.nodeActivity().first);
      else for (let i = 0; i < n; i++) out[i] = env.connectors[i].t_start ?? NaN;
      return { values: out, title: "time" };
    }
    case "values": {
      const v = spec.values;
      if (!v || v.length !== n) throw new RangeError(`values must have one number per ${target} (${n}), got ${v ? v.length : "none"}`);
      for (let i = 0; i < n; i++) out[i] = Number(v[i]);
      return { values: out, title: "value" };
    }
  }
  throw new RangeError(`unknown source ${JSON.stringify(kind)}`);
}

function extent(values: Float64Array, domain?: [number, number]): [number, number] {
  if (domain) {
    if (!(Number.isFinite(domain[0]) && Number.isFinite(domain[1]))) throw new RangeError("domain must be two finite numbers");
    return domain;
  }
  let lo = Infinity, hi = -Infinity;
  for (const v of values) if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  return Number.isFinite(lo) ? [lo, hi] : [0, 1];
}

const unit = (v: number, lo: number, hi: number): number => (hi > lo ? Math.min(1, Math.max(0, (v - lo) / (hi - lo))) : 0.5);

function categoricalLabel(v: unknown): string {
  return typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
}

function resolveTimeBucket(target: Target, enc: Extract<ColorEncoding, { kind: "timeBucket" }>, env: StyleEnv, base: BaseColor): ColorResult {
  const domain = env.timeDomain;
  if (domain === null) throw new RangeError("the graph has no time information, so it cannot be colored by time bucket");
  const wanted = enc.buckets ?? 6;
  if (!Number.isFinite(wanted) || wanted < 1) throw new RangeError("buckets must be a positive number");
  const edges = timeBucketEdges(env.timeConnectors, domain, Math.min(64, Math.floor(wanted)), enc.split ?? "time");
  const n = edges.length - 1;
  const span = domain.max - domain.min;
  const missingColor = enc.missing !== undefined ? parseColor(enc.missing) : null;
  const missingAt = (i: number): RGBA => missingColor ?? baseAt(base, i);
  const colorOf = (i: number): RGBA => (enc.colormap === undefined || enc.colormap === "ribbon" ? timeSliceColor(i, n) : sampleColormap(enc.colormap, (i + 0.5) / n, enc.reverse));
  const bucketOf = (t: number): number => {
    let lo = 0, hi = n - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (edges[mid] <= t) lo = mid; else hi = mid - 1; }
    return lo;
  };
  const count = countOf(target, env);
  const times = target === "node"
    ? (enc.nodeTime === "last" ? env.nodeActivity().last : env.nodeActivity().first)
    : Float64Array.from(env.connectors, c => c.t_start ?? NaN);
  const colors = new Float32Array(count * 4);
  const tally = new Array<number>(n).fill(0);
  for (let i = 0; i < count; i++) {
    const t = times[i];
    if (Number.isFinite(t)) { const b = bucketOf(t); tally[b]++; colors.set(colorOf(b), i * 4); } else colors.set(missingAt(i), i * 4);
  }
  const entries = Array.from({ length: n }, (_, i) => ({
    value: `${formatTime(edges[i], domain.unit, span)} → ${formatTime(edges[i + 1], domain.unit, span)}`,
    color: colorOf(i),
    count: tally[i],
  }));
  return { colors, legend: { type: "categorical", title: target === "node" ? `first activity, ${n} buckets` : `time, ${n} buckets`, entries } };
}

/** Resolve a color encoding to one RGBA per node or edge. `base` colors anything the encoding leaves out. */
export function resolveColors(target: Target, enc: ColorEncoding | undefined, env: StyleEnv, base: BaseColor): ColorResult {
  const n = countOf(target, env);
  if (enc === undefined) return { colors: fill(n, base), legend: null };
  if (isPlainColor(enc)) return { colors: fill(n, parseColor(enc)), legend: null };
  switch (enc.kind) {
    case "constant":
      return { colors: fill(n, parseColor(enc.color)), legend: null };
    case "colors": {
      const missingColor = enc.missing !== undefined ? parseColor(enc.missing) : null;
      const src = enc.colors;
      const out = new Float32Array(n * 4);
      if (src.length === n * 4 && typeof src[0] === "number") {
        for (let i = 0; i < n * 4; i++) out[i] = Number(src[i]);
        if (enc.present) {
          if (enc.present.length !== n) throw new RangeError(`present must have one entry per ${target} (${n}), got ${enc.present.length}`);
          for (let i = 0; i < n; i++) if (!enc.present[i]) out.set(missingColor ?? baseAt(base, i), i * 4);
        }
      } else if (src.length === n) {
        for (let i = 0; i < n; i++) {
          const c = (src as ColorInput[])[i];
          out.set(c === null || c === undefined ? (missingColor ?? baseAt(base, i)) : parseColor(c), i * 4);
        }
      } else throw new RangeError(`colors must have one entry per ${target} (${n}), or ${n * 4} numbers, got ${src.length}`);
      return { colors: out, legend: null };
    }
    case "timeBucket":
      return resolveTimeBucket(target, enc, env, base);
    case "attribute": {
      if (!enc.attribute) throw new TypeError("an attribute encoding needs an attribute name");
      const missingColor = enc.missing !== undefined ? parseColor(enc.missing) : null;
      const raw: unknown[] = Array.from({ length: n }, (_, i) => attrsOf(target, env, i)[enc.attribute]);
      const present = raw.filter(v => v !== undefined && v !== null);
      const numeric = present.length > 0 && present.every(v => typeof v === "number" && Number.isFinite(v));
      const distinct = new Set(present.map(categoricalLabel)).size;
      const continuous = enc.scale === "continuous" || (enc.scale !== "categorical" && numeric && distinct > MANY_DISTINCT);
      if (continuous) return resolveContinuous(target, "attribute", enc, env, base);
      const palette = enc.palette === undefined ? DEFAULT_PALETTE : typeof enc.palette === "string" ? paletteColors(enc.palette) : enc.palette.map(c => parseColor(c));
      if (palette.length === 0) throw new RangeError("palette must contain at least one color");
      const assigned = new Map<string, RGBA>();
      const tally = new Map<string, number>();
      const out = new Float32Array(n * 4);
      raw.forEach((v, i) => {
        if (v === undefined || v === null) { out.set(missingColor ?? baseAt(base, i), i * 4); return; }
        const label = categoricalLabel(v);
        if (!assigned.has(label)) assigned.set(label, palette[assigned.size % palette.length]);
        tally.set(label, (tally.get(label) ?? 0) + 1);
        out.set(assigned.get(label)!, i * 4);
      });
      return { colors: out, legend: { type: "categorical", title: enc.attribute, entries: Array.from(assigned, ([value, color]) => ({ value, color, count: tally.get(value) })) } };
    }
    case "degree":
    case "weight":
    case "time":
    case "values":
      return resolveContinuous(target, enc.kind, enc, env, base);
  }
  throw new RangeError(`unknown color encoding ${JSON.stringify((enc as { kind?: string }).kind)}`);
}

function resolveContinuous(
  target: Target, kind: string,
  enc: { attribute?: string; values?: ArrayLike<number>; colormap?: string; reverse?: boolean; domain?: [number, number]; missing?: ColorInput },
  env: StyleEnv, base: BaseColor,
): ColorResult {
  const { values, title } = sourceValues(target, kind, env, enc);
  const [lo, hi] = extent(values, enc.domain);
  const colormap = enc.colormap ?? "viridis";
  const reverse = enc.reverse ?? false;
  const missingColor = enc.missing !== undefined ? parseColor(enc.missing) : null;
  const out = new Float32Array(values.length * 4);
  for (let i = 0; i < values.length; i++) out.set(Number.isFinite(values[i]) ? sampleColormap(colormap, unit(values[i], lo, hi), reverse) : (missingColor ?? baseAt(base, i)), i * 4);
  return { colors: out, legend: { type: "continuous", title, colormap, reverse, min: lo, max: hi } };
}

/** Resolve a size encoding to one pixel size per node or edge. */
export function resolveSizes(target: Target, enc: SizeEncoding | undefined, env: StyleEnv, base: number): Float32Array {
  const n = countOf(target, env);
  const out = new Float32Array(n);
  const check = (v: number, what: string) => { if (!Number.isFinite(v) || v < 0) throw new RangeError(`${what} must be a non-negative number, got ${v}`); return v; };
  if (enc === undefined) return out.fill(base);
  if (typeof enc === "number") return out.fill(check(enc, "size"));
  if (enc.kind === "constant") return out.fill(check(enc.value, "size"));
  if (enc.kind === "pixels") {
    if (enc.values.length !== n) throw new RangeError(`pixel sizes must have one number per ${target} (${n}), got ${enc.values.length}`);
    for (let i = 0; i < n; i++) { const v = Number(enc.values[i]); out[i] = Number.isNaN(v) ? base : check(v, "size"); }
    return out;
  }
  const [a, b] = enc.range ?? [NaN, NaN];
  check(a, "range[0]"); check(b, "range[1]");
  const { values } = sourceValues(target, enc.kind, env, enc);
  const [lo, hi] = extent(values, enc.domain);
  const shape = (t: number) => (enc.scale === "sqrt" ? Math.sqrt(t) : enc.scale === "log" ? Math.log1p(t * 9) / Math.log(10) : t);
  const missing = enc.missing ?? base;
  for (let i = 0; i < n; i++) out[i] = Number.isFinite(values[i]) ? a + (b - a) * shape(unit(values[i], lo, hi)) : missing;
  return out;
}

const SHAPE_ID = new Map<string, number>(NODE_SHAPES.map((s, i) => [s, i]));

function shapeId(shape: string | number): number {
  if (typeof shape === "number") {
    if (!Number.isInteger(shape) || shape < 0 || shape >= NODE_SHAPES.length) throw new RangeError(`shape number must be 0-${NODE_SHAPES.length - 1}`);
    return shape;
  }
  const id = SHAPE_ID.get(shape);
  if (id === undefined) throw new RangeError(`unknown shape ${JSON.stringify(shape)}; choose one of ${NODE_SHAPES.join(", ")}`);
  return id;
}

/** Resolve a shape encoding to a shape id (index into NODE_SHAPES) per node. */
export function resolveShapes(enc: ShapeEncoding | undefined, env: StyleEnv): Uint8Array {
  const n = env.nodes.length;
  const out = new Uint8Array(n);
  if (enc === undefined) return out;
  if (typeof enc === "string") return out.fill(shapeId(enc));
  if (enc.kind === "constant") return out.fill(shapeId(enc.shape));
  if (enc.kind === "values") {
    if (enc.values.length !== n) throw new RangeError(`shape values must have one entry per node (${n}), got ${enc.values.length}`);
    for (let i = 0; i < n; i++) out[i] = shapeId(enc.values[i]);
    return out;
  }
  const shapes = enc.shapes ?? [...NODE_SHAPES];
  if (shapes.length === 0) throw new RangeError("shapes must not be empty");
  const assigned = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    const v = env.nodes[i].attrs[enc.attribute];
    if (v === undefined || v === null) continue;
    const label = categoricalLabel(v);
    if (!assigned.has(label)) assigned.set(label, shapeId(shapes[assigned.size % shapes.length]));
    out[i] = assigned.get(label)!;
  }
  return out;
}

export type { NodeShape };
