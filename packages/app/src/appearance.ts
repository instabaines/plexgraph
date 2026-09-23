import { colormapNames, paletteNames, parseColor, rgbaToHex, type AttributeSummary, type ColorEncoding, type NodeShape, type SizeEncoding, type StyleSpec, type ViewerHandle } from "@plexgraph/viz-core";

const DEFAULT_NODE_COLOR = "#2a8bf2";
const DEFAULT_EDGE_COLOR = "#9999a6";
const SHAPES: NodeShape[] = ["circle", "square", "triangle", "diamond", "cross"];

// Matches plexgraph_bridge.style._DASH_PATTERNS on the Python side: named patterns as [on1, off1, on2, off2] pixel
// lengths. Kept in sync manually since the two sides speak the resolved array over the wire, not the name.
const DASH_PATTERNS: Record<string, number[] | null> = {
  solid: null,
  dashed: [8, 5, 0, 0],
  dotted: [1.5, 4, 0, 0],
  dashdot: [8, 4, 1.5, 4],
};

type Child = Node | string;
function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { className?: string } = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function field(text: string, control: HTMLElement, hidden = false): HTMLLabelElement {
  const label = el("label", { className: "field" }, el("span", { textContent: text }), control);
  label.hidden = hidden;
  return label;
}

function select(aria: string, options: [string, string][] = []): HTMLSelectElement {
  const s = el("select");
  s.setAttribute("aria-label", aria);
  fill(s, options);
  return s;
}

function fill(s: HTMLSelectElement, options: [string, string][]): void {
  const keep = s.value;
  s.replaceChildren(...options.map(([value, text]) => el("option", { value, textContent: text })));
  if (options.some(([v]) => v === keep)) s.value = keep;
}

function slider(aria: string, min: number, max: number, step: number, value: number): HTMLInputElement {
  const s = el("input", { type: "range", min: String(min), max: String(max), step: String(step), value: String(value) });
  s.setAttribute("aria-label", aria);
  return s;
}

function colorInput(aria: string, value: string): HTMLInputElement {
  const c = el("input", { type: "color", value });
  c.setAttribute("aria-label", aria);
  return c;
}

function checkbox(aria: string): HTMLInputElement {
  const c = el("input", { type: "checkbox" });
  c.setAttribute("aria-label", aria);
  return c;
}

function button(text: string, aria = text): HTMLButtonElement {
  const b = el("button", { type: "button", textContent: text });
  b.setAttribute("aria-label", aria);
  return b;
}

const modeOptions = (base: [string, string][], attrs: AttributeSummary[]): [string, string][] => [
  ...base,
  ...attrs.filter((a) => a.kind !== "text").map((a): [string, string] => [`attr:${a.name}`, `Attribute: ${a.name}`]),
];

