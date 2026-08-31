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
    # Without clique-expansion attraction, hyperedge members would be
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
