"""Native GEXF (Graph Exchange XML Format) reader -- no external dependency beyond the standard
library's xml.etree.ElementTree.

Normative schema: the gephi/gexf GitHub repo (https://github.com/gephi/gexf). Readable primer used
for grounding here: https://gexf.net/data.html (structure/attributes) and
https://gexf.net/dynamics.html (dynamics/spells).

GEXF has no native multiplex/multi-relational concept at all -- there is nothing like GML's
`layer_key` to offer here, so `read_gexf` has no such parameter.

Dynamics: an edge may carry a single `start`/`end` pair directly, or a `<spells>` block of several
disjoint `<spell start=.. end=../>` intervals -- "if a node or edge exists at multiple points in
time, we use the concept of spells" (dynamics.html). Each spell becomes its own plexgraph Connector
with the same endpoints -- this IR already allows parallel connectors between the same node pair
(the same mechanism `from_networkx` relies on for multigraphs), so no model change is needed. A
plain `start`/`end` on the `<edge>` itself (no `<spells>`) is just the one-spell special case.

Node-level dynamics (spells or `start`/`end` on a `<node>`) are not read: plexgraph's `Node` has no
time dimension of its own (only `Connector` does), and inventing one is out of scope for a format
reader. Likewise, dynamic `<attvalue>` elements -- an attribute value that itself changes over time,
via its own `start`/`end` -- are not read; only a (static) attvalue's value is kept. Both are real
GEXF features this reader does not attempt, rather than something it gets wrong.

`<attributes class="node"|"edge">` declarations are read for type coercion (integer/long/float/
double/boolean/liststring/string/anyURI) and defaults; an `<attvalue>` referencing an undeclared
attribute id raises, the same way an edge referencing an undefined node id raises in `read_gml`.

Edge direction: `defaultedgetype` on `<graph>` sets the file's default ("undirected" if absent, per
the spec); `directed=` overrides that default (not individual edges). Each `<edge>` may still name
its own `type="directed"|"undirected"|"mutual"`, which takes precedence over the default either
way -- `"mutual"` is GEXF's shorthand for "both directions," read here as two directed connectors.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from plexgraph_core.model.ir import Graph


class GEXFParseError(ValueError):
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
    "integer": int,
    "long": int,
    "float": float,
    "double": float,
    "boolean": _coerce_bool,
    "liststring": lambda s: s.split("|"),
    "string": str,
    "anyuri": str,
}


@dataclass(frozen=True, slots=True)
class _AttrDef:
    title: str
    coerce: Any
    default: Any


def _parse_time(value: str | None, timeformat: str | None) -> float | None:
    if value is None:
        return None
    if timeformat == "date":
        return float(date.fromisoformat(value).toordinal())
    if timeformat == "datetime":
        return datetime.fromisoformat(value).timestamp()
    return float(value)


def _read_attribute_defs(graph_el: ET.Element) -> tuple[dict[str, _AttrDef], dict[str, _AttrDef]]:
    node_defs: dict[str, _AttrDef] = {}
    edge_defs: dict[str, _AttrDef] = {}
    for block in _children(graph_el, "attributes"):
        target = edge_defs if block.get("class") == "edge" else node_defs
        for attr_el in _children(block, "attribute"):
            aid = attr_el.get("id")
            if aid is None:
                continue
            title = attr_el.get("title", aid)
            coerce = _TYPE_COERCE.get((attr_el.get("type") or "string").lower(), str)
            default_el = _child(attr_el, "default")
            default = coerce(default_el.text) if default_el is not None and default_el.text is not None else None
            target[aid] = _AttrDef(title=title, coerce=coerce, default=default)
    return node_defs, edge_defs


def _read_attvalues(el: ET.Element, defs: dict[str, _AttrDef], path: Any, kind: str) -> dict[str, Any]:
    attrs = {d.title: d.default for d in defs.values() if d.default is not None}
    attvalues_el = _child(el, "attvalues")
    if attvalues_el is not None:
        for av in _children(attvalues_el, "attvalue"):
            aid = av.get("for") or av.get("id")
            if aid not in defs:
                raise GEXFParseError(f"{path}: {kind} attvalue references an undeclared attribute {aid!r}")
            d = defs[aid]
            value = av.get("value")
            attrs[d.title] = d.coerce(value) if value is not None else None
    return attrs


def read_gexf(
    path: str | Path,
    *,
    directed: bool | None = None,
    label_as_key: bool = False,
) -> Graph:
    """Read a GEXF file into a Graph.

    A node's plexgraph key is its GEXF `id` by default -- always present and unique per the spec.
    `label_as_key=True` uses the `label` attribute instead, raising if a node has none or two nodes
    share one (same tradeoff as `read_gml`'s `label_as_key`). When present, `label` is also kept as
    a plain node attribute either way.

    `directed=None` (the default) reads the graph's own `defaultedgetype` ("undirected" if absent,
    the GEXF default); pass True/False to override that default. Either way, an edge with its own
    `type="directed"|"undirected"|"mutual"` still uses that instead -- `"mutual"` becomes two
    directed connectors, one each way.

    An edge's `weight` is read directly (GEXF's `weight` is a first-class XML attribute, not a
    custom one like GML's `weight_key`). `<spells>` (several disjoint existence intervals) become
    one Connector per spell, all sharing the same endpoints; a plain `start`/`end` on the `<edge>`
    itself is the one-spell case. GEXF has no native layer/multiplex concept, so there is no
    `layer_key` here.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise GEXFParseError(f"{path}: {e}") from None

    root = tree.getroot()
    graph_el = root if _local(root.tag) == "graph" else _child(root, "graph")
    if graph_el is None:
        raise GEXFParseError(f"{path}: no 'graph' element found")

    default_directed = (graph_el.get("defaultedgetype") == "directed") if directed is None else directed
    timeformat = graph_el.get("timeformat")
    node_attr_defs, edge_attr_defs = _read_attribute_defs(graph_el)

    g = Graph()
    node_key_by_id: dict[str, Any] = {}
    seen_labels: set[Any] = set()
    nodes_el = _child(graph_el, "nodes")
    for node_el in _children(nodes_el, "node") if nodes_el is not None else []:
        gexf_id = node_el.get("id")
        if gexf_id is None:
            raise GEXFParseError(f"{path}: a 'node' element has no 'id'")
        label = node_el.get("label")
        if label_as_key:
            if label is None:
                raise GEXFParseError(f"{path}: node {gexf_id!r} has no 'label', but label_as_key=True")
            if label in seen_labels:
                raise GEXFParseError(f"{path}: node label {label!r} is not unique, but label_as_key=True")
            seen_labels.add(label)
            key = label
        else:
            key = gexf_id
        attrs = _read_attvalues(node_el, node_attr_defs, path, "node")
        if label is not None:
            attrs.setdefault("label", label)
        node_key_by_id[gexf_id] = key
        g.add_node(key, **attrs)

    edges_el = _child(graph_el, "edges")
    for edge_el in _children(edges_el, "edge") if edges_el is not None else []:
        source, target = edge_el.get("source"), edge_el.get("target")
        if source is None or target is None:
            raise GEXFParseError(f"{path}: an 'edge' element is missing 'source' or 'target'")
        if source not in node_key_by_id or target not in node_key_by_id:
            raise GEXFParseError(f"{path}: edge references a node id not defined by any 'node' element")

        edge_type = edge_el.get("type")
        if edge_type is None:
            is_directed, mutual = default_directed, False
        elif edge_type == "directed":
            is_directed, mutual = True, False
        elif edge_type == "undirected":
            is_directed, mutual = False, False
        elif edge_type == "mutual":
            is_directed, mutual = True, True
        else:
            raise GEXFParseError(f"{path}: edge has an unrecognized type {edge_type!r}")

        weight_raw = edge_el.get("weight")
        weight = float(weight_raw) if weight_raw is not None else None
        attrs = _read_attvalues(edge_el, edge_attr_defs, path, "edge")

        spells_el = _child(edge_el, "spells")
        if spells_el is not None:
            windows = [
                (_parse_time(s.get("start"), timeformat), _parse_time(s.get("end"), timeformat))
                for s in _children(spells_el, "spell")
            ]
        else:
            windows = [(_parse_time(edge_el.get("start"), timeformat), _parse_time(edge_el.get("end"), timeformat))]

        src_key, tgt_key = node_key_by_id[source], node_key_by_id[target]
        for t_start, t_end in windows:
            g.add_edge(src_key, tgt_key, directed=is_directed, weight=weight, t_start=t_start, t_end=t_end, **attrs)
            if mutual:
                g.add_edge(tgt_key, src_key, directed=is_directed, weight=weight, t_start=t_start, t_end=t_end,
                           **attrs)

    return g
