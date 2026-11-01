import type { AckMessage, ViewerHandle } from "@plexgraph/viz-core";

type Child = Node | string;
function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { className?: string } = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function field(text: string, control: HTMLElement): HTMLLabelElement {
  return el("label", { className: "field" }, el("span", { textContent: text }), control);
}

function select(aria: string, options: [string, string][]): HTMLSelectElement {
  const s = el("select", {}, ...options.map(([value, text]) => el("option", { value, textContent: text })));
  s.setAttribute("aria-label", aria);
  return s;
}

function button(text: string, aria = text): HTMLButtonElement {
  const b = el("button", { type: "button", textContent: text });
  b.setAttribute("aria-label", aria);
  return b;
}

export interface MeasuresPanel {
  /** Feed every AckMessage the viewer receives (main.ts's MountViewerOptions.onFileAck); ignores
   * anything that isn't a reply to runMeasure(). */
  handleAck(ack: AckMessage): void;
}

const MEASURES: [string, string][] = [
  ["community", "Community"],
  ["eigenvector_centrality", "Eigenvector centrality"],
  ["pagerank_centrality", "PageRank centrality"],
];

/** The Measures panel: run one of the sidecar's analysis algorithms and recolor (and, for the
 * centralities, resize) the graph by its result. See BridgeServer._run_measure. */
export function mountMeasures(root: HTMLElement, handle: ViewerHandle): MeasuresPanel {
  const measure = select("Measure", MEASURES);
  const run = button("Run");
  const message = el("div", { className: "note", ariaLive: "polite" });

  root.replaceChildren(
    el("div", { className: "panel-title", textContent: "Measures" }),
    field("Algorithm", measure),
    el("div", { className: "actions" }, run),
    message,
  );

  run.addEventListener("click", () => {
    run.disabled = true;
    run.textContent = "Running…";
    message.textContent = "";
    try {
      handle.runMeasure(measure.value, {});
    } catch (err) {
      run.disabled = false;
      run.textContent = "Run";
      message.textContent = err instanceof Error ? err.message : String(err);
    }
  });

  return {
    handleAck(ack: AckMessage): void {
      if (ack.of !== "run_measure") return;
      run.disabled = false;
      run.textContent = "Run";
      message.textContent = ack.ok ? "" : ack.error ?? "the measure could not be computed";
    },
  };
}
