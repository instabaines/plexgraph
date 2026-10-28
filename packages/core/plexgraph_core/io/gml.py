"""Native GML (Graph Modelling Language) reader -- no external dependency.

Himsolt, "GML: A Portable Graph File Format" (the original technical report; GML is the native
format of the Graphlet editor and is also read/written by Gephi, Cytoscape, igraph, yEd and
NetworkX). The format is hierarchical key-value lists: `key value` pairs where a value is an int,
a real, a quoted string, or a nested `[ ... ]` list. Duplicate keys at the same level are legal by
design (that's how a graph holds many `node [ ... ]`/`edge [ ... ]` entries) and are read back in
file order.

Parsed leniently rather than strictly per the formal grammar, matching how real GML files in the
wild actually look (and how other lenient readers, e.g. igraph's, already behave):

- Keys formally may only contain letters and digits; many real files use underscores too (ids like
  `node_type`), so both are accepted.
- Strings formally escape a literal quote/ampersand as an HTML-style entity (`&quot;`, `&amp;`,
  `&#NNN;`) -- accepted here -- but at least one other graph tool (graph-tool) instead writes
  backslash-escaped quotes (`\\"`), which conflicts with the spec but shows up in real files often
  enough to be worth reading too, so both conventions are accepted.
- A string may contain a literal newline (some writers do this for multi-line labels).
- `#` starts a line comment (to end of line), outside of a quoted string.

GML has no native concept of multiple edge-type layers or time-varying data -- it predates
multilayer/temporal network research by a couple of decades. `layer_key=`/`t_start_key=`/
`t_end_key=` name a custom GML edge attribute to read as plexgraph's layer/t_start/t_end, the same
convention-based approach `read_edgelist` already uses for arbitrary extra columns; left as None
(the default), the graph is read as plain/static.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plexgraph_core.model.ir import Graph

GMLValue = "int | float | str | list[tuple[str, 'GMLValue']]"
GMLList = list[tuple[str, Any]]  # a parsed `[ ... ]` block: ordered (key, value) pairs, duplicates allowed


class GMLParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # "KEY" | "INT" | "REAL" | "STRING" | "[" | "]"
    text: str
    line: int


_TOKEN_PATTERN = re.compile(
    r"""
    (?P<WS>\s+)
  | \#(?P<COMMENT>[^\n]*)
  | "(?P<STRING>(?:[^"\\]|\\.)*)"
  | (?P<REAL>-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+[eE][+-]?\d+)
  | (?P<INT>-?\d+)
  | (?P<KEY>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<LBRACKET>\[)
  | (?P<RBRACKET>\])
    """,
    re.VERBOSE,
)

_ENTITIES = {"quot": '"', "amp": "&", "apos": "'", "lt": "<", "gt": ">"}
_ENTITY_PATTERN = re.compile(r"&(#\d+|[A-Za-z]+);")


def _decode_string(raw: str) -> str:
    """Resolve both of GML's real-world quoting conventions: HTML-style entities (the spec's own
    way) and backslash escapes (what at least graph-tool writes instead)."""
    text = re.sub(r"\\(.)", r"\1", raw)  # backslash-escaped char -> the literal char

    def entity(match: re.Match[str]) -> str:
        body = match.group(1)
        if body.startswith("#"):
            return chr(int(body[1:]))
        return _ENTITIES.get(body, match.group(0))

    return _ENTITY_PATTERN.sub(entity, text)


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_PATTERN.match(text, pos)
        if match is None:
            line = text.count("\n", 0, pos) + 1
            raise GMLParseError(f"line {line}: unrecognized character {text[pos]!r}")
        pos = match.end()
        kind = match.lastgroup
        assert kind is not None
        if kind in ("WS", "COMMENT"):
            continue
        line = text.count("\n", 0, match.start()) + 1
        value = match.group(kind)
        if kind in ("LBRACKET", "RBRACKET"):
            tokens.append(_Token(value, value, line))
        else:
            tokens.append(_Token(kind, value, line))
    return tokens


def _parse_value(tokens: list[_Token], pos: int) -> tuple[Any, int]:
    if pos >= len(tokens):
        raise GMLParseError("unexpected end of file: expected a value")
    tok = tokens[pos]
    if tok.kind == "[":
        return _parse_list(tokens, pos)
    if tok.kind == "INT":
        return int(tok.text), pos + 1
    if tok.kind == "REAL":
        return float(tok.text), pos + 1
    if tok.kind == "STRING":
        return _decode_string(tok.text), pos + 1
    raise GMLParseError(f"line {tok.line}: expected a value (int, real, string or '['), got {tok.text!r}")


def _parse_list(tokens: list[_Token], pos: int) -> tuple[GMLList, int]:
    assert tokens[pos].kind == "["
    pos += 1
    entries: GMLList = []
    while True:
        if pos >= len(tokens):
            raise GMLParseError("unexpected end of file: unterminated '['")
        if tokens[pos].kind == "]":
            return entries, pos + 1
        key_tok = tokens[pos]
        if key_tok.kind != "KEY":
            raise GMLParseError(f"line {key_tok.line}: expected a key, got {key_tok.text!r}")
        value, pos = _parse_value(tokens, pos + 1)
        entries.append((key_tok.text, value))


def parse_gml(text: str) -> GMLList:
    """Parse raw GML text into its top-level (key, value) pairs, in file order. Low-level: most
    callers want `read_gml`, which also builds a plexgraph Graph from the parsed `graph` block."""
    tokens = _tokenize(text)
    entries: GMLList = []
    pos = 0
    while pos < len(tokens):
        key_tok = tokens[pos]
        if key_tok.kind != "KEY":
            raise GMLParseError(f"line {key_tok.line}: expected a key, got {key_tok.text!r}")
        value, pos = _parse_value(tokens, pos + 1)
        entries.append((key_tok.text, value))
    return entries


def _first(entries: GMLList, key: str) -> Any | None:
    for k, v in entries:
        if k == key:
            return v
    return None


def _all(entries: GMLList, key: str) -> list[Any]:
    return [v for k, v in entries if k == key]


def read_gml(
    path: str | Path,
    *,
    directed: bool | None = None,
    label_as_key: bool = False,
    weight_key: str | None = "weight",
    layer_key: str | None = None,
    t_start_key: str | None = None,
    t_end_key: str | None = None,
) -> Graph:
    """Read a GML file into a Graph.

    A node's plexgraph key is its GML `id` by default -- always present and unique per the spec,
    so never ambiguous. `label_as_key=True` uses the `label` attribute instead (closer to what some
    other tools default to), at the cost of raising if two nodes share a label or a node has none.

    `directed=None` (the default) reads the graph's own top-level `directed 1`/`directed 0` key
    (false if absent, the GML default); pass True/False to override it. `weight_key` names the edge
    attribute read as plexgraph's `weight` (GML has no single standard name for it across writers;
    "weight" is the common one, "value" is another you may need to pass explicitly). `layer_key`/
    `t_start_key`/`t_end_key` name edge attributes to read as plexgraph's layer/t_start/t_end --
    GML has no native concept of either, so these do nothing unless you tell it where to look.
    Every other node/edge attribute is kept as-is, with its GML type (int, float or str) -- except
    the literal names "weight"/"layer"/"t_start"/"t_end" themselves, which are always dropped (not
    kept as a generic attribute) even if a *different* custom key was configured for that role, since
    either reading would collide with the connector's own weight/layer/t_start/t_end. A file with
    both, e.g., a real "weight" attribute and a separately-configured weight_key="value", loses the
    "weight" one -- an unusual enough file shape that this is treated as an acceptable, documented
    edge case rather than complicated machinery to rename around it.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        top = parse_gml(text)
    except GMLParseError as e:
        raise GMLParseError(f"{path}: {e}") from None

    graph_block = _first(top, "graph")
    if graph_block is None:
        raise GMLParseError(f"{path}: no top-level 'graph' key found")

    is_directed = bool(_first(graph_block, "directed")) if directed is None else directed

    g = Graph()
    node_key_by_id: dict[int, Any] = {}
    seen_labels: set[Any] = set()
    for node in _all(graph_block, "node"):
        gml_id = _first(node, "id")
        if gml_id is None:
            raise GMLParseError(f"{path}: a 'node' block has no 'id'")
        label = _first(node, "label")
        if label_as_key:
            if label is None:
                raise GMLParseError(f"{path}: node {gml_id} has no 'label', but label_as_key=True")
            if label in seen_labels:
                raise GMLParseError(f"{path}: node label {label!r} is not unique, but label_as_key=True")
            seen_labels.add(label)
            key = label
        else:
            key = gml_id
        attrs = {k: v for k, v in node if k not in ("id",)}
        node_key_by_id[gml_id] = key
        g.add_node(key, **attrs)

    edges = _all(graph_block, "edge")
    layer_id_by_value: dict[Any, int] = {}
    if layer_key:
        # Layers must be declared with add_layer() before add_edge() can reference one -- unlike a
        # node id, a layer value isn't resolved/created on first use, so every distinct value seen
        # across all edges is declared once, in first-seen order, before any edge is added.
        for edge in edges:
            value = _first(edge, layer_key)
            if value is not None and value not in layer_id_by_value:
                layer_id_by_value[value] = g.add_layer(value)

    for edge in edges:
        source, target = _first(edge, "source"), _first(edge, "target")
        if source is None or target is None:
            raise GMLParseError(f"{path}: an 'edge' block is missing 'source' or 'target'")
        if source not in node_key_by_id or target not in node_key_by_id:
            raise GMLParseError(f"{path}: edge references a node id not defined by any 'node' block")
        # Exclude both the GML keys already read out explicitly above (source/target) and every name
        # that would collide with add_edge()'s own keyword arguments: the caller's configured
        # weight_key/layer_key/t_start_key/t_end_key (which may be a custom name like "value"), and
        # separately the literal names "weight"/"layer"/"t_start"/"t_end"/"directed" themselves, in
        # case the file happens to use one of those regardless of what custom key was configured.
        reserved = {"source", "target", "directed", "weight", "layer", "t_start", "t_end",
                    weight_key, layer_key, t_start_key, t_end_key}
        attrs = {k: v for k, v in edge if k not in reserved}
        layer_value = _first(edge, layer_key) if layer_key else None
        g.add_edge(
            node_key_by_id[source],
            node_key_by_id[target],
            directed=is_directed,
            weight=_first(edge, weight_key) if weight_key else None,
            layer=layer_id_by_value[layer_value] if layer_value is not None else None,
            t_start=_first(edge, t_start_key) if t_start_key else None,
            t_end=_first(edge, t_end_key) if t_end_key else None,
            **attrs,
        )

    return g