/** The Appearance panel: colors, sizes, shapes, opacity, curvature and labels, applied live. */
export function mountAppearance(root: HTMLElement, handle: ViewerHandle, getSelection: () => number[]) {
  let nodeAttrs: AttributeSummary[] = [];
  let edgeAttrs: AttributeSummary[] = [];
  let info = { hasWeights: false, hasTime: false };
  let syncing = false;
  const message = el("div", { className: "note", ariaLive: "polite" });

  // ---- nodes
  const nodeMode = select("Node color mode");
  const nodeColor = colorInput("Node color", DEFAULT_NODE_COLOR);
  const nodePalette = select("Node palette", paletteNames().map((n): [string, string] => [n, n]));
  const nodeCmap = select("Node colormap", colormapNames().map((n): [string, string] => [n, n]));
  const nodeReverse = checkbox("Reverse node colormap");
  const nodeBuckets = el("input", { type: "number", min: "1", max: "24", step: "1", value: "6" });
  nodeBuckets.setAttribute("aria-label", "Node time buckets");
  const nodeSplit = select("Node time split", [["time", "Equal time"], ["events", "Equal events"]]);
  const nodeTimeMap = select("Node time colors", [["ribbon", "Ribbon colors"], ...colormapNames().map((n): [string, string] => [n, n])]);
  const nodeSize = slider("Node size", 2, 40, 1, 10);
  const nodeShape = select("Node shape");
  const nodeOpacity = slider("Node opacity", 0.05, 1, 0.05, 1);
  const outlineWidth = slider("Outline width", 0, 4, 0.5, 0);
  const outlineColor = colorInput("Outline color", "#ffffff");

  // ---- edges
  const edgeMode = select("Edge color mode");
  const edgeColor = colorInput("Edge color", DEFAULT_EDGE_COLOR);
  const edgePalette = select("Edge palette", paletteNames().map((n): [string, string] => [n, n]));
  const edgeCmap = select("Edge colormap", colormapNames().map((n): [string, string] => [n, n]));
  const edgeReverse = checkbox("Reverse edge colormap");
  const edgeBuckets = el("input", { type: "number", min: "1", max: "24", step: "1", value: "6" });
  edgeBuckets.setAttribute("aria-label", "Edge time buckets");
  const edgeSplit = select("Edge time split", [["time", "Equal time"], ["events", "Equal events"]]);
  const edgeTimeMap = select("Edge time colors", [["ribbon", "Ribbon colors"], ...colormapNames().map((n): [string, string] => [n, n])]);
  const edgeWidth = slider("Edge width", 0.5, 8, 0.5, 1.5);
  const widthByWeight = checkbox("Edge width by weight");
  const edgeOpacity = slider("Edge opacity", 0.05, 1, 0.05, 1);
  const edgeCurve = slider("Edge curvature", -0.6, 0.6, 0.05, 0);
  const arrowSize = slider("Arrow size", 0.5, 4, 0.1, 1);
  const edgeLineStyle = select("Line style", [["solid", "Solid"], ["dashed", "Dashed"], ["dotted", "Dotted"], ["dashdot", "Dash-dot"]]);

  // ---- labels, page, painting
  const labelMode = select("Labels", [["hover", "On hover"], ["all", "All nodes"], ["none", "None"]]);
  const labelSize = el("input", { type: "number", min: "6", max: "40", step: "1", value: "11" });
  labelSize.setAttribute("aria-label", "Label size");
  const labelHalo = checkbox("Label halo");
  const background = colorInput("Background", "#fafafa");
  const paintColor = colorInput("Paint color", "#d62728");
  const paintButton = button("Paint selected nodes");
  const clearPaint = button("Clear painting");
  const resetButton = button("Reset appearance");

  const nodeColorRows = {
    single: field("Color", nodeColor), palette: field("Palette", nodePalette), cmap: field("Colormap", nodeCmap),
    reverse: el("label", { className: "check" }, nodeReverse, "reverse"),
    buckets: field("Buckets", nodeBuckets), split: field("Split", nodeSplit), timeMap: field("Colors", nodeTimeMap),
  };
  const edgeColorRows = {
    single: field("Color", edgeColor), palette: field("Palette", edgePalette), cmap: field("Colormap", edgeCmap),
    reverse: el("label", { className: "check" }, edgeReverse, "reverse"),
    buckets: field("Buckets", edgeBuckets), split: field("Split", edgeSplit), timeMap: field("Colors", edgeTimeMap),
  };
  const widthByWeightRow = el("label", { className: "check" }, widthByWeight, "width by weight");

  const section = (title: string, open: boolean, ...body: HTMLElement[]) => {
    const d = el("details", {}, el("summary", { textContent: title }), ...body);
    d.open = open;
    return d;
  };
  root.replaceChildren(
    el("div", { className: "panel-title", textContent: "Appearance" }),
    section("Nodes", true,
      field("Color by", nodeMode), ...Object.values(nodeColorRows),
      field("Size", nodeSize), field("Shape", nodeShape), field("Opacity", nodeOpacity),
      field("Outline", outlineWidth), field("Outline color", outlineColor)),
    section("Edges", false,
      field("Color by", edgeMode), ...Object.values(edgeColorRows),
      field("Width", edgeWidth), widthByWeightRow, field("Opacity", edgeOpacity),
      field("Curvature", edgeCurve), field("Arrow size", arrowSize), field("Line style", edgeLineStyle)),
    section("Labels & page", false, field("Labels", labelMode), field("Label size", labelSize), el("label", { className: "check" }, labelHalo, "halo behind text"), field("Background", background)),
    section("Paint nodes", false,
      el("div", { className: "note", textContent: "Select nodes (click them or use +), pick a color, and paint them." }),
      field("Color", paintColor), el("div", { className: "actions" }, paintButton, clearPaint)),
    el("div", { className: "actions" }, resetButton),
    message,
  );

  const attrOf = (attrs: AttributeSummary[], mode: string) => (mode.startsWith("attr:") ? attrs.find((a) => a.name === mode.slice(5)) : undefined);

  function refreshVisibility(): void {
    const show = (rows: typeof nodeColorRows, mode: string, attrs: AttributeSummary[]) => {
      const attr = attrOf(attrs, mode);
      const categorical = attr?.kind === "categorical";
      const continuous = mode === "degree" || mode === "weight" || mode === "time" || attr?.kind === "numeric";
      rows.single.hidden = mode !== "single";
      rows.palette.hidden = !categorical;
      rows.cmap.hidden = !continuous;
      rows.reverse.hidden = !continuous;
      const bucket = mode === "timeBucket";
      rows.buckets.hidden = rows.split.hidden = rows.timeMap.hidden = !bucket;
    };
    show(nodeColorRows, nodeMode.value, nodeAttrs);
    show(edgeColorRows, edgeMode.value, edgeAttrs);
  }

  function attempt(action: () => void): void {
    if (syncing) return;
    try {
      action();
      message.textContent = "";
    } catch (err) {
      message.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  function colorEncoding(mode: string, attrs: AttributeSummary[], parts: { color: HTMLInputElement; palette: HTMLSelectElement; cmap: HTMLSelectElement; reverse: HTMLInputElement; buckets: HTMLInputElement; split: HTMLSelectElement; timeMap: HTMLSelectElement }): ColorEncoding | null {
    if (mode === "custom") return null;
    if (mode === "single") return parts.color.value;
    const colormap = parts.cmap.value, reverse = parts.reverse.checked;
    if (mode === "degree" || mode === "weight" || mode === "time") return { kind: mode, colormap, reverse };
    if (mode === "timeBucket") {
      return { kind: "timeBucket", buckets: Math.max(1, Math.min(24, Number(parts.buckets.value) || 6)), split: parts.split.value as "time" | "events", ...(parts.timeMap.value === "ribbon" ? {} : { colormap: parts.timeMap.value }) };
    }
    const attr = attrOf(attrs, mode);
    if (!attr) return null;
    return attr.kind === "numeric"
      ? { kind: "attribute", attribute: attr.name, scale: "continuous", colormap, reverse }
      : { kind: "attribute", attribute: attr.name, scale: "categorical", palette: parts.palette.value };
  }

  const applyNodeColor = () => attempt(() => {
    refreshVisibility();
    const enc = colorEncoding(nodeMode.value, nodeAttrs, { color: nodeColor, palette: nodePalette, cmap: nodeCmap, reverse: nodeReverse, buckets: nodeBuckets, split: nodeSplit, timeMap: nodeTimeMap });
    if (enc !== null) handle.setStyle({ node: { color: enc } });
  });
  const applyEdgeColor = () => attempt(() => {
    refreshVisibility();
    const enc = colorEncoding(edgeMode.value, edgeAttrs, { color: edgeColor, palette: edgePalette, cmap: edgeCmap, reverse: edgeReverse, buckets: edgeBuckets, split: edgeSplit, timeMap: edgeTimeMap });
    if (enc !== null) handle.setStyle({ edge: { color: enc } });
  });
  const applyShape = () => attempt(() => {
    const v = nodeShape.value;
    if (v === "custom") return;
    handle.setStyle({ node: { shape: v.startsWith("attr:") ? { kind: "attribute", attribute: v.slice(5) } : (v as NodeShape) } });
  });
  const applyOutline = () => attempt(() => {
    const width = Number(outlineWidth.value);
    handle.setStyle({ node: { outline: width > 0 ? { color: outlineColor.value, width } : null } });
  });
  const applyEdgeWidth = () => attempt(() => {
    const encoding: SizeEncoding = widthByWeight.checked ? { kind: "weight", range: [Math.max(0.5, Number(edgeWidth.value) / 2), Number(edgeWidth.value) * 2 + 1] } : Number(edgeWidth.value);
    handle.setStyle({ edge: { width: encoding } });
  });
  const applyLabels = () => attempt(() => {
    handle.setStyle({ node: { label: { mode: labelMode.value as "hover" | "all" | "none", fontSize: Number(labelSize.value) || 11, halo: labelHalo.checked } } });
  });

  nodeMode.addEventListener("change", applyNodeColor);
  for (const c of [nodeColor, nodePalette, nodeCmap, nodeReverse, nodeBuckets, nodeSplit, nodeTimeMap]) c.addEventListener("change", applyNodeColor);
  nodeColor.addEventListener("input", applyNodeColor);
  edgeMode.addEventListener("change", applyEdgeColor);
  for (const c of [edgeColor, edgePalette, edgeCmap, edgeReverse, edgeBuckets, edgeSplit, edgeTimeMap]) c.addEventListener("change", applyEdgeColor);
  edgeColor.addEventListener("input", applyEdgeColor);
  nodeSize.addEventListener("input", () => attempt(() => handle.setStyle({ node: { size: Number(nodeSize.value) } })));
  nodeShape.addEventListener("change", applyShape);
  nodeOpacity.addEventListener("input", () => attempt(() => handle.setStyle({ node: { opacity: Number(nodeOpacity.value) } })));
  outlineWidth.addEventListener("input", applyOutline);
  outlineColor.addEventListener("input", applyOutline);
  edgeWidth.addEventListener("input", applyEdgeWidth);
  widthByWeight.addEventListener("change", applyEdgeWidth);
  edgeOpacity.addEventListener("input", () => attempt(() => handle.setStyle({ edge: { opacity: Number(edgeOpacity.value) } })));
  edgeCurve.addEventListener("input", () => attempt(() => handle.setStyle({ edge: { curvature: Number(edgeCurve.value) } })));
  arrowSize.addEventListener("input", () => attempt(() => handle.setStyle({ edge: { arrowScale: Number(arrowSize.value) } })));
  edgeLineStyle.addEventListener("change", () => attempt(() => handle.setStyle({ edge: { dash: DASH_PATTERNS[edgeLineStyle.value] ?? null } })));
  labelMode.addEventListener("change", applyLabels);
  labelSize.addEventListener("change", applyLabels);
  labelHalo.addEventListener("change", applyLabels);
  background.addEventListener("input", () => attempt(() => handle.setStyle({ background: background.value })));
  paintButton.addEventListener("click", () => attempt(() => {
    const ids = getSelection();
    if (!ids.length) throw new Error("Select some nodes first: click them, or use + beside a search result.");
    handle.paintNodes(ids, paintColor.value);
  }));
  clearPaint.addEventListener("click", () => attempt(() => handle.clearPaint()));
  resetButton.addEventListener("click", () => {
    handle.resetStyle();
    message.textContent = "";
  });

  /** Show the current style in the controls (also called when Python changes it). */
  function sync(): void {
    syncing = true;
    try {
      const spec: StyleSpec = handle.getStyle();
      const node = spec.node ?? {}, edge = spec.edge ?? {};
      const base = handle.getStyleDefaults();
      const setMode = (s: HTMLSelectElement, enc: ColorEncoding | null | undefined, color: HTMLInputElement, parts: { cmap: HTMLSelectElement; reverse: HTMLInputElement; palette: HTMLSelectElement }, base: string) => {
        if (enc === undefined || enc === null) { s.value = "single"; color.value = base; return; }
        if (typeof enc === "string" || Array.isArray(enc) || ArrayBuffer.isView(enc)) { s.value = "single"; color.value = rgbaToHex(parseColor(enc as string)); return; }
        const e = enc as Exclude<ColorEncoding, string | ArrayLike<number>>;
        if (e.kind === "constant") { s.value = "single"; color.value = rgbaToHex(parseColor(e.color)); return; }
        if (e.kind === "attribute") s.value = `attr:${e.attribute}`;
        else if (e.kind === "degree" || e.kind === "weight" || e.kind === "time" || e.kind === "timeBucket") s.value = e.kind;
        else { ensureCustom(s); s.value = "custom"; return; }
        if (!Array.from(s.options).some((o) => o.value === s.value)) { ensureCustom(s); s.value = "custom"; return; }
        if ("colormap" in e && e.colormap) parts.cmap.value = e.colormap;
        if ("reverse" in e) parts.reverse.checked = !!e.reverse;
        if (e.kind === "attribute" && typeof e.palette === "string") parts.palette.value = e.palette;
      };
      setMode(nodeMode, node.color, nodeColor, { cmap: nodeCmap, reverse: nodeReverse, palette: nodePalette }, rgbaToHex(base.nodeColor));
      setMode(edgeMode, edge.color, edgeColor, { cmap: edgeCmap, reverse: edgeReverse, palette: edgePalette }, rgbaToHex(base.edgeColor));
      if (typeof node.size === "number") nodeSize.value = String(node.size);
      else if (node.size == null) nodeSize.value = String(base.nodeSize);
      const shape = node.shape;
      if (shape === undefined || shape === null) nodeShape.value = "circle";
      else if (typeof shape === "string") nodeShape.value = shape;
      else if (shape.kind === "attribute") nodeShape.value = `attr:${shape.attribute}`;
      else if (shape.kind === "constant") nodeShape.value = shape.shape;
      else { ensureCustom(nodeShape); nodeShape.value = "custom"; }
      nodeOpacity.value = String(node.opacity ?? 1);
      outlineWidth.value = String(node.outline?.width ?? 0);
      outlineColor.value = node.outline?.color !== undefined ? rgbaToHex(parseColor(node.outline.color)) : "#ffffff";
      if (typeof edge.width === "number") { edgeWidth.value = String(edge.width); widthByWeight.checked = false; }
      else if (edge.width && typeof edge.width === "object" && edge.width.kind === "weight") widthByWeight.checked = true;
      else if (edge.width == null) { edgeWidth.value = String(base.edgeWidth); widthByWeight.checked = false; }
      edgeOpacity.value = String(edge.opacity ?? 1);
      edgeCurve.value = String(edge.curvature ?? 0);
      arrowSize.value = String(edge.arrowScale ?? 1);
      const dash = edge.dash ?? null;
      const namedDash = Object.entries(DASH_PATTERNS).find(([, pattern]) => JSON.stringify(pattern) === JSON.stringify(dash));
      edgeLineStyle.value = namedDash ? namedDash[0] : "solid";
      labelMode.value = node.label?.mode ?? "hover";
      labelSize.value = String(node.label?.fontSize ?? 11);
      labelHalo.checked = !!node.label?.halo;
      background.value = rgbaToHex(spec.background != null ? parseColor(spec.background) : base.background);
      refreshVisibility();
    } finally {
      syncing = false;
    }
  }

  function ensureCustom(s: HTMLSelectElement): void {
    if (!Array.from(s.options).some((o) => o.value === "custom")) s.append(el("option", { value: "custom", textContent: "(set from Python)" }));
  }

  return {
    /** Rebuild the choices from the freshly loaded graph. */
    graphLoaded(): void {
      nodeAttrs = handle.getNodeAttributes();
      edgeAttrs = handle.getEdgeAttributes();
      info = handle.getGraphInfo();
      fill(nodeMode, modeOptions([["single", "One color"], ["degree", "Degree"], ...(info.hasTime ? [["timeBucket", "Time bucket (first activity)"] as [string, string]] : [])], nodeAttrs));
      fill(edgeMode, modeOptions([["single", "One color"], ...(info.hasWeights ? [["weight", "Weight"] as [string, string]] : []), ...(info.hasTime ? [["time", "Time (gradient)"] as [string, string], ["timeBucket", "Time bucket"] as [string, string]] : [])], edgeAttrs));
      fill(nodeShape, [...SHAPES.map((s): [string, string] => [s, s]), ...nodeAttrs.filter((a) => a.kind === "categorical").map((a): [string, string] => [`attr:${a.name}`, `By ${a.name}`])]);
      widthByWeightRow.hidden = !info.hasWeights;
      sync();
    },
    /** The style changed (from this panel, the quick controls, or Python): show it. */
    styleChanged(): void {
      sync();
    },
  };
}
