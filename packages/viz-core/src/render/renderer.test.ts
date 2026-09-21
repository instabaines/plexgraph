import { describe, expect, it } from "vitest";
import { bucketContains, buildCategoricalPalette, computeTimeDomain, connectorActiveAt, formatTime, panelHeadingLines, timeBucketEdges, timeSliceColor } from "./renderer";
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
    expect(domain).toEqual({ min: -2, max: 12, instantaneous: false });
  });

  it("ignores always-present connectors mixed in with temporal ones", () => {
    const domain = computeTimeDomain([conn(null, null), conn(1, 4)]);
    expect(domain).toEqual({ min: 1, max: 4, instantaneous: false });
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
    expect(domain).toEqual({ min: 1, max: 6, instantaneous: false });
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

describe("time semantics for instantaneous events", () => {
  it("flags a contact sequence as instantaneous, but not one interval or an always-present connector alone", () => {
    expect(computeTimeDomain([conn(3, 3), conn(7, 7)])).toEqual({ min: 3, max: 7, instantaneous: true });
    expect(computeTimeDomain([conn(3, 3), conn(4, 6)])?.instantaneous).toBe(false);
    expect(computeTimeDomain([conn(null, null), conn(3, 3)])?.instantaneous).toBe(true);
  });

  it("shows a point event only at its exact instant in instant mode", () => {
    const e = conn(5, 5);
    expect(connectorActiveAt(e, 5)).toBe(true);
    expect(connectorActiveAt(e, 5.001)).toBe(false);
    expect(connectorActiveAt(e, 4.999)).toBe(false);
  });

  it("keeps a point event visible for a trailing window after it happens", () => {
    const e = conn(5, 5);
    expect(connectorActiveAt(e, 5, "window", 2)).toBe(true);
    expect(connectorActiveAt(e, 7, "window", 2)).toBe(true);
    expect(connectorActiveAt(e, 7.5, "window", 2)).toBe(false);
    expect(connectorActiveAt(e, 4.9, "window", 2)).toBe(false); // not yet happened
  });

  it("accumulates every event up to t in cumulative mode and keeps always-present connectors in every mode", () => {
    expect(connectorActiveAt(conn(5, 5), 9, "cumulative")).toBe(true);
    expect(connectorActiveAt(conn(5, 5), 4, "cumulative")).toBe(false);
    for (const mode of ["instant", "window", "cumulative"] as const) expect(connectorActiveAt(conn(null, null), 1, mode, 1)).toBe(true);
  });

  it("puts an event on a bucket boundary in exactly one ribbon bucket, and the final instant in the last", () => {
    const buckets = [[0, 5], [5, 10]] as const;
    const at = (t: number) => buckets.map(([a, b], i) => bucketContains(conn(t, t), a, b, i === buckets.length - 1));
    expect(at(5)).toEqual([false, true]);
    expect(at(0)).toEqual([true, false]);
    expect(at(10)).toEqual([false, true]);
    expect(at(2)).toEqual([true, false]);
    // an interval spanning the boundary belongs to both
    expect(buckets.map(([a, b], i) => bucketContains(conn(4, 6), a, b, i === 1))).toEqual([true, true]);
  });
});

describe("formatTime", () => {
  const t = Date.UTC(2013, 11, 31, 16, 39, 18) / 1000;
  it("prints plain numbers for unitless data without float noise", () => {
    expect(formatTime(50)).toBe("50");
    expect(formatTime(0.1 + 0.2)).toBe("0.3");
  });
  it("prints UTC dates for epoch seconds, dropping detail as the span grows", () => {
    expect(formatTime(t, "epoch_seconds", 30 * 86400)).toBe("2013-12-31 16:39");
    expect(formatTime(t, "epoch_seconds", 400 * 86400)).toBe("2013-12-31");
    expect(formatTime(t, "epoch_seconds", 3600)).toBe("2013-12-31 16:39:18");
  });
});

describe("timeBucketEdges", () => {
  const at = (t: number) => conn(t, t);
  const domain = { min: 0, max: 100 };
  const count = (events: WireConnector[], edges: number[]) =>
    edges.slice(0, -1).map((a, i) => events.filter(c => bucketContains(c, a, edges[i + 1], i === edges.length - 2)).length);

  it("splits equal time into equal-duration buckets that span the whole domain", () => {
    expect(timeBucketEdges([], domain, 4, "time")).toEqual([0, 25, 50, 75, 100]);
    expect(timeBucketEdges([], { min: 5, max: 5 }, 3, "time")).toHaveLength(4); // degenerate span still yields buckets
  });

  it("gives busy periods narrow buckets and quiet ones wide buckets when splitting by events", () => {
    // 90 events in the first tenth of the time, 10 spread over the rest
    const events = [...Array.from({ length: 90 }, (_, i) => at(i / 9)), ...Array.from({ length: 10 }, (_, i) => at(15 + i * 9))];
    const equalTime = count(events, timeBucketEdges(events, domain, 4, "time"));
    expect(equalTime[0]).toBeGreaterThan(85); // one bucket swallows almost everything
    const edges = timeBucketEdges(events, domain, 4, "events");
    expect(edges[0]).toBe(0); expect(edges[edges.length - 1]).toBe(100);
    expect(count(events, edges).every(n => n >= 20 && n <= 30)).toBe(true);
    expect(count(events, edges).reduce((a, b) => a + b, 0)).toBe(100);
  });

  it("never loses or duplicates an event, even when many share one instant", () => {
    const events = [...Array.from({ length: 50 }, () => at(10)), ...Array.from({ length: 5 }, (_, i) => at(60 + i * 10))];
    const edges = timeBucketEdges(events, domain, 6, "events");
    expect(edges.length).toBeGreaterThanOrEqual(2);
    expect(edges.every((t, i) => i === 0 || t > edges[i - 1])).toBe(true);
    expect(count(events, edges).reduce((a, b) => a + b, 0)).toBe(55);
  });
});

describe("panelHeadingLines", () => {
  it("stacks a date range on two lines so neighbouring headings do not overlap", () => {
    expect(panelHeadingLines(0, "2013-12-31 → 2014-07-22")).toEqual(["1 · 2013-12-31", "→ 2014-07-22"]);
  });
  it("keeps other labels on one line", () => {
    expect(panelHeadingLines(2, "Research")).toEqual(["3 · Research"]);
    expect(panelHeadingLines(1, "t=[0.00, 16.50]")).toEqual(["2 · t=[0.00, 16.50]"]);
  });
});
