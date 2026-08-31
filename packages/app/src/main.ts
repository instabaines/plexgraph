import { mountViewer, RendererOptions } from "@hyperloom/viz-core";
import { exportView, type ExportFormat } from "./export";

function parseStyleParam(raw: string | null): RendererOptions {
  if (!raw) return {};
  try {
    return JSON.parse(raw) as RendererOptions;
  } catch (err) {
    console.error("[hyperloom] failed to parse ?style= param", err);
    return {};
  }
}

function rgbaCss([r, g, b, a]: [number, number, number, number]): string {
  return `rgba(${r * 255}, ${g * 255}, ${b * 255}, ${a})`;
}

function renderColorLegend(
  el: HTMLElement,
  title: string,
  entries: { value: string; color: [number, number, number, number] }[] | null
): void {
  if (entries === null || entries.length === 0) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.replaceChildren();
  const heading = document.createElement("div");
  heading.className = "title";
  heading.textContent = title;
  el.appendChild(heading);
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "row";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = rgbaCss(entry.color);
    const text = document.createElement("span");
    text.textContent = entry.value;
    row.append(swatch, text);
    el.appendChild(row);
  }
}

const statusEl = document.getElementById("status")!;
const canvas = document.getElementById("canvas") as HTMLCanvasElement;
const timelineEl = document.getElementById("timeline")!;
const timelineToggle = document.getElementById("timeline-toggle") as HTMLButtonElement;
const timelineSlider = document.getElementById("timeline-slider") as HTMLInputElement;
const timelineValue = document.getElementById("timeline-value")!;
const layersEl = document.getElementById("layers")!;
const viewModeEl = document.getElementById("view-mode")!;
const viewFlatBtn = document.getElementById("view-flat") as HTMLButtonElement;
const viewStackLayerBtn = document.getElementById("view-stack-layer") as HTMLButtonElement;
const viewStackTimeBtn = document.getElementById("view-stack-time") as HTMLButtonElement;
const stackLegendEl = document.getElementById("stack-legend")!;
const nodeColorLegendEl = document.getElementById("node-color-legend")!;
const edgeColorLegendEl = document.getElementById("edge-color-legend")!;
const exportToggle = document.getElementById("export-toggle") as HTMLButtonElement;
const exportMenu = document.getElementById("export-menu")!;

