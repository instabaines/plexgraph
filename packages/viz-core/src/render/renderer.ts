import { GraphIndex, summarizeAttributes, type AttributeSummary, type NodeFilter } from "../interaction/graph-index";
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
import { mergeStyle, StyleManager, type ResolvedLabel, type ResolvedStyle, type StyleBase } from "../style/manager";
import type { ColorLegend, StyleEnv } from "../style/engine";
import type { NodeShape, StyleSpec } from "../style/spec";
import { NODE_SHAPES } from "../style/spec";
import { parseColor, rgbaToCss } from "../style/colors";
import {
  bucketContains, computeTimeDomain, connectorActiveAt, formatTime, SECONDS_PER_DAY, timeBucketEdges, timeSliceColor,
  type TimeDomain, type TimeFilterOptions, type TimeMode, type TimeSplit,
} from "./time";
export {
  bucketContains, computeTimeDomain, connectorActiveAt, formatTime, SECONDS_PER_DAY, timeBucketEdges, timeSliceColor,
  type TimeDomain, type TimeFilterOptions, type TimeMode, type TimeSplit,
} from "./time";
import type { GraphMessage, LayoutStepMessage, WireConnector, WireLayer, WireNode } from "../ir/types";
import { decodePositions } from "../ir/types";
import { convexHull, inflateHull, triangulateFan } from "./hull";

