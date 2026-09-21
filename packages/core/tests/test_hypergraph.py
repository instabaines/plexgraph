from plexgraph_core.algorithms.hypergraph import bipartite_expansion
from plexgraph_core.model.ir import Graph


def test_plain_edges_survive_unchanged():
    g = Graph()
    a = g.add_node("a")
    b = g.add_node("b")
    g.add_edge(a, b, weight=2.5)

    expanded = bipartite_expansion(g)
    assert expanded.num_nodes == 2
    assert expanded.num_edges == 1
    edge = expanded.connector(0)
    assert not edge.is_hyperedge
    assert edge.weight == 2.5


def test_hyperedge_becomes_a_new_node_with_spoke_edges():
    g = Graph()
    for k in ("a", "b", "c"):
        g.add_node(k)
    g.add_hyperedge(["a", "b", "c"], weight=1.0)

    expanded = bipartite_expansion(g)
    # 3 original nodes + 1 new hyperedge node.
    assert expanded.num_nodes == 4
    # 3 spoke edges, none of which are hyperedges themselves.
    assert expanded.num_edges == 3
    assert all(not c.is_hyperedge for c in expanded.connectors())


def test_expansion_has_no_hyperedges_of_its_own():
    g = Graph()
    for i in range(5):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3, 4])
    g.add_edge(0, 1)

    expanded = bipartite_expansion(g)
    assert all(not c.is_hyperedge for c in expanded.connectors())


def test_mixed_plain_and_hyperedges():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_edge(0, 1)
    g.add_hyperedge([1, 2, 3])

    expanded = bipartite_expansion(g)
    # 4 original nodes + 1 hyperedge node.
    assert expanded.num_nodes == 5
    # 1 plain edge + 3 spoke edges.
    assert expanded.num_edges == 4


def test_preserves_temporal_and_layer_bounds_on_spokes():
    g = Graph()
    for k in ("a", "b", "c"):
        g.add_node(k)
    g.add_layer("L")
    g.add_hyperedge(["a", "b", "c"], layer="L", t_start=1.0, t_end=5.0)

    expanded = bipartite_expansion(g)
    spokes = [c for c in expanded.connectors()]
    assert len(spokes) == 3
    for spoke in spokes:
        assert spoke.t_start == 1.0
        assert spoke.t_end == 5.0
        assert spoke.layer_id is not None
