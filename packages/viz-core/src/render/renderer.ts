import { GraphIndex, type AttributeSummary, type NodeFilter } from "../interaction/graph-index";
import { aggregateDensity, aggregateGroups } from "./density";
import { arrowPolygon, memberHull } from "../layout/annotations";
import { sliceGeometry, type SliceLayout } from "../layout/slices";
// The WebGL renderer, built on regl per docs/architecture/plan.md section 2
// (a thin WebGL wrapper rather than adopting Sigma.js/PIXI/deck.gl, since
// none of them natively model hyperedges or multiplex layers). Phase A
// renders only the plain-graph subset: instanced points for nodes and
// instanced/batched lines for ordinary (2-endpoint) connectors. Hyperedge
// hull geometry and multiplex layer-plane geometry are later-phase
// extensions to this same buffer-driven pipeline, not a renderer rewrite.

import createREGL, { Regl } from "regl";

// The canvas is composited as premultiplied alpha over an opaque page, so colour must be blended
// with source alpha while the destination alpha stays untouched. Without blending, a translucent
// colour is written as-is (e.g. rgb .6 with alpha .5), which the browser composites to white.
const OPAQUE_CANVAS_BLEND = {
  enable: true,
  func: { srcRGB: "src alpha", dstRGB: "one minus src alpha", srcAlpha: 0, dstAlpha: 1 },
} as const;
import { Camera } from "../interaction/camera";
import type { GraphMessage, LayoutStepMessage, WireConnector, WireLayer, WireNode } from "../ir/types";
import { decodePositions } from "../ir/types";
import { convexHull, inflateHull, triangulateFan } from "./hull";

/** The time span covered by a graph's temporal connectors (null t_start/
 * t_end on the wire — "always present" — are excluded from the domain, so
 * a graph that's entirely non-temporal has domain = null). */
export interface TimeDomain {
  min: number;
  max: number;
  /** True when every time-bounded connector is a single instant (t_start === t_end), as in contact sequences. */
  instantaneous: boolean;
  /** "epoch_seconds" when times are Unix seconds and should be shown as dates. */
  unit?: "epoch_seconds" | null;
}

export const SECONDS_PER_DAY = 86400;

/** A time value for display: dates for epoch seconds (UTC; seconds shown only over short spans), else a number. */
export function formatTime(t: number, unit: TimeDomain["unit"] = null, span = Infinity): string {
  if (unit !== "epoch_seconds") return String(Number(t.toPrecision(6)));
  const iso = new Date(t * 1000).toISOString();
  if (span > 120 * SECONDS_PER_DAY) return iso.slice(0, 10);
  return span > 2 * SECONDS_PER_DAY ? iso.slice(0, 16).replace("T", " ") : iso.slice(0, 19).replace("T", " ");
}

/** How the time slider selects connectors:
 * - "instant": active exactly at t (t_start <= t <= t_end); right for intervals, empty for most t on point events.
 * - "window": active at some moment in [t - window, t]; the trailing window that suits contact sequences.
 * - "cumulative": started at or before t (everything so far). */
export type TimeMode = "instant" | "window" | "cumulative";
export interface TimeFilterOptions { mode?: TimeMode; window?: number }

export function connectorActiveAt(c: WireConnector, t: number, mode: TimeMode = "instant", window = 0): boolean {
  const start = c.t_start ?? -Infinity, end = c.t_end ?? Infinity;
  if (mode === "cumulative") return start <= t;
  if (mode === "window") return start <= t && end >= t - window;
  return start <= t && t <= end;
}

/** How ribbon buckets divide time: "time" gives every bucket the same duration; "events" gives every bucket
 * about the same number of events (busy periods get narrow buckets, quiet ones wide). */
export type TimeSplit = "time" | "events";

/** Ribbon bucket boundaries: `n + 1` increasing times from `domain.min` to `domain.max`. Equal-events
 * boundaries follow quantiles of the connectors' start times; repeated values collapse, so a stream with many
 * simultaneous events can yield fewer than `n` buckets. */
export function timeBucketEdges(connectors: WireConnector[], domain: { min: number; max: number }, n: number, split: TimeSplit = "time"): number[] {
  n = Math.max(1, Math.floor(n));
  const width = (domain.max - domain.min) / n || 1;
  if (split === "time") return Array.from({ length: n + 1 }, (_, i) => (i === n ? domain.max : domain.min + i * width));
  const starts = connectors.map(c => c.t_start).filter((t): t is number => t !== null && Number.isFinite(t)).sort((a, b) => a - b);
  const edges = [domain.min];
  for (let i = 1; i < n; i++) {
    const t = starts[Math.floor((i * starts.length) / n)];
    if (t !== undefined && t > edges[edges.length - 1] && t < domain.max) edges.push(t);
  }
  edges.push(domain.max);
  return edges.length >= 2 && edges[edges.length - 1] > edges[0] ? edges : [domain.min, domain.min + width * n];
}

/** Lines for a panel heading. A date range ("a → b") is stacked so neighbouring panel headings cannot overlap. */
export function panelHeadingLines(index: number, label: string): string[] {
  const [from, to] = label.split(" → ");
  return to === undefined ? [`${index + 1} · ${label}`] : [`${index + 1} · ${from}`, `→ ${to}`];
}

/** Whether a connector belongs in ribbon bucket [start, end). Buckets are half-open so an event exactly on a
 * boundary lands in one bucket only; the last bucket also owns its end point. */
export function bucketContains(c: WireConnector, start: number, end: number, isLast: boolean): boolean {
  const cStart = c.t_start ?? -Infinity, cEnd = c.t_end ?? Infinity;
  return (isLast ? cStart <= end : cStart < end) && cEnd >= start;
}

/** A layer plus the color assigned to it for rendering, handed to the app
 * so it can build a legend/toggle UI with matching swatches. */
export interface LayerInfo {
  id: number;
  key: unknown;
  color: [number, number, number, number];
}

/** Cycled through by layer id. Connectors with no layer (layer_id null)
 * use the plain edgeColor instead — see layerColor(). Categorical
 * multiplex only (per docs/architecture/plan.md section 1) — general
 * multilayer coupling isn't in scope, so "layer" here just means "which
 * color/visibility group does this edge belong to". */
const LAYER_PALETTE: [number, number, number, number][] = [
  [0.85, 0.33, 0.1, 0.85],
  [0.2, 0.65, 0.32, 0.85],
  [0.55, 0.35, 0.85, 0.85],
  [0.9, 0.65, 0.13, 0.85],
  [0.13, 0.59, 0.75, 0.85],
  [0.8, 0.2, 0.45, 0.85],
  [0.4, 0.4, 0.4, 0.85],
  [0.65, 0.75, 0.15, 0.85],
];

function layerColor(
  layerId: number | null,
  fallback: [number, number, number, number]
): [number, number, number, number] {
  if (layerId === null) return fallback;
  return LAYER_PALETTE[layerId % LAYER_PALETTE.length];
}

/** Fill color for a hyperedge's hull polygon: shares LAYER_PALETTE's hues
 * (its own layer's color if it has one, else cycled by its own connector
 * id) but at low alpha, since it's a translucent background grouping
 * meant to sit behind the member nodes/edges, not compete with them. */
function hyperedgeFillColor(connectorId: number, layerId: number | null): [number, number, number, number] {
  const base = layerId !== null ? LAYER_PALETTE[layerId % LAYER_PALETTE.length] : LAYER_PALETTE[connectorId % LAYER_PALETTE.length];
  return [base[0], base[1], base[2], 0.18];
}

/** Assigns a LAYER_PALETTE color to each distinct value seen (in first-seen
 * order, so results are deterministic given the same data), shared by
 * nodeColorBy/edgeColorBy — both just need "one color per distinct
 * category", the same underlying problem layers solve for edges. */
export function buildCategoricalPalette(values: string[]): Map<string, [number, number, number, number]> {
  const map = new Map<string, [number, number, number, number]>();
  for (const v of values) {
    if (!map.has(v)) map.set(v, LAYER_PALETTE[map.size % LAYER_PALETTE.length]);
  }
  return map;
}

/** Which dimension the "stacked slices" view is currently sliced along —
 * see setStackMode. null means the normal flat/overlay view. */
export type StackAxis = "layer" | "time" | null;

/** One plane in the stacked view: which connectors belong on it, its
 * color, and (for the app's own legend/labeling) a human-readable label. */
interface StackSlice {
  label: string;
  color: [number, number, number, number];
  connectors: WireConnector[];
}

/** Info about a stacked-mode slice exposed to the app for a legend —
 * mirrors StackSlice minus the connector list (the app doesn't need it). */
export interface StackSliceInfo {
  label: string;
  /** Connectors in this slice. */
  count: number;
  color: [number, number, number, number];
}

/** Sequential (not categorical) color ramp for time-axis stacking — an
 * ordered gradient (cool -> warm) reads as "progression through time" the
 * way a qualitative palette (LAYER_PALETTE) doesn't; layer-axis stacking
 * keeps using LAYER_PALETTE instead, since layers are categorical, not
 * ordered. */
export function timeSliceColor(index: number, total: number): [number, number, number, number] {
  const t = total <= 1 ? 0 : index / (total - 1);
  const early: [number, number, number] = [0.16, 0.45, 0.85]; // cool blue
  const late: [number, number, number] = [0.9, 0.45, 0.15]; // warm orange
  return [
    early[0] + (late[0] - early[0]) * t,
    early[1] + (late[1] - early[1]) * t,
    early[2] + (late[2] - early[2]) * t,
    0.85,
  ];
}

/** A label to draw next to every node (see nodeLabel): show each node's
 * key (true), the value of one of its attrs (a string naming the attr),
 * explicit per-node text (a dict keyed by String(node.key)), or nothing
 * (false/undefined, the default). */
export type NodeLabelSpec = boolean | string | Record<string, string>;

/** One entry in a color-by-attribute legend (see nodeColorBy/edgeColorBy). */
export interface ColorLegendEntry {
  value: string;
  color: [number, number, number, number];
}

export interface RendererOptions {
  nodeRadiusPx?: number;
  nodeColor?: [number, number, number, number];
  /** Explicit per-node color overrides, keyed by String(node.key) —
   * highlights specific nodes/groups. Takes priority over nodeColorBy for
   * any node it names; unnamed nodes fall through to nodeColorBy or
   * nodeColor. */
  nodeColorOverrides?: Record<string, [number, number, number, number]>;
  /** Name of a node attribute to auto-color by: each distinct value gets
   * its own palette color (an onNodeColorLegend callback reports the
   * mapping so the app can show a legend). Nodes missing the attr, or
   * named in nodeColorOverrides, don't use this. */
  nodeColorBy?: string;
  edgeColor?: [number, number, number, number];
  /** Explicit per-edge color overrides, keyed by "sourceKey||targetKey"
   * (String() of each endpoint's node key, joined by "||") — matches
   * every connector with that exact (source, target) key pair. Takes
   * priority over edgeColorBy and layer coloring. */
  edgeColorOverrides?: Record<string, [number, number, number, number]>;
  /** Name of a connector attribute to auto-color by, analogous to
   * nodeColorBy. Lower priority than a connector's own layer color. */
  edgeColorBy?: string;
  /** Edge thickness in screen pixels — constant regardless of zoom, the
   * same way nodeRadiusPx is (see edgeDraw's comment for why this needs
   * quad geometry rather than gl.lineWidth()). */
  edgeWidthPx?: number;
  backgroundColor?: [number, number, number, number];
  /** Arrowhead color for directed edges. */
  arrowColor?: [number, number, number, number];
  /** Arrowhead length, in world units (same space as node positions). */
  arrowLength?: number;
  /** Arrowhead half-width, in world units. */
  arrowWidth?: number;
  /** Fraction (0-1) along the edge, from source to target, where the
   * arrowhead's tip sits — less than 1 so it doesn't sit under the target
   * node's dot. */
  arrowT?: number;
  /** How far a hyperedge's hull polygon extends beyond its member nodes,
   * in world units — see hull.ts's inflateHull. */
  hullPadding?: number;
  /** World-unit offset applied per stack level in the stacked-slices view
   * (see setStackMode) — each successive plane is shifted by
   * (stackShearX, stackShearY) to create the "sheets of glass" depth
   * illusion via a simple 2D affine shear rather than a true 3D camera. */
  stackShearX?: number;
  stackShearY?: number;
  /** How much each plane's node layout is shrunk (relative to the shared
   * base layout) in the stacked-slices view — without this, planes whose
   * shear offset is small relative to the layout's own spread just blur
   * into one smear instead of reading as separated sheets. */
  stackPlaneScale?: number;
  /** Fill color of the translucent "index card" backing drawn behind each
   * plane in the stacked-slices view, giving the eye a rectangle to
   * anchor each layer/time-slice to instead of just floating dots. */
  stackPlaneColor?: [number, number, number, number];
  /** Alpha of the faint "identity thread" lines connecting a node's copy
   * on one plane to its copy on the next. */
  stackThreadAlpha?: number;
  /** Text to draw next to each node — see NodeLabelSpec. Off by default. */
  nodeLabel?: NodeLabelSpec;
  /** Called when the hovered node changes (null when nothing is hovered). */
  onHover?: (nodeId: number | null) => void;
  /** Called after a graph snapshot has been loaded and indexed (node attributes are then available). */
  onGraphLoaded?: () => void;
  /** Called when a group is clicked in the grouped overview (see setGroupBy). */
  onGroupClick?: (attribute: string, value: string) => void;
  /** Called when a node is clicked (pointer released without dragging). */
  onNodeClick?: (nodeId: number, event: { shiftKey: boolean }) => void;
  /** Called after loadGraph with the graph's temporal extent, or null if
   * it has no temporal connectors — lets the app show/hide a timeline UI. */
  onTimeDomain?: (domain: TimeDomain | null) => void;
  /** Called after loadGraph with the graph's layers (each with its
   * assigned render color), or an empty array if it has none — lets the
   * app show/hide a layer legend/toggle UI. */
  onLayers?: (layers: LayerInfo[]) => void;
  /** Called when the stacked-slices view is entered/changed/exited (see
   * setStackMode) with the ordered list of planes (back to front), or
   * null when returning to the flat view — lets the app show a legend
   * naming each plane. */
  onStackChange?: (slices: StackSliceInfo[] | null) => void;
  /** Called after loadGraph with the nodeColorBy legend (distinct attr
   * values and their assigned colors), or null if nodeColorBy isn't set —
   * lets the app show a legend. */
  onNodeColorLegend?: (entries: ColorLegendEntry[] | null) => void;
  /** Same as onNodeColorLegend, for edgeColorBy. */
  onEdgeColorLegend?: (entries: ColorLegendEntry[] | null) => void;
}

