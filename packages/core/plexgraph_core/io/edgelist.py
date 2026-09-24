"""Build a Graph from plain edge lists, a delimited text file, or a pandas DataFrame — the common
"I already have my data in some other shape" starting points that aren't a networkx object (see
networkx_io.py for that path). For `(u, v, t)` rows with a timestamp, see temporal.py instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Hashable, Iterable, Mapping, Sequence

from plexgraph_core.io._text import attr_value, ensure_node, node_key, read_rows, warn_skipped_hyperedges
from plexgraph_core.model.ir import Graph


def from_edgelist(
    edges: Iterable[Sequence[Any]],
    *,
    directed: bool = False,
) -> Graph:
    """Build a Graph from an iterable of edges. Each edge is one of:
    - (u, v)
    - (u, v, weight)
    - (u, v, attrs_dict)

    A third number is a *weight*, not a timestamp. For `(u, v, t)` temporal events use
    `from_temporal_edgelist`.

    Nodes are created automatically the first time each key is seen, using
    the edge-list value itself as the node's key (so node identity is
    whatever hashable value you passed — a string, an int, a tuple, ...).
    """
    g = Graph()
    known: set[Hashable] = set()

    for edge in edges:
        if len(edge) == 2:
            u, v = edge
            ensure_node(g, known, u)
            ensure_node(g, known, v)
            g.add_edge(u, v, directed=directed)
        elif len(edge) == 3:
            u, v, extra = edge
            ensure_node(g, known, u)
            ensure_node(g, known, v)
            if isinstance(extra, dict):
                g.add_edge(u, v, directed=directed, **extra)
            else:
                g.add_edge(u, v, directed=directed, weight=float(extra))
        else:
            raise ValueError(f"edge tuple must have length 2 or 3, got {len(edge)}: {edge!r}")

    return g


def from_pandas_edgelist(
    df: Any,
    source: str = "source",
    target: str = "target",
    *,
    edge_attr: str | list[str] | bool | None = None,
    directed: bool = False,
) -> Graph:
    """Build a Graph from a pandas DataFrame with one row per edge.

    edge_attr controls which columns (besides source/target) become
    connector attrs: a column name, a list of column names, True for every
    remaining column, or None (the default) for no extra attrs.
    """
    if edge_attr is True:
        attr_columns = [c for c in df.columns if c not in (source, target)]
    elif edge_attr is None or edge_attr is False:
        attr_columns = []
    elif isinstance(edge_attr, str):
        attr_columns = [edge_attr]
    else:
        attr_columns = list(edge_attr)

    g = Graph()
    known: set[Hashable] = set()

    for row in df.itertuples(index=False):
        row_dict = row._asdict()
        u = row_dict[source]
        v = row_dict[target]
        ensure_node(g, known, u)
        ensure_node(g, known, v)
        attrs = {c: row_dict[c] for c in attr_columns}
        weight = attrs.pop("weight", None)
        g.add_edge(u, v, directed=directed, weight=weight, **attrs)

    return g


def read_edgelist(
    path: str | Path,
    *,
    delimiter: str | None = None,
    comments: str = "#",
    header: bool = False,
    columns: Sequence[int] = (0, 1),
    attrs: Mapping[str, int] | None = None,
    int_nodes: bool = True,
    directed: bool = False,
) -> Graph:
    """Read a text file of edges, one per line (`u v`, or `u v <extra columns>` — the common way graphs are
    published, e.g. what `nx.write_edgelist` produces).

    `delimiter=None` splits on whitespace; pass "," or "\\t" for delimited files. `columns` selects the source and
    target columns (default the first two). `attrs` maps an attribute name to a column to keep on each edge, e.g.
    `{"weight": 2, "color": 3}`; numeric text becomes a number, so `pg.by_attribute("color")` or
    `pg.size_by_attribute("weight")` can use the column directly, no further conversion needed. Lines starting with
    `comments` and blank lines are skipped, and `header=True` skips the first data line. Node ids that are plain
    integers (no leading zeros, within 64 bits) become integers unless `int_nodes=False`; everything else stays
    text.

    For `(u, v, t)` rows with a timestamp, use `read_temporal_edgelist` instead — there the third value is a time,
    not an edge property, and would otherwise be misread as one.
    """
    if len(columns) != 2:
        raise ValueError(f"columns must name exactly 2 columns (source, target), got {tuple(columns)}")

    g = Graph()
    known: set[Hashable] = set()
    attr_columns = dict(attrs or {})

    for parts, number in read_rows(path, delimiter=delimiter, comments=comments, header=header):
        try:
            u_text, v_text = (parts[i] for i in columns)
        except IndexError:
            raise ValueError(f"{path}:{number}: expected at least {max(columns) + 1} columns, got {len(parts)}") from None
        u, v = node_key(u_text, int_nodes), node_key(v_text, int_nodes)
        ensure_node(g, known, u)
        ensure_node(g, known, v)
        extra: dict[str, Any] = {}
        if attr_columns:
            try:
                extra = {name: attr_value(parts[i]) for name, i in attr_columns.items()}
            except IndexError:
                raise ValueError(f"{path}:{number}: expected at least {max(attr_columns.values()) + 1} columns, got {len(parts)}") from None
        weight = extra.pop("weight", None)
        try:
            g.add_edge(u, v, directed=directed, weight=weight, **extra)
        except TypeError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from None

    return g


def to_pandas_edgelist(
    graph: Graph,
    *,
    source: str = "source",
    target: str = "target",
    edge_attr: str | list[str] | bool | None = True,
) -> Any:
    """The reverse of `from_pandas_edgelist`: one row per connector, as a pandas DataFrame. `edge_attr` selects
    which connector fields become extra columns beyond `source`/`target` -- a name, a list of names, True (every
    attribute, `weight`, and, for a non-default layer or time bound, `layer`/`t_start`/`t_end` -- the default) or
    None/False for none.

    Hyperedges (more than two endpoints) have no representation in an edge list and are skipped, with a warning
    naming how many; a plain graph library has no format that carries hypergraph structure (networkx doesn't
    either), so this is an unavoidable, not incidental, limitation of this export, not of the source graph.
    """
    import pandas as pd

    if edge_attr is None or edge_attr is False:
        wanted: set[str] | None = set()
    elif edge_attr is True:
        wanted = None  # everything
    elif isinstance(edge_attr, str):
        wanted = {edge_attr}
    else:
        wanted = set(edge_attr)

    def keep(name: str) -> bool:
        return wanted is None or name in wanted

    # Index directly by internal node/layer id rather than graph.node(id)/graph.layer(id): those resolve an
    # explicit key first and only fall back to treating an int as a raw internal id if no node/layer has that key
    # (see Graph._resolve_node) -- endpoints/layer_id here always ARE raw internal ids, but a node or layer that
    # was given an explicit integer key equal to some other node/layer's internal id would otherwise resolve to
    # the wrong one. graph.nodes()/layers() are built in internal-id order, so a plain list index is both correct
    # and O(1) per lookup (graph.node(id) is not, and would also be a second O(n) rebuild for every connector).
    node_keys = [n.key for n in graph.nodes()]
    layer_keys = [l.key for l in graph.layers()]

    rows: list[dict[str, Any]] = []
    connectors = graph.connectors()
    simple = [c for c in connectors if not c.is_hyperedge]
    for c in simple:
        u, v = c.endpoints
        row: dict[str, Any] = {source: node_keys[u], target: node_keys[v]}
        if c.weight is not None and keep("weight"):
            row["weight"] = c.weight
        if c.layer_id is not None and keep("layer"):
            row["layer"] = layer_keys[c.layer_id]
        if not c.is_always_present:
            if keep("t_start"):
                row["t_start"] = c.t_start
            if keep("t_end"):
                row["t_end"] = c.t_end
        for name, value in c.attrs.items():
            if keep(name):
                row[name] = value
        rows.append(row)
    warn_skipped_hyperedges("to_pandas_edgelist", len(connectors) - len(simple), "an edge list has no way to represent them")
    return pd.DataFrame(rows, columns=[source, target] if not rows else None)


def write_edgelist(
    graph: Graph,
    path: str | Path,
    *,
    delimiter: str = " ",
    attrs: Sequence[str] | None = None,
    header: bool = False,
) -> None:
    """Write `graph` as a text file of edges, one per line: `u v`, or `u v <extra columns>` -- the reverse of
    `read_edgelist`, and readable by `nx.read_weighted_edgelist`/`nx.read_edgelist` too when `attrs=["weight"]`.

    `attrs` selects which connector attributes become extra columns, in order (default: `weight` if any connector
    has one, else none). A connector missing a named attribute writes an empty field. `header=True` writes a first
    line naming the columns. Hyperedges are skipped, with a warning naming how many -- an edge list has no way to
    represent them.
    """
    connectors = graph.connectors()
    simple = [c for c in connectors if not c.is_hyperedge]  # weight default and export both ignore skipped hyperedges

    if attrs is None:
        attrs = ["weight"] if any(c.weight is not None for c in simple) else []

    node_keys = [n.key for n in graph.nodes()]  # see to_pandas_edgelist for why not graph.node(id)
    _MISSING = object()

    with open(path, "w", encoding="utf-8") as fh:
        if header:
            fh.write(delimiter.join(["source", "target", *attrs]) + "\n")
        for c in simple:
            u, v = c.endpoints
            values = {**c.attrs}
            if c.weight is not None:
                values["weight"] = c.weight
            fields = [str(node_keys[u]), str(node_keys[v])]
            # Trim only attrs that are genuinely absent, trailing -- not one whose value happens to BE "" (that is
            # a real, present value, not a placeholder, and must still be written so the row stays the right width;
            # comparing rendered "" values, as before, could not tell the two apart and silently dropped the
            # latter, misaligning that row's columns against every other row, including on read-back).
            raw = [values[name] if name in values else _MISSING for name in attrs]
            while raw and raw[-1] is _MISSING:
                raw.pop()
            fields += ["" if value is _MISSING else str(value) for value in raw]
            fh.write(delimiter.join(fields) + "\n")
    warn_skipped_hyperedges("write_edgelist", len(connectors) - len(simple), "an edge list has no way to represent them")
