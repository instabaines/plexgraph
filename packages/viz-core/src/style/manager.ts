import { parseColor, type RGBA } from "./colors";
import { resolveColors, resolveShapes, resolveSizes, type ColorLegend, type StyleEnv } from "./engine";
import type { EdgeStyle, LabelStyle, NodeStyle, StyleSpec } from "./spec";

/** Defaults the style falls back to wherever it says nothing (the renderer's options). */
export interface StyleBase {
  nodeColor: RGBA;
  nodeRadiusPx: number;
  edgeColor: RGBA;
  edgeWidthPx: number;
  backgroundColor: RGBA;
  /** Each connector's own default color (its layer color), used where an encoding has no value. */
  edgeBaseColor?: (connectorIndex: number) => RGBA;
  /** Fixed per-element colors from the renderer's options (by key), applied over an encoding. */
  nodeOverride?: (nodeIndex: number) => RGBA | undefined;
  edgeOverride?: (connectorIndex: number) => RGBA | undefined;
}

export interface ResolvedLabel { mode: "hover" | "all" | "none"; fontSize: number; color: RGBA; halo: boolean; attribute: string | null }

export interface ResolvedStyle {
  nodeColors: Float32Array;
  /** Diameter in pixels. */
  nodeSizes: Float32Array;
  nodeShapes: Uint8Array;
  nodeOpacity: number;
  outline: { color: RGBA; width: number };
  nodeLegend: ColorLegend | null;
  edgeColors: Float32Array;
  edgeWidths: Float32Array;
  edgeOpacity: number;
  curvature: number;
  arrowScale: number;
  /** `[on1, off1, on2, off2]` pixel lengths, or null for a solid line. */
  dash: number[] | null;
  edgeLegend: ColorLegend | null;
  label: ResolvedLabel;
  background: RGBA;
}

const NODE_KEYS = ["color", "size", "shape", "opacity", "outline", "label"] as const;
const EDGE_KEYS = ["color", "width", "opacity", "curvature", "arrowScale", "dash"] as const;
const LABEL_KEYS = ["mode", "fontSize", "color", "halo", "attribute"] as const;
const TOP_KEYS = ["node", "edge", "background"] as const;

function unknownKeys(where: string, given: object, allowed: readonly string[]): void {
  const bad = Object.keys(given).filter(k => !allowed.includes(k));
  if (bad.length) throw new TypeError(`unknown style field${bad.length > 1 ? "s" : ""} ${bad.map(b => JSON.stringify(b)).join(", ")} in ${where}; allowed: ${allowed.join(", ")}`);
}

function unit(where: string, v: unknown): void {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 1) throw new RangeError(`${where} must be a number from 0 to 1, got ${JSON.stringify(v)}`);
}

/** Reject typos and out-of-range values with a message that names the field. */
export function validateStyle(spec: StyleSpec): void {
  if (typeof spec !== "object" || spec === null) throw new TypeError("style must be an object");
  unknownKeys("the style", spec, TOP_KEYS);
  if (spec.node) {
    unknownKeys("node", spec.node, NODE_KEYS);
    if (spec.node.opacity != null) unit("node.opacity", spec.node.opacity);
    const o = spec.node.outline;
    if (o) {
      unknownKeys("node.outline", o, ["color", "width"]);
      if (o.width !== undefined && (!Number.isFinite(o.width) || o.width < 0)) throw new RangeError("node.outline.width must be a non-negative number");
    }
    const l = spec.node.label;
    if (l) {
      unknownKeys("node.label", l, LABEL_KEYS);
      if (l.mode !== undefined && !["hover", "all", "none"].includes(l.mode)) throw new RangeError(`node.label.mode must be "hover", "all" or "none", got ${JSON.stringify(l.mode)}`);
      if (l.fontSize !== undefined && !(l.fontSize >= 4 && l.fontSize <= 72)) throw new RangeError("node.label.fontSize must be between 4 and 72");
    }
  }
  if (spec.edge) {
    unknownKeys("edge", spec.edge, EDGE_KEYS);
    if (spec.edge.opacity != null) unit("edge.opacity", spec.edge.opacity);
    const c = spec.edge.curvature;
    if (c != null && (!Number.isFinite(c) || Math.abs(c) > 2)) throw new RangeError(`edge.curvature must be a number between -2 and 2, got ${c}`);
    const a = spec.edge.arrowScale;
    if (a != null && (!Number.isFinite(a) || a <= 0 || a > 20)) throw new RangeError(`edge.arrowScale must be a positive number up to 20, got ${a}`);
    const d = spec.edge.dash;
    if (d != null) {
      if (!Array.isArray(d) || d.length !== 4 || d.some(v => !Number.isFinite(v) || v < 0)) {
        throw new RangeError(`edge.dash must be an array of 4 non-negative numbers [on1, off1, on2, off2], got ${JSON.stringify(d)}`);
      }
    }
  }
}

function mergeGroup<T extends object>(current: T | undefined, update: T | null | undefined): T | undefined {
  if (update === undefined) return current;
  if (update === null) return undefined;
  const next: Record<string, unknown> = { ...(current ?? {}) };
  for (const [k, v] of Object.entries(update)) {
    if (v === undefined) continue;
    if (v === null) delete next[k];
    else next[k] = v;
  }
  return Object.keys(next).length ? (next as T) : undefined;
}

/** Merge an update into a spec: fields you omit stay, `null` clears a field or a whole group. */
export function mergeStyle(current: StyleSpec, update: StyleSpec): StyleSpec {
  validateStyle(update);
  const next: StyleSpec = { node: mergeGroup<NodeStyle>(current.node ?? undefined, update.node), edge: mergeGroup<EdgeStyle>(current.edge ?? undefined, update.edge) };
  if (next.node?.label && update.node?.label && current.node?.label) next.node.label = mergeGroup<LabelStyle>(current.node.label, update.node.label);
  const background = update.background === undefined ? current.background : update.background;
  if (background !== undefined && background !== null) next.background = background;
  for (const k of ["node", "edge"] as const) if (next[k] === undefined) delete next[k];
  return next;
}

