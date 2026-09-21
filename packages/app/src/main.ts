import { formatTime, mountViewer, RendererOptions, rgbaToCss, sampleColormap, SECONDS_PER_DAY, type ColorLegend, type TimeDomain, type TimeMode, type TimeSplit } from "@plexgraph/viz-core";
import { exportView, type ExportFormat } from "./export";
import { mountAppearance } from "./appearance";
import { mountTools } from "./tools";

function parseStyleParam(raw: string | null): RendererOptions {
  if (!raw) return {};
  try {
    return JSON.parse(raw) as RendererOptions;
  } catch (err) {
    console.error("[plexgraph] failed to parse ?style= param", err);
    return {};
  }
}

function rgbaCss([r, g, b, a]: [number, number, number, number]): string {
  return `rgba(${r * 255}, ${g * 255}, ${b * 255}, ${a})`;
}

function renderLegend(el: HTMLElement, heading: string, legend: ColorLegend | null, timeUnit: "epoch_seconds" | null): void {
  if (legend === null || (legend.type === "categorical" && legend.entries.length === 0)) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.replaceChildren();
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = legend.title ? `${heading} · ${legend.title}` : heading;
  el.appendChild(title);
  if (legend.type === "continuous") {
    const stops = Array.from({ length: 9 }, (_, i) => rgbaToCss(sampleColormap(legend.colormap, i / 8, legend.reverse)));
    const bar = document.createElement("div");
    bar.className = "gradient";
    bar.style.background = `linear-gradient(to right, ${stops.join(", ")})`;
    const ends = document.createElement("div");
    ends.className = "ends";
    const span = legend.max - legend.min;
    const fmt = (v: number) => (legend.title === "time" && timeUnit ? formatTime(v, timeUnit, span) : String(Number(v.toPrecision(4))));
    ends.append(Object.assign(document.createElement("span"), { textContent: fmt(legend.min) }), Object.assign(document.createElement("span"), { textContent: fmt(legend.max) }));
    el.append(bar, ends);
    return;
  }
  for (const entry of legend.entries) {
    const row = document.createElement("div");
    row.className = "row";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = rgbaCss(entry.color);
    const text = document.createElement("span");
    text.textContent = entry.value;
    row.append(swatch, text);
    if (entry.count !== undefined) {
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = entry.count.toLocaleString();
      row.append(count);
    }
    el.appendChild(row);
  }
}

