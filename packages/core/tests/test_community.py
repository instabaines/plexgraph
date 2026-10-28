import pytest

from plexgraph_core.algorithms.community import detect_communities
from plexgraph_core.algorithms.hypergraph import bipartite_expansion
from plexgraph_core.model.ir import Graph


def _two_cliques(clique_size: int = 5) -> tuple[Graph, list[int], list[int]]:
    g = Graph()
    a = [g.add_node(f"a{i}") for i in range(clique_size)]
    b = [g.add_node(f"b{i}") for i in range(clique_size)]
    for nodes in (a, b):
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                g.add_edge(nodes[i], nodes[j])
    g.add_edge(a[0], b[0])  # one weak bridge -- shouldn't stop them from being detected separately
    return g, a, b


def test_two_disconnected_dense_groups_become_two_communities():
    g, a, b = _two_cliques()
    result = detect_communities(g, seed=0)
    communities = result.node_communities()
    a_communities = {communities[n] for n in a}
    b_communities = {communities[n] for n in b}
    assert len(a_communities) == 1 and len(b_communities) == 1
    assert a_communities != b_communities
    assert result.modularity > 0.2


def test_a_single_clique_is_one_community():
    g = Graph()
    nodes = [g.add_node(i) for i in range(6)]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            g.add_edge(nodes[i], nodes[j])
    result = detect_communities(g, seed=0)
    assert len(set(result.node_communities().values())) == 1


def test_empty_graph():
    result = detect_communities(Graph())
    assert result.state_communities == {}
    assert result.modularity == 0.0


def test_isolated_nodes_get_their_own_singleton_community():
    g = Graph()
    isolated = [g.add_node(f"iso{i}") for i in range(3)]
    result = detect_communities(g, seed=0)
    communities = result.node_communities()
    assert len(set(communities[n] for n in isolated)) == 3


def test_weight_none_defaults_to_one():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b)  # no explicit weight
    result_default = detect_communities(g, seed=0)
    g2 = Graph()
    a2, b2 = g2.add_node("a"), g2.add_node("b")
    g2.add_edge(a2, b2, weight=1.0)
    result_explicit = detect_communities(g2, seed=0)
    assert result_default.modularity == pytest.approx(result_explicit.modularity)


def test_is_deterministic_given_seed():
    g, _, _ = _two_cliques()
    a = detect_communities(g, seed=7).node_communities()
    b = detect_communities(g, seed=7).node_communities()
    assert a == b


def test_invalid_parameters_raise():
    g, _, _ = _two_cliques()
    for kwargs in ({"resolution": -1}, {"omega": -1}, {"max_passes": 0}):
        with pytest.raises(ValueError):
            detect_communities(g, **kwargs)


def test_hyperedges_raise_without_expansion():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])
    with pytest.raises(ValueError, match="hyperedge"):
        detect_communities(g)


def test_hyperedges_work_after_bipartite_expansion():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])
    result = detect_communities(bipartite_expansion(g), seed=0)
    assert result.state_communities  # just confirm it runs and produces an assignment


def test_karate_club_reaches_a_reasonable_modularity():
    # Zachary's karate club: the standard real-world community-detection benchmark. Known-good
    # partitions score roughly 0.35-0.42; a badly broken implementation would score far lower
    # (or <= 0 -- the score a single all-in-one-community or all-isolated partition gets).
    nx = pytest.importorskip("networkx")
    from plexgraph_core.io.networkx_io import from_networkx

    g = from_networkx(nx.karate_club_graph())
    result = detect_communities(g, seed=0)
    assert result.modularity > 0.3
    communities = set(result.node_communities().values())
    assert 2 <= len(communities) <= 8  # a sensible number of groups, not 1 and not 34


# ---- multilayer-specific ----


