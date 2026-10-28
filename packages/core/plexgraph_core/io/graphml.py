"""Native GraphML reader -- no external dependency beyond the standard library's
xml.etree.ElementTree.

Spec: http://graphml.graphdrawing.org/specification.html and the primer at
http://graphml.graphdrawing.org/primer/graphml-primer.html. Real-world grounding: networkx's own
`networkx/readwrite/graphml.py`, whose own header names several things it deliberately does not
support (mixed graphs, hyperedges, nested graphs, ports) -- a second data point that these are
reasonable things for a reader to leave out, not just this project's own shortcut.

GraphML has no native multiplex or temporal concept at all -- no equivalent of GML's common
`layer`-style attribute names and nothing like GEXF's `<spells>`. `layer_key=`/`t_start_key=`/
`t_end_key=` name a custom GraphML key's `attr.name` to read as plexgraph's layer/t_start/t_end,
the same convention `read_gml` already uses for exactly the same reason.

A GraphML document may legally contain more than one top-level `<graph>` element. Only the first
is read here -- matching `networkx.read_graphml`'s own behavior (it parses all of them internally
but its public reader returns just the first) -- rather than treating siblings as plexgraph layers:
there's no real-world convention for using them that way (unlike GEXF's genuine `<spells>`), so
inventing one here would be guessing, not reading what the format actually means.

Also out of scope, for the same reason: a `<graph>` nested inside a `<node>` or `<edge>` (GraphML's
hierarchical/compound-graph feature, used by tools like yEd for folder groups) is simply not
descended into -- it is unrelated to multiplex layering. `<hyperedge>` elements are not read either,
matching networkx's own documented limitation. A `<data>` element whose content is itself XML (e.g.
a yFiles `<y:ShapeNode>` sub-schema, not a plain value) is skipped rather than mis-parsed as text.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from plexgraph_core.model.ir import Graph


class GraphMLParseError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    for el in parent:
        if _local(el.tag) == name:
            return el
    return None


def _children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in parent if _local(el.tag) == name]


def _coerce_bool(s: str) -> bool:
    return s.strip().lower() in ("true", "1")


_TYPE_COERCE: dict[str, Any] = {
    "boolean": _coerce_bool,
    "int": int,
    "long": int,
    "float": float,
    "double": float,
    "string": str,
}


@dataclass(frozen=True, slots=True)
class _KeyDef:
    name: str
    for_: str  # "node" | "edge" | "graph" | "all"
    coerce: Any
    default: Any


def _read_key_defs(root: ET.Element) -> dict[str, _KeyDef]:
    defs: dict[str, _KeyDef] = {}
    for key_el in _children(root, "key"):
        kid = key_el.get("id")
        if kid is None:
            continue
        name = key_el.get("attr.name", kid)
        for_ = key_el.get("for", "all")
        coerce = _TYPE_COERCE.get((key_el.get("attr.type") or "string").lower(), str)
        default_el = _child(key_el, "default")
        default = coerce(default_el.text) if default_el is not None and default_el.text is not None else None
        defs[kid] = _KeyDef(name=name, for_=for_, coerce=coerce, default=default)
    return defs


def _read_data(el: ET.Element, key_defs: dict[str, _KeyDef], path: Any, kind: str) -> dict[str, Any]:
    attrs = {d.name: d.default for d in key_defs.values() if d.for_ in (kind, "all") and d.default is not None}
    for data_el in _children(el, "data"):
        kid = data_el.get("key")
        if kid not in key_defs:
            raise GraphMLParseError(f"{path}: {kind} data references an undeclared key {kid!r}")
        d = key_defs[kid]
        if d.for_ not in (kind, "all"):
            continue  # a key declared for the other element type; lenient, not an error
        if list(data_el):
            continue  # XML content (e.g. a yFiles sub-schema), not a plain value -- skip, don't mis-parse
        text = data_el.text
        attrs[d.name] = d.coerce(text) if text is not None else None
    return attrs


def read_graphml(
    path: str | Path,
    *,
    directed: bool | None = None,
    label_as_key: bool = False,
    weight_key: str | None = "weight",
    layer_key: str | None = None,
    t_start_key: str | None = None,
    t_end_key: str | None = None,
) -> Graph:
    """Read a GraphML file into a Graph.

    A node's plexgraph key is its GraphML `id` by default -- always present and unique per the
    spec. `label_as_key=True` uses whichever key's `attr.name` is `"label"` instead, raising if a
    node has none or two nodes share one (same tradeoff as `read_gml`'s `label_as_key`); either
    way, a `label` attribute is kept on the node as plain data too.

    `directed=None` (the default) reads the graph's own `edgedefault` ("undirected" if the
    attribute is missing, a lenient fallback since the spec itself requires it); pass True/False to
    override it. An individual `<edge>` may still set its own `directed="true"|"false"` (also
    accepting `"1"|"0"`), which always wins over the graph's default either way.

    GraphML has no first-class `weight` XML attribute (unlike GEXF) -- `weight_key` (default
    `"weight"`) names the declared key whose `attr.name` is read as plexgraph's `weight`, the same
    convention `read_gml`'s own `weight_key` uses. `layer_key`/`t_start_key`/`t_end_key` name a
    declared key's `attr.name` to read as plexgraph's layer/t_start/t_end -- GraphML has no native
    concept of either. Every other declared key is kept as a plain node/edge attribute, coerced to
    its declared `attr.type` (boolean/int/long/float/double/string) -- except the literal names
    "weight"/"layer"/"t_start"/"t_end" themselves, always dropped from an edge's generic attrs (not
    kept as a duplicate), even if a *different* custom key name was configured for that role; the
    same documented edge case `read_gml` accepts for the same reason.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise GraphMLParseError(f"{path}: {e}") from None

    root = tree.getroot()
    if _local(root.tag) != "graphml":
        raise GraphMLParseError(f"{path}: no top-level 'graphml' element")

    key_defs = _read_key_defs(root)
    graph_el = _child(root, "graph")
    if graph_el is None:
        raise GraphMLParseError(f"{path}: no 'graph' element found")

    default_directed = (graph_el.get("edgedefault") == "directed") if directed is None else directed

    g = Graph()
    node_key_by_id: dict[str, Any] = {}
    seen_labels: set[Any] = set()
    for node_el in _children(graph_el, "node"):
        gid = node_el.get("id")
        if gid is None:
            raise GraphMLParseError(f"{path}: a 'node' element has no 'id'")
        attrs = _read_data(node_el, key_defs, path, "node")
        if label_as_key:
            label = attrs.get("label")
            if label is None:
                raise GraphMLParseError(f"{path}: node {gid!r} has no 'label', but label_as_key=True")
            if label in seen_labels:
                raise GraphMLParseError(f"{path}: node label {label!r} is not unique, but label_as_key=True")
            seen_labels.add(label)
            key = label
        else:
            key = gid
        node_key_by_id[gid] = key
        g.add_node(key, **attrs)

    parsed_edges = []
    for edge_el in _children(graph_el, "edge"):
        source, target = edge_el.get("source"), edge_el.get("target")
        if source is None or target is None:
            raise GraphMLParseError(f"{path}: an 'edge' element is missing 'source' or 'target'")
        if source not in node_key_by_id or target not in node_key_by_id:
            raise GraphMLParseError(f"{path}: edge references a node id not defined by any 'node' element")
        directed_raw = edge_el.get("directed")
        is_edge_directed = default_directed if directed_raw is None else _coerce_bool(directed_raw)
        parsed_edges.append((source, target, is_edge_directed, _read_data(edge_el, key_defs, path, "edge")))

    layer_id_by_value: dict[Any, int] = {}
    if layer_key:
        for _, _, _, attrs in parsed_edges:
            value = attrs.get(layer_key)
            if value is not None and value not in layer_id_by_value:
                layer_id_by_value[value] = g.add_layer(value)

    reserved = {"weight", "layer", "t_start", "t_end", weight_key, layer_key, t_start_key, t_end_key}
    for source, target, is_edge_directed, attrs in parsed_edges:
        layer_value = attrs.get(layer_key) if layer_key else None
        plain_attrs = {k: v for k, v in attrs.items() if k not in reserved}
        g.add_edge(
            node_key_by_id[source],
            node_key_by_id[target],
            directed=is_edge_directed,
            weight=attrs.get(weight_key) if weight_key else None,
            layer=layer_id_by_value[layer_value] if layer_value is not None else None,
            t_start=attrs.get(t_start_key) if t_start_key else None,
            t_end=attrs.get(t_end_key) if t_end_key else None,
            **plain_attrs,
        )

    return g
