import type { SliceLayout } from "./layout/slices";
export { sliceGeometry, type SliceLayout } from "./layout/slices";
import { Renderer, RendererOptions, StackAxis, TimeFilterOptions, TimeSplit } from "./render/renderer";
import { WebSocketTransport, type TransportHandlers } from "./transport/websocket";
import { ParentTransport, PARENT_TRANSPORT } from "./transport/parent";
export { PARENT_TRANSPORT } from "./transport/parent";
import type { WireNode } from "./ir/types";
import { isGraphMessage, isLayoutStepMessage, isStyleMessage } from "./ir/types";
import { applyStyleMessage } from "./style/messages";
import type { AttributeSummary, NodeFilter } from "./interaction/graph-index";
import type { StyleSpec } from "./style/spec";

export { Renderer, formatTime, SECONDS_PER_DAY } from "./render/renderer";
export type {
  RendererOptions,
  LayerInfo,
  TimeDomain,
  TimeMode,
  TimeFilterOptions,
  TimeSplit,
  StackAxis,
  StackSliceInfo,
  NodeLabelSpec,
  ColorLegendEntry,
} from "./render/renderer";
export { Camera } from "./interaction/camera";
export type { AttributeSummary, NodeFilter } from "./interaction/graph-index";
export type { StyleSpec, NodeStyle, EdgeStyle, LabelStyle, ColorEncoding, SizeEncoding, ShapeEncoding, NodeShape } from "./style/spec";
export { NODE_SHAPES } from "./style/spec";
export { parseColor, rgbaToCss, rgbaToHex } from "./style/colors";
export { colormapNames, paletteNames, sampleColormap } from "./style/colormaps";
export type { ColorLegend } from "./style/engine";
export * from "./ir/types";

export interface ViewerHandle {
  searchNodes(query: string): WireNode[];
  inspectNode(nodeId: number): {node: WireNode; neighbors: number; connectors: number} | null;
  /** Show connectors incident to this node and their members; null restores the graph. */
  focusNeighborhood(nodeId: number | null): void;
  fitView(): void;
  /** Multiply the zoom (1.5 zooms in, 1/1.5 zooms out). */
  zoomBy(factor: number): void;
  /** Node attributes with value counts / numeric ranges, for building controls. */
  getNodeAttributes(): AttributeSummary[];
  /** The same for edge attributes. */
  getEdgeAttributes(): AttributeSummary[];
  /** The look when the style says nothing (what controls show and reset to). */
  getStyleDefaults(): { nodeSize: number; edgeWidth: number; nodeColor: [number, number, number, number]; edgeColor: [number, number, number, number]; background: [number, number, number, number] };
  /** What the graph has (weights, time), so a UI offers only encodings that make sense. */
  getGraphInfo(): { nodes: number; edges: number; hasWeights: boolean; hasTime: boolean; timeUnit: "epoch_seconds" | null };
  /** Show only nodes passing the filter (attribute values, numeric range, degree); null clears. Returns the count shown. */
  setNodeFilter(filter: NodeFilter | null): number;
  getVisibleNodeCount(): number;
  /** Aggregate nodes into one point per value of a categorical attribute (collapse communities); null expands. */
  setGroupBy(attribute: string | null): void;
  /** What is on screen: individual nodes, the nodes in view of a larger graph, aggregated cells, or groups. */
  getLodState(): { mode: "detail" | "region" | "overview" | "groups"; nodesInView: number; nodesDrawn: number };
  getNodeKey(id: number): string | null;
  /** Canvas-relative pixel position of an individually drawn node, or null (hidden, or aggregated in the density overview). */
  getNodeScreenPosition(id: number): [number, number] | null;
  /** Colour nodes by an attribute, or null for the plain colour. */
  setNodeColorBy(attribute: string | null): void;
  /** Size nodes by "degree" or a numeric attribute, or null for uniform size. */
  setNodeSizeBy(by: string | null): void;
  /** Show only the fewest-hop route between two nodes; null when they are not connected. */
  focusPath(from: number, to: number): { nodes: number[]; connectors: number[]; hops: number } | null;
  /** Change how the graph looks, live. Fields you omit stay, `null` restores a field's default; an invalid update
   * throws and changes nothing. See StyleSpec. */
  setStyle(update: StyleSpec): void;
  /** The current style as plain data. */
  getStyle(): StyleSpec;
  /** Restore the initial style and clear painted nodes. */
  resetStyle(): void;
  /** Give specific nodes (by id) a color of their own, over any encoding; null removes it. */
  paintNodes(ids: number[], color: string | ArrayLike<number> | null): void;
  clearPaint(): void;
  /** Ring and label these nodes (a selection); [] clears. */
  setHighlightedNodes(ids: number[]): void;
  dispose(): void;
  /** Show only connectors valid at time `t`, or null to show everything
   * regardless of time (the default). No-op if the graph has no temporal
   * connectors — see Renderer.setTimeFilter. */
  setTimeFilter(t: number | null, options?: TimeFilterOptions): void;
  /** Show only connectors in the given layer ids (layer-less connectors
   * always show), or null for every layer (the default). No-op if the
   * graph has no layers — see Renderer.setLayerFilter. */
  setLayerFilter(visibleLayerIds: number[] | null): void;
  /** Number of connectors currently drawn (after time/layer filtering, if
   * any). */
  getVisibleEdgeCount(): number;
  /** Number of hyperedges currently drawn (after time/layer filtering). */
  getVisibleHyperedgeCount(): number;
  /** Switch between the flat/overlay view and the stacked-slices view
   * (each layer or time-bucket as its own offset plane) — see
   * Renderer.setStackMode. */
  setStackMode(axis: StackAxis, options?: { timeBuckets?: number; timeSplit?: TimeSplit; layout?: SliceLayout }): void;
  /** Serialize the current view (whichever mode is active) to a vector
   * SVG string — see Renderer.exportSVG. */
  exportSVG(): string;
}