type VisualDefaults = Required<
  Omit<
    RendererOptions,
    | "onHover"
    | "onNodeClick"
    | "onGroupClick"
    | "onGraphLoaded"
    | "onTimeDomain"
    | "onLayers"
    | "onStackChange"
    | "onNodeColorLegend"
    | "onEdgeColorLegend"
    | "nodeColorOverrides"
    | "nodeColorBy"
    | "edgeColorOverrides"
    | "edgeColorBy"
    | "nodeLabel"
  >
>;

const DEFAULTS: VisualDefaults = {
  nodeRadiusPx: 5,
  nodeColor: [0.16, 0.55, 0.95, 1],
  edgeColor: [0.6, 0.6, 0.65, 0.5],
  edgeWidthPx: 1.5,
  backgroundColor: [0.98, 0.98, 0.98, 1],
  arrowColor: [0.4, 0.4, 0.46, 0.9],
  arrowLength: 0.025,
  arrowWidth: 0.012,
  arrowT: 0.92,
  hullPadding: 0.035,
  stackShearX: 0.22,
  stackShearY: 0.4,
  stackPlaneScale: 0.5,
  stackPlaneColor: [1, 1, 1, 0.6],
  stackThreadAlpha: 0.35,
};

/** Encode a node id (0-based) into an opaque RGBA color for GPU picking.
 * id + 1 is packed into the RGB channels (24 bits, so up to ~16.7M nodes)
 * so that the framebuffer's (0,0,0,0) clear color decodes to "no node"
 * rather than colliding with node id 0. */
const PICK_ENCODE_GLSL = `
  vec4 encodeId(float id) {
    float v = id + 1.0;
    float r = mod(v, 256.0);
    v = floor(v / 256.0);
    float g = mod(v, 256.0);
    v = floor(v / 256.0);
    float b = mod(v, 256.0);
    return vec4(r / 255.0, g / 255.0, b / 255.0, 1.0);
  }
`;

/** Props for the stacked-view draw commands (stackPlaneDraw,
 * stackedEdgeDraw, stackedNodeDraw), which are invoked once per slice
 * with a different offset/count each time — see drawStackedScene. */
interface StackDrawRange {
  offset: number;
  count: number;
}

function decodePickedId(pixel: Uint8Array): number | null {
  const raw = pixel[0] + pixel[1] * 256 + pixel[2] * 65536;
  return raw === 0 ? null : raw - 1;
}

/** The [min, max] span across all connectors' finite t_start/t_end. A null
 * bound means "always present" and is excluded from the domain — a graph
 * with no temporal connectors (or only always-present ones) has no domain,
 * so the caller knows to skip showing a timeline UI at all. */