/** Lines for a panel heading. A date range ("a → b") is stacked so neighbouring panel headings cannot overlap. */
export function panelHeadingLines(index: number, label: string): string[] {
  const [from, to] = label.split(" → ");
  return to === undefined ? [`${index + 1} · ${label}`] : [`${index + 1} · ${from}`, `→ ${to}`];
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
  /** Initial style (see StyleSpec); the individual options above still work and this is merged over them. */
  style?: StyleSpec;
  /** Called after every style change (including ones pushed from Python), so a UI can refresh its controls. */
  onStyleChange?: () => void;
  /** Called when the background color changes (including at load), so a page can match it. */
  onBackgroundColor?: (color: [number, number, number, number]) => void;
  /** Called whenever the node or edge color scale changes: a categorical legend, a continuous colormap range, or null. */
  onColorLegend?: (target: "node" | "edge", legend: ColorLegend | null) => void;
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
    | "style"
    | "onColorLegend"
    | "onBackgroundColor"
    | "onStyleChange"
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

/** The legacy per-option colouring (nodeColorBy, edgeColorBy) expressed as a style. */
function initialStyle(options: RendererOptions): StyleSpec {
  const spec: StyleSpec = {};
  if (options.nodeColorBy) spec.node = { color: { kind: "attribute", attribute: options.nodeColorBy } };
  if (options.edgeColorBy) spec.edge = { color: { kind: "attribute", attribute: options.edgeColorBy } };
  return spec;
}

/** One node mark as SVG, matching the shapes the node shader draws (see NODE_SHAPES). */
function svgNodeShape(shape: number, x: number, y: number, r: number, fill: string, extra: string): string {
  const f = (v: number) => v.toFixed(1);
  const poly = (pts: [number, number][]) => `<polygon points="${pts.map(([px, py]) => `${f(x + px * r)},${f(y + py * r)}`).join(" ")}" fill="${fill}"${extra} />`;
  switch (NODE_SHAPES[shape] ?? "circle") {
    case "square": return `<rect x="${f(x - 0.86 * r)}" y="${f(y - 0.86 * r)}" width="${f(1.72 * r)}" height="${f(1.72 * r)}" fill="${fill}"${extra} />`;
    case "triangle": return poly([[0, -1], [-0.95, 0.75], [0.95, 0.75]]);
    case "diamond": return poly([[0, -1], [1, 0], [0, 1], [-1, 0]]);
    case "cross": {
      const w = 0.34;
      return poly([[-w, -1], [w, -1], [w, -w], [1, -w], [1, w], [w, w], [w, 1], [-w, 1], [-w, w], [-1, w], [-1, -w], [-w, -w]]);
    }
    default: return `<circle cx="${f(x)}" cy="${f(y)}" r="${f(r)}" fill="${fill}"${extra} />`;
  }
}

/** Whether a style update can change what the edge vertex buffers hold (opacity and arrow size are uniforms). */
function changesEdgeBuffers(update: StyleSpec): boolean {
  const edge = update.edge;
  if (edge === undefined) return false;
  if (edge === null) return true;
  return "color" in edge || "width" in edge || "curvature" in edge;
}

const DETAIL_LIMIT = 5000;
const CURVE_SEGMENTS = 12;
const MAX_ALL_LABELS = 2500; // labelling every node beyond this many is unreadable and slow, so only hover labels remain
const CURVED_EDGE_LIMIT = 30_000; // above this many drawn edges, curvature is ignored to keep frames cheap

/** A run of thin (one-pixel) edges sharing one color, drawn with one call. */
interface ThinEdgeGroup { color: [number, number, number, number]; offset: number; count: number }
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
  private edgeColorOverrides: Record<string, [number, number, number, number]>;
  private nodeLabelSpec: NodeLabelSpec;

  private index = new GraphIndex([], []);
  private focusedNodes: Set<number> | null = null;
  private nodeFilterNodes: Set<number> | null = null;
  private highlighted: number[] = [];
  private nodeSizeBuffer: ReturnType<Regl["buffer"]> | null = null;
  private nodeShapeBuffer: ReturnType<Regl["buffer"]> | null = null;
  private stackedNodeColorBuffer: ReturnType<Regl["buffer"]> | null = null;
  private stackedNodeSizeBuffer: ReturnType<Regl["buffer"]> | null = null;
  private nodeSizes: Float32Array = new Float32Array(0); // diameter in pixels, per node
  private nodeOpacity = 1;
  private nodeOutline: { color: [number, number, number, number]; width: number } = { color: [1, 1, 1, 1], width: 0 };
  private nodeSizeBy: string | null = null;
  private styleManager: StyleManager;
  private optionStyle: StyleSpec;
  private resolved: ResolvedStyle | null = null;
  private backgroundColor: [number, number, number, number];
  private labelStyle: ResolvedLabel = { mode: "hover", fontSize: 11, color: [0.12, 0.12, 0.12, 0.9], halo: false, attribute: null };
  private nodeActivityCache: { first: Float64Array; last: Float64Array } | null = null;
  private graphVersion = 0;
  private edgeStyleColors: Float32Array = new Float32Array(0);   // per connector in allConnectors order
  private edgeStyleWidths: Float32Array = new Float32Array(0);
  private edgeIndexById = new Map<number, number>();
  private edgeOpacity = 1;
  private curvature = 0;
  private arrowScale = 1;
  private dash: number[] | null = null;
  private onNodeClick: ((nodeId: number, event: { shiftKey: boolean }) => void) | null;
  private onGraphLoaded: (() => void) | null;
  private onGroupClick: ((attribute: string, value: string) => void) | null;
  private onColorLegend: ((target: "node" | "edge", legend: ColorLegend | null) => void) | null;
  private onBackgroundColor: ((color: [number, number, number, number]) => void) | null;
  private onStyleChange: (() => void) | null;
  private focusedConnectors: Set<number> | null = null;
  private selectedNode: number | null = null;
  private visibleNodeIds: number[] = [];
  private nodeElements: ReturnType<Regl["elements"]> | null = null;
  private sliceNodeElements: ReturnType<Regl["elements"]> | null = null;
  // dirty/pointerDirty are accessors, not plain fields, so that ANY code setting them to true (existing call sites
  // and future ones alike) automatically wakes the frame loop -- see wake() and the render-on-demand note on loop().
  private _dirty = true;
  private get dirty(): boolean { return this._dirty; }
  private set dirty(value: boolean) { this._dirty = value; if (value) this.wake(); }
  private _pointerDirty = true;
  private get pointerDirty(): boolean { return this._pointerDirty; }
  private set pointerDirty(value: boolean) { this._pointerDirty = value; if (value) this.wake(); }
  private lastCamera = "";
  private densityDirty = true;
  private densityNodes = new Float32Array(0);
  private nodeColorValues: Float32Array = new Float32Array(0);
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
  // A camera move alone must not force the expensive GPU-readback hover pick (updateHover) every single
  // frame of a pan/zoom -- confirmed by profiling to cost several ms per call from the readback alone,
  // dwarfing the draw itself. Real pointer movement still updates hover immediately via pointerDirty;
  // this only debounces the camera-triggered re-check. updateSliceHover (stacked views) is pure CPU
  // math and cheap, so it keeps running on every dirty frame unthrottled.
  private hoverStale = false;
  private hoverChangedAt = 0;
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

  /** The level of detail may be out of date. A camera move restarts the wait for quiet (so we do not re-evaluate mid
   * drag); streaming layout steps only start it, or a stream that never pauses would keep the view stuck in the
   * overview however far you zoom. */
  /** Debounced like markLodStale: a camera move alone marks hover possibly-stale rather than forcing an
   * immediate re-pick, so continuous panning/zooming does not pay a GPU readback every frame. */
  private markHoverStale(): void {
    if (!this.hoverStale) this.hoverChangedAt = performance.now();
    this.hoverStale = true;
  }

  private markLodStale(restartWait = true): void {
    if (!this.needsLod()) return;
    if (!this.lodPending || restartWait) this.lodChangedAt = performance.now();
    this.lodPending = true;
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
  private edges: { source: number; target: number; color: [number, number, number, number]; width: number }[] = [];
  private edgeSegments = 1; // quad-strip segments per edge: 1 when straight, CURVE_SEGMENTS when curved
  private edgesAreCurved = false;
  private thinEdgeGroups: ThinEdgeGroup[] = [];
  private thinEdgeColors: Float32Array = new Float32Array(0); // per thin edge, parallel to thinEdgePairs
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
  private edgeWidthBuffer: ReturnType<Regl["buffer"]> | null = null; // static, per vertex, rebuilt on setEdgeSet
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
  private resizeObserver: ResizeObserver | null = null;
  private onWindowResize = (): void => this.wake();
  /** Re-arms the frame loop if it has gone idle (render-on-demand: the loop does not run forever, only while
   * dirty/pointerDirty/lodPending/hoverStale gives it a reason to). Safe to call whether or not it is running. */
  private wake(): void {
    if (this.rafHandle === null) this.rafHandle = requestAnimationFrame(this.loop);
  }

  private lastCanvasSize = "";

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
    this.onColorLegend = options.onColorLegend ?? null;
    this.onBackgroundColor = options.onBackgroundColor ?? null;
    this.onStyleChange = options.onStyleChange ?? null;
    this.optionStyle = mergeStyle(initialStyle(options), options.style ?? {});
    this.styleManager = new StyleManager(this.optionStyle);
    this.backgroundColor = [...this.opts.backgroundColor];
    this.onTimeDomain = options.onTimeDomain ?? null;
    this.onLayers = options.onLayers ?? null;
    this.onStackChange = options.onStackChange ?? null;
    this.onNodeColorLegend = options.onNodeColorLegend ?? null;
    this.onEdgeColorLegend = options.onEdgeColorLegend ?? null;
    this.nodeColorOverrides = options.nodeColorOverrides ?? {};
    this.edgeColorOverrides = options.edgeColorOverrides ?? {};
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
    this.camera = new Camera(canvas, () => this.wake());
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
    // loop() only notices a canvas resize (window resize, a notebook widget's height changing, ...) by polling
    // canvas.width/height at its own top; with render-on-demand that no longer runs while idle, so without this the
    // canvas could sit at its old drawing-buffer size (and the camera at its old aspect ratio) after a resize, until
    // something unrelated happened to wake the loop. This is what makes a resize itself wake it, independent of
    // whatever caller resized the element (the app's own resizeCanvas(), a test, or anything else).
    // Both listeners, not just one: ResizeObserver fires when the canvas's own CSS box size changes (a container
    // resizing without the window doing so, e.g. a sidebar toggling); window "resize" additionally covers a device
    // pixel ratio change alone (dragging the window to a differently-scaled display), which typically does not
    // change the canvas's CSS box size at all and so would not trigger the ResizeObserver by itself.
    this.resizeObserver = new ResizeObserver(() => this.wake());
    this.resizeObserver.observe(canvas);
    window.addEventListener("resize", this.onWindowResize);

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
    this.nodeShapeBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.stackedNodeColorBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.stackedNodeSizeBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.idBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.arrowCornerBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.arrowSrcDstBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.edgeSrcDstBuffer = regl.buffer({ data: new Float32Array(0), usage: "dynamic" });
    this.edgeAlongSideBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.edgeColorBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
    this.edgeWidthBuffer = regl.buffer({ data: new Float32Array(0), usage: "static" });
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
        attribute float size;   // diameter in pixels, per node
        attribute float shape;  // index into NODE_SHAPES
        uniform mat3 view;
        varying vec4 vColor;
        varying float vShape;
        varying float vSize;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          gl_PointSize = size;
          vColor = color;
          vShape = shape;
          vSize = size;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        varying float vShape;
        varying float vSize;
        uniform float opacity;
        uniform vec4 outlineColor;
        uniform float outlinePx;
        // Distance from the centre in units where 1.0 is the shape's edge (so inside is <= 1.0).
        float shapeDistance(vec2 c, float s) {
          vec2 p = vec2(c.x, -c.y) * 2.0;  // gl_PointCoord's y points down
          if (s < 0.5) return length(p);                                   // circle
          if (s < 1.5) return max(abs(p.x), abs(p.y)) / 0.86;              // square
          if (s < 2.5) {                                                   // triangle, apex up
            if (p.y > 1.0) return 2.0;
            return max(-p.y / 0.75, abs(p.x) / ((1.0 - p.y) * 0.5429));
          }
          if (s < 3.5) return abs(p.x) + abs(p.y);                         // diamond
          return min(max(abs(p.x) / 0.34, abs(p.y)), max(abs(p.y) / 0.34, abs(p.x)));  // cross
        }
        void main() {
          float d = shapeDistance(gl_PointCoord - vec2(0.5), vShape);
          if (d > 1.0) discard;
          vec4 col = vColor;
          if (outlinePx > 0.0 && d > 1.0 - outlinePx / (vSize * 0.5)) col = outlineColor;
          gl_FragColor = vec4(col.rgb, col.a * opacity);
        }
      `,
      attributes: {
        position: () => this.positionBuffer!,
        color: () => this.nodeColorBuffer!,
        size: () => this.nodeSizeBuffer!,
        shape: () => this.nodeShapeBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        opacity: () => this.nodeOpacity,
        outlineColor: () => this.nodeOutline.color,
        outlinePx: () => this.nodeOutline.width,
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
        // x: position along the edge, 0 at src to 1 at dst. y: -1 or +1, which side of the line the vertex is on.
        attribute vec2 alongSide;
        attribute vec4 color;
        attribute float width;
        uniform mat3 view;
        uniform vec2 viewportSize;
        uniform float curvature;
        varying vec4 vColor;
        // Distance in screen pixels from the edge's start, used by the fragment shader to dash the line. It is
        // t times the *straight-line* pixel distance between the endpoints, not the true length along a curved
        // edge -- a cheap approximation (dashes stretch slightly on curved edges) that needs no extra tessellation.
        varying float vArcPx;
        void main() {
          vec2 a = srcDst.xy;
          vec2 b = srcDst.zw;
          vec2 d = b - a;
          float len = length(d);
          vec2 perp = len > 1e-9 ? vec2(-d.y, d.x) / len : vec2(0.0);
          // Quadratic Bezier bent to one side by curvature * length; curvature 0 is the straight segment.
          vec2 c = 0.5 * (a + b) + perp * curvature * len;
          float t = alongSide.x;
          float u = 1.0 - t;
          vec2 point = u * u * a + 2.0 * u * t * c + t * t * b;
          vec2 tangent = 2.0 * u * (c - a) + 2.0 * t * (b - c);
          vec3 clip = view * vec3(point, 1.0);
          vec2 pixelDir = (view * vec3(tangent, 0.0)).xy * viewportSize;
          float pixelLen = length(pixelDir);
          pixelDir = pixelLen > 0.0001 ? pixelDir / pixelLen : vec2(1.0, 0.0);
          vec2 pixelPerp = vec2(-pixelDir.y, pixelDir.x);
          vec2 ndcPerpUnit = pixelPerp * (2.0 / viewportSize);
          gl_Position = vec4(clip.xy + ndcPerpUnit * alongSide.y * (width * 0.5), 0, 1);
          vColor = color;
          vec2 clipA = (view * vec3(a, 1.0)).xy;
          vec2 clipB = (view * vec3(b, 1.0)).xy;
          vArcPx = t * length((clipB - clipA) * 0.5 * viewportSize);
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        varying float vArcPx;
        uniform float opacity;
        // [on1, off1, on2, off2] in pixels; all zero means solid (no dashing).
        uniform vec4 dashPattern;
        void main() {
          float total = dashPattern.x + dashPattern.y + dashPattern.z + dashPattern.w;
          if (total > 0.0001) {
            float m = mod(vArcPx, total);
            bool on = m < dashPattern.x || (m >= dashPattern.x + dashPattern.y && m < dashPattern.x + dashPattern.y + dashPattern.z);
            if (!on) discard;
          }
          gl_FragColor = vec4(vColor.rgb, vColor.a * opacity);
        }
      `,
      attributes: {
        srcDst: () => this.edgeSrcDstBuffer!,
        alongSide: () => this.edgeAlongSideBuffer!,
        color: () => this.edgeColorBuffer!,
        width: () => this.edgeWidthBuffer!,
      },
      elements: () => this.edgeIndexBuffer!,
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        viewportSize: (_ctx: any) => [this.canvas.clientWidth, this.canvas.clientHeight],
        curvature: () => (this.edgesAreCurved ? this.curvature : 0),
        opacity: () => this.edgeOpacity,
        dashPattern: () => (this.dash ?? [0, 0, 0, 0]),
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
    // MAX_EXACT_REPULSION_NODES in plexgraph_core.algorithms.layout).
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
        uniform float opacity;
        void main() {
          gl_FragColor = vec4(color.rgb, color.a * opacity);
        }
      `,
      attributes: {
        position: () => this.positionBuffer!,
      },
      elements: () => this.edgeThinIndexBuffer!,
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        color: regl.prop<ThinEdgeGroup, "color">("color"),
        opacity: () => this.edgeOpacity,
      },
      offset: regl.prop<ThinEdgeGroup, "offset">("offset"),
      count: regl.prop<ThinEdgeGroup, "count">("count"),
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
        attribute vec4 color;
        attribute float size;
        uniform mat3 view;
        varying vec4 vColor;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          gl_PointSize = size;
          vColor = color;
        }
      `,
      frag: `
        precision mediump float;
        varying vec4 vColor;
        uniform float opacity;
        void main() {
          vec2 c = gl_PointCoord - vec2(0.5);
          if (dot(c, c) > 0.25) discard;
          gl_FragColor = vec4(vColor.rgb, vColor.a * opacity);
        }
      `,
      attributes: {
        position: () => this.stackedNodePositionBuffer!,
        color: () => this.stackedNodeColorBuffer!,
        size: () => this.stackedNodeSizeBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        opacity: () => this.nodeOpacity,
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
        uniform float opacity;
        void main() {
          gl_FragColor = vec4(color.rgb, color.a * opacity);
        }
      `,
      attributes: {
        corner: () => this.arrowCornerBuffer!,
        srcDst: () => this.arrowSrcDstBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
        arrowLength: () => this.opts.arrowLength * this.arrowScale,
        arrowWidth: () => this.opts.arrowWidth * this.arrowScale,
        opacity: () => this.edgeOpacity,
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
        attribute float size;
        uniform mat3 view;
        varying float vId;
        void main() {
          vec3 p = view * vec3(position, 1.0);
          gl_Position = vec4(p.xy, 0, 1);
          // The mark itself (so a node hovers exactly where it is drawn, and the one on top wins), with a small
          // minimum so tiny nodes stay easy to hit.
          gl_PointSize = max(size, 14.0);
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
        size: () => this.nodeSizeBuffer!,
      },
      uniforms: {
        view: (_ctx: any) => this.camera.matrix(this.canvas.clientWidth / this.canvas.clientHeight),
      },
      elements: () => this.nodeElements!,
      count: () => this.visibleNodeIds.length,
      primitive: "points",
      // Later nodes draw on top of earlier ones on screen, so picking must let the last one drawn win too. With
      // regl's default depth test all nodes sit at the same depth and the FIRST drawn would win where they overlap.
      depth: { enable: false },
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
        for (const group of this.thinEdgeGroups) edgeDrawThin(group);
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

  /** What the graph looks like when the style says nothing: the values a control should show and reset to. */
  getStyleDefaults(): { nodeSize: number; edgeWidth: number; nodeColor: [number, number, number, number]; edgeColor: [number, number, number, number]; background: [number, number, number, number] } {
    return {
      nodeSize: this.opts.nodeRadiusPx * 2,
      edgeWidth: this.opts.edgeWidthPx,
      nodeColor: [...this.opts.nodeColor],
      edgeColor: [...this.opts.edgeColor],
      background: [...this.opts.backgroundColor],
    };
  }

  /** The same for edge (connector) attributes. */
  getEdgeAttributes(): AttributeSummary[] { return summarizeAttributes(this.allConnectors); }

  /** What kinds of data the graph has, so a UI can offer only the encodings that make sense. */
  getGraphInfo(): { nodes: number; edges: number; hasWeights: boolean; hasTime: boolean; timeUnit: "epoch_seconds" | null } {
    return {
      nodes: this.numNodes,
      edges: this.allConnectors.length,
      hasWeights: this.allConnectors.some((c) => c.weight !== null && Number.isFinite(c.weight)),
      hasTime: this.timeDomain !== null,
      timeUnit: this.timeDomain?.unit ?? null,
    };
  }

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

  /** Colour nodes by an attribute (one colour per distinct value, or a colormap for a numeric attribute), or null for the plain colour. */
  setNodeColorBy(attribute: string | null): void {
    this.setStyle({ node: { color: attribute === null ? null : { kind: "attribute", attribute } } });
  }

  /** Scale node size by "degree", by a numeric attribute, or null for uniform size. Sizes span 0.7x-3x. */
  setNodeSizeBy(by: string | null): void {
    const d = this.opts.nodeRadiusPx * 2;
    const range: [number, number] = [0.7 * d, 3 * d];
    this.setStyle({ node: { size: by === null ? null : by === "degree" ? { kind: "degree", range, scale: "sqrt" } : { kind: "attribute", attribute: by, range, scale: "sqrt" } } });
    this.nodeSizeBy = by;
  }

  /** Change how the graph looks, live. Fields you omit stay as they are, `null` restores a field's default, and an
   * invalid update throws and changes nothing. See StyleSpec. */
  setStyle(update: StyleSpec): void {
    this.resolveStyle(update);
  }

  /** The current style (colour encodings, sizes, and so on) as plain data. */
  getStyle(): StyleSpec {
    return this.styleManager.get();
  }

  /** Back to the options' style, and clear all painted nodes. */
  resetStyle(): void {
    this.styleManager = new StyleManager(this.optionStyle);
    this.nodeSizeBy = null;
    this.resolveStyle();
  }

  /** Give specific nodes (by id) a color of their own, over any encoding; a null color removes it. */
  paintNodes(ids: number[], color: string | ArrayLike<number> | null): void {
    this.styleManager.paint(ids, color === null ? null : parseColor(color));
    this.resolveStyle(undefined, false); // painting only touches nodes
  }

  clearPaint(): void {
    this.styleManager.clearPaint();
    this.resolveStyle(undefined, false);
  }

  private styleEnv(): StyleEnv {
    const nodeCount = this.index.nodes.length;
    return {
      nodes: this.index.nodes,
      connectors: this.allConnectors,
      timeConnectors: [...this.allConnectors, ...this.allHyperedges],
      degree: (i) => this.index.degree(i),
      nodeActivity: () => {
        if (this.nodeActivityCache) return this.nodeActivityCache;
        const first = new Float64Array(nodeCount).fill(NaN), last = new Float64Array(nodeCount).fill(NaN);
        for (const c of [...this.allConnectors, ...this.allHyperedges]) {
          for (const n of c.endpoints) {
            if (c.t_start !== null) first[n] = Number.isNaN(first[n]) ? c.t_start : Math.min(first[n], c.t_start);
            if (c.t_end !== null) last[n] = Number.isNaN(last[n]) ? c.t_end : Math.max(last[n], c.t_end);
          }
        }
        return (this.nodeActivityCache = { first, last });
      },
      timeDomain: this.timeDomain,
      cacheKey: this.graphVersion,
    };
  }

  private styleBase(): StyleBase {
    return {
      nodeColor: this.opts.nodeColor,
      nodeRadiusPx: this.opts.nodeRadiusPx,
      edgeColor: this.opts.edgeColor,
      edgeWidthPx: this.opts.edgeWidthPx,
      backgroundColor: this.opts.backgroundColor,
      edgeBaseColor: (i) => layerColor(this.allConnectors[i].layer_id, this.opts.edgeColor),
      nodeOverride: Object.keys(this.nodeColorOverrides).length ? (i) => this.nodeColorOverrides[this.nodeKeys[i]] : undefined,
      edgeOverride: Object.keys(this.edgeColorOverrides).length
        ? (i) => {
            const c = this.allConnectors[i];
            return this.edgeColorOverrides[`${this.nodeKeys[c.endpoints[0]]}||${this.nodeKeys[c.endpoints[1]]}`];
          }
        : undefined,
    };
  }

  /** Resolve the style (merging `update` first, if given) and push the result to the GPU. */
  private resolveStyle(update?: StyleSpec, rebuildEdges = update === undefined || changesEdgeBuffers(update)): void {
    // Edge buffers only need rebuilding when edge colors, widths or curvature can have changed; node changes and
    // edge opacity or arrow size (shader uniforms) leave them alone.
    const env = this.styleEnv(), base = this.styleBase();
    const resolved = update ? this.styleManager.apply(update, env, base) : this.styleManager.resolve(env, base);
    this.applyResolved(resolved, rebuildEdges);
  }

  private applyResolved(r: ResolvedStyle, rebuildEdges: boolean): void {
    this.resolved = r;
    this.nodeColorValues = r.nodeColors;
    this.nodeSizes = r.nodeSizes;
    this.nodeOpacity = r.nodeOpacity;
    this.nodeOutline = { color: [...r.outline.color] as [number, number, number, number], width: r.outline.width };
    this.labelStyle = r.label;
    this.backgroundColor = [...r.background] as [number, number, number, number];
    this.onBackgroundColor?.(this.backgroundColor);
    this.nodeColorBuffer?.({ data: r.nodeColors, usage: "static" } as any);
    this.nodeSizeBuffer?.({ data: r.nodeSizes, usage: "static" } as any);
    this.nodeShapeBuffer?.({ data: Float32Array.from(r.nodeShapes), usage: "static" } as any);
    this.uploadStackedNodeStyle();
    this.edgeStyleColors = r.edgeColors;
    this.edgeStyleWidths = r.edgeWidths;
    this.edgeOpacity = r.edgeOpacity;
    this.arrowScale = r.arrowScale;
    const curvatureChanged = r.curvature !== this.curvature;
    this.curvature = r.curvature;
    this.dash = r.dash;
    this.emitLegends(r);
    this.dirty = this.densityDirty = this.pointerDirty = true;
    this.onStyleChange?.();
    if (rebuildEdges) {
      this.setEdgeSet(this.filterConnectors(this.allConnectors));
      void curvatureChanged;
    }
  }

  /** Text to draw beside a node, or null. Custom `nodeLabel` text always shows; otherwise it depends on the label mode. */
  private labelTextFor(n: number, hovered: boolean): string | null {
    const l = this.labelStyle;
    if (l.mode === "none") return null;
    const base = (): string | null => {
      if (l.attribute === null) return this.nodeKeys[n];
      const v = this.index.nodes[n]?.attrs[l.attribute];
      return v === undefined || v === null ? null : String(v);
    };
    const custom = this.nodeLabelText[n];
    if (l.mode === "all" && this.visibleNodeIds.length <= MAX_ALL_LABELS) return custom ?? base();
    return custom ?? (hovered ? base() : null);
  }

  private nodeRadiusOf(n: number): number {
    return (this.nodeSizes[n] ?? this.opts.nodeRadiusPx * 2) / 2;
  }

  private drawLabelText(ctx: CanvasRenderingContext2D, text: string, x: number, y: number): void {
    if (this.labelStyle.halo) {
      ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.strokeStyle = "rgba(255,255,255,0.85)";
      ctx.strokeText(text, x, y);
    }
    ctx.fillText(text, x, y);
  }

  private emitLegends(r: ResolvedStyle): void {
    const categorical = (l: ColorLegend | null): ColorLegendEntry[] | null =>
      l && l.type === "categorical" ? l.entries.map((e) => ({ value: e.value, color: e.color })) : null;
    this.onNodeColorLegend?.(categorical(r.nodeLegend));
    this.onEdgeColorLegend?.(categorical(r.edgeLegend));
    this.onColorLegend?.("node", r.nodeLegend);
    this.onColorLegend?.("edge", r.edgeLegend);
  }

  /** The stacked (atlas/ribbon) views index node attributes per slice, so repeat the per-node style for each slice. */
  private uploadStackedNodeStyle(): void {
    const slices = this.stackNodeSliceCount, n = this.numNodes;
    if (slices === 0 || n === 0) return;
    const colors = new Float32Array(slices * n * 4), sizes = new Float32Array(slices * n);
    for (let s = 0; s < slices; s++) {
      colors.set(this.nodeColorValues, s * n * 4);
      for (let i = 0; i < n; i++) sizes[s * n + i] = this.nodeSizes[i] * 0.8;
    }
    this.stackedNodeColorBuffer?.({ data: colors, usage: "static" } as any);
    this.stackedNodeSizeBuffer?.({ data: sizes, usage: "static" } as any);
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
    this.uploadStackedNodeStyle();
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
    // One allocation, not two: evaluateLod/updateNodeElements below only read positions (nodesInViewport), and every
    // node starts at the origin regardless of which fresh, zero-filled array they read it from — a second identical
    // allocation right after was pure waste (800KB thrown away per load at 100K nodes).
    this.positions = new Float32Array(this.numNodes * 2);
    this.evaluateLod();
    this.nodeSizeBy = null;
    this.updateNodeElements();
    this.positionBuffer?.({ data: this.positions, usage: "dynamic" } as any);

    this.nodeIds = new Float32Array(this.numNodes);
    for (let i = 0; i < this.numNodes; i++) this.nodeIds[i] = i;
    this.idBuffer?.({ data: this.nodeIds, usage: "dynamic" } as any);

    this.nodeKeys = msg.nodes.map((n) => String(n.key));
    this.resolveNodeLabels(msg.nodes);

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

    // A new graph starts from the options' style; anything applied to the previous graph does not carry over.
    this.styleManager = new StyleManager(this.optionStyle);
    this.nodeActivityCache = null;
    this.graphVersion++;
    this.edgeIndexById = new Map(this.allConnectors.map((c, i) => [c.id, i]));
    this.resolveStyle(undefined, false);
    this.setEdgeSet(this.allConnectors);
    this.setHyperedgeSet(this.allHyperedges);
    this.onGraphLoaded?.();
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


  /** Rebuild edge/arrow geometry from a given connector set — used for the
   * initial full load and for time/layer-filtered subsets. */
  private edgeColorAt(c: WireConnector): [number, number, number, number] {
    const i = this.edgeIndexById.get(c.id);
    if (i === undefined || i * 4 + 3 >= this.edgeStyleColors.length) return layerColor(c.layer_id, this.opts.edgeColor);
    const o = i * 4;
    return [this.edgeStyleColors[o], this.edgeStyleColors[o + 1], this.edgeStyleColors[o + 2], this.edgeStyleColors[o + 3]];
  }

  private edgeWidthAt(c: WireConnector): number {
    const i = this.edgeIndexById.get(c.id);
    return i === undefined || i >= this.edgeStyleWidths.length ? this.opts.edgeWidthPx : this.edgeStyleWidths[i];
  }

  private setEdgeSet(connectors: WireConnector[]): void {
    this.dirty = this.densityDirty = true;
    this.visibleConnectorCount = connectors.length;
    this.useThinEdges = connectors.length > Renderer.THIN_EDGE_THRESHOLD;

    if (this.useThinEdges) {
      // Cheap path: index directly into positionBuffer, no per-edge CPU work on layout steps (see edgeDrawThin's
      // comment) and no `edges` object array. Edges are one pixel wide and straight, but each keeps its color: they
      // are sorted into runs of one color so a handful of draw calls covers them all.
      this.edges = [];
      this.edgesAreCurved = false;
      this.thinEdgeCount = connectors.length;
      const colors = connectors.map((c) => this.edgeColorAt(c));
      const groups = this.groupThinEdges(colors);
      const indexData = new Uint32Array(connectors.length * 2);
      const sortedColors = new Float32Array(connectors.length * 4);
      const pairs = new Uint32Array(connectors.length * 2);
      let cursor = 0;
      this.thinEdgeGroups = [];
      for (const { color, members } of groups) {
        this.thinEdgeGroups.push({ color, offset: cursor * 2, count: members.length * 2 });
        for (const m of members) {
          const c = connectors[m];
          indexData[cursor * 2] = pairs[cursor * 2] = c.endpoints[0];
          indexData[cursor * 2 + 1] = pairs[cursor * 2 + 1] = c.endpoints[1];
          sortedColors.set(colors[m], cursor * 4);
          cursor++;
        }
      }
      this.thinEdgePairs = pairs;
      this.thinEdgeColors = sortedColors;
      this.edgeThinIndexBuffer?.({ data: indexData, primitive: "lines", usage: "static" } as any);
    } else {
      this.thinEdgeGroups = [];
      this.edges = connectors.map((c) => ({ source: c.endpoints[0], target: c.endpoints[1], color: this.edgeColorAt(c), width: this.edgeWidthAt(c) }));
      this.thinEdgeCount = 0;
      this.edgesAreCurved = Math.abs(this.curvature) > 1e-6 && this.edges.length <= CURVED_EDGE_LIMIT;
      const segments = (this.edgeSegments = this.edgesAreCurved ? CURVE_SEGMENTS : 1);
      const perEdge = 2 * (segments + 1); // vertices per edge: a (t, -1), (t, +1) pair at every step along it
      const alongSideData = new Float32Array(this.edges.length * perEdge * 2);
      const colorData = new Float32Array(this.edges.length * perEdge * 4);
      const widthData = new Float32Array(this.edges.length * perEdge);
      const indexData = new Uint32Array(this.edges.length * segments * 6);
      this.edges.forEach((e, i) => {
        const base = i * perEdge;
        for (let k = 0; k <= segments; k++) {
          const t = k / segments;
          for (let side = 0; side < 2; side++) {
            const v = base + 2 * k + side;
            alongSideData[v * 2] = t;
            alongSideData[v * 2 + 1] = side === 0 ? -1 : 1;
            colorData.set(e.color, v * 4);
            widthData[v] = e.width;
          }
        }
        for (let k = 0; k < segments; k++) {
          const a = base + 2 * k, o = (i * segments + k) * 6;
          indexData.set([a, a + 1, a + 2, a + 1, a + 3, a + 2], o);
        }
      });
      this.edgeAlongSideBuffer?.({ data: alongSideData, usage: "static" } as any);
      this.edgeColorBuffer?.({ data: colorData, usage: "static" } as any);
      this.edgeWidthBuffer?.({ data: widthData, usage: "static" } as any);
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

  /** Group edges by color for the thin-edge path. Colors are quantised (more coarsely if there are very many
   * distinct ones) so the number of draw calls stays small. */
  private groupThinEdges(colors: [number, number, number, number][]): { color: [number, number, number, number]; members: number[] }[] {
    const MAX_GROUPS = 256;
    for (let bits = 8; bits >= 2; bits -= 2) {
      const step = 255 / (2 ** bits - 1);
      const quant = (v: number) => Math.round(Math.round((v * 255) / step) * step) / 255;
      const groups = new Map<string, { color: [number, number, number, number]; members: number[] }>();
      let tooMany = false;
      colors.forEach((c, i) => {
        if (tooMany) return;
        const q: [number, number, number, number] = [quant(c[0]), quant(c[1]), quant(c[2]), quant(c[3])];
        const key = q.join(",");
        let g = groups.get(key);
        if (!g) {
          if (groups.size >= MAX_GROUPS && bits > 2) { tooMany = true; return; }
          g = { color: q, members: [] };
          groups.set(key, g);
        }
        g.members.push(i);
      });
      if (!tooMany) return Array.from(groups.values());
    }
    return [];
  }

  /** Recompute the (source, target) positions baked into the edge vertex buffer from the current position buffer
   * — must be called whenever positions change (mirrors rebuildArrowSrcDst). Every vertex of an edge gets the same
   * srcDst pair; only alongSide (static) says where along the edge, and on which side, a vertex sits. */
  private rebuildEdgePositions(): void {
    const perEdge = 2 * (this.edgeSegments + 1);
    const data = new Float32Array(this.edges.length * perEdge * 4);
    this.edges.forEach((e, i) => {
      const sx = this.positions[e.source * 2] ?? 0;
      const sy = this.positions[e.source * 2 + 1] ?? 0;
      const tx = this.positions[e.target * 2] ?? 0;
      const ty = this.positions[e.target * 2 + 1] ?? 0;
      for (let v = 0; v < perEdge; v++) {
        const o = (i * perEdge + v) * 4;
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
    const curved = this.edgesAreCurved && !this.useThinEdges;
    const t = this.opts.arrowT;
    for (let i = 0; i < this.directedEdges.length; i++) {
      const { source, target } = this.directedEdges[i];
      let sx = this.positions[source * 2] ?? 0;
      let sy = this.positions[source * 2 + 1] ?? 0;
      let tx = this.positions[target * 2] ?? 0;
      let ty = this.positions[target * 2 + 1] ?? 0;
      if (curved) {
        // The arrow sits on the curve and points along its tangent: give the shader a segment that passes through
        // the tip with that direction (it places the tip at src + (dst - src) * arrowT).
        const dx = tx - sx, dy = ty - sy, len = Math.hypot(dx, dy);
        if (len > 1e-9) {
          const cx = (sx + tx) / 2 - (dy / len) * this.curvature * len, cy = (sy + ty) / 2 + (dx / len) * this.curvature * len;
          const u = 1 - t;
          const px = u * u * sx + 2 * u * t * cx + t * t * tx, py = u * u * sy + 2 * u * t * cy + t * t * ty;
          const gx = 2 * u * (cx - sx) + 2 * t * (tx - cx), gy = 2 * u * (cy - sy) + 2 * t * (ty - cy);
          const gl = Math.hypot(gx, gy) || 1;
          const ux = gx / gl, uy = gy / gl;
          sx = px - ux * len * t; sy = py - uy * len * t;
          tx = sx + ux * len; ty = sy + uy * len;
        }
      }
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
    this.markLodStale(false);
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

  /** Render-on-demand: this only runs while something needs it. It stops scheduling itself (rafHandle = null, no
   * running RAF) once a frame finds nothing dirty and nothing pending (LOD/hover debounce); wake() -- called
   * automatically whenever dirty/pointerDirty is set true, and directly by Camera on wheel/drag -- restarts it. A
   * viewer sitting on a converged, unchanging graph therefore does not run the browser's compositor and burn CPU
   * forever; it draws its last frame and goes quiet until something actually changes. */
  private loop = (): void => {
    // regl reads the drawing-buffer size when it is polled, and this loop draws without regl.frame, so nothing polls it.
    // A canvas that was resized after regl started (a viewer created inside a container that had no size yet, as in a
    // notebook widget) would otherwise keep drawing into its first, 1x1, viewport.
    const size = `${this.canvas.width}x${this.canvas.height}`;
    if (size !== this.lastCanvasSize) { this.lastCanvasSize = size; this.regl.poll(); }
    const camera = `${this.camera.x},${this.camera.y},${this.camera.zoom},${this.canvas.width},${this.canvas.height}`;
    if (camera !== this.lastCamera) { this.lastCamera = camera; this.dirty = true; this.markLodStale(); this.markHoverStale(); }
    if (this.lodPending && performance.now() - this.lodChangedAt > 160) this.applyLod();
    if (this.dirty) {
      this.resizePickFboIfNeeded();
      if (this.densityMode && this.densityDirty) this.rebuildDensity();
      this.regl.clear({color:this.backgroundColor,depth:1});
      if (this.stackAxis !== null) {
        if (!this.densityMode) this.drawStackThreads();
        this.drawStackedScene();
      } else if (this.densityMode) this.drawDensity(0);
      else { this.drawHyperedges(); this.drawEdges(); this.drawArrows(); this.drawNodes(); }
    }
    // updateSliceHover (stacked views) is cheap and always runs while dirty; updateHover (flat views) does a
    // GPU pick pass plus a synchronous readback, so it only runs on real pointer movement or once camera movement
    // has been quiet for a moment -- see markHoverStale. This check is unconditional (like the LOD one above), not
    // nested inside `if (this.dirty)`: dirty goes false as soon as the camera stops, and the deferred hover update
    // must still happen after that point, not only while something else keeps the frame dirty.
    const hoverSettled = this.hoverStale && performance.now() - this.hoverChangedAt > 50;
    if (this.pointerDirty || this.dirty || hoverSettled) {
      if (this.densityMode) this.setHovered(null);
      else if (this.stackAxis !== null) this.updateSliceHover();
      else if (this.pointerDirty || hoverSettled) { this.hoverStale = false; this.updateHover(); }
      this.drawLabels();
    }
    this.dirty = this.pointerDirty = false;
    // The canvas-size/camera polling above is why this must keep running while lodPending/hoverStale are waiting out
    // their debounce window (nothing else would ever re-check them); otherwise, with nothing left to do, it stops.
    if (this.lodPending || this.hoverStale) this.rafHandle = requestAnimationFrame(this.loop);
    else this.rafHandle = null;
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
    const label = this.labelStyle;
    const labelFont = `${label.fontSize}px system-ui, sans-serif`, labelFill = rgbaToCss(label.color);
    ctx.font = labelFont;
    ctx.fillStyle = labelFill;
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
          const text = this.labelTextFor(n, hovered);
          if (!text && !hovered) continue;
          const [nx, ny] = this.worldToScreen(...g.point(n, i), w, h);
          if (hovered) {
            ctx.strokeStyle = "#183e51"; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(nx, ny, this.nodeRadiusOf(n) * 0.8 + 3, 0, Math.PI * 2); ctx.stroke();
          }
          if (text) this.drawLabelText(ctx, text, nx + this.nodeRadiusOf(n) * 0.8 + 6, ny);
        }
      });
      return;
    }
    if (this.densityMode) return;
    for (const n of this.visibleNodeIds) {
      const text = this.labelTextFor(n, n === this.selectedNode || n === this.hoveredNodeId);
      if (!text) continue;
      const [x, y] = this.worldToScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0, w, h);
      this.drawLabelText(ctx, text, x + this.nodeRadiusOf(n) + 3, y);
    }
    ctx.strokeStyle = "#183e51"; ctx.lineWidth = 2; ctx.fillStyle = "rgba(24, 62, 81, 0.95)";
    const shown = this.viewNodes();
    for (const n of this.highlighted) {
      if (shown && !shown.has(n)) continue;
      const [x, y] = this.worldToScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0, w, h);
      ctx.beginPath(); ctx.arc(x, y, this.nodeRadiusOf(n) + 4, 0, Math.PI * 2); ctx.stroke();
      this.drawLabelText(ctx, this.nodeKeys[n], x + this.nodeRadiusOf(n) + 8, y - 8);
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
      `<rect x="0" y="0" width="${w}" height="${h}" fill="${rgba(this.backgroundColor)}" />`,
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

      const alpha = (c: [number, number, number, number]): [number, number, number, number] => [c[0], c[1], c[2], c[3] * this.edgeOpacity];
      if (this.useThinEdges) {
        // Matches edgeDrawThin: one pixel wide and straight, but each edge keeps its color.
        for (let i = 0; i < this.thinEdgePairs.length; i += 2) {
          const u = this.thinEdgePairs[i];
          const v = this.thinEdgePairs[i + 1];
          const o = (i / 2) * 4;
          const stroke = rgba(alpha([this.thinEdgeColors[o], this.thinEdgeColors[o + 1], this.thinEdgeColors[o + 2], this.thinEdgeColors[o + 3]]));
          const [x1, y1] = toScreen(this.positions[u * 2] ?? 0, this.positions[u * 2 + 1] ?? 0);
          const [x2, y2] = toScreen(this.positions[v * 2] ?? 0, this.positions[v * 2 + 1] ?? 0);
          parts.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${stroke}" stroke-width="1" />`);
        }
      } else {
        // SVG has native dash support, so this is exact (unlike the shader's straight-line-distance
        // approximation): stroke-dasharray takes the same [on1, off1, on2, off2] pixel lengths directly, cycling
        // as needed. Trailing zeros (a plain dashed/dotted pattern, not dashdot) are dropped since a zero-length
        // dash segment renders inconsistently across SVG viewers.
        const dashArray = this.dash ? (this.dash[2] === 0 && this.dash[3] === 0 ? `${this.dash[0]} ${this.dash[1]}` : this.dash.join(" ")) : null;
        const dashAttr = dashArray ? ` stroke-dasharray="${dashArray}"` : "";
        for (const e of this.edges) {
          const sx = this.positions[e.source * 2] ?? 0, sy = this.positions[e.source * 2 + 1] ?? 0;
          const tx = this.positions[e.target * 2] ?? 0, ty = this.positions[e.target * 2 + 1] ?? 0;
          const [x1, y1] = toScreen(sx, sy);
          const [x2, y2] = toScreen(tx, ty);
          const stroke = rgba(alpha(e.color));
          if (this.edgesAreCurved) {
            // The same quadratic Bezier the shader draws (an SVG "Q" segment is exactly that curve).
            const len = Math.hypot(tx - sx, ty - sy) || 1;
            const [cx, cy] = toScreen((sx + tx) / 2 - ((ty - sy) / len) * this.curvature * len, (sy + ty) / 2 + ((tx - sx) / len) * this.curvature * len);
            parts.push(`<path d="M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}" fill="none" stroke="${stroke}" stroke-width="${e.width}"${dashAttr} />`);
          } else {
            parts.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${stroke}" stroke-width="${e.width}"${dashAttr} />`);
          }
        }
      }

      // Arrowheads — same triangle construction as arrowDraw's vertex
      // shader (corner.y in {+-0.5} is why the perpendicular offset below
      // is arrowWidth * 0.5, not arrowWidth).
      for (let arrow = 0; arrow < this.directedEdges.length; arrow++) {
        // arrowSrcDstData holds the segment the shader uses: the edge itself, or a tangent segment for curved edges.
        const psx = this.arrowSrcDstData[arrow * 12];
        const psy = this.arrowSrcDstData[arrow * 12 + 1];
        const ptx = this.arrowSrcDstData[arrow * 12 + 2];
        const pty = this.arrowSrcDstData[arrow * 12 + 3];
        const dx = ptx - psx;
        const dy = pty - psy;
        const len = Math.hypot(dx, dy) || 1;
        const dirX = dx / len;
        const dirY = dy / len;
        const perpX = -dirY;
        const perpY = dirX;
        const tipX = psx + dx * this.opts.arrowT;
        const tipY = psy + dy * this.opts.arrowT;
        const baseX = tipX - dirX * this.opts.arrowLength * this.arrowScale;
        const baseY = tipY - dirY * this.opts.arrowLength * this.arrowScale;
        const half = this.opts.arrowWidth * this.arrowScale * 0.5;
        const pts: [number, number][] = [
          toScreen(tipX, tipY),
          toScreen(baseX + perpX * half, baseY + perpY * half),
          toScreen(baseX - perpX * half, baseY - perpY * half),
        ];
        parts.push(`<polygon points="${pointsAttr(pts)}" fill="${rgba(alpha(this.opts.arrowColor))}" />`);
      }

      // Nodes: each with its own color, size, shape and outline, so the export matches the screen.
      const outline = this.nodeOutline.width > 0 ? ` stroke="${rgba(this.nodeOutline.color)}" stroke-width="${this.nodeOutline.width}"` : "";
      for (const n of this.visibleNodeIds) {
        const [x, y] = toScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0);
        const o = n * 4;
        const fill = rgba([this.nodeColorValues[o], this.nodeColorValues[o + 1], this.nodeColorValues[o + 2], this.nodeColorValues[o + 3] * this.nodeOpacity]);
        parts.push(svgNodeShape(this.resolved?.nodeShapes[n] ?? 0, x, y, this.nodeRadiusOf(n), fill, outline));
      }
      // Labels the screen shows without hovering (custom labels, or every node in "all" mode).
      const labelFill = rgba(this.labelStyle.color);
      for (const n of this.visibleNodeIds) {
        const text = this.labelTextFor(n, n === this.selectedNode);
        if (!text) continue;
        const [x, y] = toScreen(this.positions[n * 2] ?? 0, this.positions[n * 2 + 1] ?? 0);
        const escaped = text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
        const halo = this.labelStyle.halo ? ` stroke="rgba(255,255,255,0.85)" stroke-width="3" paint-order="stroke" stroke-linejoin="round"` : "";
        parts.push(`<text x="${(x + this.nodeRadiusOf(n) + 3).toFixed(1)}" y="${y.toFixed(1)}" font-size="${this.labelStyle.fontSize}" dominant-baseline="middle" fill="${labelFill}"${halo}>${escaped}</text>`);
      }
    }

    parts.push(`</svg>`);
    return parts.join("\n");
  }

  dispose(): void {
    if (this.rafHandle !== null) cancelAnimationFrame(this.rafHandle);
    this.resizeObserver?.disconnect();
    window.removeEventListener("resize", this.onWindowResize);
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerleave", this.onPointerLeave);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.labelCanvas?.remove();
    this.camera.dispose();
    this.regl.destroy();
  }
}
