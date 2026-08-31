// TypeScript mirror of hyperloom_core/wire/protocol.py's payload shapes.
// Keeping this in lockstep with the Python side is the main integration
// risk across the project (see docs/architecture/plan.md, critical files).
// Every field from the IR is represented here from Phase A onward — layer,
// temporal, and hyperedge fields are decoded even though the Phase A
// renderer only draws the plain-graph subset (endpoints.length === 2,
// layer_id === null, t_start/t_end === null).

export interface WireNode {
  id: number;
  key: unknown;
  attrs: Record<string, unknown>;
}

export interface WireLayer {
  id: number;
  key: unknown;
  attrs: Record<string, unknown>;
}

export interface WireConnector {
  id: number;
  endpoints: number[];
  directed: boolean;
  layer_id: number | null;
  /** null means "always present" (the NEG_INF sentinel on the Python side). */
  t_start: number | null;
  /** null means "always present" (the POS_INF sentinel on the Python side). */
  t_end: number | null;
  weight: number | null;
  attrs: Record<string, unknown>;
}

export interface GraphMessage {
  type: "graph";
  schema_version: number;
  wire_version: number;
  nodes: WireNode[];
  layers: WireLayer[];
  connectors: WireConnector[];
}

export interface LayoutStepMessage {
  type: "layout_step";
  iteration: number;
  converged: boolean;
  num_nodes: number;
  /** Raw float32 (x, y) pairs, num_nodes * 2 floats. */
  positions: Uint8Array;
}

export type WireMessage = GraphMessage | LayoutStepMessage;

export function isGraphMessage(msg: WireMessage): msg is GraphMessage {
  return msg.type === "graph";
}

export function isLayoutStepMessage(msg: WireMessage): msg is LayoutStepMessage {
  return msg.type === "layout_step";
}

/** Decode the raw positions buffer into a Float32Array. Copies into a
 * fresh, 0-offset buffer rather than viewing msg.positions.buffer directly
 * at its byteOffset — msgpack's decoder returns bin fields as subarray
 * views into a larger shared buffer, and that offset isn't guaranteed to
 * be a multiple of 4, which Float32Array's constructor requires. Observed
 * in practice at 100K-node scale, where the offset happened to land
 * unaligned. */
export function decodePositions(msg: LayoutStepMessage): Float32Array {
  const aligned = new Uint8Array(msg.positions.byteLength);
  aligned.set(msg.positions);
  return new Float32Array(aligned.buffer);
}
