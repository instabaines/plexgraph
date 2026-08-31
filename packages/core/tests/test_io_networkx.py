import networkx as nx

from hyperloom_core.io import from_networkx


def test_from_networkx_undirected_graph():
    nxg = nx.Graph()
    nxg.add_node("a", label="Alice")
    nxg.add_node("b", label="Bob")
    nxg.add_edge("a", "b", weight=2.0)

    g = from_networkx(nxg)
    assert g.num_nodes == 2
    assert g.num_edges == 1
    edge = g.connector(0)
    assert not edge.directed
    assert edge.weight == 2.0
    assert g.node("a").attrs["label"] == "Alice"


def test_from_networkx_digraph_produces_directed_connectors():
    nxg = nx.DiGraph()
    nxg.add_edge("a", "b")
    g = from_networkx(nxg)
    assert g.connector(0).directed


def test_from_networkx_preserves_edge_attrs_besides_weight():
    nxg = nx.Graph()
    nxg.add_edge("a", "b", kind="friend", weight=1.5)
    g = from_networkx(nxg)
    edge = g.connector(0)
    assert edge.weight == 1.5
    assert edge.attrs["kind"] == "friend"


def test_from_networkx_multigraph_keeps_parallel_edges():
    nxg = nx.MultiGraph()
    nxg.add_edge("a", "b")
    nxg.add_edge("a", "b")
    g = from_networkx(nxg)
    assert g.num_edges == 2
