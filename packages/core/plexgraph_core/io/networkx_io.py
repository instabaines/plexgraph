"""Convert a networkx graph into our IR. This is the highest-value input
path, not a native format parser: networkx already reads GEXF, GraphML,
GML, Pajek, adjacency matrices, and dozens of other formats, plus dozens
of built-in graph generators — from_networkx() means all of that is
reachable without reimplementing any of it, e.g.
`from_networkx(nx.read_gexf("foo.gexf"))`. from_gexf/from_graphml below
are thin convenience wrappers for the two most common file formats.

networkx is an optional dependency: the import is deferred into each
function body so that importing plexgraph_core.io doesn't require it —
only actually calling one of these functions does.
"""

from __future__ import annotations

from typing import Any

from plexgraph_core.model.ir import Graph


def from_networkx(nx_graph: Any) -> Graph:
    """Build a Graph from a networkx Graph/DiGraph/MultiGraph/MultiDiGraph.

    Node attrs and edge attrs (including 'weight') are preserved. Directed
    input graphs produce directed connectors. Multigraphs' parallel edges
    become separate connectors (this IR allows that natively — see
    docs/architecture/plan.md section 1 — no special-casing needed).
    """
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError(
            "from_networkx() requires networkx to be installed: pip install networkx"
        ) from e

    g = Graph()
    for node, attrs in nx_graph.nodes(data=True):
        g.add_node(node, **attrs)

    directed = isinstance(nx_graph, nx.DiGraph)
    for u, v, attrs in nx_graph.edges(data=True):
        edge_attrs = dict(attrs)
        weight = edge_attrs.pop("weight", None)
        g.add_edge(u, v, directed=directed, weight=weight, **edge_attrs)

    return g


def from_gexf(path: str) -> Graph:
    """Read a GEXF file via networkx and convert it — see from_networkx()."""
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError("from_gexf() requires networkx to be installed: pip install networkx") from e
    return from_networkx(nx.read_gexf(path))


def from_graphml(path: str) -> Graph:
    """Read a GraphML file via networkx and convert it — see from_networkx()."""
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError("from_graphml() requires networkx to be installed: pip install networkx") from e
    return from_networkx(nx.read_graphml(path))


def to_networkx(graph: Graph, *, multigraph: bool = True) -> Any:
    """The reverse of `from_networkx`: a networkx Graph/DiGraph/MultiGraph/MultiDiGraph built from `graph`.
    Directed if any connector is directed (an undirected connector in an otherwise-directed graph becomes an edge
    in both directions, which is how networkx itself represents "undirected" within a directed graph).
    `multigraph=True` (the default) keeps parallel edges as separate edges, losslessly; `multigraph=False`
    collapses them into one, last-write-wins on attributes, for callers whose networkx code assumes a plain graph.

    Node attrs, connector attrs, weight and, for a non-default layer or time bound, layer (by key)/t_start/t_end
    become networkx node/edge attributes. Hyperedges (more than two endpoints) have no representation in networkx
    and are skipped, with a warning naming how many -- a limitation of networkx's own data model, not of `graph`.
    """
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError("to_networkx() requires networkx to be installed: pip install networkx") from e

    directed = any(c.directed for c in graph.connectors())
    cls = (nx.MultiDiGraph if directed else nx.MultiGraph) if multigraph else (nx.DiGraph if directed else nx.Graph)
    out = cls()
    for n in graph.nodes():
        out.add_node(n.key, **n.attrs)

    skipped = 0
    for c in graph.connectors():
        if c.is_hyperedge:
            skipped += 1
            continue
        u, v = graph.node(c.endpoints[0]).key, graph.node(c.endpoints[1]).key
        attrs = dict(c.attrs)
        if c.weight is not None:
            attrs["weight"] = c.weight
        if c.layer_id is not None:
            attrs["layer"] = graph.layer(c.layer_id).key
        if not c.is_always_present:
            attrs["t_start"], attrs["t_end"] = c.t_start, c.t_end
        out.add_edge(u, v, **attrs)
        if directed and not c.directed:
            out.add_edge(v, u, **attrs)
    if skipped:
        import warnings

        warnings.warn(f"to_networkx: skipped {skipped} hyperedge(s) (more than 2 endpoints); "
                      "networkx has no hypergraph representation", UserWarning, stacklevel=2)
    return out


def write_graphml(graph: "Graph", path: str, **kwargs: Any) -> None:
    """Write `graph` as GraphML, via `to_networkx()` and `networkx.write_graphml` -- see `to_networkx` for what
    is and is not preserved (hyperedges are not). Extra keyword arguments are passed to `nx.write_graphml`."""
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError("write_graphml() requires networkx to be installed: pip install networkx") from e
    nx.write_graphml(to_networkx(graph), path, **kwargs)


def write_gexf(graph: "Graph", path: str, **kwargs: Any) -> None:
    """Write `graph` as GEXF, via `to_networkx()` and `networkx.write_gexf` -- see `to_networkx` for what is and
    is not preserved (hyperedges are not). Extra keyword arguments are passed to `nx.write_gexf`."""
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError("write_gexf() requires networkx to be installed: pip install networkx") from e
    nx.write_gexf(to_networkx(graph), path, **kwargs)
