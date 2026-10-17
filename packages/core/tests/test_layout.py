import numpy as np

from plexgraph_core.algorithms.layout import force_directed_layout
from plexgraph_core.model.ir import Graph


def _path_graph(n: int) -> Graph:
    g = Graph()
    for i in range(n):
        g.add_node(i)
    for i in range(n - 1):
        g.add_edge(i, i + 1)
    return g


def test_layout_yields_a_step_per_iteration_until_convergence():
    g = _path_graph(5)
    steps = list(force_directed_layout(g, iterations=50, seed=42))
    assert len(steps) <= 50
    assert steps[-1].converged or len(steps) == 50


def test_layout_positions_shape_matches_node_count():
    g = _path_graph(5)
    step = next(force_directed_layout(g, iterations=1, seed=42))
    assert step.positions.shape == (5, 2)


def test_layout_empty_graph():
    g = Graph()
    steps = list(force_directed_layout(g, iterations=10))
    assert len(steps) == 1
    assert steps[0].converged
    assert steps[0].positions.shape == (0, 2)


def test_layout_is_deterministic_given_seed():
    g = _path_graph(6)
    a = list(force_directed_layout(g, iterations=20, seed=7))
    b = list(force_directed_layout(g, iterations=20, seed=7))
    np.testing.assert_array_equal(a[-1].positions, b[-1].positions)


def test_layout_handles_hyperedges_without_raising():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])
    steps = list(force_directed_layout(g, iterations=5, seed=1))
    assert steps[-1].positions.shape == (4, 2)


def test_hyperedge_members_are_pulled_together():
    # Without hyperedge attraction, hyperedge members would be
    # pulled by nothing at all and stay at their random initial spread.
    # With it, they should end up measurably closer together than nodes
    # with no connections at all.
    g = Graph()
    for i in range(4):
        g.add_node(i)  # 0-3: hyperedge members
    for i in range(4, 8):
        g.add_node(i)  # 4-7: isolated, no connectors at all
    g.add_hyperedge([0, 1, 2, 3])

    final = list(force_directed_layout(g, iterations=100, seed=3))[-1].positions

    def mean_pairwise_dist(indices):
        pts = final[indices]
        dists = [
            np.linalg.norm(pts[i] - pts[j])
            for i in range(len(pts))
            for j in range(i + 1, len(pts))
        ]
        return np.mean(dists)

    hyperedge_spread = mean_pairwise_dist([0, 1, 2, 3])
    isolated_spread = mean_pairwise_dist([4, 5, 6, 7])
    assert hyperedge_spread < isolated_spread


def test_path_is_ordered_without_crossings():
    final = list(force_directed_layout(_path_graph(120), seed=0))[-1]
    assert final.converged
    assert np.all(np.diff(final.positions[:, 0]) > 0)
    np.testing.assert_allclose(final.positions[:, 1], 0)


def test_disconnected_components_have_disjoint_bounds():
    g = Graph()
    for i in range(40):
        g.add_node(i)
    for start in (0, 10, 20, 30):
        for i in range(start + 1, start + 10):
            g.add_edge(start, i)
    p = list(force_directed_layout(g, iterations=40, seed=0))[-1].positions
    boxes = [(p[i:i + 10].min(axis=0), p[i:i + 10].max(axis=0)) for i in (0, 10, 20, 30)]
    for a, (low, high) in enumerate(boxes):
        for other_low, other_high in boxes[a + 1:]:
            assert np.any(high < other_low) or np.any(other_high < low)


def test_cooling_does_not_report_false_convergence():
    g = Graph()
    for i in range(15):
        g.add_node(i)
    for i in range(1, 15):
        g.add_edge(0, i)
    steps = list(force_directed_layout(g, iterations=30, seed=0, convergence_threshold=0))
    assert len(steps) == 30
    assert not steps[-1].converged


def test_exact_repulsion_matches_dense_reference():
    from plexgraph_core.algorithms.layout import _repulsion
    p = np.random.default_rng(0).normal(size=(300, 2))
    delta = p[:, None, :] - p[None, :, :]
    d2 = np.maximum(np.sum(delta * delta, axis=2), (.1 * .05) ** 2)
    expected = np.sum(delta * (.01 / d2)[..., None], axis=1)
    np.testing.assert_allclose(_repulsion(p, .1, True), expected)


def test_mesh_repulsion_remains_active_and_finite():
    from plexgraph_core.algorithms.layout import _repulsion
    p = np.random.default_rng(4).uniform(-1, 1, (6000, 2))
    f = _repulsion(p, .01, False)
    assert np.isfinite(f).all()
    assert np.mean(np.sum(p * f, axis=1)) > 0  # outward pressure, not zero forces
    np.testing.assert_array_equal(f, _repulsion(p, .01, False))


def test_invalid_layout_parameters_raise():
    import pytest
    for kwargs in ({"iterations": 0}, {"area": 0}, {"area": float("nan")},
                   {"gravity": -1}, {"convergence_threshold": -1},
                   {"max_exact_repulsion_nodes": -1}, {"layer_gravity": -1},
                   {"hub_inertia": -0.1}, {"hub_inertia": 1.1}):
        with pytest.raises(ValueError):
            next(force_directed_layout(_path_graph(3), **kwargs))


