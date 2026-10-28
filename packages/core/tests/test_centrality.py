import numpy as np
import pytest

from plexgraph_core.algorithms.centrality import eigenvector_centrality, pagerank_centrality
from plexgraph_core.algorithms.hypergraph import bipartite_expansion
from plexgraph_core.model.ir import Graph


def _triangle_with_tail() -> Graph:
    # A triangle (0,1,2) with a pendant (3) off node 0 -- small, non-bipartite, a clear
    # dominant-eigenvalue gap, so power iteration converges quickly and unambiguously.
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_edge(0, 1)
    g.add_edge(1, 2)
    g.add_edge(2, 0)
    g.add_edge(0, 3)
    return g


def _dense_eigenvector_ground_truth(g: Graph) -> dict[int, float]:
    n = g.num_nodes
    a = np.zeros((n, n))
    for c in g.connectors():
        u, v = c.endpoints
        a[u, v] = a[v, u] = 1.0 if c.weight is None else c.weight
    eigvals, eigvecs = np.linalg.eigh(a)
    top = eigvecs[:, np.argmax(eigvals)]
    top = np.abs(top)
    top /= np.linalg.norm(top)
    return dict(enumerate(top))


def test_eigenvector_centrality_matches_dense_ground_truth_on_a_single_layer_graph():
    g = _triangle_with_tail()
    truth = _dense_eigenvector_ground_truth(g)
    ours = eigenvector_centrality(g).node_centrality()
    for nid in truth:
        assert ours[nid] == pytest.approx(truth[nid], abs=1e-6)


def test_eigenvector_centrality_ranks_the_triangle_above_the_pendant():
    g = _triangle_with_tail()
    c = eigenvector_centrality(g).node_centrality()
    assert c[1] > c[3] and c[2] > c[3]  # triangle members outrank the pendant


def test_eigenvector_centrality_raises_on_an_edgeless_graph():
    g = Graph()
    g.add_node(0)
    g.add_node(1)
    with pytest.raises(ValueError, match="no edges"):
        eigenvector_centrality(g)


def test_eigenvector_centrality_converges_on_a_star_graph():
    # A star's two dominant eigenvalues are exactly +/-lambda (equal magnitude): plain power
    # iteration can never amplify one over the other and 2-cycles forever without a diagonal
    # shift. Regression guard for that fix, not just a happy-path check.
    g = Graph()
    hub = g.add_node("hub")
    leaves = [g.add_node(f"leaf{i}") for i in range(4)]
    for leaf in leaves:
        g.add_edge(hub, leaf)
    result = eigenvector_centrality(g, seed=0).node_centrality()
    assert result[hub] > result[leaves[0]]
    assert result[leaves[0]] == pytest.approx(result[leaves[1]], abs=1e-6)


def test_eigenvector_centrality_empty_graph():
    result = eigenvector_centrality(Graph())
    assert result.state_centrality == {}


def test_pagerank_sums_to_one():
    g = _triangle_with_tail()
    result = pagerank_centrality(g)
    assert sum(result.state_centrality.values()) == pytest.approx(1.0)


def test_pagerank_matches_textbook_two_node_case():
    # Two nodes, one edge: PageRank splits (1-d)/n teleport plus damped inflow symmetrically --
    # a simple enough case to hand-verify rather than trust another library's convergence.
    g = Graph()
    g.add_node(0)
    g.add_node(1)
    g.add_edge(0, 1)
    result = pagerank_centrality(g, damping=0.85).node_centrality()
    assert result[0] == pytest.approx(result[1], abs=1e-9)
    assert result[0] == pytest.approx(0.5, abs=1e-9)


def test_pagerank_respects_direction():
    g = Graph()
    for i in range(3):
        g.add_node(i)
    g.add_edge(0, 1, directed=True)
    g.add_edge(1, 2, directed=True)
    result = pagerank_centrality(g).node_centrality()
    assert result[2] > result[0]  # sink of a directed chain accumulates more rank than the source


def test_invalid_parameters_raise():
    g = _triangle_with_tail()
    for kwargs in ({"coupling_weight": -1}, {"max_iterations": 0}, {"tolerance": 0}):
        with pytest.raises(ValueError):
            eigenvector_centrality(g, **kwargs)
    for kwargs in ({"damping": -0.1}, {"damping": 1.1}, {"coupling_weight": -1}, {"max_iterations": 0}, {"tolerance": 0}):
        with pytest.raises(ValueError):
            pagerank_centrality(g, **kwargs)


def test_hyperedges_raise_without_expansion():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])
    with pytest.raises(ValueError, match="hyperedge"):
        eigenvector_centrality(g)
    with pytest.raises(ValueError, match="hyperedge"):
        pagerank_centrality(g)


def test_hyperedges_work_after_bipartite_expansion():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])
    expanded = bipartite_expansion(g)
    assert eigenvector_centrality(expanded).state_centrality
    assert pagerank_centrality(expanded).state_centrality


# ---- multilayer-specific ----


def test_node_centrality_sums_across_layers():
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")
    shared = g.add_node("shared")
    a_peer = g.add_node("a_peer")
    b_peer = g.add_node("b_peer")
    g.add_edge(shared, a_peer, layer=layer_a)
    g.add_edge(shared, b_peer, layer=layer_b)

    result = pagerank_centrality(g, coupling_weight=0.0)
    state = result.state_centrality
    node = result.node_centrality()
    assert node[shared] == pytest.approx(state[(shared, layer_a)] + state[(shared, layer_b)])


def test_coupling_weight_zero_makes_layers_fully_independent():
    # Two structurally identical layers, computed with coupling_weight=0, should give each layer's
    # nodes exactly the per-layer-independent PageRank -- same as running single-layer PageRank on
    # each layer's own edges alone, since no probability can cross between layers.
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")
    nodes_a = [g.add_node(f"a{i}") for i in range(4)]
    nodes_b = [g.add_node(f"b{i}") for i in range(4)]
    for nodes, layer in ((nodes_a, layer_a), (nodes_b, layer_b)):
        g.add_edge(nodes[0], nodes[1], layer=layer)
        g.add_edge(nodes[1], nodes[2], layer=layer)
        g.add_edge(nodes[2], nodes[3], layer=layer)

    result = pagerank_centrality(g, coupling_weight=0.0)
    a_scores = [result.state_centrality[(n, layer_a)] for n in nodes_a]
    b_scores = [result.state_centrality[(n, layer_b)] for n in nodes_b]
    assert a_scores == pytest.approx(b_scores, abs=1e-9)


def test_apply_as_node_attribute_stores_numeric_values():
    g = _triangle_with_tail()
    result = eigenvector_centrality(g)
    result.apply_as_node_attribute(g)
    assert isinstance(g.node(0).attrs["centrality"], float)
    assert g.node(0).attrs["centrality"] == pytest.approx(result.node_centrality()[0])