def _two_layers_with_different_structure() -> tuple[Graph, list[int], list[int], list[int], list[int]]:
    """Layer A groups nodes [0,1,2,3] vs [4,5,6,7]; layer B groups [0,1,4,5] vs [2,3,6,7] --
    a genuinely different partition per layer, so omega is the only thing that can make them agree."""
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")
    nodes = [g.add_node(i) for i in range(8)]

    def clique(members, layer):
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                g.add_edge(members[i], members[j], layer=layer)

    a1, a2 = nodes[0:4], nodes[4:8]
    b1, b2 = [nodes[0], nodes[1], nodes[4], nodes[5]], [nodes[2], nodes[3], nodes[6], nodes[7]]
    clique(a1, layer_a)
    clique(a2, layer_a)
    clique(b1, layer_b)
    clique(b2, layer_b)
    return g, a1, a2, b1, b2


def test_each_layer_finds_its_own_structure_when_omega_is_zero():
    g, a1, a2, b1, b2 = _two_layers_with_different_structure()
    layer_a = g.layer("a").id
    layer_b = g.layer("b").id
    result = detect_communities(g, omega=0.0, seed=0)
    sc = result.state_communities
    assert len({sc[(n, layer_a)] for n in a1}) == 1
    assert len({sc[(n, layer_a)] for n in a2}) == 1
    assert sc[(a1[0], layer_a)] != sc[(a2[0], layer_a)]
    assert len({sc[(n, layer_b)] for n in b1}) == 1
    assert len({sc[(n, layer_b)] for n in b2}) == 1
    assert sc[(b1[0], layer_b)] != sc[(b2[0], layer_b)]


def test_large_omega_pulls_a_nodes_layers_toward_agreement():
    g, a1, a2, b1, b2 = _two_layers_with_different_structure()
    layer_a = g.layer("a").id
    layer_b = g.layer("b").id

    def agreement_fraction(omega: float) -> float:
        sc = detect_communities(g, omega=omega, seed=0).state_communities
        agree = sum(1 for n in range(8) if sc[(n, layer_a)] == sc[(n, layer_b)])
        return agree / 8

    low = agreement_fraction(0.0)
    high = agreement_fraction(50.0)
    assert high > low


def test_node_communities_collapses_by_degree_weight():
    # A node present in two layers: a full member of a clique in one (degree 6), a single pendant
    # edge into a different clique in the other (degree 1) -- node_communities() should side with
    # the layer it has more actual edges in, not an arbitrary or alphabetical choice.
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")
    shared = g.add_node("shared")
    strong_side = [g.add_node(f"strong{i}") for i in range(6)]
    weak_side = [g.add_node(f"weak{i}") for i in range(6)]
    for i in range(len(strong_side)):
        for j in range(i + 1, len(strong_side)):
            g.add_edge(strong_side[i], strong_side[j], layer=layer_a)
        g.add_edge(shared, strong_side[i], layer=layer_a)
    for i in range(len(weak_side)):
        for j in range(i + 1, len(weak_side)):
            g.add_edge(weak_side[i], weak_side[j], layer=layer_b)
    g.add_edge(shared, weak_side[0], layer=layer_b)  # the only connection shared has in layer b

    result = detect_communities(g, omega=0.0, seed=0)
    communities = result.node_communities()
    strong_community = communities[strong_side[0]]
    assert all(communities[n] == strong_community for n in strong_side)
    assert communities[shared] == strong_community


def test_apply_as_node_attribute_stamps_string_values_matching_node_communities():
    g, a, b = _two_cliques()
    result = detect_communities(g, seed=0)
    expected = result.node_communities()
    result.apply_as_node_attribute(g)
    for n in a + b:
        assert g.node(n).attrs["community"] == str(expected[n])


def test_apply_as_node_attribute_uses_a_custom_attribute_name():
    g, a, b = _two_cliques()
    result = detect_communities(g, seed=0)
    result.apply_as_node_attribute(g, attribute="cluster")
    assert "cluster" in g.node(a[0]).attrs
    assert "community" not in g.node(a[0]).attrs
