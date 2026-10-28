"""Native DOT (the Graphviz language) reader -- no external dependency.

Spec: https://graphviz.org/doc/info/lang.html. DOT's own grammar is large (ports, HTML-like labels,
record shapes, subgraph-as-edge-endpoint cross products) -- this reads a **practical subset**, the
shape the overwhelming majority of real DOT files actually use, rather than the full language.

In scope: `strict? (graph|digraph) ID? '{' stmt_list '}'`; node/edge statements, including edge
chains (`a -> b -> c`) and multiple trailing `[...]` attribute-list groups; `graph`/`node`/`edge
[...]` statements, which set the *default* attributes for nodes/edges declared afterward in the
same (sub)graph scope -- "a subgraph … receives [its parent's] attribute settings … at the time of
[its] definition" (the spec's own wording): defaults are copied into a subgraph, and changes inside
it do not leak back out. Named and anonymous subgraphs (`subgraph name? { ... }`, bare `{ ... }`)
are flattened into the main graph -- their nodes/edges/attribute defaults merge in directly, with no
nested-graph concept created (plexgraph's IR has none; this is the same treatment `read_graphml`
gives its own hierarchical, nested `<graph>` elements). A node mentioned more than once merges its
attributes rather than raising; one appearing only as an edge endpoint is auto-created. Comments are
`//` and `/* */` (C-style) and `#`-to-end-of-line ("Python/Shell style", the spec's own description,
despite the superficial resemblance to a C preprocessor line). Quoted strings support `+`
concatenation and backslash-newline continuation; `\\"` is the only recognized escape (the spec:
"the only escaped character is double-quote") -- any other backslash sequence passes through
unchanged, backslash included. Ports (`node:port` / `node:port:compass`) are stripped when resolving
a node's identity -- they only affect where Graphviz draws an edge on a shape, not graph topology.
An HTML-like label (`<...>`) is kept as one opaque string, not parsed as HTML -- a reasonable and
precedented scope limit (pydot itself does the same).

Out of scope, raising a clear, located error rather than guessing: a subgraph used as an edge
endpoint (`{a,b} -> c`, `a -> subgraph_B`), DOT's way of saying "connect every node on one side to
every node on the other" -- a real complexity jump for an uncommon pattern (most files write one
node per endpoint). The `strict` keyword is accepted but not enforced -- duplicate edges are not
collapsed into one. Top-level `ID = ID` assignments (e.g. `rankdir=LR`) and `graph [...]` attribute
statements are parsed but discarded -- they are Graphviz rendering metadata with no plexgraph
representation, the same treatment other native readers give data they cannot carry over.

DOT has no native multiplex or temporal concept at all -- it is a static visual-layout language.
`layer_key=`/`t_start_key=`/`t_end_key=` name a custom node/edge attribute to read as plexgraph's
layer/t_start/t_end, the same convention `read_gml`/`read_graphml` already use for the same reason.
Unlike those formats, DOT's own attribute values are always plain strings (it has no `<key>`/type
system) -- `weight_key`/`t_start_key`/`t_end_key` values are coerced to `float` here, with a clear
error if that fails; every other attribute is kept as the string DOT wrote.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plexgraph_core.model.ir import Graph


class DOTParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # "ID" | "STRING" | "NUMERAL" | "HTMLSTRING" | "EDGEOP" | a literal punctuation char
    text: str
    line: int


_SKIP_PATTERN = re.compile(
    r"""
    \s+
  | //[^\n]*
  | \#[^\n]*
  | /\*.*?\*/
    """,
    re.VERBOSE | re.DOTALL,
)

_TOKEN_PATTERN = re.compile(
    r"""
    "(?P<STRING>(?:[^"\\]|\\.)*)"
  | (?P<EDGEOP>->|--)
  | (?P<NUMERAL>-?(?:\.\d+|\d+\.\d*|\d+))
  | (?P<ID>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<PUNCT>[{}\[\];,=:+])
    """,
    re.VERBOSE | re.DOTALL,
)


def _decode_dot_string(raw: str) -> str:
    out = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "\n":  # backslash-newline: a line continuation, not part of the value
                i += 2
                continue
            if nxt == '"':  # the only recognized escape
                out.append('"')
                i += 2
                continue
            out.append(c)  # any other backslash sequence passes through unchanged
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _scan_html_string(text: str, pos: int, path: Any) -> int:
    depth, i, n = 0, pos, len(text)
    while i < n:
        if text[i] == "<":
            depth += 1
        elif text[i] == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    line = text.count("\n", 0, pos) + 1
    raise DOTParseError(f"{path}: line {line}: unterminated HTML-like label '<'")


def _tokenize(text: str, path: Any) -> list[_Token]:
    tokens: list[_Token] = []
    pos, n = 0, len(text)
    while pos < n:
        m = _SKIP_PATTERN.match(text, pos)
        if m:
            pos = m.end()
            continue
        if text[pos] == "<":
            line = text.count("\n", 0, pos) + 1
            end = _scan_html_string(text, pos, path)
            tokens.append(_Token("HTMLSTRING", text[pos:end], line))
            pos = end
            continue
        m = _TOKEN_PATTERN.match(text, pos)
        if not m:
            line = text.count("\n", 0, pos) + 1
            raise DOTParseError(f"{path}: line {line}: unrecognized character {text[pos]!r}")
        line = text.count("\n", 0, m.start()) + 1
        kind = m.lastgroup
        if kind == "PUNCT":
            p = m.group("PUNCT")
            tokens.append(_Token(p, p, line))
        else:
            assert kind is not None
            tokens.append(_Token(kind, m.group(kind), line))
        pos = m.end()
    return tokens


def _read_id_value(tokens: list[_Token], pos: int, path: Any) -> tuple[str, int]:
    if pos >= len(tokens):
        raise DOTParseError(f"{path}: unexpected end of file: expected an identifier")
    tok = tokens[pos]
    if tok.kind == "STRING":
        value = _decode_dot_string(tok.text)
        pos += 1
        while pos + 1 < len(tokens) and tokens[pos].kind == "+" and tokens[pos + 1].kind == "STRING":
            value += _decode_dot_string(tokens[pos + 1].text)
            pos += 2
        return value, pos
    if tok.kind in ("ID", "NUMERAL", "HTMLSTRING"):
        return tok.text, pos + 1
    raise DOTParseError(f"{path}: line {tok.line}: expected an identifier, got {tok.text!r}")


def _read_node_id(tokens: list[_Token], pos: int, path: Any) -> tuple[str, int]:
    key, pos = _read_id_value(tokens, pos, path)
    if pos < len(tokens) and tokens[pos].kind == ":":
        pos += 1
        _, pos = _read_id_value(tokens, pos, path)  # port, ignored -- edge routing only
        if pos < len(tokens) and tokens[pos].kind == ":":
            pos += 1
            _, pos = _read_id_value(tokens, pos, path)  # compass point, ignored
    return key, pos


def _parse_attr_list(tokens: list[_Token], pos: int, path: Any) -> tuple[dict[str, Any], int]:
    attrs: dict[str, Any] = {}
    while pos < len(tokens) and tokens[pos].kind == "[":
        pos += 1
        while True:
            if pos >= len(tokens):
                raise DOTParseError(f"{path}: unexpected end of file: unterminated '['")
            if tokens[pos].kind == "]":
                pos += 1
                break
            key, pos = _read_id_value(tokens, pos, path)
            if not (pos < len(tokens) and tokens[pos].kind == "="):
                raise DOTParseError(f"{path}: expected '=' after attribute name {key!r}")
            pos += 1
            value, pos = _read_id_value(tokens, pos, path)
            attrs[key] = value
            if pos < len(tokens) and tokens[pos].kind in (",", ";"):
                pos += 1
    return attrs, pos


@dataclass
class _ParseState:
    node_attrs: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)


def _declare_node(state: _ParseState, key: str, defaults: dict[str, Any], attrs: dict[str, Any]) -> None:
    if key not in state.node_attrs:
        state.node_attrs[key] = dict(defaults)
    state.node_attrs[key].update(attrs)


def _parse_endpoint(tokens: list[_Token], pos: int, path: Any) -> tuple[str, int]:
    tok = tokens[pos]
    if (tok.kind == "ID" and tok.text.lower() == "subgraph") or tok.kind == "{":
        raise DOTParseError(f"{path}: line {tok.line}: a subgraph used as an edge endpoint is not supported")
    return _read_node_id(tokens, pos, path)


def _parse_subgraph(
    tokens: list[_Token], pos: int, path: Any, state: _ParseState,
    node_defaults: dict[str, Any], edge_defaults: dict[str, Any],
) -> int:
    if tokens[pos].kind == "ID" and tokens[pos].text.lower() == "subgraph":
        pos += 1
        if pos < len(tokens) and tokens[pos].kind in ("ID", "STRING", "NUMERAL"):
            pos += 1  # subgraph name, discarded -- flattened, not modeled as a nested graph
    if not (pos < len(tokens) and tokens[pos].kind == "{"):
        raise DOTParseError(f"{path}: line {tokens[pos].line}: expected '{{' to start a subgraph")
    pos += 1
    return _parse_block_body(tokens, pos, path, state, dict(node_defaults), dict(edge_defaults))


def _parse_stmt(
    tokens: list[_Token], pos: int, path: Any, state: _ParseState,
    node_defaults: dict[str, Any], edge_defaults: dict[str, Any],
) -> int:
    tok = tokens[pos]

    if (tok.kind == "ID" and tok.text.lower() == "subgraph") or tok.kind == "{":
        pos2 = _parse_subgraph(tokens, pos, path, state, node_defaults, edge_defaults)
        if pos2 < len(tokens) and tokens[pos2].kind == "EDGEOP":
            raise DOTParseError(f"{path}: line {tokens[pos2].line}: "
                                 "a subgraph used as an edge endpoint is not supported")
        return pos2

    if tok.kind == "ID" and tok.text.lower() in ("graph", "node", "edge") and \
            pos + 1 < len(tokens) and tokens[pos + 1].kind == "[":
        scope = tok.text.lower()
        attrs, pos = _parse_attr_list(tokens, pos + 1, path)
        if scope == "node":
            node_defaults.update(attrs)
        elif scope == "edge":
            edge_defaults.update(attrs)
        # scope == "graph": rendering-only graph defaults, no plexgraph representation -- discarded
        return pos

    if tok.kind in ("ID", "STRING", "NUMERAL") and pos + 1 < len(tokens) and tokens[pos + 1].kind == "=":
        _, pos = _read_id_value(tokens, pos, path)
        pos += 1
        _, pos = _read_id_value(tokens, pos, path)
        return pos

    first, pos = _parse_endpoint(tokens, pos, path)
    chain = [first]
    while pos < len(tokens) and tokens[pos].kind == "EDGEOP":
        pos += 1
        nxt, pos = _parse_endpoint(tokens, pos, path)
        chain.append(nxt)
    attrs, pos = _parse_attr_list(tokens, pos, path)

    if len(chain) == 1:
        _declare_node(state, chain[0], node_defaults, attrs)
    else:
        merged_attrs = {**edge_defaults, **attrs}
        for a, b in zip(chain, chain[1:]):
            _declare_node(state, a, node_defaults, {})
            _declare_node(state, b, node_defaults, {})
            state.edges.append((a, b, dict(merged_attrs)))
    return pos


def _parse_block_body(
    tokens: list[_Token], pos: int, path: Any, state: _ParseState,
    node_defaults: dict[str, Any], edge_defaults: dict[str, Any],
) -> int:
    """Parse statements starting right after an already-consumed opening '{', returning the
    position right after the matching closing '}'."""
    while True:
        if pos >= len(tokens):
            raise DOTParseError(f"{path}: unexpected end of file: unterminated '{{'")
        if tokens[pos].kind == "}":
            return pos + 1
        if tokens[pos].kind == ";":
            pos += 1
            continue
        pos = _parse_stmt(tokens, pos, path, state, node_defaults, edge_defaults)


def read_dot(
    path: str | Path,
    *,
    directed: bool | None = None,
    label_as_key: bool = False,
    weight_key: str | None = "weight",
    layer_key: str | None = None,
    t_start_key: str | None = None,
    t_end_key: str | None = None,
) -> Graph:
    """Read a DOT file into a Graph -- see the module docstring for exactly what subset of the DOT
    language this covers.

    A node's plexgraph key is its DOT id by default. `label_as_key=True` uses its `label` attribute
    instead, raising if a node has none or two nodes share one (same tradeoff as `read_gml`'s
    `label_as_key`). `directed=None` (the default) reads it from the file's own `graph`/`digraph`
    keyword; pass True/False to override it -- DOT has no per-edge direction override of its own.

    `weight_key`/`layer_key`/`t_start_key`/`t_end_key` name a node/edge attribute read as
    plexgraph's weight/layer/t_start/t_end -- DOT has no native concept of any of them. Unlike
    `read_gml`, whose GML source already types its values, DOT attribute values are always plain
    strings; `weight_key`/`t_start_key`/`t_end_key` are coerced to `float` here (raising clearly if
    that fails), while `layer_key`'s value is used as-is. Every other attribute is kept as the
    string DOT wrote it -- except the literal names "weight"/"layer"/"t_start"/"t_end" themselves,
    always dropped from an edge's generic attrs even if a *different* custom key was configured for
    that role, the same documented edge case `read_gml` accepts for the same reason.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    tokens = _tokenize(text, path)

    pos = 0
    if pos < len(tokens) and tokens[pos].kind == "ID" and tokens[pos].text.lower() == "strict":
        pos += 1
    if not (pos < len(tokens) and tokens[pos].kind == "ID" and tokens[pos].text.lower() in ("graph", "digraph")):
        raise DOTParseError(f"{path}: expected 'graph' or 'digraph'")
    file_is_directed = tokens[pos].text.lower() == "digraph"
    is_directed = file_is_directed if directed is None else directed
    pos += 1
    if pos < len(tokens) and tokens[pos].kind in ("ID", "STRING", "NUMERAL"):
        pos += 1  # graph name, discarded
    if not (pos < len(tokens) and tokens[pos].kind == "{"):
        raise DOTParseError(f"{path}: expected '{{' to start the graph body")
    pos += 1

    state = _ParseState()
    _parse_block_body(tokens, pos, path, state, {}, {})

    g = Graph()
    node_key_by_dot_id: dict[str, Any] = {}
    seen_labels: set[Any] = set()
    for dot_id, attrs in state.node_attrs.items():
        if label_as_key:
            label = attrs.get("label")
            if label is None:
                raise DOTParseError(f"{path}: node {dot_id!r} has no 'label', but label_as_key=True")
            if label in seen_labels:
                raise DOTParseError(f"{path}: node label {label!r} is not unique, but label_as_key=True")
            seen_labels.add(label)
            key = label
        else:
            key = dot_id
        node_key_by_dot_id[dot_id] = key
        g.add_node(key, **attrs)

    layer_id_by_value: dict[Any, int] = {}
    if layer_key:
        for _, _, attrs in state.edges:
            value = attrs.get(layer_key)
            if value is not None and value not in layer_id_by_value:
                layer_id_by_value[value] = g.add_layer(value)

    def _as_float(value: Any, name: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            raise DOTParseError(f"{path}: attribute {name!r} value {value!r} is not a number") from None

    reserved = {"weight", "layer", "t_start", "t_end", weight_key, layer_key, t_start_key, t_end_key}
    for source, target, attrs in state.edges:
        layer_value = attrs.get(layer_key) if layer_key else None
        plain_attrs = {k: v for k, v in attrs.items() if k not in reserved}
        g.add_edge(
            node_key_by_dot_id[source],
            node_key_by_dot_id[target],
            directed=is_directed,
            weight=_as_float(attrs.get(weight_key), weight_key) if weight_key else None,
            layer=layer_id_by_value[layer_value] if layer_value is not None else None,
            t_start=_as_float(attrs.get(t_start_key), t_start_key) if t_start_key else None,
            t_end=_as_float(attrs.get(t_end_key), t_end_key) if t_end_key else None,
            **plain_attrs,
        )

    return g
