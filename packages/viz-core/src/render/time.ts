// Pure time helpers: domains, formatting, filters and ribbon buckets. No rendering state, so the style engine
// and the renderer can both use them.
import type { WireConnector } from "../ir/types";

/** The time span covered by a graph's temporal connectors (null t_start/
 * t_end on the wire — "always present" — are excluded from the domain, so
 * a graph that's entirely non-temporal has domain = null). */
export interface TimeDomain {
  min: number;
  max: number;
  /** True when every time-bounded connector is a single instant (t_start === t_end), as in contact sequences. */
  instantaneous: boolean;
  /** "epoch_seconds" when times are Unix seconds and should be shown as dates. */
  unit?: "epoch_seconds" | null;
}

export const SECONDS_PER_DAY = 86400;

/** A time value for display: dates for epoch seconds (UTC; seconds shown only over short spans), else a number. */
export function formatTime(t: number, unit: TimeDomain["unit"] = null, span = Infinity): string {
  if (unit !== "epoch_seconds") return String(Number(t.toPrecision(6)));
  const iso = new Date(t * 1000).toISOString();
  if (span > 120 * SECONDS_PER_DAY) return iso.slice(0, 10);
  return span > 2 * SECONDS_PER_DAY ? iso.slice(0, 16).replace("T", " ") : iso.slice(0, 19).replace("T", " ");
}

/** How the time slider selects connectors:
 * - "instant": active exactly at t (t_start <= t <= t_end); right for intervals, empty for most t on point events.
 * - "window": active at some moment in [t - window, t]; the trailing window that suits contact sequences.
 * - "cumulative": started at or before t (everything so far). */
export type TimeMode = "instant" | "window" | "cumulative";

export interface TimeFilterOptions { mode?: TimeMode; window?: number }

export function connectorActiveAt(c: WireConnector, t: number, mode: TimeMode = "instant", window = 0): boolean {
  const start = c.t_start ?? -Infinity, end = c.t_end ?? Infinity;
  if (mode === "cumulative") return start <= t;
  if (mode === "window") return start <= t && end >= t - window;
  return start <= t && t <= end;
}

/** How ribbon buckets divide time: "time" gives every bucket the same duration; "events" gives every bucket
 * about the same number of events (busy periods get narrow buckets, quiet ones wide). */
export type TimeSplit = "time" | "events";

/** Ribbon bucket boundaries: `n + 1` increasing times from `domain.min` to `domain.max`. Equal-events
 * boundaries follow quantiles of the connectors' start times; repeated values collapse, so a stream with many
 * simultaneous events can yield fewer than `n` buckets. */
export function timeBucketEdges(connectors: WireConnector[], domain: { min: number; max: number }, n: number, split: TimeSplit = "time"): number[] {
  n = Math.max(1, Math.floor(n));
  const width = (domain.max - domain.min) / n || 1;
  if (split === "time") return Array.from({ length: n + 1 }, (_, i) => (i === n ? domain.max : domain.min + i * width));
  const starts = connectors.map(c => c.t_start).filter((t): t is number => t !== null && Number.isFinite(t)).sort((a, b) => a - b);
  const edges = [domain.min];
  for (let i = 1; i < n; i++) {
    const t = starts[Math.floor((i * starts.length) / n)];
    if (t !== undefined && t > edges[edges.length - 1] && t < domain.max) edges.push(t);
  }
  edges.push(domain.max);
  return edges.length >= 2 && edges[edges.length - 1] > edges[0] ? edges : [domain.min, domain.min + width * n];
}

/** Whether a connector belongs in ribbon bucket [start, end). Buckets are half-open so an event exactly on a
 * boundary lands in one bucket only; the last bucket also owns its end point. */
export function bucketContains(c: WireConnector, start: number, end: number, isLast: boolean): boolean {
  const cStart = c.t_start ?? -Infinity, cEnd = c.t_end ?? Infinity;
  return (isLast ? cStart <= end : cStart < end) && cEnd >= start;
}

/** Sequential (not categorical) color ramp for time-axis stacking — an
 * ordered gradient (cool -> warm) reads as "progression through time" the
 * way a qualitative palette (LAYER_PALETTE) doesn't; layer-axis stacking
 * keeps using LAYER_PALETTE instead, since layers are categorical, not
 * ordered. */
export function timeSliceColor(index: number, total: number): [number, number, number, number] {
  const t = total <= 1 ? 0 : index / (total - 1);
  const early: [number, number, number] = [0.16, 0.45, 0.85]; // cool blue
  const late: [number, number, number] = [0.9, 0.45, 0.15]; // warm orange
  return [
    early[0] + (late[0] - early[0]) * t,
    early[1] + (late[1] - early[1]) * t,
    early[2] + (late[2] - early[2]) * t,
    0.85,
  ];
}

/** The [min, max] span across all connectors' finite t_start/t_end. A null
 * bound means "always present" and is excluded from the domain — a graph
 * with no temporal connectors (or only always-present ones) has no domain,
 * so the caller knows to skip showing a timeline UI at all. */
export function computeTimeDomain(connectors: WireConnector[]): TimeDomain | null {
  let min = Infinity;
  let max = -Infinity;
  let bounded = 0, instants = 0;
  for (const c of connectors) {
    if (c.t_start !== null) min = Math.min(min, c.t_start);
    if (c.t_end !== null) max = Math.max(max, c.t_end);
    if (c.t_start !== null && c.t_end !== null) { bounded++; if (c.t_start === c.t_end) instants++; }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  return { min, max, instantaneous: bounded > 0 && instants === bounded };
}
