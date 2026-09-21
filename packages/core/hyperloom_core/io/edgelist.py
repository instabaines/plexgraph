"""Build a Graph from plain edge lists or a pandas DataFrame — the two
most common "I already have my data in some other shape" starting points
that aren't a networkx object (see networkx_io.py for that path)."""

from __future__ import annotations

from typing import Any, Hashable, Iterable, Sequence

from hyperloom_core.model.ir import Graph


def from_edgelist(
    edges: Iterable[Sequence[Any] | tuple[Hashable, Hashable]],
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

    def ensure_node(key: Hashable) -> None:
        if key not in known:
            g.add_node(key)
            known.add(key)

    for edge in edges:
        if len(edge) == 2:
            u, v = edge
            ensure_node(u)
            ensure_node(v)
            g.add_edge(u, v, directed=directed)
        elif len(edge) == 3:
            u, v, extra = edge
            ensure_node(u)
            ensure_node(v)
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
    elif edge_attr is None:
        attr_columns = []
    elif isinstance(edge_attr, str):
        attr_columns = [edge_attr]
    else:
        attr_columns = list(edge_attr)

    g = Graph()
    known: set[Hashable] = set()

    def ensure_node(key: Hashable) -> None:
        if key not in known:
            g.add_node(key)
            known.add(key)

    for row in df.itertuples(index=False):
        row_dict = row._asdict()
        u = row_dict[source]
        v = row_dict[target]
        ensure_node(u)
        ensure_node(v)
        attrs = {c: row_dict[c] for c in attr_columns}
        weight = attrs.pop("weight", None)
        g.add_edge(u, v, directed=directed, weight=weight, **attrs)

    return g
