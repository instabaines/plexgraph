import { convexHull, inflateHull, type Point } from "../render/hull";
/** A directed edge triangle; coincident endpoints have no direction. */
export function arrowPolygon(source: Point, target: Point, length: number, width: number, t: number): Point[] {
  const dx = target.x - source.x, dy = target.y - source.y;
  const distance = Math.hypot(dx, dy);
  if (distance < 1e-9) return [];
  const ux = dx / distance, uy = dy / distance;
  const tip = {x: source.x + dx * t, y: source.y + dy * t};
  const size = Math.min(length, distance * 0.35);
  const half = Math.min(width / 2, size / 2);
  return [tip, {x: tip.x - ux * size - uy * half, y: tip.y - uy * size + ux * half},
    {x: tip.x - ux * size + uy * half, y: tip.y - uy * size - ux * half}];
}
/** Include a visible capsule even when members are collinear or coincident. */
export function memberHull(points: Point[], padding: number): Point[] {
  const hull = convexHull(points);
  if (hull.length >= 3) return inflateHull(hull, padding);
  return convexHull(hull.flatMap(p => Array.from({length: 12}, (_, i) => ({
    x: p.x + Math.cos(i * Math.PI / 6) * padding,
    y: p.y + Math.sin(i * Math.PI / 6) * padding,
  }))));
}
