import type { AttributeSummary, NodeFilter, ViewerHandle } from "@hyperloom/viz-core";

const MAX_SELECTED = 8;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { className?: string } = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function labelled(text: string, control: HTMLElement): HTMLLabelElement {
  return el("label", { className: "field" }, el("span", {}, text), control);
}

function select(label: string, options: [string, string][]): HTMLSelectElement {
  const s = el("select");
  s.setAttribute("aria-label", label);
  for (const [value, text] of options) s.append(el("option", { value, textContent: text }));
  return s;
}

/** Style, filter, selection/path and zoom controls for a mounted viewer. */
export function mountTools(root: HTMLElement, handle: ViewerHandle, keyOf: (id: number) => string) {
  let attributes: AttributeSummary[] = [];
  let selected: number[] = [];

  const colorBy = select("Color by", []);
  const sizeBy = select("Size by", []);
  const groupBy = select("Group by", []);
  const filterAttr = select("Filter attribute", []);
  const valueBox = el("div", { className: "values" });
  const rangeBox = el("div", { className: "range" });
  const minDegree = el("input", { type: "number", min: "0", step: "1", value: "0" });
  minDegree.setAttribute("aria-label", "Minimum degree");
  const shown = el("div", { className: "note", ariaLive: "polite" });
  const selectionList = el("div", { className: "chips" });
  const pathButton = el("button", { type: "button", textContent: "Find path" });
  pathButton.setAttribute("aria-label", "Find path");
  const neighbourButton = el("button", { type: "button", textContent: "Show neighbourhood" });
  const clearButton = el("button", { type: "button", textContent: "Clear selection" });
  const message = el("div", { className: "note", ariaLive: "polite" });
  const zoomIn = el("button", { type: "button", textContent: "+" });
  zoomIn.setAttribute("aria-label", "Zoom in");
  const zoomOut = el("button", { type: "button", textContent: "−" });
  zoomOut.setAttribute("aria-label", "Zoom out");
  const reset = el("button", { type: "button", textContent: "Reset filter" });

  const section = (title: string, ...body: HTMLElement[]) => {
    const d = el("details", {}, el("summary", { textContent: title }), ...body);
    d.open = true;
    return d;
  };
  root.replaceChildren(
    el("div", { className: "zoom" }, zoomOut, zoomIn),
    section("Style", labelled("Color by", colorBy), labelled("Size by", sizeBy), labelled("Group by", groupBy)),
    section("Filter", labelled("Attribute", filterAttr), valueBox, rangeBox, labelled("Min degree", minDegree), shown, reset),
    section("Select & path", el("div", { className: "note", textContent: "Click nodes to select them (click again to unselect)." }), selectionList,
      el("div", { className: "actions" }, pathButton, neighbourButton, clearButton), message),
  );

  function refreshShown(): void {
    shown.textContent = `Showing ${handle.getVisibleNodeCount().toLocaleString()} nodes`;
  }

  function currentFilter(): NodeFilter | null {
    const filter: NodeFilter = {};
    const attr = attributes.find(a => a.name === filterAttr.value);
    if (attr) {
      filter.attribute = attr.name;
      if (attr.kind === "categorical") {
        const values = Array.from(valueBox.querySelectorAll<HTMLInputElement>("input:checked"), c => c.value);
        if (values.length) filter.values = values;
      } else if (attr.kind === "numeric") {
        const [lo, hi] = Array.from(rangeBox.querySelectorAll<HTMLInputElement>("input"), i => Number(i.value));
        if (Number.isFinite(lo) && Number.isFinite(hi) && (lo > (attr.min ?? lo) || hi < (attr.max ?? hi))) filter.range = [lo, hi];
      }
      if (filter.values === undefined && filter.range === undefined) delete filter.attribute;
    }
    const degree = Number(minDegree.value);
    if (degree > 0) filter.minDegree = degree;
    return Object.keys(filter).length ? filter : null;
  }

  function applyFilter(): void {
    handle.setNodeFilter(currentFilter());
    refreshShown();
  }

  function rebuildFilterControls(): void {
    valueBox.replaceChildren();
    rangeBox.replaceChildren();
    const attr = attributes.find(a => a.name === filterAttr.value);
    if (attr?.kind === "categorical") {
      for (const { value, count } of attr.values ?? []) {
        const box = el("input", { type: "checkbox", value });
        box.addEventListener("change", applyFilter);
        valueBox.append(el("label", {}, box, el("span", { textContent: `${value} (${count})` })));
      }
      if ((attr.values?.length ?? 0) < attr.distinct) valueBox.append(el("div", { className: "note", textContent: `Top ${attr.values!.length} of ${attr.distinct} values` }));
    } else if (attr?.kind === "numeric") {
      const lo = el("input", { type: "number", value: String(attr.min), step: "any" });
      const hi = el("input", { type: "number", value: String(attr.max), step: "any" });
      lo.setAttribute("aria-label", `${attr.name} minimum`);
      hi.setAttribute("aria-label", `${attr.name} maximum`);
      lo.addEventListener("change", applyFilter);
      hi.addEventListener("change", applyFilter);
      rangeBox.append(lo, el("span", { textContent: "to" }), hi);
    }
    applyFilter();
  }

  function renderSelection(): void {
    selectionList.replaceChildren(...selected.map(id => {
      const chip = el("button", { type: "button", className: "chip", textContent: `${keyOf(id)} ×` });
      chip.setAttribute("aria-label", `Unselect ${keyOf(id)}`);
      chip.addEventListener("click", () => toggle(id));
      return chip;
    }));
    pathButton.disabled = selected.length !== 2;
    neighbourButton.disabled = selected.length !== 1;
    clearButton.disabled = selected.length === 0;
    handle.setHighlightedNodes(selected);
  }

  function toggle(id: number): void {
    message.textContent = "";
    if (selected.includes(id)) selected = selected.filter(n => n !== id);
    else if (selected.length < MAX_SELECTED) selected = [...selected, id];
    else message.textContent = `Select at most ${MAX_SELECTED} nodes.`;
    renderSelection();
  }

  colorBy.addEventListener("change", () => handle.setNodeColorBy(colorBy.value || null));
  sizeBy.addEventListener("change", () => handle.setNodeSizeBy(sizeBy.value || null));
  groupBy.addEventListener("change", () => {
    // Grouping is only readable when groups are coloured, so colour by the same attribute unless one is chosen.
    if (groupBy.value && !colorBy.value) { colorBy.value = groupBy.value; handle.setNodeColorBy(groupBy.value); }
    handle.setGroupBy(groupBy.value || null);
    message.textContent = groupBy.value ? `Grouped by ${groupBy.value}. Click a group to expand it.` : "";
  });
  filterAttr.addEventListener("change", rebuildFilterControls);
  minDegree.addEventListener("change", applyFilter);
  reset.addEventListener("click", () => { filterAttr.value = ""; minDegree.value = "0"; rebuildFilterControls(); });
  zoomIn.addEventListener("click", () => handle.zoomBy(1.5));
  zoomOut.addEventListener("click", () => handle.zoomBy(1 / 1.5));
  clearButton.addEventListener("click", () => { selected = []; message.textContent = ""; renderSelection(); });
  neighbourButton.addEventListener("click", () => {
    handle.focusNeighborhood(selected[0]);
    message.textContent = `Showing ${keyOf(selected[0])} and its neighbours. Use “Show all nodes” to return.`;
    refreshShown();
  });
  pathButton.addEventListener("click", () => {
    const [a, b] = selected;
    const route = handle.focusPath(a, b);
    message.textContent = route
      ? `${route.hops} hop${route.hops === 1 ? "" : "s"}: ${route.nodes.map(keyOf).join(" → ")}`
      : `No route between ${keyOf(a)} and ${keyOf(b)}.`;
    refreshShown();
  });

  return {
    /** Rebuild every control from the freshly loaded graph. */
    graphLoaded(): void {
      attributes = handle.getNodeAttributes();
      const usable = attributes.filter(a => a.kind !== "text");
      const set = (s: HTMLSelectElement, head: [string, string], opts: [string, string][]) => {
        s.replaceChildren(...[head, ...opts].map(([value, text]) => el("option", { value, textContent: text })));
      };
      set(colorBy, ["", "None"], usable.filter(a => a.kind === "categorical").map(a => [a.name, a.name]));
      set(groupBy, ["", "None (individual nodes)"], usable.filter(a => a.kind === "categorical").map(a => [a.name, a.name]));
      set(sizeBy, ["", "Uniform"], [["degree", "Degree"], ...attributes.filter(a => a.kind === "numeric").map((a): [string, string] => [a.name, a.name])]);
      set(filterAttr, ["", "— none —"], usable.map(a => [a.name, a.name]));
      selected = [];
      message.textContent = "";
      renderSelection();
      valueBox.replaceChildren();
      rangeBox.replaceChildren();
      refreshShown();
    },
    nodeClicked(id: number): void { toggle(id); },
    getSelection(): number[] { return [...selected]; },
    /** The style changed somewhere else (the Appearance panel, or Python): keep the quick controls honest. */
    syncFromStyle(): void {
      const spec = handle.getStyle();
      const enc = spec.node?.color as { kind?: string; attribute?: string } | string | undefined;
      const colorAttr = typeof enc === "object" && !Array.isArray(enc) && !ArrayBuffer.isView(enc) && enc?.kind === "attribute" ? enc.attribute ?? "" : "";
      colorBy.value = Array.from(colorBy.options).some((o) => o.value === colorAttr) ? colorAttr : "";
      const size = spec.node?.size as { kind?: string; attribute?: string } | number | undefined;
      const sizeKey = typeof size === "object" && size !== null ? (size.kind === "degree" ? "degree" : size.kind === "attribute" ? size.attribute ?? "" : "") : "";
      sizeBy.value = Array.from(sizeBy.options).some((o) => o.value === sizeKey) ? sizeKey : "";
    },
    /** A group was clicked in the grouped overview: show just that group's nodes. */
    groupClicked(attribute: string, value: string): void {
      groupBy.value = "";
      handle.setGroupBy(null);
      filterAttr.value = attribute;
      rebuildFilterControls();
      const box = Array.from(valueBox.querySelectorAll<HTMLInputElement>("input")).find(i => i.value === value);
      if (box) { box.checked = true; applyFilter(); }
      else { handle.setNodeFilter({ attribute, values: [value] }); refreshShown(); }
      message.textContent = `Showing the ${value} group (${attribute}). Reset filter to see everything.`;
    },
  };
}