export function computeTimeDomain(connectors: WireConnector[]): TimeDomain | null {
  let min = Infinity;
  let max = -Infinity;
  let bounded = 0, instants = 0;
  for (const c of connectors) {
    if (c.t_start !== null) min = Math.min(min, c.t_start);
    if (c.t_end !== null) max = Math.max(max, c.t_end);
    if (c.t_start !== null && c.t_end !== null) { bounded++; if (c.t_start === c.t_end) instants++; }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  return { min, max, instantaneous: bounded > 0 && instants === bounded };
}

const DETAIL_LIMIT = 5000;
const DENSITY_RESOLUTION = 48;
const SLICE_DENSITY_RESOLUTION = 32; // small atlas/ribbon panels: coarser cells and fewer links keep frames cheap

export class Renderer {
  private regl: Regl;
  private camera: Camera;
  private opts: VisualDefaults;
  private onHover: ((nodeId: number | null) => void) | null;
  private onTimeDomain: ((domain: TimeDomain | null) => void) | null;
  private onLayers: ((layers: LayerInfo[]) => void) | null;
  private onStackChange: ((slices: StackSliceInfo[] | null) => void) | null;
  private onNodeColorLegend: ((entries: ColorLegendEntry[] | null) => void) | null;
  private onEdgeColorLegend: ((entries: ColorLegendEntry[] | null) => void) | null;
  private nodeColorOverrides: Record<string, [number, number, number, number]>;
  private nodeColorByAttr: string | null;
  private edgeColorOverrides: Record<string, [number, number, number, number]>;
  private edgeColorByAttr: string | null;
  private nodeLabelSpec: NodeLabelSpec;

  private index = new GraphIndex([], []);
  private focusedNodes: Set<number> | null = null;
  private nodeFilterNodes: Set<number> | null = null;
  private highlighted: number[] = [];
  private nodeSizeBuffer: ReturnType<Regl["buffer"]> | null = null;
  private nodeSizeBy: string | null = null;
  private onNodeClick: ((nodeId: number, event: { shiftKey: boolean }) => void) | null;
  private onGraphLoaded: (() => void) | null;
  private onGroupClick: ((attribute: string, value: string) => void) | null;
  private focusedConnectors: Set<number> | null = null;
  private selectedNode: number | null = null;
  private visibleNodeIds: number[] = [];
  private nodeElements: ReturnType<Regl["elements"]> | null = null;
  private sliceNodeElements: ReturnType<Regl["elements"]> | null = null;
  private dirty = true;
  private pointerDirty = true;
  private lastCamera = "";
  private densityDirty = true;
  private densityNodes = new Float32Array(0);
  private nodeColorValues = new Float32Array(0);
  private densityEdges = new Float32Array(0);
  private densityRanges: {nodes: StackDrawRange; edges: StackDrawRange}[] = [];
  private densityNodeBuffer: ReturnType<Regl["buffer"]> | null = null;
  private densityEdgeBuffer: ReturnType<Regl["buffer"]> | null = null;
  private drawDensity: (slice: number) => void = () => {};
  // Level of detail: individual nodes up to DETAIL_LIMIT; beyond that, either the nodes inside the current
  // viewport (region detail, when the viewport is small enough) or aggregated cells/groups (overview).
  private densityMode = false;
  private regionNodes: Set<number> | null = null;
  private groupBy: string | null = null;
  private lodPending = false;
  private lodChangedAt = 0;
  private densityCounts: number[] = [];
  private densityGroupKeys: string[] = [];
  private densityLinksShown = 0;
  private densityLinksTotal = 0;
  private densityNodesInView = 0;

  /** Nodes allowed by the neighbourhood focus and the attribute/degree filter together (null = every node). */
  private baseNodes(): Set<number> | null {
    const focus = this.focusedNodes, filter = this.nodeFilterNodes;
    if (!focus || !filter) return focus ?? filter;
    const both = new Set<number>();
    for (const n of focus) if (filter.has(n)) both.add(n);
    return both;
  }

  /** Base nodes further restricted to the viewport when zoomed into a large graph (null = no restriction). */
  private viewNodes(): Set<number> | null {
    return this.regionNodes ?? this.baseNodes();
  }

  private nodesInViewport(base: Set<number> | null): number[] {
    const aspect = this.canvas.clientWidth / Math.max(this.canvas.clientHeight, 1);
    const hw = (aspect / this.camera.zoom) * 1.15, hh = (1 / this.camera.zoom) * 1.15;
    const { x: cx, y: cy } = this.camera;
    const out: number[] = [];
    const test = (n: number) => {
      if (Math.abs(this.positions[n * 2] - cx) <= hw && Math.abs(this.positions[n * 2 + 1] - cy) <= hh) out.push(n);
    };
    if (base) base.forEach(test); else for (let n = 0; n < this.numNodes; n++) test(n);
    return out;
  }

  private evaluateLod(): void {
    const base = this.baseNodes();
    const total = base?.size ?? this.numNodes;
    this.regionNodes = null;
    this.densityNodesInView = total;
    if (this.stackAxis !== null) { this.densityMode = total > DETAIL_LIMIT; return; }
    if (this.groupBy !== null) { this.densityMode = total > 0; return; }
    if (total <= DETAIL_LIMIT) { this.densityMode = false; return; }
    const inView = this.nodesInViewport(base);
    this.densityNodesInView = inView.length;
    if (inView.length <= DETAIL_LIMIT) { this.regionNodes = new Set(inView); this.densityMode = false; }
    else this.densityMode = true;
  }

  private needsLod(): boolean {
    return this.stackAxis === null && this.groupBy === null && (this.baseNodes()?.size ?? this.numNodes) > DETAIL_LIMIT;
  }

  private markLodStale(): void {
    if (!this.needsLod()) return;
    this.lodPending = true;
    this.lodChangedAt = performance.now();
  }

  /** Re-evaluate the level of detail once the camera (or layout) has stopped changing. */
  private applyLod(): void {
    this.lodPending = false;
    const beforeMode = this.densityMode, beforeRegion = this.regionNodes;
    this.evaluateLod();
    const same = beforeMode === this.densityMode && beforeRegion?.size === this.regionNodes?.size
      && (!beforeRegion || !this.regionNodes || [...beforeRegion].every(n => this.regionNodes!.has(n)));
    if (!same) this.refreshVisibleSet(false);
    else if (this.densityMode) this.dirty = this.densityDirty = true;
  }

  /** What is on screen now: individual nodes, the nodes in the viewport of a larger graph, aggregated
   * spatial cells, or attribute groups. */
  getLodState(): { mode: "detail" | "region" | "overview" | "groups"; nodesInView: number; nodesDrawn: number } {
    const mode = this.densityMode ? (this.groupBy !== null && this.stackAxis === null ? "groups" : "overview") : this.regionNodes ? "region" : "detail";
    return { mode, nodesInView: this.densityNodesInView, nodesDrawn: this.densityMode ? this.densityCounts.length : this.visibleNodeIds.length };
  }

  /** Aggregate nodes into one point per distinct value of a categorical attribute (community collapse); null expands them. */
  setGroupBy(attribute: string | null): void {
    this.groupBy = attribute;
    this.refreshVisibleSet();
    this.fitView();
  }
  private numNodes = 0;
  private positions: Float32Array<ArrayBufferLike> = new Float32Array(0);
  private nodeLabelText: (string | null)[] = [];
  // String(key) per node id, indexed by id — used to match
  // nodeColorOverrides/edgeColorOverrides and nodeLabel's dict form
  // against the arbitrary (string/int/tuple) keys a Python caller used.
  private nodeKeys: string[] = [];
  private edgeColorPalette: Map<string, [number, number, number, number]> | null = null;
  // Ordinary (2-endpoint) connectors and hyperedges (>2 endpoints) are
  // split into separate lists — they render as completely different
  // geometry (lines vs. hull polygons) even though both come from the
  // same wire connector list and share the same time/layer filtering.
  // The full sets (unfiltered) are kept so setTimeFilter/setLayerFilter
  // can re-slice them without a fresh GraphMessage.
  private allConnectors: WireConnector[] = [];
  private allHyperedges: WireConnector[] = [];
  private timeDomain: TimeDomain | null = null;
  private currentTimeFilter: number | null = null;
  private timeMode: TimeMode = "instant";
  private timeWindow = 0;
  private layers: WireLayer[] = [];
  // null = show every layer (default); otherwise only connectors whose
  // layer_id is in this set (plus layer-less connectors, always shown).
  private layerVisibility: Set<number> | null = null;
  private hasLayers = false;
  private visibleConnectorCount = 0;
  private visibleHyperedgeCount = 0;
  private nodeIds = new Float32Array(0);

  // Hyperedge hull geometry: a filled, translucent convex-hull polygon per
  // hyperedge, wrapping its member nodes (see hull.ts). Fully rebuilt
  // (both the polygon shape and the interleaved position+color buffer)
  // whenever positions change or the visible set changes — unlike edges/
  // arrows, a hull's vertex COUNT can itself change frame to frame (the
  // convex hull of a moving point set gains/loses vertices as points
  // become interior/exterior), so there's no fixed-size buffer to patch
  // in place the way arrow/edge positions are.
  private hyperedgeTriangleBuffer: ReturnType<Regl["buffer"]> | null = null;
  private hyperedgeTriangleVertexCount = 0;
  private visibleHyperedges: WireConnector[] = [];

  // Edge geometry: every edge (whether it has a layer color or the
  // default edgeColor) is one thick-line quad — see edgeDraw's comment for
  // why this is a quad, not GL_LINES. srcDst/alongSide/color are baked
  // into dedicated buffers rather than indexed into positionBuffer,
  // because a quad needs *two* different node positions per vertex (to
  // compute direction) plus a per-edge color, neither of which a single
  // shared node-position index buffer can express. The index buffer
  // (2 triangles per edge) and colors only change when the edge set
  // changes; positions are rebuilt every layout step.
  private edges: { source: number; target: number; color: [number, number, number, number] }[] = [];
  // Above this many connectors, edges fall back to edgeDrawThin (cheap
  // indexed GL_LINES reusing positionBuffer directly — no per-edge CPU
  // rebuild on every layout step) instead of edgeDraw's per-edge-colored,
  // adjustable-width quads. Chosen so typical multiplex/styled graphs
  // (thousands of edges) keep full color/width control, while 100K-node-
  // scale graphs (hundreds of thousands of edges) stay responsive — see
  // docs/architecture/plan.md's note on this kind of size-based tradeoff.
  private static readonly THIN_EDGE_THRESHOLD = 20_000;
  private useThinEdges = false;
  private thinEdgeCount = 0;
  private thinEdgePairs = new Uint32Array(0); // (source, target) pairs when useThinEdges — see setEdgeSet

  // Directed-edge arrowheads: each is a triangle (3 vertices) whose world
  // position is computed in the vertex shader from its edge's live
  // source/target positions, rather than being indexed into positionBuffer
  // like node/edge geometry — a triangle needs *two* different node
  // positions per vertex (to compute direction), which a single shared
  // index buffer can't express (WebGL indexes all attributes of a vertex
  // together). So srcDst is baked directly into a dedicated buffer and
  // rebuilt whenever positions change (once per streamed layout step, not
  // per animation frame — layout steps arrive far less often than frames).
  private directedEdges: { source: number; target: number }[] = [];
  private arrowCornerData = new Float32Array(0); // static per triangle: (along, side)
  private arrowSrcDstData = new Float32Array(0); // rebuilt on every layout step

  // Stacked-slices view (see setStackMode): each layer or time-bucket
  // drawn as its own plane, offset by (stackShearX, stackShearY) per
  // level, with all planes sharing the same underlying node layout so a
  // node's identity is trackable across planes via the "thread" lines.
  // Replaces the flat edges/colored-edges/hulls/arrows/nodes entirely
  // while active — see the loop().
  private slicePolygons: { points: {x: number; y: number}[]; color: [number, number, number, number]; kind: "hull" | "arrow" }[][] = [];
  private sliceTriangleRanges: { offset: number; count: number }[] = [];
  private sliceTriangleBuffer: ReturnType<Regl["buffer"]> | null = null;
  private stackAxis: StackAxis = null;
  private sliceLayout: SliceLayout = "stack";
  private stackTimeBuckets = 6;
  private stackTimeSplit: TimeSplit = "time";
  private stackSlices: StackSlice[] = [];
  // Persisted so positions can be recomputed cheaply on every layout step
  // without recomputing slice membership (which connector is on which
  // plane doesn't change with node movement, only the plane's shear
  // offset applied to it does).
  private stackEdgeList: { sliceIndex: number; source: number; target: number }[] = [];
  private stackThreadList: { sliceIndex: number; nodeId: number }[] = []; // -> next slice
  private stackNodeSliceCount = 0;
  private stackPlaneVertexCount = 0;
  // Per-slice vertex ranges into the (slice-contiguous) stacked edge
  // buffer, so each plane's content can be drawn in its own call —
  // needed so plane N's backing quad visually occludes plane N-1's edges/
  // nodes where they overlap (drawing all edges in one combined pass
  // after all planes would break that layering).
  private stackSliceEdgeVertexRanges: { offset: number; count: number }[] = [];

  private drawNodes: () => void = () => {};
  private drawEdges: () => void = () => {};
  private drawArrows: () => void = () => {};
  private drawHyperedges: () => void = () => {};
  private drawStackThreads: () => void = () => {};
  private drawStackedScene: () => void = () => {};
  private drawPick: () => void = () => {};
  private positionBuffer: ReturnType<Regl["buffer"]> | null = null;
  private nodeColorBuffer: ReturnType<Regl["buffer"]> | null = null; // static per-vertex color, rebuilt on loadGraph
  private idBuffer: ReturnType<Regl["buffer"]> | null = null;
  private arrowCornerBuffer: ReturnType<Regl["buffer"]> | null = null;
  private arrowSrcDstBuffer: ReturnType<Regl["buffer"]> | null = null;
  // Edge quad geometry (see the `edges` field's comment): srcDst/color are
  // interleaved 4x per edge (one per quad vertex); alongSide is the same
  // 4-vertex pattern for every edge, so it's built once and reused.
  // Index buffer for the 2 triangles/quad — must be created via
  // regl.elements() (an ELEMENT_ARRAY_BUFFER), not regl.buffer() (an
  // ARRAY_BUFFER); using the wrong one throws when the draw command runs,
  // which kills the requestAnimationFrame loop mid-frame (after the
  // background clear but before drawNodes) — the "connected but blank
  // canvas" bug from earlier in this project.
  private edgeSrcDstBuffer: ReturnType<Regl["buffer"]> | null = null; // dynamic, rebuilt on layout step
  private edgeAlongSideBuffer: ReturnType<Regl["buffer"]> | null = null; // static
  private edgeColorBuffer: ReturnType<Regl["buffer"]> | null = null; // static, rebuilt on setEdgeSet
  private edgeIndexBuffer: ReturnType<Regl["elements"]> | null = null; // static, rebuilt on setEdgeSet
  private edgeThinIndexBuffer: ReturnType<Regl["elements"]> | null = null; // static, thin-edge fallback
  private stackedNodePositionBuffer: ReturnType<Regl["buffer"]> | null = null; // dynamic
  private stackedEdgePositionBuffer: ReturnType<Regl["buffer"]> | null = null; // dynamic
  private stackedEdgeColorBuffer: ReturnType<Regl["buffer"]> | null = null; // static per slice-def rebuild
  private stackThreadPositionBuffer: ReturnType<Regl["buffer"]> | null = null; // dynamic
  private stackPlaneBuffer: ReturnType<Regl["buffer"]> | null = null; // dynamic, interleaved pos+color
  private pickFbo: ReturnType<Regl["framebuffer"]> | null = null;
  // regl's TS types don't expose width/height on Framebuffer2D even though
  // the object has them at runtime, so we track the size ourselves.
  private pickFboSize = { width: 0, height: 0 };

  private rafHandle: number | null = null;

  // Hover/picking state: the pointer position is tracked passively and
  // picked once per frame (a 1x1 offscreen readback is cheap) rather than
  // per pointermove event, so picking doesn't get more expensive as the
  // user moves the mouse faster.
  private pointerPx: { x: number; y: number } | null = null;
  private hoveredNodeId: number | null = null;

  // Node labels are drawn on a plain Canvas2D overlay, not WebGL — text in
  // WebGL means either a font-atlas texture or SDF glyphs, real extra
  // machinery for what's otherwise a rarely-used, non-performance-critical
  // feature; a synced 2D overlay (the same trick deck.gl/Sigma.js/MapLibre
  // use for labels) gets real, crisp, selectable-if-needed text for
  // effectively no extra rendering complexity. Only created when
  // nodeLabelSpec is actually set — no overlay element at all for the
  // common no-labels case.
  private labelCanvas: HTMLCanvasElement | null = null;
  private labelCtx: CanvasRenderingContext2D | null = null;

  constructor(private canvas: HTMLCanvasElement, options: RendererOptions = {}) {
    this.opts = { ...DEFAULTS, ...options };
    this.onHover = options.onHover ?? null;
    this.onNodeClick = options.onNodeClick ?? null;
    this.onGraphLoaded = options.onGraphLoaded ?? null;
    this.onGroupClick = options.onGroupClick ?? null;
    this.onTimeDomain = options.onTimeDomain ?? null;
    this.onLayers = options.onLayers ?? null;
    this.onStackChange = options.onStackChange ?? null;
    this.onNodeColorLegend = options.onNodeColorLegend ?? null;
    this.onEdgeColorLegend = options.onEdgeColorLegend ?? null;
    this.nodeColorOverrides = options.nodeColorOverrides ?? {};
    this.nodeColorByAttr = options.nodeColorBy ?? null;
    this.edgeColorOverrides = options.edgeColorOverrides ?? {};
    this.edgeColorByAttr = options.edgeColorBy ?? null;
    this.nodeLabelSpec = options.nodeLabel ?? false;
    this.regl = createREGL({
      canvas,
      // preserveDrawingBuffer: true costs a little performance (the
      // browser can't just swap buffers, it must copy) but is required
      // for canvas.toDataURL()/toBlob() (PNG/JPEG export) to reliably
      // capture what's on screen — without it the drawing buffer isn't
      // guaranteed to still hold the last frame by the time export code
      // runs, and captures can come back blank.
      attributes: { antialias: true, preserveDrawingBuffer: true },
      // Needed for Uint32Array edge index buffers (see loadGraph) — node
      // ids can exceed 65535 at the 100K-node scale this project targets,
      // so 16-bit indices aren't enough. Virtually universal on WebGL1
      // (core in WebGL2), but required explicitly since regl doesn't
      // enable it implicitly.
      extensions: ["OES_element_index_uint"],
    });
    this.camera = new Camera(canvas);
    this.pickFboSize = { width: Math.max(1, canvas.width), height: Math.max(1, canvas.height) };
    this.pickFbo = this.regl.framebuffer({
      width: this.pickFboSize.width,
      height: this.pickFboSize.height,
      colorFormat: "rgba",
    });
    this.buildDrawCommands();
    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerleave", this.onPointerLeave);
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointerup", this.onPointerUp);

    {
      const labelCanvas = document.createElement("canvas");
      labelCanvas.style.cssText = "position:fixed;margin:0;pointer-events:none;";
      canvas.parentElement?.insertBefore(labelCanvas, canvas.nextSibling);
      this.labelCanvas = labelCanvas;
      this.labelCtx = labelCanvas.getContext("2d");
    }

    this.loop();
  }

  private onPointerMove = (e: PointerEvent): void => {
    this.pointerDirty = true;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.pointerPx = {
      x: (e.clientX - rect.left) * dpr,
      y: (e.clientY - rect.top) * dpr,
    };
  };

  private pressAt: { x: number; y: number } | null = null;

  private onPointerDown = (e: PointerEvent): void => {
    this.pressAt = { x: e.clientX, y: e.clientY };
  };

  private onPointerUp = (e: PointerEvent): void => {
    const press = this.pressAt;
    this.pressAt = null;
    if (!press || Math.hypot(e.clientX - press.x, e.clientY - press.y) > 4) return;
    this.onPointerMove(e); // make sure hover reflects the release position before reading it
    if (this.densityMode) {
      const rect = this.canvas.getBoundingClientRect();
      const hit = this.densityPointAt(e.clientX - rect.left, e.clientY - rect.top);
      if (hit < 0) return;
      const key = this.densityGroupKeys[hit];
      if (key !== undefined && this.groupBy !== null) { this.onGroupClick?.(this.groupBy, key); return; }
      this.camera.x = this.densityNodes[hit * 7];
      this.camera.y = this.densityNodes[hit * 7 + 1];
      this.camera.zoom = Math.min(50, this.camera.zoom * 3);
      this.dirty = this.pointerDirty = true;
      return;
    }
    if (this.stackAxis !== null) this.updateSliceHover(); else this.updateHover();
    if (this.hoveredNodeId !== null) this.onNodeClick?.(this.hoveredNodeId, { shiftKey: e.shiftKey });
  };

  private onPointerLeave = (): void => {
    this.pointerPx = null;
    this.pointerDirty = true;
  };

  private buildDrawCommands(): void {
    const regl = this.regl;

    this.positionBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.nodeColorBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.nodeSizeBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.idBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.arrowCornerBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.arrowSrcDstBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.edgeSrcDstBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.edgeAlongSideBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.edgeColorBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.edgeIndexBuffer = regl.elements({ data: new Uint32Array(0), usage: "static" });
    this.edgeThinIndexBuffer = regl.elements({ data: new Uint32Array(0), primitive: "lines", usage: "static" });
    this.hyperedgeTriangleBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.stackedNodePositionBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.stackedEdgePositionBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.stackedEdgeColorBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.stackThreadPositionBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.nodeElements = regl.elements({data: new Uint32Array(0), primitive: "points"});
    this.sliceNodeElements = regl.elements({data: new Uint32Array(0), primitive: "points"});
    this.densityNodeBuffer = regl.buffer({data: new Float32Array(0), usage: "dynamic"});
    this.densityEdgeBuffer = regl.buffer({data: new Float32Array(0), usage: "dynamic"});
    this.sliceTriangleBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.stackPlaneBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });

    // Nodes render with per-vertex color (not a uniform) so individual
    // nodes/groups can be highlighted — see nodeColorOverrides/nodeColorBy
    // and resolveNodeColors(). Most graphs use the same color for every
    // node, in which case this buffer just holds nodeColor repeated
    // numNodes times; the per-vertex path costs nothing extra to keep
    // always-on versus branching between a uniform and per-vertex shader.
    const nodeDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        attribute vec4 color;
        attribute float sizeMul;
        uniform mat3 view;
        uniform float pointSize;
        varying vec4 vColor;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          gl_PointSize = pointSize * sizeMul;
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        void main() {
          vec2 c = gl_PointCoord - vec2(0.5);
          if (dot(c, c) > 0.25) discard;
          gl_FragColor = vColor;
        }
      `,
      attributes: {
        position: () => this.positionBuffer!,
        color: () => this.nodeColorBuffer!,
        sizeMul: () => this.nodeSizeBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        pointSize: this.opts.nodeRadiusPx * 2,
      },
      elements: () => this.nodeElements!,
      count: () => this.visibleNodeIds.length,
      blend: OPAQUE_CANVAS_BLEND,
      depth: { enable: false },
      primitive: "points",
    });

    // Edges render as screen-space-width quads (2 triangles, 4 vertices),
    // not GL_LINES with gl.lineWidth() — WebGL's line width is unreliable
    // across browsers (commonly clamped to 1px regardless of what's
    // requested, a long-standing ANGLE/driver limitation), so it's the
    // only way to actually honor a configurable edge width. Direction and
    // perpendicular offset are computed in clip space so the requested
    // width stays constant in *screen* pixels regardless of zoom, the
    // same way nodeRadiusPx does via gl_PointSize.
    //
    // Every edge (whether it has a layer color or just the default
    // edgeColor — see layerColor()'s null-layer fallback) goes through
    // this one path now; there's no separate "plain" vs. "colored" edge
    // draw command anymore.
    const edgeDraw = regl({
      vert: `
        precision mediump float;
        attribute vec4 srcDst;
        // x: 0 = this vertex sits at src, 1 = at dst.
        // y: -1 or +1, which side of the line this vertex is offset to.
        attribute vec2 alongSide;
        attribute vec4 color;
        uniform mat3 view;
        uniform vec2 viewportSize;
        uniform float edgeWidthPx;
        varying vec4 vColor;
        void main() {
          vec3 clipSrc = view * vec3(srcDst.xy, 1.0);
          vec3 clipDst = view * vec3(srcDst.zw, 1.0);
          vec2 clipDir = clipDst.xy - clipSrc.xy;
          vec2 pixelDir = clipDir * viewportSize;
          float len = length(pixelDir);
          pixelDir = len > 0.0001 ? pixelDir / len : vec2(1.0, 0.0);
          vec2 pixelPerp = vec2(-pixelDir.y, pixelDir.x);
          vec2 ndcPerpUnit = pixelPerp * (2.0 / viewportSize);
          vec2 base = mix(clipSrc.xy, clipDst.xy, alongSide.x);
          vec2 offset = ndcPerpUnit * alongSide.y * (edgeWidthPx * 0.5);
          gl_Position = vec4(base + offset, 0, 1);
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        void main() {
          gl_FragColor = vColor;
        }
      `,
      attributes: {
        srcDst: () => this.edgeSrcDstBuffer!,
        alongSide: () => this.edgeAlongSideBuffer!,
        color: () => this.edgeColorBuffer!,
      },
      elements: () => this.edgeIndexBuffer!,
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        viewportSize: (_ctx: any) => [this.canvas.clientWidth, this.canvas.clientHeight],
        edgeWidthPx: this.opts.edgeWidthPx,
      },
      blend: OPAQUE_CANVAS_BLEND,
      depth: { enable: false },
      primitive: "triangles",
    });

    // Cheap fallback for huge graphs (see THIN_EDGE_THRESHOLD): plain
    // GL_LINES indexed directly into positionBuffer — no per-edge CPU
    // rebuild on every layout step (unlike edgeDraw's quads, which need
    // O(edges) work client-side each time positions change) and no extra
    // per-vertex data upload beyond a static index buffer. The tradeoff is
    // real: uniform 1px-ish width (browsers mostly ignore gl.lineWidth
    // anyway) and no per-layer color — full edgeWidthPx/color control
    // only kicks in below the threshold, the same kind of size-based
    // quality/perf tradeoff the Python layout already makes (see
    // MAX_EXACT_REPULSION_NODES in hyperloom_core.algorithms.layout).
    const edgeDrawThin = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        uniform mat3 view;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
        }
      `,
      frag: `
        precision mediump float;
        uniform vec4 color;
        void main() {
          gl_FragColor = color;
        }
      `,
      attributes: {
        position: () => this.positionBuffer!,
      },
      elements: () => this.edgeThinIndexBuffer!,
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        color: this.opts.edgeColor,
      },
      blend: OPAQUE_CANVAS_BLEND,
      depth: { enable: false },
      primitive: "lines",
    });

    const hyperedgeDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        attribute vec4 color;
        uniform mat3 view;
        varying vec4 vColor;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        void main() {
          gl_FragColor = vColor;
        }
      `,
      attributes: {
        // Interleaved buffer: 6 floats/vertex (x, y, r, g, b, a).
        position: { buffer: () => this.hyperedgeTriangleBuffer!, offset: 0, stride: 24 },
        color: { buffer: () => this.hyperedgeTriangleBuffer!, offset: 8, stride: 24 },
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
      },
      // Translucent fills need real alpha blending (unlike the rest of
      // this renderer's draw commands, which never overlap each other and
      // so have gotten away without it) — hulls can overlap each other
      // and sit behind edges/nodes.
      blend: {
        enable: true,
        func: { srcRGB: "src alpha", dstRGB: "one minus src alpha", srcAlpha: 0, dstAlpha: 1 },
      },
      depth: { enable: false },
      count: () => this.hyperedgeTriangleVertexCount,
      primitive: "triangles",
    });

    const stackPlaneDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        attribute vec4 color;
        uniform mat3 view;
        varying vec4 vColor;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        void main() {
          gl_FragColor = vColor;
        }
      `,
      attributes: {
        // Interleaved buffer: 6 floats/vertex (x, y, r, g, b, a).
        position: { buffer: () => this.stackPlaneBuffer!, offset: 0, stride: 24 },
        color: { buffer: () => this.stackPlaneBuffer!, offset: 8, stride: 24 },
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
      },
      blend: {
        enable: true,
        func: { srcRGB: "src alpha", dstRGB: "one minus src alpha", srcAlpha: 0, dstAlpha: 1 },
      },
      depth: { enable: false },
      offset: regl.prop<StackDrawRange, "offset">("offset"),
      count: regl.prop<StackDrawRange, "count">("count"),
      primitive: "triangles",
    });

    const sliceTriangleDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        attribute vec4 color;
        uniform mat3 view;
        varying vec4 vColor;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        void main() {
          gl_FragColor = vColor;
        }
      `,
      attributes: {
        // Interleaved buffer: 6 floats/vertex (x, y, r, g, b, a).
        position: { buffer: () => this.sliceTriangleBuffer!, offset: 0, stride: 24 },
        color: { buffer: () => this.sliceTriangleBuffer!, offset: 8, stride: 24 },
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
      },
      blend: {
        enable: true,
        func: { srcRGB: "src alpha", dstRGB: "one minus src alpha", srcAlpha: 0, dstAlpha: 1 },
      },
      depth: { enable: false },
      offset: regl.prop<StackDrawRange, "offset">("offset"),
      count: regl.prop<StackDrawRange, "count">("count"),
      primitive: "triangles",
    });

    const stackedNodeDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        uniform mat3 view;
        uniform float pointSize;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          gl_PointSize = pointSize;
        }
      `,
      frag: `
        precision mediump float;
        uniform vec4 color;
        void main() {
          vec2 c = gl_PointCoord - vec2(0.5);
          if (dot(c, c) > 0.25) discard;
          gl_FragColor = color;
        }
      `,
      attributes: {
        position: () => this.stackedNodePositionBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        pointSize: this.opts.nodeRadiusPx * 1.6,
        color: [this.opts.nodeColor[0], this.opts.nodeColor[1], this.opts.nodeColor[2], 0.9],
      },
      elements: () => this.sliceNodeElements!,
      offset: regl.prop<StackDrawRange, "offset">("offset"),
      count: regl.prop<StackDrawRange, "count">("count"),
      primitive: "points",
    });

    const stackedEdgeDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        attribute vec4 color;
        uniform mat3 view;
        varying vec4 vColor;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        void main() {
          gl_FragColor = vColor;
        }
      `,
      attributes: {
        position: () => this.stackedEdgePositionBuffer!,
        color: () => this.stackedEdgeColorBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
      },
      // Drawn per-slice (see drawStackedScene) interleaved with that
      // slice's plane backing and nodes, back-to-front, so ordinary alpha
      // blending with no depth test gives correct painter's-algorithm
      // layering — same trick used for hyperedge hulls, but per-slice
      // here instead of one combined call, so each plane's backing quad
      // actually occludes the plane behind it.
      blend: {
        enable: true,
        func: { srcRGB: "src alpha", dstRGB: "one minus src alpha", srcAlpha: 0, dstAlpha: 1 },
      },
      depth: { enable: false },
      offset: regl.prop<StackDrawRange, "offset">("offset"),
      count: regl.prop<StackDrawRange, "count">("count"),
      primitive: "lines",
    });

    const stackThreadDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        uniform mat3 view;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
        }
      `,
      frag: `
        precision mediump float;
        uniform vec4 color;
        void main() {
          gl_FragColor = color;
        }
      `,
      attributes: {
        position: () => this.stackThreadPositionBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        color: [0.4, 0.4, 0.45, this.opts.stackThreadAlpha],
      },
      blend: {
        enable: true,
        func: { srcRGB: "src alpha", dstRGB: "one minus src alpha", srcAlpha: 0, dstAlpha: 1 },
      },
      depth: { enable: false },
      count: () => this.stackThreadList.length * 2,
      primitive: "lines",
    });

    const arrowDraw = regl({
      vert: `
        precision mediump float;
        // (along, side): tip is (1, 0); the two base corners are (0, 0.5)
        // and (0, -0.5). Static per triangle — doesn't change with layout.
        attribute vec2 corner;
        // (srcX, srcY, dstX, dstY) of this triangle's edge — rebuilt every
        // time positions change, since direction must be recomputed.
        attribute vec4 srcDst;
        uniform mat3 view;
        uniform float arrowLength;
        uniform float arrowWidth;
        uniform float arrowT;
        void main() {
          vec2 src = srcDst.xy;
          vec2 dst = srcDst.zw;
          vec2 dir = dst - src;
          float len = length(dir);
          vec2 dirN = len > 0.0001 ? dir / len : vec2(1.0, 0.0);
          vec2 perp = vec2(-dirN.y, dirN.x);
          vec2 tip = src + dir * arrowT;
          // corner.x==1 (tip): worldPos == tip.
          // corner.x==0 (base corners): pulled back by arrowLength along
          // -dirN, then offset sideways by arrowWidth for the two corners.
          vec2 worldPos = tip - dirN * arrowLength * (1.0 - corner.x) + perp * corner.y * arrowWidth;
          vec3 p = view * vec3(worldPos, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
        }
      `,
      frag: `
        precision mediump float;
        uniform vec4 color;
        void main() {
          gl_FragColor = color;
        }
      `,
      attributes: {
        corner: () => this.arrowCornerBuffer!,
        srcDst: () => this.arrowSrcDstBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        arrowLength: this.opts.arrowLength,
        arrowWidth: this.opts.arrowWidth,
        arrowT: this.opts.arrowT,
        color: this.opts.arrowColor,
      },
      blend: OPAQUE_CANVAS_BLEND,
      depth: { enable: false },
      count: () => this.directedEdges.length * 3,
      primitive: "triangles",
    });

    const pickDraw = regl({
      vert: `
        precision mediump float;
        attribute vec2 position;
        attribute float id;
        uniform mat3 view;
        uniform float pointSize;
        varying float vId;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          gl_PointSize = pointSize;
          vId = id;
        }
      `,
      frag: `
        precision mediump float;
        varying float vId;
        ${PICK_ENCODE_GLSL}
        void main() {
          vec2 c = gl_PointCoord - vec2(0.5);
          if (dot(c, c) > 0.25) discard;
          gl_FragColor = encodeId(vId);
        }
      `,
      attributes: {
        position: () => this.positionBuffer!,
        id: () => this.idBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        // A larger hit target than the visible dot makes hovering easier
        // without changing what's drawn to the visible canvas.
        pointSize: this.opts.nodeRadiusPx * 3,
      },
      elements: () => this.nodeElements!,
      count: () => this.visibleNodeIds.length,
      primitive: "points",
      framebuffer: () => this.pickFbo!,
    });

    const densityNodeDraw = regl({
      vert: `precision mediump float; attribute vec2 position; attribute float size; attribute vec4 color; uniform mat3 view; varying vec4 vColor;
        void main(){vec3 p=view*vec3(position,1.0);gl_Position=vec4(p.xy,0,1);gl_PointSize=size;vColor=color;}`,
      frag: `precision mediump float; varying vec4 vColor; void main(){vec2 c=gl_PointCoord-vec2(.5);if(dot(c,c)>.25)discard;gl_FragColor=vColor;}`,
      attributes: {position:{buffer:()=>this.densityNodeBuffer!,stride:28,offset:0}, size:{buffer:()=>this.densityNodeBuffer!,stride:28,offset:8}, color:{buffer:()=>this.densityNodeBuffer!,stride:28,offset:12}},
      uniforms: {view:()=>this.camera.matrix(this.canvas.clientWidth/this.canvas.clientHeight)},
      primitive:"points", depth:{enable:false}, blend:OPAQUE_CANVAS_BLEND,
      offset:regl.prop<StackDrawRange,"offset">("offset"), count:regl.prop<StackDrawRange,"count">("count"),
    });
    const densityEdgeDraw = regl({
      vert: `precision mediump float; attribute vec2 position; attribute float alpha; uniform mat3 view; varying float vAlpha; void main(){vec3 p=view*vec3(position,1.0);gl_Position=vec4(p.xy,0,1);vAlpha=alpha;}`,
      frag: `precision mediump float; varying float vAlpha; void main(){gl_FragColor=vec4(.42,.5,.58,vAlpha);}`,
      attributes:{position:{buffer:()=>this.densityEdgeBuffer!,stride:12,offset:0}, alpha:{buffer:()=>this.densityEdgeBuffer!,stride:12,offset:8}},
      uniforms:{view:()=>this.camera.matrix(this.canvas.clientWidth/this.canvas.clientHeight)},
      primitive:"lines", depth:{enable:false}, blend:OPAQUE_CANVAS_BLEND,
      offset:regl.prop<StackDrawRange,"offset">("offset"), count:regl.prop<StackDrawRange,"count">("count"),
    });
    this.drawDensity = slice => {
      const range = this.densityRanges[slice];
      if (!range) return;
      if (range.edges.count) densityEdgeDraw(range.edges);
      if (range.nodes.count) densityNodeDraw(range.nodes);
    };
    this.drawNodes = () => nodeDraw();
    this.drawEdges = () => {
      if (this.useThinEdges) {
        if (this.thinEdgeCount > 0) edgeDrawThin();
      } else if (this.edges.length > 0) {
        edgeDraw();
      }
    };
    this.drawHyperedges = () => (this.hyperedgeTriangleVertexCount > 0 ? hyperedgeDraw() : undefined);
    this.drawArrows = () => (this.directedEdges.length > 0 ? arrowDraw() : undefined);
    this.drawStackThreads = () => (this.stackThreadList.length > 0 ? stackThreadDraw() : undefined);
    // Interleaved per-slice, back-to-front: each plane's backing quad,
    // then its edges, then its nodes — so plane N visually occludes
    // plane N-1's content where they overlap (see the comment on
    // stackedEdgeDraw's blend config for why this can't be 3 combined
    // passes instead).
    this.drawStackedScene = () => {
      for (let s = 0; s < this.stackNodeSliceCount; s++) {
        stackPlaneDraw({ offset: s * 6, count: 6 });
        if (this.densityMode) { this.drawDensity(s); continue; }
        const triangles = this.sliceTriangleRanges[s];
        if (triangles?.count) sliceTriangleDraw(triangles);
        const range = this.stackSliceEdgeVertexRanges[s];
        if (range && range.count > 0) stackedEdgeDraw(range);
        stackedNodeDraw({ offset: s * this.visibleNodeIds.length, count: this.visibleNodeIds.length });
      }
    };
    this.drawPick = () => (this.numNodes > 0 ? pickDraw() : undefined);
  }

  private updateHover(): void {
    if (!this.pointerPx || this.numNodes === 0 || !this.pickFbo) {
      this.setHovered(null);
      return;
    }
    this.regl.clear({ color: [0, 0, 0, 0], depth: 1, framebuffer: this.pickFbo });
    this.drawPick();
    // WebGL's Y axis is flipped relative to screen/pointer coordinates.
    const y = this.pickFboSize.height - Math.round(this.pointerPx.y);
    const x = Math.round(this.pointerPx.x);
    if (x < 0 || y < 0 || x >= this.pickFboSize.width || y >= this.pickFboSize.height) {
      this.setHovered(null);
      return;
    }
    const pixel = this.regl.read({
      framebuffer: this.pickFbo,
      x,
      y,
      width: 1,
      height: 1,
    }) as Uint8Array;
    this.setHovered(decodePickedId(pixel));
  }

  /** Hit-test frontmost panels first; blank foreground panels occlude back nodes. */
  private updateSliceHover(): void {
    if (!this.pointerPx) { this.setHovered(null); return; }
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const px = this.pointerPx.x * w / this.canvas.width, py = this.pointerPx.y * h / this.canvas.height;
    const g = this.getSliceGeometry();
    for (let s = this.stackNodeSliceCount - 1; s >= 0; s--) {
      const [dx, dy] = g.offset(s);
      const [left, bottom] = this.worldToScreen(g.bounds[0] + dx, g.bounds[1] + dy, w, h);
      const [right, top] = this.worldToScreen(g.bounds[2] + dx, g.bounds[3] + dy, w, h);
      const radius = Math.max(6, this.opts.nodeRadiusPx);
      if (px < left - radius || px > right + radius || py < top - radius || py > bottom + radius) continue;
      let found: number | null = null, nearest = radius * radius;
      for (const n of this.visibleNodeIds) {
        const [x, y] = this.worldToScreen(...g.point(n, s), w, h);
        const distance = (px - x) ** 2 + (py - y) ** 2;
        if (distance < nearest) { found = n; nearest = distance; }
      }
      if (found !== null) { this.setHovered(found); return; }
      if (px >= left && px <= right && py >= top && py <= bottom) break;
    }
    this.setHovered(null);
  }

  private setHovered(nodeId: number | null): void {
    if (nodeId === this.hoveredNodeId) return;
    this.hoveredNodeId = nodeId;
    this.onHover?.(nodeId);
  }

  searchNodes(query: string): WireNode[] { return this.index.search(query); }

  inspectNode(nodeId: number): {node: WireNode; neighbors: number; connectors: number} | null {
    const node = this.index.nodes[nodeId];
    if (!node) return null;
    const neighborhood = this.index.neighborhood(nodeId);
    return {node, neighbors: neighborhood.nodes.size - 1, connectors: neighborhood.connectors.size};
  }

  focusNeighborhood(nodeId: number | null): void {
    if (nodeId !== null && !this.index.nodes[nodeId]) throw new RangeError("Unknown node id");
    const neighborhood = nodeId === null ? null : this.index.neighborhood(nodeId);
    this.selectedNode = nodeId;
    this.focusedNodes = neighborhood?.nodes ?? null;
    this.focusedConnectors = neighborhood?.connectors ?? null;
    this.refreshVisibleSet();
    this.fitView();
  }

  private refreshVisibleSet(reevaluate = true): void {
    if (reevaluate) this.evaluateLod();
    this.updateNodeElements();
    this.setEdgeSet(this.filterConnectors(this.allConnectors));
    this.setHyperedgeSet(this.filterConnectors(this.allHyperedges));
    if (this.stackAxis !== null) this.rebuildStackSlices();
    this.dirty = this.densityDirty = this.pointerDirty = true;
  }

  /** Attribute names with their value counts or numeric ranges, for building filter/colour/size controls. */
  getNodeAttributes(): AttributeSummary[] { return this.index.attributeSummary(); }

  /** Show only nodes passing the filter (attribute values, numeric range, degree); connectors need every
   * endpoint visible. Node positions do not change. Pass null to clear. Composes with neighbourhood focus and
   * with the layer/time filters. Returns how many nodes pass. */
  setNodeFilter(filter: NodeFilter | null): number {
    this.nodeFilterNodes = filter ? this.index.matchNodes(filter) : null;
    this.refreshVisibleSet();
    return this.nodeFilterNodes?.size ?? this.numNodes;
  }

  /** Number of nodes currently eligible to be drawn (after focus and node filter). */
  getVisibleNodeCount(): number { return this.baseNodes()?.size ?? this.numNodes; }

  /** Colour nodes by an attribute (one colour per distinct value), or null for the plain node colour. */
  setNodeColorBy(attribute: string | null): void {
    this.nodeColorByAttr = attribute;
    this.resolveNodeColors(this.index.nodes);
    this.dirty = this.densityDirty = true;
  }

  /** Scale node size by "degree", by a numeric attribute, or null for uniform size. Sizes span 0.7x-3x. */
  setNodeSizeBy(by: string | null): void {
    const nodes = this.index.nodes;
    const raw = new Float64Array(nodes.length).fill(NaN);
    if (by === "degree") nodes.forEach((n, i) => (raw[i] = this.index.degree(n.id)));
    else if (by !== null) nodes.forEach((n, i) => { const v = n.attrs[by]; if (typeof v === "number" && Number.isFinite(v)) raw[i] = v; });
    let lo = Infinity, hi = -Infinity;
    for (const v of raw) if (!Number.isNaN(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    const sizes = new Float32Array(nodes.length).fill(1);
    if (by !== null && hi > lo) raw.forEach((v, i) => { if (!Number.isNaN(v)) sizes[i] = 0.7 + 2.3 * Math.sqrt((v - lo) / (hi - lo)); });
    this.nodeSizeBy = by !== null && hi > lo ? by : null;
    this.nodeSizeBuffer?.({ data: sizes, usage: "static" } as any);
    this.dirty = true;
  }

  getNodeSizeBy(): string | null { return this.nodeSizeBy; }

  getNodeKey(id: number): string | null { return this.nodeKeys[id] ?? null; }

  /** Canvas-relative CSS pixel position of a drawn node in the flat view, or null if it is not individually drawn. */
  getNodeScreenPosition(id: number): [number, number] | null {
    if (this.stackAxis !== null || this.densityMode || !this.visibleNodeIds.includes(id)) return null;
    return this.worldToScreen(this.positions[id * 2], this.positions[id * 2 + 1], this.canvas.clientWidth, this.canvas.clientHeight);
  }

  /** Ring and label these nodes (for example a selection); pass [] to clear. Only affects the flat view. */
  setHighlightedNodes(ids: number[]): void {
    this.highlighted = ids.filter(id => !!this.index.nodes[id]);
    this.pointerDirty = true;
  }

  /** Show only the fewest-hop route between two nodes (any direction; a hyperedge is one hop) and fit it.
   * Clears the attribute filter so the whole route is visible. Returns null if the nodes are not connected. */
  focusPath(from: number, to: number): { nodes: number[]; connectors: number[]; hops: number } | null {
    const path = this.index.shortestPath(from, to);
    if (!path) return null;
    this.nodeFilterNodes = null;
    this.selectedNode = null;
    this.focusedNodes = new Set(path.nodes);
    this.focusedConnectors = new Set(path.connectors);
    this.refreshVisibleSet();
    this.fitView();
    return { ...path, hops: path.connectors.length };
  }

  /** Multiply the camera zoom (e.g. 1.5 to zoom in, 1/1.5 to zoom out). */
  zoomBy(factor: number): void {
    this.camera.zoom = Math.min(50, Math.max(0.02, this.camera.zoom * factor));
    this.dirty = this.pointerDirty = true;
  }

  fitView(): void {
    let left=Infinity, right=-Infinity, bottom=Infinity, top=-Infinity;
    const geometry = this.stackAxis !== null ? this.getSliceGeometry() : null;
    const base = this.baseNodes();
    const fitIds: Iterable<number> = geometry ? this.visibleNodeIds : base ?? Array.from({length:this.numNodes},(_,i)=>i);
    for(let slice=0; slice<(geometry ? this.stackNodeSliceCount : 1); slice++) {
      for(const n of fitIds) {
        const [x,y] = geometry ? geometry.point(n,slice) : [this.positions[n*2],this.positions[n*2+1]];
        left=Math.min(left,x);right=Math.max(right,x);bottom=Math.min(bottom,y);top=Math.max(top,y);
      }
    }
    if (!Number.isFinite(left)) {left=bottom=-.5;right=top=.5;}
    this.camera.x=(left+right)/2;this.camera.y=(bottom+top)/2;
    this.camera.zoom=Math.min(20,1.4/Math.max(top-bottom,.1),1.4*(this.canvas.clientWidth/Math.max(this.canvas.clientHeight,1))/Math.max(right-left,.1));
    this.dirty=this.pointerDirty=true;
  }

  private updateNodeElements(): void {
    const view = this.viewNodes();
    this.visibleNodeIds = view ? Array.from(view) : Array.from({length:this.numNodes},(_,i)=>i);
    this.nodeElements?.({data:new Uint32Array(this.visibleNodeIds),primitive:"points"} as any);
    const ids = new Uint32Array(this.densityMode ? 0 : this.visibleNodeIds.length*this.stackNodeSliceCount);
    if (!this.densityMode) for(let s=0;s<this.stackNodeSliceCount;s++) this.visibleNodeIds.forEach((n,i)=>ids[s*this.visibleNodeIds.length+i]=s*this.numNodes+n);
    this.sliceNodeElements?.({data:ids,primitive:"points"} as any);
  }

  private rebuildDensity(): void {
    const nodeData:number[]=[],edgeData:number[]=[];
    this.densityRanges=[];this.densityCounts=[];this.densityGroupKeys=[];this.densityLinksShown=0;this.densityLinksTotal=0;
    const geometry = this.stackAxis !== null ? this.getSliceGeometry() : null;
    const grouping = geometry ? null : this.groupBy;
    const slices = geometry ? this.stackSlices : [{connectors:[...this.filterConnectors(this.allConnectors),...this.filterConnectors(this.allHyperedges)]}];
    slices.forEach((slice,s)=>{
      let active = geometry ? Array.from(new Set(slice.connectors.flatMap(c=>c.endpoints))) : this.visibleNodeIds;
      // Aggregate only what is on screen so cells get finer as you zoom; groups always cover everything.
      if (!geometry && !grouping) active = this.nodesInViewport(new Set(active));
      const positions=new Float32Array(active.length*2), ids=new Map<number,number>();
      active.forEach((n,i)=>{ids.set(n,i);positions[i*2]=this.positions[n*2];positions[i*2+1]=this.positions[n*2+1];});
      const edges:{source:number;target:number}[]=[];
      for(const c of slice.connectors) {
        if(c.endpoints.length!==2) continue;
        const a=ids.get(c.endpoints[0]),b=ids.get(c.endpoints[1]);
        if(a!==undefined&&b!==undefined) edges.push({source:a,target:b});
      }
      let groupNames: string[] = [];
      let density;
      if (grouping) {
        const index=new Map<string,number>();
        const groupIds=active.map(n=>{
          const v=this.index.nodes[n].attrs[grouping];
          const key=v===undefined||v===null?"(none)":typeof v==="object"?JSON.stringify(v):String(v);
          let g=index.get(key);if(g===undefined){g=index.size;index.set(key,g);}
          return g;
        });
        density=aggregateGroups(positions,edges,groupIds);
        groupNames=Array.from(index.keys());
        density.membership.forEach((pt,i)=>{ if(this.densityGroupKeys[pt]===undefined) this.densityGroupKeys[pt]=groupNames[groupIds[i]]; });
      } else density=aggregateDensity(positions,edges,geometry?SLICE_DENSITY_RESOLUTION:DENSITY_RESOLUTION);
      const point=(x:number,y:number):[number,number]=>{
        if (!geometry) return [x,y];
        const [dx,dy]=geometry.offset(s);return [x*geometry.scale+dx,y*geometry.scale+dy];
      };
      // Pixel size of one grid cell, so neighbouring dots overlap into a continuous density map.
      let spanWorld=1e-9;
      if(!geometry&&!grouping&&active.length) {
        let lx=Infinity,hx=-Infinity,ly=Infinity,hy=-Infinity;
        for(let i=0;i<positions.length;i+=2){lx=Math.min(lx,positions[i]);hx=Math.max(hx,positions[i]);ly=Math.min(ly,positions[i+1]);hy=Math.max(hy,positions[i+1]);}
        spanWorld=Math.max(hx-lx,hy-ly,1e-9);
      }
      const cellPx=spanWorld/DENSITY_RESOLUTION*this.camera.zoom*this.canvas.clientHeight/2;
      const maxCount=density.points.reduce((m,p)=>Math.max(m,p.count),1);
      const nodeOffset=nodeData.length/7, edgeOffset=edgeData.length/3;
      // A cell shows its most common node colour, so planted groups stay identifiable in the overview.
      const tallies=density.points.map(()=>new Map<number,number>());
      active.forEach((n,i)=>{
        const c=this.nodeColorValues, o=n*4;
        const key=(Math.round(c[o]*255)<<24|Math.round(c[o+1]*255)<<16|Math.round(c[o+2]*255)<<8|Math.round(c[o+3]*255))>>>0;
        const tally=tallies[density.membership[i]];tally.set(key,(tally.get(key)??0)+1);
      });
      density.points.forEach((p,i)=>{
        let best=0,bestCount=-1;
        for(const [key,count] of tallies[i]) if(count>bestCount){best=key;bestCount=count;}
        const color=bestCount<0?[...this.opts.nodeColor]:[(best>>>24)/255,((best>>>16)&255)/255,((best>>>8)&255)/255,(best&255)/255];
        const share=Math.sqrt(p.count/maxCount);
        let size:number;
        if(geometry) size=Math.min(12,3+Math.log2(p.count+1));
        else if(grouping) { size=Math.min(56,12+9*Math.log10(p.count)); color[3]=.9; }
        else { size=Math.min(40,Math.max(4,cellPx*(.9+.9*share))); color[3]=.35+.5*share; }
        nodeData.push(...point(p.x,p.y),size,...color);
        this.densityCounts.push(p.count);
      });
      // Show the heaviest links; opacity follows how many connectors each one stands for.
      const limit=grouping?4000:geometry?250:1500;
      const links=density.links.slice().sort((x,y)=>y[2]-x[2]);
      const heaviest=links.length?links[0][2]:1;
      this.densityLinksTotal+=links.length;
      for(const [a,b,w] of links.slice(0,limit)) {
        const alpha=Math.min(.8,.1+.7*Math.sqrt(w/heaviest));
        edgeData.push(...point(density.points[a].x,density.points[a].y),alpha,...point(density.points[b].x,density.points[b].y),alpha);
        this.densityLinksShown++;
      }
      this.densityRanges.push({nodes:{offset:nodeOffset,count:nodeData.length/7-nodeOffset},edges:{offset:edgeOffset,count:edgeData.length/3-edgeOffset}});
    });
    this.densityNodes=new Float32Array(nodeData);this.densityEdges=new Float32Array(edgeData);
    this.densityNodeBuffer?.({data:this.densityNodes,usage:"dynamic"} as any);
    this.densityEdgeBuffer?.({data:this.densityEdges,usage:"dynamic"} as any);
    this.densityDirty=false;
  }

  private densityCaption(): string {
    const total = (this.baseNodes()?.size ?? this.numNodes).toLocaleString();
    const links = this.densityLinksTotal === 0 ? "no links between cells"
      : this.densityLinksShown < this.densityLinksTotal ? `strongest ${this.densityLinksShown.toLocaleString()} of ${this.densityLinksTotal.toLocaleString()} links`
      : `${this.densityLinksShown.toLocaleString()} links`;
    if (this.groupBy !== null && this.stackAxis === null) return `Grouped by ${this.groupBy} · ${total} nodes · click a group to expand it · ${links}`;
    return `Density overview · ${this.densityNodesInView.toLocaleString()} of ${total} nodes in view · click a cell or zoom in to see individual nodes · ${links}`;
  }

  /** Index of the aggregated point under a canvas-relative CSS pixel position (flat overview only), or -1. */
  private densityPointAt(px: number, py: number): number {
    if (!this.densityMode || this.stackAxis !== null) return -1;
    const w=this.canvas.clientWidth,h=this.canvas.clientHeight;
    let best=-1,bestDistance=Infinity;
    for(let i=0;i<this.densityNodes.length;i+=7) {
      const [x,y]=this.worldToScreen(this.densityNodes[i],this.densityNodes[i+1],w,h);
      const d=Math.hypot(x-px,y-py);
      if(d<=this.densityNodes[i+2]/2+3&&d<bestDistance){best=i/7;bestDistance=d;}
    }
    return best;
  }

  /** Load a full graph snapshot: allocates the position/edge/id buffers. */
  loadGraph(msg: GraphMessage): void {
    this.setStackMode(null); // reset in case a renderer instance is reused across graphs

    this.index = new GraphIndex(msg.nodes, msg.connectors);
    this.focusedNodes = this.focusedConnectors = this.nodeFilterNodes = this.regionNodes = null;
    this.groupBy = null;
    this.selectedNode = null;
    this.numNodes = msg.nodes.length;
    this.positions = new Float32Array(this.numNodes * 2);
    this.evaluateLod();
    this.nodeSizeBy = null;
    this.nodeSizeBuffer?.({ data: new Float32Array(msg.nodes.length).fill(1), usage: "static" } as any);
    this.updateNodeElements();
    this.positions = new Float32Array(this.numNodes * 2);
    this.positionBuffer?.({ data: this.positions, usage: "dynamic" } as any);

    this.nodeIds = new Float32Array(this.numNodes);
    for (let i = 0; i < this.numNodes; i++) this.nodeIds[i] = i;
    this.idBuffer?.({ data: this.nodeIds, usage: "dynamic" } as any);

    this.nodeKeys = msg.nodes.map((n) => String(n.key));
    this.resolveNodeColors(msg.nodes);
    this.resolveNodeLabels(msg.nodes);
    this.buildEdgeColorLegend(msg.connectors);

    this.allConnectors = msg.connectors.filter((c) => c.endpoints.length === 2);
    this.allHyperedges = msg.connectors.filter((c) => c.endpoints.length > 2);
    // Temporal domain spans both kinds — a hyperedge can be just as
    // time-bounded as an ordinary connector.
    const domain = computeTimeDomain(msg.connectors);
    this.timeDomain = domain && { ...domain, unit: msg.time_unit === "epoch_seconds" ? "epoch_seconds" : null };
    this.currentTimeFilter = null; // default: show everything, unfiltered
    this.timeMode = "instant";
    this.timeWindow = 0;
    this.onTimeDomain?.(this.timeDomain);

    this.layers = msg.layers;
    this.hasLayers = this.layers.length > 0;
    this.layerVisibility = null; // default: show every layer
    this.onLayers?.(
      this.layers.map((l) => ({ id: l.id, key: l.key, color: layerColor(l.id, this.opts.edgeColor) }))
    );

    this.setEdgeSet(this.allConnectors);
    this.setHyperedgeSet(this.allHyperedges);
    this.onGraphLoaded?.();
  }

  /** Resolve each node's fill color: nodeColorOverrides (by key) beats
   * nodeColorBy (by attribute value) beats the plain nodeColor default.
   * Rebuilds the per-vertex color buffer nodeDraw reads from, and reports
   * the nodeColorBy legend (or null) via onNodeColorLegend. */
  private resolveNodeColors(nodes: WireNode[]): void {
    let palette: Map<string, [number, number, number, number]> | null = null;
    if (this.nodeColorByAttr) {
      const attr = this.nodeColorByAttr;
      palette = buildCategoricalPalette(nodes.map((n) => String(n.attrs[attr] ?? "")));
    }

    const colors = new Float32Array(nodes.length * 4);
    nodes.forEach((n, i) => {
      let color = this.opts.nodeColor;
      if (palette && this.nodeColorByAttr) {
        const c = palette.get(String(n.attrs[this.nodeColorByAttr] ?? ""));
        if (c) color = c;
      }
      const override = this.nodeColorOverrides[String(n.key)];
      if (override) color = override;
      colors.set(color, i * 4);
    });
    this.nodeColorValues = colors;
    this.nodeColorBuffer?.({ data: colors, usage: "static" } as any);

    this.onNodeColorLegend?.(
      palette ? Array.from(palette, ([value, color]) => ({ value, color })) : null
    );
  }

  /** Resolve each node's label text from nodeLabelSpec — see
   * NodeLabelSpec's doc comment for the three forms. */
  private resolveNodeLabels(nodes: WireNode[]): void {
    const spec = this.nodeLabelSpec;
    if (!spec) {
      this.nodeLabelText = [];
      return;
    }
    if (spec === true) {
      this.nodeLabelText = nodes.map((n) => String(n.key));
    } else if (typeof spec === "string") {
      this.nodeLabelText = nodes.map((n) => {
        const v = n.attrs[spec];
        return v == null ? null : String(v);
      });
    } else {
      this.nodeLabelText = nodes.map((n) => spec[String(n.key)] ?? null);
    }
  }

  /** Build (but don't apply) the edgeColorBy legend from the full
   * connector set — resolveEdgeColor() below does the actual per-edge
   * lookup lazily as each edge set is built. */
  private buildEdgeColorLegend(connectors: WireConnector[]): void {
    if (!this.edgeColorByAttr) {
      this.edgeColorPalette = null;
      this.onEdgeColorLegend?.(null);
      return;
    }
    const attr = this.edgeColorByAttr;
    this.edgeColorPalette = buildCategoricalPalette(connectors.map((c) => String(c.attrs[attr] ?? "")));
    this.onEdgeColorLegend?.(
      Array.from(this.edgeColorPalette, ([value, color]) => ({ value, color }))
    );
  }

  /** Resolve one connector's color: edgeColorOverrides (by "sourceKey||
   * targetKey") beats edgeColorBy (by attribute value) beats its layer
   * color beats the plain edgeColor default. */
  private resolveEdgeColor(c: WireConnector): [number, number, number, number] {
    let color = layerColor(c.layer_id, this.opts.edgeColor);
    if (this.edgeColorPalette && this.edgeColorByAttr) {
      const found = this.edgeColorPalette.get(String(c.attrs[this.edgeColorByAttr] ?? ""));
      if (found) color = found;
    }
    const key = `${this.nodeKeys[c.endpoints[0]]}||${this.nodeKeys[c.endpoints[1]]}`;
    const override = this.edgeColorOverrides[key];
    if (override) color = override;
    return color;
  }

  /** Rebuild edge/arrow geometry from a given connector set — used for the
   * initial full load and for time/layer-filtered subsets. */
  private setEdgeSet(connectors: WireConnector[]): void {
    this.dirty = this.densityDirty = true;
    this.visibleConnectorCount = connectors.length;
    this.useThinEdges = connectors.length > Renderer.THIN_EDGE_THRESHOLD;

    if (this.useThinEdges) {
      // Cheap path: index directly into positionBuffer, no per-edge CPU
      // work needed here or on layout steps (see edgeDrawThin's comment) —
      // and no `edges` object array either (200K+ small-object
      // allocations is exactly the kind of per-edge overhead this path
      // exists to avoid; SVG export falls back to one uniform color for
      // these, which is what thin mode actually renders anyway).
      this.edges = [];
      this.thinEdgeCount = connectors.length;
      const thinIndexData = new Uint32Array(connectors.length * 2);
      connectors.forEach((c, i) => {
        thinIndexData[i * 2] = c.endpoints[0];
        thinIndexData[i * 2 + 1] = c.endpoints[1];
      });
      this.thinEdgePairs = thinIndexData;
      this.edgeThinIndexBuffer?.({ data: thinIndexData, primitive: "lines", usage: "static" } as any);
    } else {
      this.edges = connectors.map((c) => ({
        source: c.endpoints[0],
        target: c.endpoints[1],
        color: this.resolveEdgeColor(c),
      }));
      this.thinEdgeCount = 0;
      // Static per-quad layout (4 vertices: src/-1, src/+1, dst/+1,
      // dst/-1) and its matching color (repeated for all 4 vertices) and
      // triangle indices (0,1,2 and 0,2,3, offset per quad).
      const alongSideData = new Float32Array(this.edges.length * 4 * 2);
      const colorData = new Float32Array(this.edges.length * 4 * 4);
      const indexData = new Uint32Array(this.edges.length * 6);
      const alongSidePattern = [0, -1, 0, 1, 1, 1, 1, -1];
      this.edges.forEach((e, i) => {
        alongSideData.set(alongSidePattern, i * 8);
        colorData.set(e.color, i * 16);
        colorData.set(e.color, i * 16 + 4);
        colorData.set(e.color, i * 16 + 8);
        colorData.set(e.color, i * 16 + 12);
        const base = i * 4;
        indexData.set([base, base + 1, base + 2, base, base + 2, base + 3], i * 6);
      });
      this.edgeAlongSideBuffer?.({ data: alongSideData, usage: "static" } as any);
      this.edgeColorBuffer?.({ data: colorData, usage: "static" } as any);
      this.edgeIndexBuffer?.({ data: indexData, usage: "static" } as any);
      this.rebuildEdgePositions();
    }

    this.directedEdges = connectors
      .filter((c) => c.directed)
      .map((c) => ({ source: c.endpoints[0], target: c.endpoints[1] }));

    // Static per-triangle corner layout: tip, then two base corners.
    this.arrowCornerData = new Float32Array(this.directedEdges.length * 3 * 2);
    const corners = [1, 0, 0, 0.5, 0, -0.5];
    for (let i = 0; i < this.directedEdges.length; i++) {
      this.arrowCornerData.set(corners, i * 6);
    }
    this.arrowCornerBuffer?.({ data: this.arrowCornerData, usage: "static" } as any);

    this.rebuildArrowSrcDst();
  }

  /** Recompute the (source, target) positions baked into the edge quad
   * buffer from the current position buffer — must be called whenever
   * positions change (mirrors rebuildArrowSrcDst). Each of a quad's 4
   * vertices gets the same srcDst pair; only alongSide (static) picks
   * which endpoint/side a given vertex represents. */
  private rebuildEdgePositions(): void {
    const data = new Float32Array(this.edges.length * 4 * 4);
    this.edges.forEach((e, i) => {
      const sx = this.positions[e.source * 2] ?? 0;
      const sy = this.positions[e.source * 2 + 1] ?? 0;
      const tx = this.positions[e.target * 2] ?? 0;
      const ty = this.positions[e.target * 2 + 1] ?? 0;
      for (let v = 0; v < 4; v++) {
        const o = (i * 4 + v) * 4;
        data[o] = sx;
        data[o + 1] = sy;
        data[o + 2] = tx;
        data[o + 3] = ty;
      }
    });
    this.edgeSrcDstBuffer?.({ data, usage: "dynamic" } as any);
  }

  /** Apply the current time and layer filters to an arbitrary connector
   * list — shared by plain connectors and hyperedges, since both filter
   * the same way. */
  private filterConnectors(connectors: WireConnector[]): WireConnector[] {
    let visible = this.focusedConnectors ? connectors.filter(c => this.focusedConnectors!.has(c.id)) : connectors;
    if (this.nodeFilterNodes) {
      const keep = this.nodeFilterNodes;
      visible = visible.filter(c => c.endpoints.every(n => keep.has(n)));
    }
    if (this.regionNodes) {
      const inView = this.regionNodes;
      visible = visible.filter(c => c.endpoints.every(n => inView.has(n)));
    }
    if (this.currentTimeFilter !== null) {
      const t = this.currentTimeFilter, mode = this.timeMode, window = this.timeWindow;
      visible = visible.filter((c) => connectorActiveAt(c, t, mode, window));
    }
    if (this.layerVisibility !== null) {
      const vis = this.layerVisibility;
      visible = visible.filter((c) => c.layer_id === null || vis.has(c.layer_id));
    }
    return visible;
  }

  /** Show only connectors valid at time `t` (t_start <= t <= t_end, where a
   * null bound on the wire means "always present"). Pass null to show
   * every connector regardless of time (the default). No-op if the graph
   * has no temporal connectors. Composes with setLayerFilter. Applies to
   * both plain edges and hyperedges. */
  setTimeFilter(t: number | null, options: TimeFilterOptions = {}): void {
    if (options.window !== undefined && (!Number.isFinite(options.window) || options.window < 0)) throw new RangeError("window must be a non-negative finite number");
    this.currentTimeFilter = t;
    this.timeMode = options.mode ?? "instant";
    this.timeWindow = options.window ?? 0;
    this.setEdgeSet(this.filterConnectors(this.allConnectors));
    this.setHyperedgeSet(this.filterConnectors(this.allHyperedges));
  }

  /** Show only connectors in the given layer ids (plus layer-less
   * connectors, always shown regardless of filter). Pass null to show
   * every layer (the default). Composes with setTimeFilter. Applies to
   * both plain edges and hyperedges. */
  setLayerFilter(visibleLayerIds: number[] | null): void {
    this.layerVisibility = visibleLayerIds === null ? null : new Set(visibleLayerIds);
    this.setEdgeSet(this.filterConnectors(this.allConnectors));
    this.setHyperedgeSet(this.filterConnectors(this.allHyperedges));
  }

  getTimeDomain(): TimeDomain | null {
    return this.timeDomain;
  }

  /** Number of connectors currently drawn (after time/layer filtering, if
   * any). Useful for tests/tooling to confirm filtering actually changed
   * what's on screen, without relying on pixel-level comparisons. */
  getVisibleEdgeCount(): number {
    return this.visibleConnectorCount;
  }

  /** Number of hyperedges currently drawn (after time/layer filtering). */
  getVisibleHyperedgeCount(): number {
    return this.visibleHyperedgeCount;
  }

  /** Switch between the flat/overlay view (axis=null) and the stacked-
   * slices view, where each layer (axis="layer") or time-bucket
   * (axis="time") is drawn as its own offset plane, sharing one common
   * node layout so identity is trackable via the faint connecting
   * threads. No-op (clears back to flat) if the graph has no layers for
   * axis="layer" or no temporal domain for axis="time". Replaces the
   * flat edges/colored-edges/hulls/arrows/nodes entirely while active —
   * includes directed arrows and hyperedge hulls on their matching slices.
   * timeBuckets (default 6) only applies to axis="time".
   */
  setStackMode(axis: StackAxis, options?: { timeBuckets?: number; timeSplit?: TimeSplit; layout?: SliceLayout }): void {
    if (axis === "layer" && !this.hasLayers) axis = null;
    if (axis === "time" && this.timeDomain === null) axis = null;
    if (options?.timeBuckets !== undefined) {
      if (!Number.isFinite(options.timeBuckets) || options.timeBuckets < 1) throw new RangeError("timeBuckets must be positive and finite");
      this.stackTimeBuckets = Math.min(64, Math.floor(options.timeBuckets));
    }
    if (options?.timeSplit !== undefined) this.stackTimeSplit = options.timeSplit === "events" ? "events" : "time";
    this.dirty = this.densityDirty = true;
    this.setHovered(null);
    this.stackAxis = axis;
    this.sliceLayout = options?.layout ?? "stack";


    if (axis === null) {
      this.camera.x = this.camera.y = 0;
      this.camera.zoom = 1;
      this.stackSlices = [];
      this.stackEdgeList = [];
      this.stackThreadList = [];
      this.stackNodeSliceCount = 0;
      this.stackPlaneVertexCount = 0;
      this.stackSliceEdgeVertexRanges = [];
      this.onStackChange?.(null);
      return;
    }
    this.rebuildStackSlices();
    const g = this.getSliceGeometry();
    const points = this.stackSlices.flatMap((_, i) => {
      const [x, y] = g.offset(i);
      return [[g.bounds[0] + x, g.bounds[1] + y], [g.bounds[2] + x, g.bounds[3] + y]];
    });
    if (points.length) {
      const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
      const left = Math.min(...xs), right = Math.max(...xs), bottom = Math.min(...ys), top = Math.max(...ys);
      this.camera.x = (left + right) / 2; this.camera.y = (bottom + top) / 2;
      this.camera.zoom = Math.min(1.4 / Math.max(top - bottom, 0.1),
        1.4 * (this.canvas.clientWidth / Math.max(this.canvas.clientHeight, 1)) / Math.max(right - left, 0.1));
    }
  }

  /** Recompute slice membership (which connectors are on which plane) —
   * only needed when the axis/bucket count changes or a new graph loads,
   * not on every layout step (node movement doesn't change which plane a
   * connector is on). Also performs the first position build. */
  private rebuildStackSlices(): void {
    if (this.stackAxis === "layer") {
      // Include an unassigned panel only when it contains connectors.
      const base: StackSlice = {
        label: "(no layer)",
        color: this.opts.edgeColor,
        connectors: [...this.allConnectors, ...this.allHyperedges].filter(c => !this.focusedConnectors || this.focusedConnectors.has(c.id)).filter((c) => c.layer_id === null),
      };
      const named: StackSlice[] = this.layers.map((l) => ({
        label: String(l.key),
        color: layerColor(l.id, this.opts.edgeColor),
        connectors: [...this.allConnectors, ...this.allHyperedges].filter(c => !this.focusedConnectors || this.focusedConnectors.has(c.id)).filter((c) => c.layer_id === l.id),
      }));
      this.stackSlices = base.connectors.length ? [base, ...named] : named;
    } else if (this.stackAxis === "time") {
      const domain = this.timeDomain!;
      const all = [...this.allConnectors, ...this.allHyperedges].filter(c => !this.focusedConnectors || this.focusedConnectors.has(c.id));
      const edges = timeBucketEdges(all, domain, this.stackTimeBuckets, this.stackTimeSplit);
      const n = edges.length - 1;
      const span = domain.max - domain.min;
      this.stackSlices = Array.from({ length: n }, (_, i) => {
        const bucketStart = edges[i], bucketEnd = edges[i + 1];
        return {
          label: domain.unit === "epoch_seconds"
            ? `${formatTime(bucketStart, domain.unit, span)} → ${formatTime(bucketEnd, domain.unit, span)}`
            : `t=[${bucketStart.toFixed(2)}, ${bucketEnd.toFixed(2)}]`,
          color: timeSliceColor(i, n),
          connectors: all.filter((c) => bucketContains(c, bucketStart, bucketEnd, i === n - 1)),
        };
      });
    } else {
      return;
    }

    this.stackNodeSliceCount = this.stackSlices.length;
    this.updateNodeElements();
    this.onStackChange?.(this.stackSlices.map((s) => ({ label: s.label, color: s.color, count: s.connectors.length })));

    // Edge list + static color buffer + per-slice vertex ranges (for
    // drawStackedScene's interleaved per-slice draw calls).
    this.stackEdgeList = [];
    this.stackSliceEdgeVertexRanges = [];
    const colorFloats: number[] = [];
    this.stackSlices.forEach((slice, sliceIndex) => {
      const vertexOffset = this.stackEdgeList.length * 2;
      for (const c of slice.connectors.filter(c => c.endpoints.length === 2)) {
        this.stackEdgeList.push({ sliceIndex, source: c.endpoints[0], target: c.endpoints[1] });
        colorFloats.push(...slice.color, ...slice.color);
      }
      this.stackSliceEdgeVertexRanges.push({
        offset: vertexOffset,
        count: this.stackEdgeList.length * 2 - vertexOffset,
      });
    });
    this.stackedEdgeColorBuffer?.({ data: new Float32Array(colorFloats), usage: "static" } as any);

    // Thread list: one segment per node per consecutive slice pair,
    // connecting its copy on slice i to slice i+1.
    this.stackThreadList = [];
    for (let s = 0; !this.densityMode && this.sliceLayout === "stack" && s < this.stackSlices.length - 1; s++) {
      for (let nodeId = 0; nodeId < this.numNodes; nodeId++) {
        this.stackThreadList.push({ sliceIndex: s, nodeId });
      }
    }

    this.rebuildStackedPositions();
  }

  /** Recompute every stacked-mode buffer's positions from the current
   * base layout + each slice's shear offset. Cheap (no membership
   * recomputation), so this runs on every layout step while stacked mode
   * is active.
   *
   * Each plane's layout is shrunk by stackPlaneScale before the shear is
   * applied — without this, a shear offset small relative to the base
   * layout's own spread just blurs every plane into one smear instead of
   * reading as separated sheets (this is what the first version of this
   * feature looked like; scaling down is what actually fixes it). */
  private getSliceGeometry() {
    return sliceGeometry(this.positions, this.stackNodeSliceCount, this.sliceLayout,
      this.opts.stackShearX, this.opts.stackShearY, this.opts.stackPlaneScale);
  }

  private rebuildStackedPositions(): void {
    if (this.stackAxis === null) return;
    const geometry = this.getSliceGeometry();
    const shiftedX = (n: number, s: number) => geometry.point(n, s)[0];
    const shiftedY = (n: number, s: number) => geometry.point(n, s)[1];

    if (!this.densityMode) {
    const nodeData = new Float32Array(this.stackNodeSliceCount * this.numNodes * 2);
    for (let s = 0; s < this.stackNodeSliceCount; s++) {
      for (let n = 0; n < this.numNodes; n++) {
        const o = (s * this.numNodes + n) * 2;
        nodeData[o] = shiftedX(n, s);
        nodeData[o + 1] = shiftedY(n, s);
      }
    }
    this.stackedNodePositionBuffer?.({ data: nodeData, usage: "dynamic" } as any);

    const edgeData = new Float32Array(this.stackEdgeList.length * 4);
    this.stackEdgeList.forEach((e, i) => {
      edgeData[i * 4] = shiftedX(e.source, e.sliceIndex);
      edgeData[i * 4 + 1] = shiftedY(e.source, e.sliceIndex);
      edgeData[i * 4 + 2] = shiftedX(e.target, e.sliceIndex);
      edgeData[i * 4 + 3] = shiftedY(e.target, e.sliceIndex);
    });
    this.stackedEdgePositionBuffer?.({ data: edgeData, usage: "dynamic" } as any);

    const threadData = new Float32Array(this.stackThreadList.length * 4);
    this.stackThreadList.forEach((t, i) => {
      threadData[i * 4] = shiftedX(t.nodeId, t.sliceIndex);
      threadData[i * 4 + 1] = shiftedY(t.nodeId, t.sliceIndex);
      threadData[i * 4 + 2] = shiftedX(t.nodeId, t.sliceIndex + 1);
      threadData[i * 4 + 3] = shiftedY(t.nodeId, t.sliceIndex + 1);
    });
    this.stackThreadPositionBuffer?.({ data: threadData, usage: "dynamic" } as any);

    }
    // Plane "index card" backing: one quad per slice, sized to the
    // (scaled) layout's bounding box + a margin, shifted by that slice's
    // shear offset. Every plane shares the same shape (just shifted),
    // since they all show the same scaled base layout.
    const [minX, minY, maxX, maxY] = geometry.bounds;

    const planeData = new Float32Array(this.stackNodeSliceCount * 6 * 6); // 2 tris * 3 verts * 6 floats
    const [pr, pg, pb, pa] = this.opts.stackPlaneColor;
    for (let s = 0; s < this.stackNodeSliceCount; s++) {
      const [dx, dy] = geometry.offset(s);
      const corners = [
        [minX + dx, minY + dy],
        [maxX + dx, minY + dy],
        [maxX + dx, maxY + dy],
        [minX + dx, minY + dy],
        [maxX + dx, maxY + dy],
        [minX + dx, maxY + dy],
      ];
      for (let v = 0; v < 6; v++) {
        const o = (s * 6 + v) * 6;
        planeData[o] = corners[v][0];
        planeData[o + 1] = corners[v][1];
        planeData[o + 2] = pr;
        planeData[o + 3] = pg;
        planeData[o + 4] = pb;
        planeData[o + 5] = pa;
      }
    }
    const triangles: number[] = [];
    this.sliceTriangleRanges = [];
    this.slicePolygons = this.stackSlices.map((slice, index) => {
      const polygons: typeof this.slicePolygons[number] = [];
      const point = (id: number) => { const [x, y] = geometry.point(id, index); return {x, y}; };
      for (const c of this.densityMode ? [] : slice.connectors) {
        if (c.endpoints.length > 2) polygons.push({kind: "hull",
          points: memberHull(c.endpoints.map(point), Math.max(0.008, this.opts.hullPadding * geometry.scale)),
          color: hyperedgeFillColor(c.id, c.layer_id)});
        else if (c.directed) polygons.push({kind: "arrow",
          points: arrowPolygon(point(c.endpoints[0]), point(c.endpoints[1]),
            this.opts.arrowLength, this.opts.arrowWidth, this.opts.arrowT), color: this.opts.arrowColor});
      }
      polygons.sort((a, b) => (a.kind === "hull" ? 0 : 1) - (b.kind === "hull" ? 0 : 1));
      const offset = triangles.length / 6;
      for (const polygon of polygons) {
        const vertices = triangulateFan(polygon.points);
        for (let i = 0; i < vertices.length; i += 2) triangles.push(vertices[i], vertices[i + 1], ...polygon.color);
      }
      this.sliceTriangleRanges.push({offset, count: triangles.length / 6 - offset});
      return polygons;
    });
    this.sliceTriangleBuffer?.({data: new Float32Array(triangles), usage: "dynamic"} as any);
    this.stackPlaneVertexCount = this.stackNodeSliceCount * 6;
    this.stackPlaneBuffer?.({ data: planeData, usage: "dynamic" } as any);
  }

  /** Set the visible hyperedge subset and rebuild their hull geometry. */
  private setHyperedgeSet(hyperedges: WireConnector[]): void {
    this.dirty = this.densityDirty = true;
    this.visibleHyperedgeCount = hyperedges.length;
    this.visibleHyperedges = hyperedges;
    this.rebuildHyperedgeGeometry();
  }

  /** Recompute every visible hyperedge's convex hull, inflate it, fan-
   * triangulate it, and re-upload the combined interleaved (position,
   * color) buffer. Must be called whenever positions OR the visible
   * hyperedge set changes — unlike edges/arrows, hull vertex count isn't
   * fixed per-hyperedge, so there's no fixed-size buffer to patch. */
  private rebuildHyperedgeGeometry(): void {
    const floats: number[] = [];
    for (const h of this.visibleHyperedges) {
      const points = h.endpoints.map((nodeId) => ({
        x: this.positions[nodeId * 2] ?? 0,
        y: this.positions[nodeId * 2 + 1] ?? 0,
      }));
      const hull = inflateHull(convexHull(points), this.opts.hullPadding);
      const tris = triangulateFan(hull);
      const color = hyperedgeFillColor(h.id, h.layer_id);
      for (let i = 0; i < tris.length; i += 2) {
        floats.push(tris[i], tris[i + 1], color[0], color[1], color[2], color[3]);
      }
    }
    this.hyperedgeTriangleVertexCount = floats.length / 6;
    this.hyperedgeTriangleBuffer?.({ data: new Float32Array(floats), usage: "dynamic" } as any);
  }

  /** Recompute the (source, target) positions baked into the arrowhead
   * geometry from the current position buffer — must be called whenever
   * positions change. */
  private rebuildArrowSrcDst(): void {
    this.arrowSrcDstData = new Float32Array(this.directedEdges.length * 3 * 4);
    for (let i = 0; i < this.directedEdges.length; i++) {
      const { source, target } = this.directedEdges[i];
      const sx = this.positions[source * 2] ?? 0;
      const sy = this.positions[source * 2 + 1] ?? 0;
      const tx = this.positions[target * 2] ?? 0;
      const ty = this.positions[target * 2 + 1] ?? 0;
      for (let corner = 0; corner < 3; corner++) {
        const o = (i * 3 + corner) * 4;
        this.arrowSrcDstData[o] = sx;
        this.arrowSrcDstData[o + 1] = sy;
        this.arrowSrcDstData[o + 2] = tx;
        this.arrowSrcDstData[o + 3] = ty;
      }
    }
    this.arrowSrcDstBuffer?.({ data: this.arrowSrcDstData, usage: "dynamic" } as any);
  }

  /** Apply a streamed layout update: re-upload positions to the GPU. */
  applyLayoutStep(msg: LayoutStepMessage): void {
    this.dirty = this.densityDirty = true;
    this.markLodStale();
    this.positions = decodePositions(msg);
    this.positionBuffer?.subdata(this.positions);
    this.rebuildArrowSrcDst();
    // Thin edges reuse positionBuffer directly via GPU-side indexing — no
    // per-edge CPU rebuild needed (that's the whole point of the
    // threshold; see the `edges` field's comment).
    if (!this.useThinEdges) this.rebuildEdgePositions();
    if (this.visibleHyperedges.length > 0) this.rebuildHyperedgeGeometry();
    if (this.stackAxis !== null) this.rebuildStackedPositions();
  }

  private loop = (): void => {
    const camera = `${this.camera.x},${this.camera.y},${this.camera.zoom},${this.canvas.width},${this.canvas.height}`;
    if (camera !== this.lastCamera) { this.lastCamera = camera; this.dirty = this.pointerDirty = true; this.markLodStale(); }
    if (this.lodPending && performance.now() - this.lodChangedAt > 160) this.applyLod();
    if (this.dirty) {
      this.resizePickFboIfNeeded();
      if (this.densityMode && this.densityDirty) this.rebuildDensity();
      this.regl.clear({color:this.opts.backgroundColor,depth:1});
      if (this.stackAxis !== null) {
        if (!this.densityMode) this.drawStackThreads();
        this.drawStackedScene();
      } else if (this.densityMode) this.drawDensity(0);
      else { this.drawHyperedges(); this.drawEdges(); this.drawArrows(); this.drawNodes(); }
    }
    if (this.pointerDirty || this.dirty) {
      if (this.densityMode) this.setHovered(null);
      else if (this.stackAxis !== null) this.updateSliceHover();
      else this.updateHover();
      this.drawLabels();
    }
    this.dirty = this.pointerDirty = false;
    this.rafHandle = requestAnimationFrame(this.loop);
  };

  private resizePickFboIfNeeded(): void {
    const w = Math.max(1, this.canvas.width);
    const h = Math.max(1, this.canvas.height);
    if (this.pickFbo && (this.pickFboSize.width !== w || this.pickFboSize.height !== h)) {
      this.pickFbo.resize(w, h);
      this.pickFboSize = { width: w, height: h };
    }
  }

  /** Redraw the node-label Canvas2D overlay, if one exists (nodeLabelSpec
   * was set). Cheap enough to just redo every frame — a handful of
   * fillText calls, no different from the pattern deck.gl/MapLibre use
   * for label overlays. */
  private drawLabels(): void {
    if (!this.labelCanvas || !this.labelCtx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    const pixelW = Math.max(1, Math.round(w * dpr));
    const pixelH = Math.max(1, Math.round(h * dpr));
    if (this.labelCanvas.width !== pixelW || this.labelCanvas.height !== pixelH) {
      this.labelCanvas.width = pixelW;
      this.labelCanvas.height = pixelH;
    }
    const rect = this.canvas.getBoundingClientRect();
    Object.assign(this.labelCanvas.style, {left:`${rect.left}px`,top:`${rect.top}px`,width:`${w}px`,height:`${h}px`});
    const ctx = this.labelCtx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = "rgba(30, 30, 30, 0.9)";
    ctx.textBaseline = "middle";
    if (this.densityMode) {
      ctx.fillText(this.densityCaption(), 24, 20); // top edge: the timeline bar covers the bottom
      if (this.pointerPx) {
        const hit = this.densityPointAt(this.pointerPx.x / dpr, this.pointerPx.y / dpr);
        if (hit >= 0) {
          const key = this.densityGroupKeys[hit];
          const text = `${key !== undefined ? `${this.groupBy}: ${key} · ` : ""}${this.densityCounts[hit].toLocaleString()} node${this.densityCounts[hit] === 1 ? "" : "s"}${key !== undefined ? " · click to expand" : " · click to zoom in"}`;
          const px = this.pointerPx.x / dpr + 14, py = this.pointerPx.y / dpr - 14;
          ctx.font = "600 11px system-ui, sans-serif";
          const tw = ctx.measureText(text).width;
          ctx.fillStyle = "rgba(255,255,255,0.95)"; ctx.fillRect(px - 6, py - 10, tw + 12, 20);
          ctx.strokeStyle = "#dce3ea"; ctx.lineWidth = 1; ctx.strokeRect(px - 6, py - 10, tw + 12, 20);
          ctx.fillStyle = "rgba(24, 62, 81, 0.95)"; ctx.fillText(text, px, py);
          ctx.font = "11px system-ui, sans-serif"; ctx.fillStyle = "rgba(30, 30, 30, 0.9)";
        }
      }
    } else if (this.regionNodes) {
      ctx.fillText(`Zoomed detail · ${this.visibleNodeIds.length.toLocaleString()} of ${(this.baseNodes()?.size ?? this.numNodes).toLocaleString()} nodes in view · zoom out for the overview`, 24, 20);
    }
    if (this.stackAxis !== null) {
      const g = this.getSliceGeometry();
      ctx.font = "600 12px system-ui, sans-serif";
      this.stackSlices.forEach((slice, i) => {
        const [dx, dy] = g.offset(i);
        const [x, y] = this.worldToScreen(g.bounds[0] + dx, g.bounds[3] + dy, w, h);
        const heading = panelHeadingLines(i, slice.label);
        heading.forEach((line, k) => ctx.fillText(line, x, y - 18 - (heading.length - 1 - k) * 14));
        for (const n of this.densityMode ? [] : this.visibleNodeIds) {
          if (this.focusedNodes && !this.focusedNodes.has(n)) continue;
          const hovered = n === this.hoveredNodeId || n === this.selectedNode;
          const text = this.nodeLabelText[n] ?? (hovered ? this.nodeKeys[n] : null);
          if (!text && !hovered) continue;
          const [nx, ny] = this.worldToScreen(...g.point(n, i), w, h);
          if (hovered) {
            ctx.strokeStyle = "#183e51"; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(nx, ny, this.opts.nodeRadiusPx + 3, 0, Math.PI * 2); ctx.stroke();
          }
          if (text) ctx.fillText(text, nx + this.opts.nodeRadiusPx + 6, ny);
        }
      });
      return;
    }
    if (this.densityMode) return;
    for (const n of this.visibleNodeIds) {
      const text = this.nodeLabelText[n] ?? ((n === this.selectedNode || n === this.hoveredNodeId) ? this.nodeKeys[n] : null);
      if (!text) continue;
      const [x, y] = this.worldToScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0, w, h);
      ctx.fillText(text, x + this.opts.nodeRadiusPx + 3, y);
    }
    ctx.strokeStyle = "#183e51"; ctx.lineWidth = 2; ctx.fillStyle = "rgba(24, 62, 81, 0.95)";
    const shown = this.viewNodes();
    for (const n of this.highlighted) {
      if (shown && !shown.has(n)) continue;
      const [x, y] = this.worldToScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0, w, h);
      ctx.beginPath(); ctx.arc(x, y, this.opts.nodeRadiusPx + 4, 0, Math.PI * 2); ctx.stroke();
      ctx.fillText(this.nodeKeys[n], x + this.opts.nodeRadiusPx + 8, y - 8);
    }
  }

  /** World-space point to on-screen pixel, applying the same camera
   * transform (pan/zoom/aspect) the WebGL shaders use — see Camera.matrix
   * for the column-major mat3 layout this replicates in JS. Used by
   * exportSVG and the label overlay (the WebGL draws themselves never go
   * through JS-side matrix math). */
  private worldToScreen(x: number, y: number, w: number, h: number): [number, number] {
    const m = this.camera.matrix(w / h);
    const clipX = m[0] * x + m[3] * y + m[6];
    const clipY = m[1] * x + m[4] * y + m[7];
    return [((clipX + 1) / 2) * w, ((1 - clipY) / 2) * h];
  }

  /** Serialize the current view to an SVG string — real vector geometry
   * (circle/line/polygon elements) built from the same node/edge/hull/
   * arrow state the WebGL renderer draws from, not a rasterized
   * screenshot. Reflects whichever view is active (flat overlay or
   * stacked-slices) and the current pan/zoom framing. */
  exportSVG(): string {
    const w = this.canvas.clientWidth || 800;
    const h = this.canvas.clientHeight || 600;
    const toScreen = (x: number, y: number) => this.worldToScreen(x, y, w, h);
    const rgba = (c: [number, number, number, number]) =>
      `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${c[3]})`;
    const pointsAttr = (pts: [number, number][]) =>
      pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

    const parts: string[] = [
      `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`,
      `<rect x="0" y="0" width="${w}" height="${h}" fill="${rgba(this.opts.backgroundColor)}" />`,
    ];

    if (this.densityMode) {
      if (this.densityDirty) this.rebuildDensity();
      for(let i=0;i<this.densityEdges.length;i+=6) {
        const [x1,y1]=toScreen(this.densityEdges[i],this.densityEdges[i+1]);
        const [x2,y2]=toScreen(this.densityEdges[i+3],this.densityEdges[i+4]);
        parts.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#6b8094" stroke-opacity="${this.densityEdges[i+2].toFixed(2)}"/>`);
      }
      for(let i=0;i<this.densityNodes.length;i+=7) {
        const [x,y]=toScreen(this.densityNodes[i],this.densityNodes[i+1]);
        parts.push(`<circle cx="${x}" cy="${y}" r="${this.densityNodes[i+2]/2}" fill="${rgba([this.densityNodes[i+3],this.densityNodes[i+4],this.densityNodes[i+5],this.densityNodes[i+6]])}"/>`);
      }
      if(this.stackAxis !== null) {
        const g=this.getSliceGeometry();
        this.stackSlices.forEach((slice,i)=>{
          const [dx,dy]=g.offset(i), [x,y]=toScreen(g.bounds[0]+dx,g.bounds[3]+dy);
          const lines=panelHeadingLines(i,slice.label);
          lines.forEach((line,k)=>parts.push(`<text x="${x}" y="${y-18-(lines.length-1-k)*14}" font-size="12">${line.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]!))}</text>`));
        });
      }
      parts.push(`<text x="24" y="${h-24}" font-size="12">${this.densityCaption().replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]!))}</text></svg>`);
      return parts.join("\n");
    }
    if (this.stackAxis !== null) {
      // Stacked-slices view: mirror rebuildStackedPositions' math and
      // drawStackedScene's back-to-front per-slice draw order.
      const geometry = this.getSliceGeometry();
      const sx = (n: number, s: number) => geometry.point(n, s)[0];
      const sy = (n: number, s: number) => geometry.point(n, s)[1];

      parts.push(`<g stroke="rgba(102,102,115,${this.opts.stackThreadAlpha})" stroke-width="1">`);
      for (const t of this.stackThreadList) {
        const [x1, y1] = toScreen(sx(t.nodeId, t.sliceIndex), sy(t.nodeId, t.sliceIndex));
        const [x2, y2] = toScreen(sx(t.nodeId, t.sliceIndex + 1), sy(t.nodeId, t.sliceIndex + 1));
        parts.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" />`);
      }
      parts.push(`</g>`);

      const [minX, minY, maxX, maxY] = geometry.bounds;

      for (let s = 0; s < this.stackNodeSliceCount; s++) {
        const [dx, dy] = geometry.offset(s);
        const corners: [number, number][] = [
          toScreen(minX + dx, minY + dy),
          toScreen(maxX + dx, minY + dy),
          toScreen(maxX + dx, maxY + dy),
          toScreen(minX + dx, maxY + dy),
        ];
        parts.push(`<polygon points="${pointsAttr(corners)}" fill="${rgba(this.opts.stackPlaneColor)}" />`);

        for (const polygon of this.slicePolygons[s] ?? []) {
          parts.push(`<polygon data-kind="${polygon.kind}" points="${pointsAttr(polygon.points.map(p => toScreen(p.x, p.y)))}" fill="${rgba(polygon.color)}" />`);
        }
        const [labelX, labelY] = toScreen(minX + dx, maxY + dy);
        const headingLines = panelHeadingLines(s, this.stackSlices[s].label);
        headingLines.forEach((line, k) => {
          const escaped = line.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c]!));
          parts.push(`<text x="${labelX}" y="${labelY - 18 - (headingLines.length - 1 - k) * 14}" font-family="system-ui, sans-serif" font-size="12" font-weight="600" fill="#26384d">${escaped}</text>`);
        });
        const range = this.stackSliceEdgeVertexRanges[s];
        if (range) {
          const startEdge = range.offset / 2;
          const endEdge = startEdge + range.count / 2;
          for (let i = startEdge; i < endEdge; i++) {
            const e = this.stackEdgeList[i];
            const [x1, y1] = toScreen(sx(e.source, e.sliceIndex), sy(e.source, e.sliceIndex));
            const [x2, y2] = toScreen(sx(e.target, e.sliceIndex), sy(e.target, e.sliceIndex));
            const color = this.stackSlices[s]?.color ?? this.opts.edgeColor;
            parts.push(
              `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${rgba(color)}" stroke-width="${this.opts.edgeWidthPx}" />`
            );
          }
        }

        for (const n of this.visibleNodeIds) {
          const [x, y] = toScreen(sx(n, s), sy(n, s));
          const text = this.nodeLabelText[n] ?? (n === this.selectedNode ? this.nodeKeys[n] : null);
          if(text) {
            const escaped=text.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]!));
            parts.push(`<text x="${x+this.opts.nodeRadiusPx+6}" y="${y}" font-size="12">${escaped}</text>`);
          }
          parts.push(
            `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${this.opts.nodeRadiusPx * 0.8}" fill="${rgba([this.opts.nodeColor[0], this.opts.nodeColor[1], this.opts.nodeColor[2], 0.9])}" />`
          );
        }
      }
    } else {
      // Flat/overlay view — same draw order as loop(): hulls, edges, arrows, nodes.
      for (const hEdge of this.visibleHyperedges) {
        const points = hEdge.endpoints.map((nodeId) => ({
          x: this.positions[nodeId * 2] ?? 0,
          y: this.positions[nodeId * 2 + 1] ?? 0,
        }));
        const hull = inflateHull(convexHull(points), this.opts.hullPadding);
        if (hull.length < 3) continue;
        const color = hyperedgeFillColor(hEdge.id, hEdge.layer_id);
        const pts = hull.map((p): [number, number] => toScreen(p.x, p.y));
        parts.push(`<polygon points="${pointsAttr(pts)}" fill="${rgba(color)}" />`);
      }

      if (this.useThinEdges) {
        // Matches edgeDrawThin: one uniform color, no per-edge lookup.
        const edgeColorCss = rgba(this.opts.edgeColor);
        for (let i = 0; i < this.thinEdgePairs.length; i += 2) {
          const u = this.thinEdgePairs[i];
          const v = this.thinEdgePairs[i + 1];
          const [x1, y1] = toScreen(this.positions[u * 2] ?? 0, this.positions[u * 2 + 1] ?? 0);
          const [x2, y2] = toScreen(this.positions[v * 2] ?? 0, this.positions[v * 2 + 1] ?? 0);
          parts.push(
            `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${edgeColorCss}" stroke-width="1" />`
          );
        }
      } else {
        for (const e of this.edges) {
          const [x1, y1] = toScreen(this.positions[e.source * 2] ?? 0, this.positions[e.source * 2 + 1] ?? 0);
          const [x2, y2] = toScreen(this.positions[e.target * 2] ?? 0, this.positions[e.target * 2 + 1] ?? 0);
          parts.push(
            `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${rgba(e.color)}" stroke-width="${this.opts.edgeWidthPx}" />`
          );
        }
      }

      // Arrowheads — same triangle construction as arrowDraw's vertex
      // shader (corner.y in {+-0.5} is why the perpendicular offset below
      // is arrowWidth * 0.5, not arrowWidth).
      for (const d of this.directedEdges) {
        const psx = this.positions[d.source * 2] ?? 0;
        const psy = this.positions[d.source * 2 + 1] ?? 0;
        const ptx = this.positions[d.target * 2] ?? 0;
        const pty = this.positions[d.target * 2 + 1] ?? 0;
        const dx = ptx - psx;
        const dy = pty - psy;
        const len = Math.hypot(dx, dy) || 1;
        const dirX = dx / len;
        const dirY = dy / len;
        const perpX = -dirY;
        const perpY = dirX;
        const tipX = psx + dx * this.opts.arrowT;
        const tipY = psy + dy * this.opts.arrowT;
        const baseX = tipX - dirX * this.opts.arrowLength;
        const baseY = tipY - dirY * this.opts.arrowLength;
        const half = this.opts.arrowWidth * 0.5;
        const pts: [number, number][] = [
          toScreen(tipX, tipY),
          toScreen(baseX + perpX * half, baseY + perpY * half),
          toScreen(baseX - perpX * half, baseY - perpY * half),
        ];
        parts.push(`<polygon points="${pointsAttr(pts)}" fill="${rgba(this.opts.arrowColor)}" />`);
      }

      for (const n of this.visibleNodeIds) {
        const [x, y] = toScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0);
        parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${this.opts.nodeRadiusPx}" fill="${rgba(this.opts.nodeColor)}" />`);
      }
    }

    parts.push(`</svg>`);
    return parts.join("\n");
  }

  dispose(): void {
    if (this.rafHandle !== null) cancelAnimationFrame(this.rafHandle);
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerleave", this.onPointerLeave);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.labelCanvas?.remove();
    this.camera.dispose();
    this.regl.destroy();
  }
}
