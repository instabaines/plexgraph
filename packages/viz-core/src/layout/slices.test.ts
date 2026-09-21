import { describe, it, expect } from "vitest";
import { sliceGeometry } from "./slices";
describe("slice layouts", () => {
  const positions = new Float32Array([-10, -2, 10, 2, 0, 0]);
  it("separates atlas panels without changing relative node positions", () => {
    const g = sliceGeometry(positions, 6, "atlas");
    expect(g.bounds[2] - g.bounds[0]).toBeLessThan(1.15);
    expect(g.bounds[3] - g.bounds[1]).toBeLessThan(1.15);
    for (let s = 1; s < 6; s++) {
      expect(g.point(1, s)[0] - g.point(0, s)[0]).toBeCloseTo(g.point(1, 0)[0] - g.point(0, 0)[0]);
    }
  });
  it("orders time left to right on a shared horizontal axis", () => {
    const g = sliceGeometry(positions, 6, "ribbon");
    for (let s = 1; s < 6; s++) {
      expect(g.point(0, s)[0]).toBeGreaterThan(g.point(0, s - 1)[0]);
      expect(g.point(0, s)[1]).toBe(g.point(0, 0)[1]);
    }
  });
  it("handles empty and coincident graphs", () => {
    for (const p of [new Float32Array(), new Float32Array([0, 0])]) {
      const g = sliceGeometry(p, 1, "atlas");
      expect([...g.bounds, ...g.point(0, 0)].every(Number.isFinite)).toBe(true);
    }
  });
});
