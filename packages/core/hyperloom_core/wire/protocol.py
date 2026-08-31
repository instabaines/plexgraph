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


def encode_graph(graph: Graph) -> bytes:
    """Full graph snapshot: schema version, nodes, layers, connectors."""
    payload = {
        "type": "graph",
        "schema_version": graph.schema_version,
        "wire_version": WIRE_PROTOCOL_VERSION,
        "nodes": [{"id": n.id, "key": n.key, "attrs": n.attrs} for n in graph.nodes()],
        "layers": [{"id": l.id, "key": l.key, "attrs": l.attrs} for l in graph.layers()],
        "connectors": _connector_payload(graph),
    }
    return msgpack.packb(payload, use_bin_type=True)


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