const DEFAULT_LABEL_COLOR: RGBA = [0.12, 0.12, 0.12, 0.9];

function cloneSpec(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(cloneSpec);
  if (value === null || typeof value !== "object" || ArrayBuffer.isView(value)) return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, cloneSpec(v)]));
}

/** Holds the current style plus manually painted nodes, and turns them into arrays the renderer uploads. */
export class StyleManager {
  private spec: StyleSpec;
  private painted = new Map<number, RGBA>();
  // Edges dominate the cost on big graphs, and most updates only touch nodes: reuse the edge result while the edge
  // style (compared by identity: merging keeps an untouched group's object) and the graph are unchanged.
  private edgeCache: { spec: unknown; key: number | undefined; colors: Float32Array; legend: ColorLegend | null; widths: Float32Array } | null = null;

  constructor(initial: StyleSpec = {}) {
    validateStyle(initial);
    this.spec = mergeStyle({}, initial);
  }

  /** A copy of the current style. Large arrays (per-node colors and the like) are shared, not copied: they are never modified. */
  get(): StyleSpec { return cloneSpec(this.spec) as StyleSpec; }

  /** Merge `update`, resolve it, and only then keep it, so an invalid update leaves the current style untouched. */
  apply(update: StyleSpec, env: StyleEnv, base: StyleBase): ResolvedStyle {
    const next = mergeStyle(this.spec, update);
    this.pendingEdges = null;
    const resolved = this.resolveSpec(next, env, base);
    this.spec = next;
    if (this.pendingEdges) this.edgeCache = this.pendingEdges; // only a successful update may replace the cache
    return resolved;
  }

  /** Forget the style and all painting (back to the base defaults). */
  reset(): void {
    this.spec = {};
    this.painted.clear();
    this.edgeCache = null;
  }

  /** Give specific nodes a color of their own (over any encoding); null removes it. */
  paint(ids: Iterable<number>, color: RGBA | null): void {
    for (const id of ids) {
      if (!Number.isInteger(id) || id < 0) throw new RangeError(`node id must be a non-negative integer, got ${id}`);
      if (color === null) this.painted.delete(id);
      else this.painted.set(id, color);
    }
  }

  clearPaint(): void { this.painted.clear(); }
  paintedCount(): number { return this.painted.size; }
  paintedColor(id: number): RGBA | undefined { return this.painted.get(id); }

  resolve(env: StyleEnv, base: StyleBase): ResolvedStyle {
    this.pendingEdges = null;
    const resolved = this.resolveSpec(this.spec, env, base);
    if (this.pendingEdges) this.edgeCache = this.pendingEdges;
    return resolved;
  }

  private pendingEdges: StyleManager["edgeCache"] = null;

  private resolveSpec(spec: StyleSpec, env: StyleEnv, base: StyleBase): ResolvedStyle {
    const node = spec.node ?? {}, edge = spec.edge ?? {};
    const nodes = resolveColors("node", node.color ?? undefined, env, base.nodeColor);
    for (let i = 0; i < env.nodes.length; i++) {
      const over = base.nodeOverride?.(i);
      if (over) nodes.colors.set(over, i * 4);
    }
    for (const [id, color] of this.painted) if (id < env.nodes.length) nodes.colors.set(color, id * 4);

    const cached = this.edgeCache;
    let edges: { colors: Float32Array; legend: ColorLegend | null };
    let edgeWidths: Float32Array;
    if (cached && spec.edge === cached.spec && env.cacheKey !== undefined && env.cacheKey === cached.key) {
      edges = cached;
      edgeWidths = cached.widths;
    } else {
      edges = resolveColors("edge", edge.color ?? undefined, env, i => base.edgeBaseColor?.(i) ?? base.edgeColor);
      for (let i = 0; i < env.connectors.length; i++) {
        const over = base.edgeOverride?.(i);
        if (over) edges.colors.set(over, i * 4);
      }
      edgeWidths = resolveSizes("edge", edge.width ?? undefined, env, base.edgeWidthPx);
      this.pendingEdges = { spec: spec.edge, key: env.cacheKey, colors: edges.colors, legend: edges.legend, widths: edgeWidths };
    }

    const label = node.label ?? {};
    const outline = node.outline ?? null;
    return {
      nodeColors: nodes.colors,
      nodeSizes: resolveSizes("node", node.size ?? undefined, env, base.nodeRadiusPx * 2),
      nodeShapes: resolveShapes(node.shape ?? undefined, env),
      nodeOpacity: node.opacity ?? 1,
      outline: { color: outline?.color !== undefined ? parseColor(outline.color) : [1, 1, 1, 1], width: outline?.width ?? 0 },
      nodeLegend: nodes.legend,
      edgeColors: edges.colors,
      edgeWidths,
      edgeOpacity: edge.opacity ?? 1,
      curvature: edge.curvature ?? 0,
      arrowScale: edge.arrowScale ?? 1,
      dash: edge.dash ?? null,
      edgeLegend: edges.legend,
      label: {
        mode: label.mode ?? "hover",
        fontSize: label.fontSize ?? 11,
        color: label.color !== undefined ? parseColor(label.color) : DEFAULT_LABEL_COLOR,
        halo: label.halo ?? false,
        attribute: label.attribute ?? null,
      },
      background: spec.background != null ? parseColor(spec.background) : base.backgroundColor,
    };
  }
}
