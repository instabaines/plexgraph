"""Load per-node properties from a table, separate from the edges that connect them -- a very common real-world
shape: an edges file defining the structure, and a nodes table (id, category, importance, ...) enriching it with
properties to color, size or filter by. Complements edgelist.py/temporal.py, which only ever see nodes as edge
endpoints and so have no way to carry a property that isn't attached to some edge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Hashable, Mapping, Sequence

from plexgraph_core.io._text import attr_value, node_key, read_header, read_rows
from plexgraph_core.model.ir import Graph


def read_node_attributes(
    graph: Graph,
    path: str | Path,
    *,
    delimiter: str | None = None,
    comments: str = "#",
    header: bool = True,
    key_column: int = 0,
    attrs: Mapping[str, int] | None = None,
    int_nodes: bool = True,
    create_missing: bool = True,
) -> Graph:
    """Read a text file of per-node properties, one node per line (`id,category,importance`, say), and set them on
    `graph`'s nodes. Typically called after building the graph from an edge list, to enrich it with data that
    isn't tied to any particular edge:

        g = pg.read_edgelist("edges.csv", delimiter=",", header=True)
        pg.read_node_attributes(g, "nodes.csv", delimiter=",")
        pg.show(g, node_color=pg.by_attribute("category"), node_size=pg.size_by_attribute("importance"))

    `attrs` maps an attribute name to a column to keep, e.g. `{"category": 1, "importance": 2}`. Left as the
    default `None`, every column other than `key_column` is kept, named from the header row (so `header=True`,
    also the default -- a node table with no header has no way to name its own columns). A row naming a node the
    edges never created adds it (`create_missing=True`, the default: an isolated node with only attributes is
    still worth showing); pass `create_missing=False` to skip such rows instead, matching `networkx.
    set_node_attributes`, which requires the node to already exist. Returns `graph`, mutated in place and also
    returned, so this composes into one expression.
    """
    if attrs is None:
        fields = read_header(path, delimiter=delimiter, comments=comments) if header else None
        if fields is None:
            attrs = {}
        elif key_column >= len(fields):
            raise ValueError(f"{path}: key_column {key_column} is out of range for a {len(fields)}-column header {fields!r}")
        else:
            attrs = {name: i for i, name in enumerate(fields) if i != key_column}

    existing = _node_keys(graph)
    for parts, number in read_rows(path, delimiter=delimiter, comments=comments, header=header):
        try:
            key = node_key(parts[key_column], int_nodes)
        except IndexError:
            raise ValueError(f"{path}:{number}: expected at least {key_column + 1} columns, got {len(parts)}") from None
        try:
            values = {name: attr_value(parts[i]) for name, i in attrs.items()}
        except IndexError:
            raise ValueError(f"{path}:{number}: expected at least {max(attrs.values()) + 1} columns, got {len(parts)}") from None
        if key not in existing:
            if not create_missing:
                continue
            graph.add_node(key)
            existing.add(key)
        graph.set_node_attrs(key, **values)

    return graph


def _node_keys(graph: Graph) -> set[Hashable]:
    return {n.key for n in graph.nodes()}


def read_pandas_node_attributes(
    graph: Graph,
    df: Any,
    key: str = "id",
    *,
    attrs: str | Sequence[str] | bool | None = True,
    int_nodes: bool = True,
    create_missing: bool = True,
) -> Graph:
    """The DataFrame equivalent of `read_node_attributes`: one row per node, a `key` column identifying it.
    `attrs` selects which other columns become node attributes -- a column name, a list, True (every remaining
    column, the default) or None/False for none. A text `key` column matches `read_node_attributes`'/`read_edgelist`'s
    own `int_nodes` coercion (so, e.g., a plain-integer id read as text still matches an int-keyed node built by
    `read_edgelist`); a `key` column that pandas already parsed as a number is used as-is either way. Returns
    `graph`, mutated in place and also returned."""
    if attrs is True:
        attr_columns = [c for c in df.columns if c != key]
    elif attrs is None or attrs is False:
        attr_columns = []
    elif isinstance(attrs, str):
        attr_columns = [attrs]
    else:
        attr_columns = list(attrs)

    existing = _node_keys(graph)
    for row in df.itertuples(index=False):
        row_dict = row._asdict()
        node = row_dict[key]
        if int_nodes and isinstance(node, str):
            node = node_key(node, int_nodes)
        values = {c: row_dict[c] for c in attr_columns}
        if node not in existing:
            if not create_missing:
                continue
            graph.add_node(node)
            existing.add(node)
        graph.set_node_attrs(node, **values)

    return graph
