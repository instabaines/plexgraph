import { describe, expect, it } from "vitest";
import {
  decodePositions,
  isGraphMessage,
  isLayoutStepMessage,
  type GraphMessage,
  type LayoutStepMessage,
} from "./types";

describe("message type guards", () => {
  const graphMsg: GraphMessage = {
    type: "graph",
    schema_version: 1,
    wire_version: 1,
    nodes: [],
    layers: [],
    connectors: [],
  };
  const layoutMsg: LayoutStepMessage = {
    type: "layout_step",
    iteration: 1,
    converged: false,
    num_nodes: 0,
    positions: new Uint8Array(0),
  };

  it("isGraphMessage narrows correctly", () => {
    expect(isGraphMessage(graphMsg)).toBe(true);
    expect(isGraphMessage(layoutMsg)).toBe(false);
  });

  it("isLayoutStepMessage narrows correctly", () => {
    expect(isLayoutStepMessage(layoutMsg)).toBe(true);
    expect(isLayoutStepMessage(graphMsg)).toBe(false);
  });
});

describe("decodePositions", () => {
  it("reinterprets raw bytes as float32 pairs", () => {
    const floats = new Float32Array([0, 1, 2, 3, 4, 5]);
    const bytes = new Uint8Array(floats.buffer);
    const msg: LayoutStepMessage = {
      type: "layout_step",
      iteration: 1,
      converged: true,
      num_nodes: 3,
      positions: bytes,
    };
    const decoded = decodePositions(msg);
    expect(Array.from(decoded)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("respects a byte offset into a larger buffer", () => {
    const floats = new Float32Array([9, 9, 1, 2]);
    const fullBytes = new Uint8Array(floats.buffer);
    // Slice a view starting after the first two floats (8 bytes).
    const sliced = new Uint8Array(fullBytes.buffer, 8, 8);
    const msg: LayoutStepMessage = {
      type: "layout_step",
      iteration: 1,
      converged: true,
      num_nodes: 1,
      positions: sliced,
    };
    const decoded = decodePositions(msg);
    expect(Array.from(decoded)).toEqual([1, 2]);
  });
});
