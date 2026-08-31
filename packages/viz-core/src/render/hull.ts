// Convex hull + inflation + fan-triangulation for hyperedge rendering.
// A hyperedge's members are grouped visually by drawing a translucent
// polygon around their current positions (recomputed every layout step,
// since positions move) — this is the "convex-hull grouping" rendering
// mode from docs/architecture/plan.md section 5, chosen over bipartite
// expansion as the primary visual for Phase D.

export interface Point {
  x: number;
  y: number;
}

/** Andrew's monotone chain convex hull. Input order doesn't matter;
 * output is the hull vertices in counter-clockwise order with no
 * duplicate closing point. Points are deduplicated first so repeated
 * positions (e.g. two hyperedge members that haven't moved apart yet)
 * don't produce degenerate zero-length hull edges. */
export function convexHull(points: Point[]): Point[] {
  const unique = dedupe(points);
  if (unique.length <= 2) return unique;

  const sorted = [...unique].sort((a, b) => a.x - b.x || a.y - b.y);

  const cross = (o: Point, a: Point, b: Point): number =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);

  const lower: Point[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }

  const upper: Point[] = [];
  for (let i = sorted.length - 1; i >= 0; i--) {
    const p = sorted[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }

  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

function dedupe(points: Point[]): Point[] {
  const seen = new Set<string>();
  const out: Point[] = [];
  for (const p of points) {
    const key = `${p.x},${p.y}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push(p);
    }
  }
  return out;
}

/** Push each hull vertex outward from the centroid by `padding` world
 * units, so the polygon doesn't sit exactly on the node dots — it wraps
 * around them with visible margin. No-op (returns input) for <3 points,
 * since there's no well-defined "outward" for a point or a segment;
 * callers should handle those as a fallback shape instead (see
 * hyperedgeToTriangles). */
export function inflateHull(hull: Point[], padding: number): Point[] {
  if (hull.length < 3) return hull;
  const cx = hull.reduce((s, p) => s + p.x, 0) / hull.length;
  const cy = hull.reduce((s, p) => s + p.y, 0) / hull.length;
  return hull.map((p) => {
    const dx = p.x - cx;
    const dy = p.y - cy;
    const len = Math.hypot(dx, dy) || 1;
    return { x: p.x + (dx / len) * padding, y: p.y + (dy / len) * padding };
  });
}

/** Fan-triangulate a convex polygon (valid for any convex hull): vertex 0
 * paired with every consecutive edge. Returns a flat [x0,y0, x1,y1, x2,y2, ...]
 * array, 3 points (9... actually 6 floats) per triangle, (n-2) triangles
 * for an n-gon. Returns an empty array for <3 points — a hull that thin
 * has no area to fill. */
export function triangulateFan(polygon: Point[]): number[] {
  if (polygon.length < 3) return [];
  const out: number[] = [];
  for (let i = 1; i < polygon.length - 1; i++) {
    out.push(polygon[0].x, polygon[0].y, polygon[i].x, polygon[i].y, polygon[i + 1].x, polygon[i + 1].y);
  }
  return out;
}
