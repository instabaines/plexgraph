"""Native, lossless JSON serialization of plexgraph's own Graph IR -- no external dependency
beyond the standard library's `json`.

Unlike the other native readers (`read_gml`, `read_gexf`, `read_graphml`, `read_dot`, `read_pajek`),
each of which reads a *foreign* format with its own conventions and limitations, this is
plexgraph's own format: every IR field round-trips exactly, including hyperedges, roles, multiplex
layers, temporal bounds, and `Graph.time_unit`. Use it when fidelity matters more than
interoperability with another tool -- for example, as the graph component of a saved project.

A node/layer key must be a JSON-native scalar (str, int, float, bool, or None) -- the overwhelming
common case, and literally what every native reader in this project already produces -- since JSON
has no equivalent of Python's broader "hashable." A different key type (e.g. a tuple) raises a
clear error rather than silently stringifying or dropping it. Node/connector/layer `attrs` must
themselves already be JSON-serializable; this does not attempt to coerce arbitrary Python objects,
the same way `json.dumps` itself would raise on one.

`t_start`/`t_end` of -inf/+inf ("always present") are encoded as `null`, the same convention the
wire protocol already uses (`-inf`/`+inf` are not valid numbers in every JSON decoder) -- and
decoded back through the same `None` default `add_edge`/`add_hyperedge` already understand.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from plexgraph_core.model.ir import NEG_INF, POS_INF, Graph

_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _encode_key(value: Any, what: str) -> Any:
    if not isinstance(value, _JSON_SCALAR_TYPES):
        raise TypeError(
            f"{what} key {value!r} is not a JSON-native scalar (str/int/float/bool/None) -- "
            "graph_to_dict only supports keys that are"
        )
    return value


def graph_to_dict(graph: Graph) -> dict[str, Any]:
    """Convert `graph` to a plain, JSON-serializable dict -- every IR field, losslessly. See the
    module docstring for the two things this does not attempt to coerce (non-scalar keys,
    non-JSON-serializable attribute values)."""
    return {
        "plexgraph_schema": graph.schema_version,
        "time_unit": graph.time_unit,
        "nodes": [{"key": _encode_key(n.key, "a node"), "attrs": n.attrs} for n in graph.nodes()],
        "layers": [{"key": _encode_key(layer.key, "a layer"), "attrs": layer.attrs} for layer in graph.layers()],
        "connectors": [
            {
                "endpoints": list(c.endpoints),
                "directed": c.directed,
                "layer_id": c.layer_id,
                "t_start": None if c.t_start == NEG_INF else c.t_start,
                "t_end": None if c.t_end == POS_INF else c.t_end,
                "weight": c.weight,
                "roles": [[node_id, role] for node_id, role in c.roles.items()] if c.roles else None,
                "attrs": c.attrs,
            }
            for c in graph.connectors()
        ],
    }


def graph_from_dict(data: dict[str, Any]) -> Graph:
    """The inverse of `graph_to_dict`."""
    g = Graph()
    g.time_unit = data.get("time_unit")
    for node in data.get("nodes", []):
        g.add_node(node["key"], **node.get("attrs", {}))
    for layer in data.get("layers", []):
        g.add_layer(layer["key"], **layer.get("attrs", {}))
    for c in data.get("connectors", []):
        endpoints = c["endpoints"]
        roles = {node_id: role for node_id, role in c["roles"]} if c.get("roles") else None
        kwargs = dict(
            directed=c.get("directed", False),
            layer=c.get("layer_id"),
            t_start=c.get("t_start"),
            t_end=c.get("t_end"),
            weight=c.get("weight"),
            **c.get("attrs", {}),
        )
        if len(endpoints) == 2 and roles is None:
            g.add_edge(endpoints[0], endpoints[1], **kwargs)
        else:
            g.add_hyperedge(endpoints, roles=roles, **kwargs)
    return g


def write_json(graph: Graph, path: str | Path) -> None:
    """Write `graph` to `path` as plexgraph's own lossless JSON format -- see `graph_to_dict`."""
    Path(path).write_text(json.dumps(graph_to_dict(graph), indent=2), encoding="utf-8")


def read_json(path: str | Path) -> Graph:
    """Read a Graph back from a file written by `write_json` -- see `graph_from_dict`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return graph_from_dict(data)
