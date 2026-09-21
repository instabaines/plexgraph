import { describe, expect, it } from "vitest";
import { arrowPolygon, memberHull } from "./annotations";
import { triangulateFan } from "../render/hull";
describe("slice annotations", () => {
  it("points toward the target and caps triangles on short edges", () => {
    const points = arrowPolygon({x:0,y:0}, {x:0.01,y:0}, 1, 1, 0.9);
    expect(points[0].x).toBeCloseTo(0.009);
    expect(points[1].x).toBeGreaterThan(0);
    expect(points[1].y).toBeGreaterThan(0);
    expect(points[2].y).toBeLessThan(0);
  });
  it("omits arrowheads without a defined direction", () => {
    expect(arrowPolygon({x:1,y:1}, {x:1,y:1}, 1, 1, 0.9)).toEqual([]);
  });
  it("gives coincident and collinear hyperedge members a visible area", () => {
    for (const points of [[{x:0,y:0},{x:0,y:0},{x:0,y:0}], [{x:0,y:0},{x:1,y:0},{x:2,y:0}]]) {
      const hull = memberHull(points, 0.1);
      expect(triangulateFan(hull).length).toBeGreaterThan(0);
      expect(Math.max(...hull.map(p=>p.y))).toBeGreaterThan(0);
      expect(Math.min(...hull.map(p=>p.y))).toBeLessThan(0);
    }
  });
});