const statusEl = document.getElementById("status")!;
const canvas = document.getElementById("canvas") as HTMLCanvasElement;
const timelineEl = document.getElementById("timeline")!;
const timelineToggle = document.getElementById("timeline-toggle") as HTMLButtonElement;
const timelineSlider = document.getElementById("timeline-slider") as HTMLInputElement;
const timelineValue = document.getElementById("timeline-value")!;
const timelineKind = document.getElementById("timeline-kind") as HTMLSelectElement;
const timelineWindow = document.getElementById("timeline-window") as HTMLInputElement;
const timelineWindowLabel = document.getElementById("timeline-window-label")!;
const timelineWindowUnit = document.getElementById("timeline-window-unit")!;
const ribbonOptionsEl = document.getElementById("ribbon-options")!;
const ribbonBuckets = document.getElementById("ribbon-buckets") as HTMLInputElement;
const ribbonSplit = document.getElementById("ribbon-split") as HTMLSelectElement;
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
const toolsEl = document.getElementById("tools")!;
const appearanceEl = document.getElementById("appearance")!;

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
  // ?ws= is normally a port on this host. `same-origin` means the server that served this page (a remote notebook
  // forwards one port for both); a full ws:// or wss:// address is used as given.
  const wsUrl = wsPort === "same-origin"
    ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/`
    : /^wss?:\/\//.test(wsPort) ? wsPort : `ws://${wsHost}:${wsPort}`;
  // Idle/connected state shows nothing (no "connected (ws://host:port)"
  // clutter — it's debug info, not something a viewer or an exported
  // image should carry) — only genuinely useful states (an error, or the
  // hovered node) get shown.
  let connectionStatus = "";
  setStatus(connectionStatus);
  let timelineMode: "all" | "scrub" = "all";
  let hasLayers = false;
  let hasTimeDomain = false;
  let timeDomain: TimeDomain | null = null;

  function updateViewModeVisibility(): void {
    viewModeEl.hidden = !hasLayers && !hasTimeDomain;
    viewStackLayerBtn.hidden = !hasLayers;
    viewStackTimeBtn.hidden = !hasTimeDomain;
  }

  let tools: ReturnType<typeof mountTools> | null = null;
  let appearance: ReturnType<typeof mountAppearance> | null = null;
  let timeUnit: "epoch_seconds" | null = null;
  const handle = mountViewer(canvas, wsUrl, {
    ...style,
    onGraphLoaded: () => {
      tools?.graphLoaded();
      appearance?.graphLoaded();
      timeUnit = handle.getGraphInfo().timeUnit;
    },
    onStyleChange: () => {
      appearance?.styleChanged();
      tools?.syncFromStyle();
    },
    onBackgroundColor: (c) => {
      // The page around the canvas matches, so a dark theme is dark everywhere.
      document.body.style.background = rgbaCss(c);
    },
    onStyleError: (err) => setStatus(`style not applied: ${err instanceof Error ? err.message : String(err)}`),
    onNodeClick: (id) => tools?.nodeClicked(id),
    onGroupClick: (attribute, value) => tools?.groupClicked(attribute, value),
    onOpen: () => {
      connectionStatus = "";
      setStatus(connectionStatus);
    },
    onClose: () => {
      connectionStatus = `disconnected (${wsUrl})`;
      setStatus(connectionStatus);
    },
    onError: () => {
      connectionStatus = `connection error (${wsUrl})`;
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
      // Point events (contact sequences) are only "active" at one exact instant, so scrubbing them needs a
      // trailing window; interval data reads naturally as "at this moment".
      timelineKind.value = domain.instantaneous ? "window" : "instant";
      timeDomain = domain;
      // Calendar times are windowed in days; plain numbers in the data's own units.
      const tenth = (domain.max - domain.min) / 10 || 1;
      timelineWindowUnit.textContent = domain.unit === "epoch_seconds" ? "days" : "";
      timelineWindow.value = String(Number((domain.unit === "epoch_seconds" ? Math.max(tenth / SECONDS_PER_DAY, 1 / 24) : tenth).toPrecision(2)));
      timelineWindowLabel.hidden = timelineKind.value !== "window";
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
      // Atlas and ribbon legends follow panel reading order.
      for (const slice of slices) {
        const row = document.createElement("div");
        row.className = "row";
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = rgbaCss(slice.color);
        const text = document.createElement("span");
        text.textContent = slice.label;
        const count = document.createElement("span");
        count.className = "count";
        count.textContent = slice.count.toLocaleString();
        count.title = "connectors in this slice";
        row.append(swatch, text, count);
        stackLegendEl.appendChild(row);
      }
    },
    onColorLegend: (target, legend) => renderLegend(target === "node" ? nodeColorLegendEl : edgeColorLegendEl, target === "node" ? "Node color" : "Edge color", legend, timeUnit),
  });
  tools = mountTools(toolsEl, handle, (id) => handle.getNodeKey(id) ?? String(id));
  appearance = mountAppearance(appearanceEl, handle, () => tools?.getSelection() ?? []);
  const search = document.getElementById("node-search") as HTMLInputElement;
  const results = document.getElementById("search-results")!;
  const inspector = document.getElementById("node-inspector")!;
  let searchTimer: ReturnType<typeof setTimeout>;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      results.replaceChildren();
      if (!search.value.trim()) return;
      const matches = handle.searchNodes(search.value);
      if (!matches.length) results.textContent = "No matching nodes";
      for (const node of matches) {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = String(node.key);
        button.addEventListener("click", () => {
          handle.focusNeighborhood(node.id);
          const info = handle.inspectNode(node.id)!;
          inspector.hidden = false;
          inspector.textContent = `${String(node.key)}\n${info.neighbors} neighbors · ${info.connectors} incident connectors\n${Object.entries(node.attrs).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`).join("\n")}\nShowing this node’s relationships. Layer/time filters still apply in overview.`;
          results.replaceChildren();
        });
        const pick = document.createElement("button");
        pick.type = "button"; pick.textContent = "＋"; pick.title = "Add to selection";
        pick.setAttribute("aria-label", `Select ${String(node.key)}`);
        pick.addEventListener("click", () => tools?.nodeClicked(node.id));
        const row = document.createElement("div");
        row.className = "result-row";
        row.append(button, pick);
        results.appendChild(row);
      }
      if(matches.length === 20) {
        const hint = document.createElement("div"); hint.textContent = "First 20 matches — refine your search."; results.appendChild(hint);
      }
    }, 120);
  });
  document.getElementById("fit-view")!.addEventListener("click", () => handle.fitView());
  document.getElementById("clear-focus")!.addEventListener("click", () => {
    handle.focusNeighborhood(null); inspector.hidden = true; search.value = ""; results.replaceChildren();
  });
  window.addEventListener("beforeunload", () => handle.dispose());

  // Exposed for local debugging / automated visual verification only —
  // not part of the public viz-core API surface.
  (window as unknown as { __plexgraphHandle: typeof handle }).__plexgraphHandle = handle;

  function applyTimeFilter(): void {
    const t = Number(timelineSlider.value);
    const mode = timelineKind.value as TimeMode;
    const length = Math.max(0, Number(timelineWindow.value) || 0);
    const calendar = timeDomain?.unit === "epoch_seconds";
    handle.setTimeFilter(t, { mode, window: calendar ? length * SECONDS_PER_DAY : length });
    const span = timeDomain ? timeDomain.max - timeDomain.min : Infinity;
    const shown = formatTime(t, timeDomain?.unit, span);
    timelineValue.textContent = mode === "window" ? `${shown} (last ${length}${calendar ? " d" : ""})` : mode === "cumulative" ? `≤ ${shown}` : shown;
  }

  timelineToggle.addEventListener("click", () => {
    if (timelineMode === "all") {
      timelineMode = "scrub";
      timelineToggle.textContent = "Show all";
      applyTimeFilter();
    } else {
      timelineMode = "all";
      timelineToggle.textContent = "All time";
      handle.setTimeFilter(null);
      timelineValue.textContent = "";
    }
  });

  timelineSlider.addEventListener("input", () => {
    if (timelineMode === "scrub") applyTimeFilter();
  });
  timelineKind.addEventListener("change", () => {
    timelineWindowLabel.hidden = timelineKind.value !== "window";
    if (timelineMode === "scrub") applyTimeFilter();
  });
  timelineWindow.addEventListener("input", () => {
    if (timelineMode === "scrub") applyTimeFilter();
  });

  function applyRibbon(): void {
    const buckets = Math.min(24, Math.max(2, Math.floor(Number(ribbonBuckets.value) || 6)));
    ribbonBuckets.value = String(buckets);
    handle.setStackMode("time", { timeBuckets: buckets, timeSplit: ribbonSplit.value as TimeSplit, layout: "ribbon" });
  }
  ribbonBuckets.addEventListener("change", applyRibbon);
  ribbonSplit.addEventListener("change", applyRibbon);

  function setViewMode(mode: "flat" | "stack-layer" | "stack-time"): void {
    viewFlatBtn.setAttribute("aria-pressed", String(mode === "flat"));
    viewStackLayerBtn.setAttribute("aria-pressed", String(mode === "stack-layer"));
    viewStackTimeBtn.setAttribute("aria-pressed", String(mode === "stack-time"));

    // Stacked mode always shows every layer/time-slice at once — the
    // flat view's filter controls (which narrow down to one moment/subset)
    // don't apply there, so hide them rather than leave them present but
    // inert. The time ribbon has its own bucket controls in the same place.
    ribbonOptionsEl.hidden = mode !== "stack-time";
    toolsEl.hidden = mode !== "flat"; // its filters, selection and colouring act on the flat view; free the space for the legend
    if (mode === "flat") {
      if (hasTimeDomain) timelineEl.hidden = false;
      if (hasLayers) layersEl.hidden = false;
      handle.setStackMode(null);
    } else {
      timelineEl.hidden = true;
      layersEl.hidden = true;
      if (mode === "stack-time") applyRibbon();
      else handle.setStackMode("layer", { layout: "atlas" });
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
        console.error("[plexgraph] export failed", err);
      }
    });
  });
}
