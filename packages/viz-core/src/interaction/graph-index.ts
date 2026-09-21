import type { WireNode, WireConnector } from "../ir/types";

export interface AttributeSummary {
  name: string;
  /** numeric: every value is a finite number; categorical: few distinct values; text: too many to list. */
  kind: "numeric" | "categorical" | "text";
  distinct: number;
  /** Most common values first (categorical only), capped at `valueLimit`. */
  values?: { value: string; count: number }[];
  min?: number;
  max?: number;
}

export interface NodeFilter {
  attribute?: string;
  /** Keep nodes whose attribute (compared as text) is one of these. */
  values?: (string | number)[];
  /** Keep nodes whose numeric attribute lies in [min, max]. */
  range?: [number, number];
  minDegree?: number;
  maxDegree?: number;
}

const MAX_CATEGORIES = 200;

/** Per-attribute value summary of any list of items with `attrs` (nodes or connectors). */
export function summarizeAttributes(items: { attrs: Record<string, unknown> }[], valueLimit = 30): AttributeSummary[] {
  const names = new Set<string>();
  for (const node of items) for (const key of Object.keys(node.attrs)) names.add(key);
  const out: AttributeSummary[] = [];
  for (const name of names) {
    const counts = new Map<string, number>();
    let numeric = true, min = Infinity, max = -Infinity;
    for (const node of items) {
      const v = node.attrs[name];
      if (v === undefined || v === null) continue;
      if (typeof v !== "number" || !Number.isFinite(v)) numeric = false;
      else { min = Math.min(min, v); max = Math.max(max, v); }
      const key = typeof v === "object" ? JSON.stringify(v) : String(v);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    if (!counts.size) continue;
    if (numeric && counts.size > 12) out.push({ name, kind: "numeric", distinct: counts.size, min, max });
    else if (counts.size <= MAX_CATEGORIES) {
      const values = Array.from(counts, ([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value)).slice(0, valueLimit);
      out.push({ name, kind: "categorical", distinct: counts.size, values, ...(numeric ? { min, max } : {}) });
    } else out.push({ name, kind: "text", distinct: counts.size });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/** Incidence index stays linear in hyperedge memberships; never expands cliques. */
export class GraphIndex {
  private incident: number[][];
  private degrees: Int32Array | null = null;
  constructor(readonly nodes: WireNode[], readonly connectors: WireConnector[]) {
    this.incident = Array.from({length: nodes.length}, () => []);
    connectors.forEach((c, i) => c.endpoints.forEach(n => this.incident[n]?.push(i)));
  }
  search(query: string, limit = 20): WireNode[] {
    const q = query.trim().toLocaleLowerCase();
    if (!q) return [];
    const found: WireNode[] = [];
    for (const node of this.nodes) {
      if (String(node.key).toLocaleLowerCase().includes(q)) found.push(node);
      if (found.length >= limit) break;
    }
    return found;
  }
  neighborhood(nodeId: number): { nodes: Set<number>; connectors: Set<number> } {
    const nodes = new Set<number>();
    const connectors = new Set<number>();
    if (!this.nodes[nodeId]) return {nodes, connectors};
    nodes.add(nodeId);
    for (const i of this.incident[nodeId]) {
      const c = this.connectors[i];
      connectors.add(c.id);
      c.endpoints.forEach(n => nodes.add(n));
    }
    return {nodes, connectors};
  }

  /** Distinct neighbours of a node; a hyperedge contributes every other member once. */
  degree(nodeId: number): number {
    if (!this.degrees) this.degrees = new Int32Array(this.nodes.length).fill(-1);
    if (this.degrees[nodeId] >= 0) return this.degrees[nodeId];
    const seen = new Set<number>();
    for (const i of this.incident[nodeId] ?? []) for (const n of this.connectors[i].endpoints) if (n !== nodeId) seen.add(n);
    return (this.degrees[nodeId] = seen.size);
  }

  /** Per-attribute value summary used to build colour, size and filter controls. */
  attributeSummary(valueLimit = 30): AttributeSummary[] {
    return summarizeAttributes(this.nodes, valueLimit);
  }

  /** Ids of nodes passing every criterion of the filter (all criteria combine with AND). */
  matchNodes(filter: NodeFilter): Set<number> {
    const wanted = filter.values ? new Set(filter.values.map(String)) : null;
    const out = new Set<number>();
    for (const node of this.nodes) {
      if (filter.attribute !== undefined) {
        const v = node.attrs[filter.attribute];
        if (v === undefined || v === null) continue;
        if (wanted && !wanted.has(typeof v === "object" ? JSON.stringify(v) : String(v))) continue;
        if (filter.range && !(typeof v === "number" && v >= filter.range[0] && v <= filter.range[1])) continue;
      }
      if (filter.minDegree !== undefined || filter.maxDegree !== undefined) {
        const d = this.degree(node.id);
        if (filter.minDegree !== undefined && d < filter.minDegree) continue;
        if (filter.maxDegree !== undefined && d > filter.maxDegree) continue;
      }
      out.add(node.id);
    }
    return out;
  }

  /** Fewest-hops route between two nodes (ignoring direction); a hyperedge is one hop between any two members. */
  shortestPath(from: number, to: number): { nodes: number[]; connectors: number[] } | null {
    if (!this.nodes[from] || !this.nodes[to]) return null;
    if (from === to) return { nodes: [from], connectors: [] };
    const parent = new Map<number, { node: number; connector: number }>();
    const scanned = new Set<number>();
    let frontier = [from];
    const visited = new Set<number>([from]);
    while (frontier.length && !visited.has(to)) {
      const next: number[] = [];
      for (const u of frontier) {
        for (const i of this.incident[u]) {
          if (scanned.has(i)) continue;
          scanned.add(i);
          for (const v of this.connectors[i].endpoints) {
            if (visited.has(v)) continue;
            visited.add(v);
            parent.set(v, { node: u, connector: this.connectors[i].id });
            next.push(v);
          }
        }
      }
      frontier = next;
    }
    if (!visited.has(to)) return null;
    const nodes = [to], connectors: number[] = [];
    for (let cur = to; cur !== from;) {
      const step = parent.get(cur)!;
      connectors.push(step.connector);
      nodes.push(step.node);
      cur = step.node;
    }
    return { nodes: nodes.reverse(), connectors: connectors.reverse() };
  }
}
