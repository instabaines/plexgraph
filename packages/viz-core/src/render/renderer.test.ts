import { describe, expect, it } from "vitest";
import { buildCategoricalPalette, computeTimeDomain, timeSliceColor } from "./renderer";
import type { WireConnector } from "../ir/types";

function conn(t_start: number | null, t_end: number | null): WireConnector {
  return {
    id: 0,
    endpoints: [0, 1],
    directed: false,
    layer_id: null,
    t_start,
    t_end,
    weight: null,
    attrs: {},
  };
}

describe("computeTimeDomain", () => {
  it("returns null for a graph with no connectors", () => {
    expect(computeTimeDomain([])).toBeNull();
  });

  it("returns null when every connector is always-present (null bounds)", () => {
    expect(computeTimeDomain([conn(null, null), conn(null, null)])).toBeNull();
  });

  it("computes the min/max span across temporal connectors", () => {
    const domain = computeTimeDomain([conn(0, 5), conn(-2, 3), conn(10, 12)]);
    expect(domain).toEqual({ min: -2, max: 12 });
  });

  it("ignores always-present connectors mixed in with temporal ones", () => {
    const domain = computeTimeDomain([conn(null, null), conn(1, 4)]);
    expect(domain).toEqual({ min: 1, max: 4 });
  });

  it("returns null when no connector supplies a finite upper bound", () => {
    // t_start set, t_end null (open-ended) on every connector: min is
    // finite but max never is, so there's no well-defined domain to show
    // a timeline for. A known limitation for open-ended-only graphs,
    // not a bug — most real temporal graphs have closed intervals.
    const domain = computeTimeDomain([conn(2, null)]);
    expect(domain).toBeNull();
  });

  it("uses the finite bound when both are present on at least one connector", () => {
    const domain = computeTimeDomain([conn(2, null), conn(1, 6)]);
    expect(domain).toEqual({ min: 1, max: 6 });
  });
});

describe("timeSliceColor", () => {
  it("is a sequential (ordered) ramp, not a repeating/categorical cycle", () => {
    // Unlike LAYER_PALETTE (cycled, categorical), consecutive time slices
    // should never repeat the same color within a reasonable bucket count
    // — the whole point is that color encodes position in the sequence.
    const colors = Array.from({ length: 6 }, (_, i) => timeSliceColor(i, 6));
    const unique = new Set(colors.map((c) => c.join(",")));
    expect(unique.size).toBe(6);
  });

  it("first and last slice match the ramp's defined endpoints", () => {
    const first = timeSliceColor(0, 5);
    const last = timeSliceColor(4, 5);
    expect(first).not.toEqual(last);
    // Early = cool blue (low R, higher B); late = warm orange (high R, low B).
    expect(first[0]).toBeLessThan(last[0]);
    expect(first[2]).toBeGreaterThan(last[2]);
  });

  it("handles a single-slice stack without dividing by zero", () => {
    expect(() => timeSliceColor(0, 1)).not.toThrow();
    const color = timeSliceColor(0, 1);
    expect(color).toHaveLength(4);
  });
});

describe("buildCategoricalPalette", () => {
  it("assigns one color per distinct value", () => {
    const palette = buildCategoricalPalette(["A", "B", "A", "C"]);
    expect(palette.size).toBe(3);
    expect(palette.get("A")).not.toEqual(palette.get("B"));
    expect(palette.get("B")).not.toEqual(palette.get("C"));
  });

  it("is deterministic given the same first-seen order", () => {
    const a = buildCategoricalPalette(["A", "B", "C"]);
    const b = buildCategoricalPalette(["A", "B", "C"]);
    expect(a.get("A")).toEqual(b.get("A"));
    expect(a.get("B")).toEqual(b.get("B"));
  });

  it("reassigns colors if first-seen order changes", () => {
    const a = buildCategoricalPalette(["A", "B"]);
    const b = buildCategoricalPalette(["B", "A"]);
    expect(a.get("A")).toEqual(b.get("B"));
  });

  it("handles an empty list", () => {
    expect(buildCategoricalPalette([]).size).toBe(0);
  });
});
