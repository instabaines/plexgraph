"""Wire protocol: serializes an IR Graph (and streaming layout updates) to
MessagePack + typed-array-friendly payloads for the bridge's WebSocket.

Per docs/architecture/plan.md section 3: prototype with MessagePack in
Phase A rather than pre-committing to Arrow IPC before real payload sizes
from the 100K-node spike are known. Every IR field (layer_id, t_start,
t_end, hyperedge endpoint count) is included from Phase A onward, even
though the Phase A frontend only renders the plain-graph subset — this
avoids a protocol-breaking change at each later phase boundary (temporal,
multiplex, hypergraph).
"""

from __future__ import annotations

from typing import Any

import msgpack
import numpy as np

from hyperloom_core.algorithms.layout import LayoutStep
from hyperloom_core.model.ir import Graph, NEG_INF, POS_INF

WIRE_PROTOCOL_VERSION = 1


def _connector_payload(graph: Graph) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "endpoints": list(c.endpoints),
            "directed": c.directed,
            "layer_id": c.layer_id,
            # inf/-inf are not valid JSON/MessagePack numbers in every
            # decoder; encode "always present" explicitly as None instead
            # of relying on float infinity round-tripping.
            "t_start": None if c.t_start == NEG_INF else c.t_start,
            "t_end": None if c.t_end == POS_INF else c.t_end,
            "weight": c.weight,
            "attrs": c.attrs,
        }
        for c in graph.connectors()
    ]


# The browser reads numbers as IEEE doubles, so integers beyond 2**53 would silently collide or round.
# They cannot be exchanged exactly, so they travel as text (MessagePack also rejects anything past 64 bits).
_JS_SAFE_INT = 2**53 - 1


def _exact(value: Any) -> Any:
    """Copy of `value` with integers outside JavaScript's exact range replaced by their decimal text."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > _JS_SAFE_INT else value
    if isinstance(value, dict):
        return {_exact(k): _exact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_exact(v) for v in value]
    return value


def encode_graph(graph: Graph) -> bytes:
    """Full graph snapshot: schema version, nodes, layers, connectors."""
    payload = {
        "type": "graph",
        "schema_version": graph.schema_version,
        "wire_version": WIRE_PROTOCOL_VERSION,
        "time_unit": graph.time_unit,
        # Node identity is what the viewer searches and labels by, so keys are always made exact.
        "nodes": [{"id": n.id, "key": _exact(n.key), "attrs": n.attrs} for n in graph.nodes()],
        "layers": [{"id": l.id, "key": _exact(l.key), "attrs": l.attrs} for l in graph.layers()],
        "connectors": _connector_payload(graph),
    }
    try:
        return msgpack.packb(payload, use_bin_type=True)
    except OverflowError:
        # An attribute holds an integer too large to pack; rare, so the extra copy is only paid then.
        return msgpack.packb(_exact(payload), use_bin_type=True)


def encode_layout_step(step: LayoutStep) -> bytes:
    """Streamed layout update: one message per iteration so the frontend
    can render the layout converging in real time (confirmed v1 requirement
    — see plan section 3)."""
    positions = np.ascontiguousarray(step.positions, dtype=np.float32)
    payload = {
        "type": "layout_step",
        "iteration": step.iteration,
        "converged": step.converged,
        "num_nodes": positions.shape[0],
        # Raw float32 bytes rather than nested lists — cheaper to encode
        # and matches what the frontend will upload straight into a WebGL
        # buffer.
        "positions": positions.tobytes(),
    }
    return msgpack.packb(payload, use_bin_type=True)


def decode(message: bytes) -> dict[str, Any]:
    return msgpack.unpackb(message, raw=False)
