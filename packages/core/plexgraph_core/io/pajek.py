"""Native Pajek .net reader -- no external dependency beyond the standard library's `shlex`.

Pajek's own format documentation (vlado.fmf.uni-lj.si) was unreachable while writing this (every
path and mirror tried refused the connection or served an expired certificate), so this is grounded
in two things actually read: networkx's own reader (`networkx/readwrite/pajek.py`) as a real-world
reference, and Tom A.B. Snijders' (Oxford) "Short Introduction to Pajek" slides, which include
worked `.net` examples for every section kind handled below.

A `.net` file is `*Vertices N` (exactly N following vertex lines), then one or more relation
sections: `*Arcs`/`*Edges` (an edge-list, one `source target [weight]` per line), `*Arcslist`/
`*Edgeslist` (an adjacency list, one `source target1 target2 ...` per line, never weighted), or
`*Matrix` (a dense N-by-N adjacency matrix, bounded by the `*Vertices` count). `*Arcs`-type sections
are directed, `*Edges`-type are undirected -- per *section*, not a single file-wide flag (unlike
`read_gml`/`read_graphml`/`read_dot`'s one `directed=`). `*Matrix` is always directed, even when
the matrix looks symmetric (Snijders' own slide: "Even if the matrix is symmetric, it still defines
a digraph").

**Genuinely native multiplex support, unlike `read_gml`/`read_graphml`/`read_dot`: more than one
relation section in a file becomes one plexgraph Layer per section** (named from a quoted label on
the section's own header line, e.g. `*Arcs "friendship"`, falling back to an auto-numbered index
when absent or repeated) -- no `layer_key=` configuration needed, because Pajek already says this
natively. A file with only one relation section (the common case) stays a flat graph with
`layer_id=None`, matching every other reader's "nothing configured" behavior. Unrecognized section
headers (`*Partition`, `*Vector`, `*Permutation`, `*Cluster`, `*Hierarchy` -- clustering/partition
results, not graph structure) are skipped gracefully rather than raised on or misparsed as edges --
fixing a real bug in networkx's own reader, which has no check for a following `*`-prefixed header
inside its edge-reading loop and silently swallows anything after the first relation section,
including a subsequent `*Partition` block parsed as bogus edges.

Pajek has no native temporal concept; `t_start_key`/`t_end_key` name a custom trailing key on an
edge/arc line to read as plexgraph's t_start/t_end, the same convention `read_gml` uses for the same
reason. Unlike those readers there is no `weight_key` here: Pajek's weight is a fixed *position*
(the third column on an edge/arc line, read directly when present and numeric), not a named
attribute, so there is nothing to rename.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from plexgraph_core.model.ir import Graph

_SECTION_PATTERN = re.compile(r"^\*(\w+)\s*(.*)$")
_QUOTED_PATTERN = re.compile(r'"([^"]*)"')


class PajekParseError(ValueError):
    pass


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("%")


def _quoted_label(rest: str) -> str | None:
    m = _QUOTED_PATTERN.search(rest)
    return m.group(1) if m else None


def _parse_vertex_line(tokens: list[str]) -> tuple[str, bool, str, dict[str, Any]]:
    vid = tokens[0]
    has_label = len(tokens) > 1
    label = tokens[1] if has_label else vid
    rest = tokens[2:]
    attrs: dict[str, Any] = {}
    if len(rest) >= 3:
        try:
            attrs["x"], attrs["y"] = float(rest[0]), float(rest[1])
            attrs["shape"] = rest[2]
            rest = rest[3:]
        except ValueError:
            pass
    elif len(rest) == 2:
        try:
            attrs["x"], attrs["y"] = float(rest[0]), float(rest[1])
            rest = []
        except ValueError:
            pass
    it = iter(rest)
    for k, v in zip(it, it):
        attrs[k] = v
    attrs.setdefault("label", label)
    return vid, has_label, label, attrs


def _parse_relation_row(tokens: list[str]) -> tuple[str, str, float | None, dict[str, Any]] | None:
    if len(tokens) < 2:
        return None
    source, target = tokens[0], tokens[1]
    rest = tokens[2:]
    weight: float | None = None
    if rest:
        try:
            weight = float(rest[0])
            rest = rest[1:]
        except ValueError:
            weight = None
    attrs: dict[str, Any] = {}
    it = iter(rest)
    for k, v in zip(it, it):
        attrs[k] = v
    return source, target, weight, attrs


def read_pajek(
    path: str | Path,
    *,
    directed: bool | None = None,
    label_as_key: bool = False,
    t_start_key: str | None = None,
    t_end_key: str | None = None,
) -> Graph:
    """Read a Pajek .net file into a Graph -- see the module docstring for the exact section
    handling and why this needs no `layer_key` (unlike `read_gml`/`read_graphml`/`read_dot`).

    A node's plexgraph key is its Pajek vertex id by default. `label_as_key=True` uses its label
    instead (the second token on its `*Vertices` line), raising if a vertex has none or two share
    one (same tradeoff as `read_gml`'s `label_as_key`); either way, the label is kept as a plain
    `label` node attribute too.

    `directed=None` (the default) lets each relation section's own kind decide: `*Arcs`/`*Arcslist`
    are directed, `*Edges`/`*Edgeslist` are undirected, `*Matrix` is always directed; pass True/False
    to override every section uniformly. `t_start_key`/`t_end_key` name a trailing `key value` pair
    on an edge/arc line to read as plexgraph's t_start/t_end -- Pajek has no native concept of
    either. The optional weight column on an edge/arc line (the token right after source/target,
    when it parses as a number) is always read directly as the connector's weight.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    n = len(lines)

    i = 0
    while i < n and _is_comment_or_blank(lines[i]):
        i += 1
    if i >= n:
        raise PajekParseError(f"{path}: no '*Vertices' section found")
    m = _SECTION_PATTERN.match(lines[i].strip())
    if not m or m.group(1).lower() != "vertices":
        raise PajekParseError(f"{path}: expected a '*Vertices' section, got {lines[i].strip()!r}")
    try:
        num_vertices = int(m.group(2).split()[0])
    except (ValueError, IndexError):
        raise PajekParseError(f"{path}: '*Vertices' has no vertex count") from None
    i += 1

    g = Graph()
    node_key_by_id: dict[str, Any] = {}
    seen_labels: set[Any] = set()
    read_rows = 0
    while read_rows < num_vertices:
        if i >= n:
            raise PajekParseError(f"{path}: expected {num_vertices} vertices, found {read_rows}")
        if _is_comment_or_blank(lines[i]):
            i += 1
            continue
        tokens = shlex.split(lines[i])
        i += 1
        if not tokens:
            continue
        read_rows += 1
        vid, has_label, label, attrs = _parse_vertex_line(tokens)
        if label_as_key:
            if not has_label:
                raise PajekParseError(f"{path}: vertex {vid!r} has no label, but label_as_key=True")
            if label in seen_labels:
                raise PajekParseError(f"{path}: vertex label {label!r} is not unique, but label_as_key=True")
            seen_labels.add(label)
            key = label
        else:
            key = vid
        node_key_by_id[vid] = key
        g.add_node(key, **attrs)

    sections: list[dict[str, Any]] = []
    while i < n:
        if _is_comment_or_blank(lines[i]):
            i += 1
            continue
        m = _SECTION_PATTERN.match(lines[i].strip())
        if not m:
            raise PajekParseError(f"{path}: line {i + 1}: expected a section header starting with '*'")
        kind = m.group(1).lower()
        relation_label = _quoted_label(m.group(2))
        i += 1
        if kind in ("arcs", "edges", "arcslist", "edgeslist"):
            rows = []
            while i < n and not lines[i].strip().startswith("*"):
                if not _is_comment_or_blank(lines[i]):
                    tokens = shlex.split(lines[i])
                    if tokens:
                        rows.append(tokens)
                i += 1
            sections.append({"kind": kind, "label": relation_label, "rows": rows})
        elif kind == "matrix":
            rows = []
            count = 0
            while i < n and count < num_vertices:
                if _is_comment_or_blank(lines[i]):
                    i += 1
                    continue
                rows.append(shlex.split(lines[i]))
                i += 1
                count += 1
            sections.append({"kind": "matrix", "label": relation_label, "rows": rows})
        else:
            # *Partition/*Vector/*Permutation/*Cluster/*Hierarchy/etc: clustering results, not graph
            # structure -- skip to the next '*'-prefixed line rather than misreading them as edges.
            while i < n and not lines[i].strip().startswith("*"):
                i += 1

    relation_sections = [s for s in sections if s["kind"] in ("arcs", "edges", "arcslist", "edgeslist", "matrix")]
    multiple_relations = len(relation_sections) > 1
    layer_id_by_index: dict[int, int] = {}
    if multiple_relations:
        used_layer_keys: set[Any] = set()
        for idx, s in enumerate(relation_sections):
            layer_key_value = s["label"] if s["label"] is not None and s["label"] not in used_layer_keys else idx
            used_layer_keys.add(layer_key_value)
            layer_id_by_index[idx] = g.add_layer(layer_key_value)

    def _as_float(value: Any, name: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            raise PajekParseError(f"{path}: attribute {name!r} value {value!r} is not a number") from None

    vertex_ids = list(node_key_by_id.keys())
    reserved = {"t_start", "t_end", t_start_key, t_end_key}
    for idx, s in enumerate(relation_sections):
        kind = s["kind"]
        layer = layer_id_by_index.get(idx) if multiple_relations else None

        if kind in ("arcs", "edges"):
            section_directed = (kind == "arcs") if directed is None else directed
            for tokens in s["rows"]:
                parsed = _parse_relation_row(tokens)
                if parsed is None:
                    continue
                source, target, weight, attrs = parsed
                if source not in node_key_by_id or target not in node_key_by_id:
                    raise PajekParseError(f"{path}: an edge references a vertex id not defined by '*Vertices'")
                plain_attrs = {k: v for k, v in attrs.items() if k not in reserved}
                g.add_edge(
                    node_key_by_id[source], node_key_by_id[target],
                    directed=section_directed, weight=weight, layer=layer,
                    t_start=_as_float(attrs.get(t_start_key), t_start_key) if t_start_key else None,
                    t_end=_as_float(attrs.get(t_end_key), t_end_key) if t_end_key else None,
                    **plain_attrs,
                )
        elif kind in ("arcslist", "edgeslist"):
            section_directed = (kind == "arcslist") if directed is None else directed
            for tokens in s["rows"]:
                if len(tokens) < 2:
                    continue
                source = tokens[0]
                if source not in node_key_by_id:
                    raise PajekParseError(f"{path}: an edge references a vertex id not defined by '*Vertices'")
                for target in tokens[1:]:
                    if target not in node_key_by_id:
                        raise PajekParseError(f"{path}: an edge references a vertex id not defined by '*Vertices'")
                    g.add_edge(node_key_by_id[source], node_key_by_id[target], directed=section_directed, layer=layer)
        else:  # matrix
            section_directed = True if directed is None else directed
            for row_i, tokens in enumerate(s["rows"]):
                for col_j, token in enumerate(tokens):
                    try:
                        value = float(token)
                    except ValueError:
                        raise PajekParseError(f"{path}: a '*Matrix' entry {token!r} is not a number") from None
                    if value == 0:
                        continue
                    if row_i >= len(vertex_ids) or col_j >= len(vertex_ids):
                        raise PajekParseError(f"{path}: '*Matrix' has more entries than vertices")
                    g.add_edge(
                        node_key_by_id[vertex_ids[row_i]], node_key_by_id[vertex_ids[col_j]],
                        directed=section_directed, weight=value, layer=layer,
                    )

    return g
