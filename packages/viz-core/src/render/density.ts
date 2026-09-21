/** Aggregates nodes into weighted "super-points". Cells/groups represent populations, not nodes.
 * Links are deduplicated between pairs and carry how many underlying connectors they stand for; this is
 * an overview, not a substitute for inspecting direction or individual relationships. */
export interface DensityPoint { x: number; y: number; count: number }
export interface DensityResult {
  points: DensityPoint[];
  /** [a, b, weight]: weight is the number of connectors between the two points. */
  links: [number, number, number][];
  /** For each input node, the index of the point it was aggregated into. */
  membership: Uint32Array;
}
type Edge = { source: number; target: number };

function build(positions: Float32Array, edges: Edge[], groupOf: (node: number) => number): DensityResult {
  const membership = new Uint32Array(positions.length / 2);
  const ids = new Map<number, number>();
  const points: DensityPoint[] = [];
  for (let n = 0; n < membership.length; n++) {
    const key = groupOf(n);
    let id = ids.get(key);
    if (id === undefined) { id = points.length; ids.set(key, id); points.push({ x: 0, y: 0, count: 0 }); }
    membership[n] = id;
    points[id].x += positions[n * 2]; points[id].y += positions[n * 2 + 1]; points[id].count++;
  }
  for (const p of points) { p.x /= p.count; p.y /= p.count; }
  const weights = new Map<number, number>();
  for (const edge of edges) {
    const a = membership[edge.source], b = membership[edge.target];
    if (a === b) continue;
    const key = Math.min(a, b) * points.length + Math.max(a, b);
    weights.set(key, (weights.get(key) ?? 0) + 1);
  }
  const links: [number, number, number][] = [];
  for (const [key, weight] of weights) links.push([Math.floor(key / points.length), key % points.length, weight]);
  return { points, links, membership };
}

/** Aggregate by a spatial grid of `resolution` x `resolution` cells over the bounding square. */
export function aggregateDensity(positions: Float32Array, edges: Edge[], resolution = 48): DensityResult {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < positions.length; i += 2) {
    minX = Math.min(minX, positions[i]); maxX = Math.max(maxX, positions[i]);
    minY = Math.min(minY, positions[i + 1]); maxY = Math.max(maxY, positions[i + 1]);
  }
  const span = Math.max(maxX - minX, maxY - minY, 1e-9);
  return build(positions, edges, n => {
    const x = Math.min(resolution - 1, Math.floor((positions[n * 2] - minX) / span * resolution));
    const y = Math.min(resolution - 1, Math.floor((positions[n * 2 + 1] - minY) / span * resolution));
    return y * resolution + x;
  });
}

/** Aggregate by an explicit group id per node (for example a community attribute). */
export function aggregateGroups(positions: Float32Array, edges: Edge[], groupIds: ArrayLike<number>): DensityResult {
  return build(positions, edges, n => groupIds[n]);
}