function resizeCanvas(): void {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * dpr;
  canvas.height = canvas.clientHeight * dpr;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

const params = new URLSearchParams(window.location.search);
const wsPort = params.get("ws");
const wsHost = params.get("host") ?? window.location.hostname;
const style = parseStyleParam(params.get("style"));

function setStatus(text: string): void {
  statusEl.textContent = text;
  statusEl.hidden = text === "";
}

if (!wsPort) {
  setStatus("no ?ws=<port> in URL — nothing to connect to");
} else {
  const wsUrl = `ws://${wsHost}:${wsPort}`;
  // Idle/connected state shows nothing (no "connected (ws://host:port)"
  // clutter — it's debug info, not something a viewer or an exported
  // image should carry) — only genuinely useful states (an error, or the
  // hovered node) get shown.
  let connectionStatus = "";
  setStatus(connectionStatus);
  let timelineMode: "all" | "scrub" = "all";
  let hasLayers = false;
  let hasTimeDomain = false;

  function updateViewModeVisibility(): void {
    viewModeEl.hidden = !hasLayers && !hasTimeDomain;
    viewStackLayerBtn.hidden = !hasLayers;
    viewStackTimeBtn.hidden = !hasTimeDomain;
  }

  const handle = mountViewer(canvas, wsUrl, {
    ...style,
    onOpen: () => {
      connectionStatus = "";
      setStatus(connectionStatus);
    },
    onClose: () => {
      connectionStatus = "disconnected";
      setStatus(connectionStatus);
    },
    onError: () => {
      connectionStatus = "connection error";
      setStatus(connectionStatus);
    },
    onHover: (nodeId) => {
      setStatus(nodeId === null ? connectionStatus : `node ${nodeId}`);
    },
    onTimeDomain: (domain) => {
      hasTimeDomain = domain !== null;
      updateViewModeVisibility();
      if (domain === null) {
        timelineEl.hidden = true;
        return;
      }
      timelineEl.hidden = false;
      timelineSlider.min = String(domain.min);
      timelineSlider.max = String(domain.max);
      timelineSlider.value = String(domain.min);
      // Start in "all time" mode (filter off) even though a domain
      // exists — scrubbing is opt-in via the toggle button.
      timelineMode = "all";
      timelineToggle.textContent = "All time";
      timelineValue.textContent = "";
    },
    onLayers: (layers) => {
      hasLayers = layers.length > 0;
      updateViewModeVisibility();
      if (layers.length === 0) {
        layersEl.hidden = true;
        return;
      }
      layersEl.hidden = false;
      layersEl.replaceChildren();
      const checkedIds = new Set(layers.map((l) => l.id));

      for (const layer of layers) {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = true;
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) checkedIds.add(layer.id);
          else checkedIds.delete(layer.id);
          handle.setLayerFilter(Array.from(checkedIds));
        });

        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = rgbaCss(layer.color);

        const text = document.createElement("span");
        text.textContent = String(layer.key);

        label.append(checkbox, swatch, text);
        layersEl.appendChild(label);
      }
    },
    onStackChange: (slices) => {
      if (slices === null) {
        stackLegendEl.hidden = true;
        return;
      }
      stackLegendEl.hidden = false;
      stackLegendEl.replaceChildren();
      // Listed front-to-back (reverse of draw order) so the "closest"
      // plane reads first, like a normal list — draw order itself stays
      // back-to-front for correct blending (see renderer.ts).
      for (const slice of [...slices].reverse()) {
        const row = document.createElement("div");
        row.className = "row";
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = rgbaCss(slice.color);
        const text = document.createElement("span");
        text.textContent = slice.label;
        row.append(swatch, text);
        stackLegendEl.appendChild(row);
      }
    },
    onNodeColorLegend: (entries) => renderColorLegend(nodeColorLegendEl, "Node color", entries),
    onEdgeColorLegend: (entries) => renderColorLegend(edgeColorLegendEl, "Edge color", entries),
  });
  window.addEventListener("beforeunload", () => handle.dispose());

  // Exposed for local debugging / automated visual verification only —
  // not part of the public viz-core API surface.
  (window as unknown as { __hyperloomHandle: typeof handle }).__hyperloomHandle = handle;

  timelineToggle.addEventListener("click", () => {
    if (timelineMode === "all") {
      timelineMode = "scrub";
      timelineToggle.textContent = "Show all";
      handle.setTimeFilter(Number(timelineSlider.value));
      timelineValue.textContent = timelineSlider.value;
    } else {
      timelineMode = "all";
      timelineToggle.textContent = "All time";
      handle.setTimeFilter(null);
      timelineValue.textContent = "";
    }
  });

  timelineSlider.addEventListener("input", () => {
    if (timelineMode !== "scrub") return;
    handle.setTimeFilter(Number(timelineSlider.value));
    timelineValue.textContent = timelineSlider.value;
  });

  function setViewMode(mode: "flat" | "stack-layer" | "stack-time"): void {
    viewFlatBtn.setAttribute("aria-pressed", String(mode === "flat"));
    viewStackLayerBtn.setAttribute("aria-pressed", String(mode === "stack-layer"));
    viewStackTimeBtn.setAttribute("aria-pressed", String(mode === "stack-time"));

    // Stacked mode always shows every layer/time-slice at once — the
    // flat view's filter controls (which narrow down to one moment/subset)
    // don't apply there, so hide them rather than leave them present but
    // inert.
    if (mode === "flat") {
      if (hasTimeDomain) timelineEl.hidden = false;
      if (hasLayers) layersEl.hidden = false;
      handle.setStackMode(null);
    } else {
      timelineEl.hidden = true;
      layersEl.hidden = true;
      handle.setStackMode(mode === "stack-layer" ? "layer" : "time", { timeBuckets: 6 });
    }
  }

  viewFlatBtn.addEventListener("click", () => setViewMode("flat"));
  viewStackLayerBtn.addEventListener("click", () => setViewMode("stack-layer"));
  viewStackTimeBtn.addEventListener("click", () => setViewMode("stack-time"));

  exportToggle.addEventListener("click", () => {
    exportMenu.hidden = !exportMenu.hidden;
  });
  document.addEventListener("click", (e) => {
    if (!exportMenu.hidden && !exportMenu.contains(e.target as Node) && e.target !== exportToggle) {
      exportMenu.hidden = true;
    }
  });
  exportMenu.querySelectorAll<HTMLButtonElement>("button[data-format]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      exportMenu.hidden = true;
      const format = btn.dataset.format as ExportFormat;
      try {
        await exportView(format, canvas, () => handle.exportSVG());
      } catch (err) {
        console.error("[hyperloom] export failed", err);
      }
    });
  });
}