def _two_layer_path_graph() -> tuple[Graph, list[int], list[int]]:
    """Two layer-tagged paths joined by one layer-less bridge edge, so they stay one connected component --
    deliberately path-shaped so the analytic path shortcut would apply without layer_gravity."""
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")
    a_nodes = [g.add_node(f"a{i}") for i in range(8)]
    b_nodes = [g.add_node(f"b{i}") for i in range(8)]
    for i in range(len(a_nodes) - 1):
        g.add_edge(a_nodes[i], a_nodes[i + 1], layer=layer_a)
    for i in range(len(b_nodes) - 1):
        g.add_edge(b_nodes[i], b_nodes[i + 1], layer=layer_b)
    g.add_edge(a_nodes[0], b_nodes[0])
    return g, a_nodes, b_nodes


def test_layer_gravity_off_by_default_matches_explicit_zero():
    g, _, _ = _two_layer_path_graph()
    default = list(force_directed_layout(g, iterations=20, seed=1))[-1].positions
    explicit = list(force_directed_layout(g, iterations=20, seed=1, layer_gravity=0.0))[-1].positions
    np.testing.assert_array_equal(default, explicit)


def test_layer_gravity_zero_still_uses_the_analytic_path_shortcut():
    # A regression guard on the fix itself: layer_gravity=0 (the default) must not pay for -- or be affected
    # by -- the hub machinery at all, including the fast analytic path for path-shaped components.
    g, _, _ = _two_layer_path_graph()
    steps = list(force_directed_layout(g, iterations=500, seed=1))
    assert len(steps) == 1
    assert steps[0].converged


def test_layer_gravity_runs_the_simulation_even_for_path_shaped_layers():
    # The bug this feature exposed: the analytic path shortcut froze every node before hub particles were
    # even added, so layer_gravity silently had no effect on any path/cycle-shaped graph.
    g, _, _ = _two_layer_path_graph()
    steps = list(force_directed_layout(g, iterations=500, seed=1, layer_gravity=1.0))
    assert len(steps) > 1


def test_layer_gravity_pulls_each_layer_toward_its_own_hub():
    g, a_nodes, b_nodes = _two_layer_path_graph()

    def layer_separation(layer_gravity: float) -> float:
        final = list(force_directed_layout(g, iterations=500, seed=3, layer_gravity=layer_gravity))[-1].positions
        return np.linalg.norm(final[a_nodes].mean(axis=0) - final[b_nodes].mean(axis=0))

    baseline = layer_separation(0.0)
    with_hubs = layer_separation(1.0)
    assert with_hubs > baseline * 1.25


def test_layer_gravity_blends_a_node_shared_across_two_layers():
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")
    shared = g.add_node("shared")
    a_rest = [g.add_node(f"a{i}") for i in range(5)]
    b_rest = [g.add_node(f"b{i}") for i in range(5)]
    for x in a_rest:
        g.add_edge(shared, x, layer=layer_a)
    for i in range(len(a_rest) - 1):
        g.add_edge(a_rest[i], a_rest[i + 1], layer=layer_a)
    for x in b_rest:
        g.add_edge(shared, x, layer=layer_b)
    for i in range(len(b_rest) - 1):
        g.add_edge(b_rest[i], b_rest[i + 1], layer=layer_b)

    final = list(force_directed_layout(g, iterations=1500, seed=5, layer_gravity=1.5))[-1].positions
    a_centroid = final[a_rest].mean(axis=0)
    b_centroid = final[b_rest].mean(axis=0)
    hub_separation = np.linalg.norm(a_centroid - b_centroid)
    distance_to_a = np.linalg.norm(final[shared] - a_centroid)
    distance_to_b = np.linalg.norm(final[shared] - b_centroid)
    # Pulled toward both hubs at once, so it settles roughly between them rather than fully inside either.
    assert abs(distance_to_a - distance_to_b) < 0.2 * hub_separation


def test_layer_gravity_never_changes_the_positions_shape():
    g, _, _ = _two_layer_path_graph()
    step = list(force_directed_layout(g, iterations=5, seed=1, layer_gravity=2.0))[-1]
    assert step.positions.shape == (g.num_nodes, 2)


def test_layer_gravity_pulls_disconnected_sub_communities_within_one_layer_together():
    # A known limitation, not a regression guard against a bug: one hub per LAYER, not per cluster, so a layer
    # containing two structurally unrelated sub-communities gets them pulled toward the same point. Documents
    # today's behavior numerically so a future per-component-hub fix has a clear before/after to compare against.
    g = Graph()
    layer_a = g.add_layer("a")
    layer_b = g.add_layer("b")

    def path(nodes, layer):
        for i in range(len(nodes) - 1):
            g.add_edge(nodes[i], nodes[i + 1], layer=layer)

    a1_nodes = [g.add_node(f"a1_{i}") for i in range(6)]
    a2_nodes = [g.add_node(f"a2_{i}") for i in range(6)]  # same layer as a1_nodes, but disconnected from it
    b_nodes = [g.add_node(f"b_{i}") for i in range(6)]
    path(a1_nodes, layer_a)
    path(a2_nodes, layer_a)
    path(b_nodes, layer_b)
    g.add_edge(a1_nodes[0], b_nodes[0])
    g.add_edge(a2_nodes[0], b_nodes[3])

    def sub_community_distance(layer_gravity: float) -> float:
        final = list(force_directed_layout(g, iterations=800, seed=7, layer_gravity=layer_gravity))[-1].positions
        return np.linalg.norm(final[a1_nodes].mean(axis=0) - final[a2_nodes].mean(axis=0))

    baseline = sub_community_distance(0.0)
    with_hub = sub_community_distance(4.0)
    assert with_hub < baseline * 0.5
