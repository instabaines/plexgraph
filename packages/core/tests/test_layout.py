import numpy as np

from hyperloom_core.algorithms.layout import force_directed_layout
from hyperloom_core.model.ir import Graph


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
    from hyperloom_core.algorithms.layout import _repulsion
    p = np.random.default_rng(0).normal(size=(300, 2))
    delta = p[:, None, :] - p[None, :, :]
    d2 = np.maximum(np.sum(delta * delta, axis=2), (.1 * .05) ** 2)
    expected = np.sum(delta * (.01 / d2)[..., None], axis=1)
    np.testing.assert_allclose(_repulsion(p, .1, True), expected)


def test_mesh_repulsion_remains_active_and_finite():
    from hyperloom_core.algorithms.layout import _repulsion
    p = np.random.default_rng(4).uniform(-1, 1, (6000, 2))
    f = _repulsion(p, .01, False)
    assert np.isfinite(f).all()
    assert np.mean(np.sum(p * f, axis=1)) > 0  # outward pressure, not zero forces
    np.testing.assert_array_equal(f, _repulsion(p, .01, False))


def test_invalid_layout_parameters_raise():
    import pytest
    for kwargs in ({"iterations": 0}, {"area": 0}, {"area": float("nan")},
                   {"gravity": -1}, {"convergence_threshold": -1},
                   {"max_exact_repulsion_nodes": -1}):
        with pytest.raises(ValueError):
            next(force_directed_layout(_path_graph(3), **kwargs))
