import { describe, expect, it } from "vitest";
import { convexHull, inflateHull, triangulateFan } from "./hull";

describe("convexHull", () => {
  it("returns the input for 0, 1, or 2 points", () => {
    expect(convexHull([])).toEqual([]);
    expect(convexHull([{ x: 1, y: 1 }])).toEqual([{ x: 1, y: 1 }]);
    expect(convexHull([{ x: 0, y: 0 }, { x: 1, y: 1 }])).toEqual([{ x: 0, y: 0 }, { x: 1, y: 1 }]);
  });

  it("computes the hull of a square with an interior point", () => {
    const points = [
      { x: 0, y: 0 },
      { x: 4, y: 0 },
      { x: 4, y: 4 },
      { x: 0, y: 4 },
      { x: 2, y: 2 }, // interior — must not appear in the hull
    ];
    const hull = convexHull(points);
    expect(hull).toHaveLength(4);
    expect(hull).not.toContainEqual({ x: 2, y: 2 });
  });

  it("dedupes coincident points so they don't produce a degenerate hull", () => {
    const points = [
      { x: 1, y: 1 },
      { x: 1, y: 1 },
      { x: 1, y: 1 },
    ];
    expect(convexHull(points)).toEqual([{ x: 1, y: 1 }]);
  });

  it("hull area is positive for a non-degenerate point set (winding is consistent)", () => {
    const hull = convexHull([
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      { x: 1, y: 2 },
    ]);
    // Shoelace formula.
    let area = 0;
    for (let i = 0; i < hull.length; i++) {
      const a = hull[i];
      const b = hull[(i + 1) % hull.length];
      area += a.x * b.y - b.x * a.y;
    }
    expect(Math.abs(area) / 2).toBeGreaterThan(0);
  });
});

describe("inflateHull", () => {
  it("leaves hulls with fewer than 3 points unchanged", () => {
    expect(inflateHull([], 1)).toEqual([]);
    expect(inflateHull([{ x: 1, y: 1 }], 1)).toEqual([{ x: 1, y: 1 }]);
  });

  it("pushes every vertex further from the centroid", () => {
    const hull = [
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      { x: 1, y: 2 },
    ];
    const inflated = inflateHull(hull, 1);
    const centroid = { x: 1, y: 2 / 3 };
    const distBefore = (p: { x: number; y: number }) => Math.hypot(p.x - centroid.x, p.y - centroid.y);
    for (let i = 0; i < hull.length; i++) {
      expect(distBefore(inflated[i])).toBeGreaterThan(distBefore(hull[i]));
    }
  });
});

describe("triangulateFan", () => {
  it("returns nothing for fewer than 3 points", () => {
    expect(triangulateFan([])).toEqual([]);
    expect(triangulateFan([{ x: 0, y: 0 }])).toEqual([]);
    expect(triangulateFan([{ x: 0, y: 0 }, { x: 1, y: 1 }])).toEqual([]);
  });

  it("produces (n-2) triangles for an n-gon", () => {
    const square = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
    ];
    const tris = triangulateFan(square);
    // 2 triangles * 3 vertices * 2 floats(x,y) = 12 floats
    expect(tris).toHaveLength(12);
  });
});
