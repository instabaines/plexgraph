import { Renderer, RendererOptions, StackAxis } from "./render/renderer";
import { WebSocketTransport } from "./transport/websocket";
import { isGraphMessage, isLayoutStepMessage } from "./ir/types";

export { Renderer } from "./render/renderer";
export type {
  RendererOptions,
  LayerInfo,
  TimeDomain,
  StackAxis,
  StackSliceInfo,
  NodeLabelSpec,
  ColorLegendEntry,
} from "./render/renderer";
export { Camera } from "./interaction/camera";
export * from "./ir/types";

export interface ViewerHandle {
  dispose(): void;
  /** Show only connectors valid at time `t`, or null to show everything
   * regardless of time (the default). No-op if the graph has no temporal
   * connectors — see Renderer.setTimeFilter. */
  setTimeFilter(t: number | null): void;
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
  setStackMode(axis: StackAxis, options?: { timeBuckets?: number }): void;
  /** Serialize the current view (whichever mode is active) to a vector
   * SVG string — see Renderer.exportSVG. */
  exportSVG(): string;
}

export interface MountViewerOptions extends RendererOptions {
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: unknown) => void;
}

/**
 * Mount a live graph viewer on `canvas`, fed by the bridge WebSocket at
 * `wsUrl`. This is the entry point both the standalone app shell and the
 * (future) anywidget transport shim build on.
 */
export function mountViewer(
  canvas: HTMLCanvasElement,
  wsUrl: string,
  options?: MountViewerOptions
): ViewerHandle {
  const renderer = new Renderer(canvas, options);

  const transport = new WebSocketTransport(wsUrl, {
    onMessage: (msg) => {
      if (isGraphMessage(msg)) {
        renderer.loadGraph(msg);
      } else if (isLayoutStepMessage(msg)) {
        renderer.applyLayoutStep(msg);
      }
    },
    onOpen: options?.onOpen,
    onClose: options?.onClose,
    onError: options?.onError ?? ((err) => console.error("[hyperloom] transport error", err)),
  });

  return {
    dispose(): void {
      transport.close();
      renderer.dispose();
    },
    setTimeFilter(t: number | null): void {
      renderer.setTimeFilter(t);
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
    setStackMode(axis: StackAxis, options?: { timeBuckets?: number }): void {
      renderer.setStackMode(axis, options);
    },
    exportSVG(): string {
      return renderer.exportSVG();
    },
  };
}
