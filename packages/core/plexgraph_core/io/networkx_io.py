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