export interface MountViewerOptions extends RendererOptions {
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: unknown) => void;
  /** Called when a style pushed from Python is invalid; the previous style stays in place. */
  onStyleError?: (err: unknown) => void;
}

/**
 * Mount a live graph viewer on `canvas`, fed by the bridge WebSocket at
 * `wsUrl`, or, when `wsUrl` is `PARENT_TRANSPORT`, by the notebook widget that hosts this page in an iframe.
 */
export function mountViewer(
  canvas: HTMLCanvasElement,
  wsUrl: string,
  options?: MountViewerOptions
): ViewerHandle {
  const renderer = new Renderer(canvas, options);

  const handlers: TransportHandlers = {
    onMessage: (msg) => {
      if (isGraphMessage(msg)) {
        renderer.loadGraph(msg);
      } else if (isLayoutStepMessage(msg)) {
        renderer.applyLayoutStep(msg);
      } else if (isStyleMessage(msg)) {
        // A bad style must not take the viewer down: report it and keep the current look.
        try {
          applyStyleMessage(renderer, msg);
        } catch (err) {
          console.error("[plexgraph] style rejected:", err);
          options?.onStyleError?.(err);
        }
      }
    },
    onOpen: options?.onOpen,
    onClose: options?.onClose,
    onError: options?.onError ?? ((err) => console.error("[plexgraph] transport error", err)),
  };
  const transport = wsUrl === PARENT_TRANSPORT ? new ParentTransport(handlers) : new WebSocketTransport(wsUrl, handlers);

  return {
    searchNodes: query => renderer.searchNodes(query),
    inspectNode: nodeId => renderer.inspectNode(nodeId),
    focusNeighborhood: nodeId => renderer.focusNeighborhood(nodeId),
    fitView: () => renderer.fitView(),
    zoomBy: factor => renderer.zoomBy(factor),
    getNodeAttributes: () => renderer.getNodeAttributes(),
    getEdgeAttributes: () => renderer.getEdgeAttributes(),
    getStyleDefaults: () => renderer.getStyleDefaults(),
    getGraphInfo: () => renderer.getGraphInfo(),
    setNodeFilter: filter => renderer.setNodeFilter(filter),
    getVisibleNodeCount: () => renderer.getVisibleNodeCount(),
    setGroupBy: attribute => renderer.setGroupBy(attribute),
    getLodState: () => renderer.getLodState(),
    getNodeKey: id => renderer.getNodeKey(id),
    getNodeScreenPosition: id => renderer.getNodeScreenPosition(id),
    setNodeColorBy: attribute => renderer.setNodeColorBy(attribute),
    setNodeSizeBy: by => renderer.setNodeSizeBy(by),
    focusPath: (from, to) => renderer.focusPath(from, to),
    setHighlightedNodes: ids => renderer.setHighlightedNodes(ids),
    setStyle: update => renderer.setStyle(update),
    getStyle: () => renderer.getStyle(),
    resetStyle: () => renderer.resetStyle(),
    paintNodes: (ids, color) => renderer.paintNodes(ids, color),
    clearPaint: () => renderer.clearPaint(),
    dispose(): void {
      transport.close();
      renderer.dispose();
    },
    setTimeFilter(t: number | null, options?: TimeFilterOptions): void {
      renderer.setTimeFilter(t, options);
    },
    setLayerFilter(visibleLayerIds: number[] | null): void {
      renderer.setLayerFilter(visibleLayerIds);
    },
    getVisibleEdgeCount(): number {
      return renderer.getVisibleEdgeCount();
    },
    getVisibleHyperedgeCount(): number {
      return renderer.getVisibleHyperedgeCount();
    },
    setStackMode(axis: StackAxis, options?: { timeBuckets?: number; timeSplit?: TimeSplit; layout?: SliceLayout }): void {
      renderer.setStackMode(axis, options);
    },
    exportSVG(): string {
      return renderer.exportSVG();
    },
  };
}
