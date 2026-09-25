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

from plexgraph_core.algorithms.layout import LayoutStep
from plexgraph_core.model.ir import Graph, NEG_INF, POS_INF

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


_DTYPES = {np.dtype(np.float32): "f32", np.dtype(np.float64): "f64", np.dtype(np.int32): "i32", np.dtype(np.uint32): "u32", np.dtype(np.uint8): "u8"}


def _wire_arrays(value: Any) -> Any:
    """Copy of `value` with NumPy arrays as `{"$dtype", "$data"}` (raw bytes the viewer turns back into typed
    arrays) and NumPy scalars as plain numbers."""
    if isinstance(value, np.ndarray):
        if value.dtype not in _DTYPES:
            if value.dtype.kind in "OUS":
                return [_wire_arrays(v) for v in value.tolist()]
            value = value.astype(np.float64)
        return {"$dtype": _DTYPES[value.dtype], "$data": np.ascontiguousarray(value).tobytes()}
    if isinstance(value, dict):
        return {k: _wire_arrays(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_arrays(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def encode_style(op: str, *, spec: dict[str, Any] | None = None, ids: Any = None, color: Any = None) -> bytes:
    """A style message for the viewer (see StyleMessage in viz-core): `set`/`replace` carry a spec, `reset` and
    `clear_paint` carry nothing, and `paint` carries node ids (uint32) and a color, or None to remove."""
    if op not in ("set", "replace", "reset", "paint", "clear_paint"):
        raise ValueError(f"unknown style operation {op!r}")
    payload: dict[str, Any] = {"type": "style", "op": op}
    if spec is not None:
        payload["spec"] = _wire_arrays(spec)
    if ids is not None:
        payload["ids"] = np.ascontiguousarray(np.asarray(ids, dtype=np.uint32)).tobytes()
    if op == "paint":
        payload["color"] = None if color is None else [float(c) for c in color]
    return msgpack.packb(payload, use_bin_type=True)


def encode_export_request(request_id: str, fmt: str) -> bytes:
    """Ask the viewer to export its current view and send the result back (see ExportRequestMessage in viz-core,
    and ClientHub.request_export). `request_id` round-trips into the reply so it can be matched to this request."""
    return msgpack.packb({"type": "export_request", "id": request_id, "format": fmt}, use_bin_type=True)

