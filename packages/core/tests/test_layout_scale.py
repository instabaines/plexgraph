import time

import numpy as np

from plexgraph_core.algorithms.layout import force_directed_layout
from plexgraph_core.model.ir import Graph


def _random_graph(num_nodes: int, num_edges: int, seed: int = 0) -> Graph:
    rng = np.random.default_rng(seed)
    g = Graph()
    for i in range(num_nodes):
        g.add_node(i)
    endpoints = rng.integers(0, num_nodes, size=(num_edges, 2))
    for u, v in endpoints:
        if u != v:
            g.add_edge(int(u), int(v))
    return g


def test_large_graph_uses_approximate_repulsion():
    # A graph just above the default threshold should not attempt O(n^2)
    # repulsion — verified indirectly by completing quickly.
    g = _random_graph(num_nodes=6000, num_edges=12000, seed=1)
    start = time.perf_counter()
    steps = list(
        force_directed_layout(g, iterations=10, seed=1, max_exact_repulsion_nodes=5000)
    )
    elapsed = time.perf_counter() - start
    assert steps[-1].positions.shape == (6000, 2)
    # 10 iterations of O(n^2) repulsion at n=6000 would take tens of
    # seconds; the bounded spatial-mesh approximation should finish in a couple of seconds.
    assert elapsed < 10.0


def test_100k_node_layout_pipeline_stays_responsive():
    # This is the Phase A scale target from docs/architecture/plan.md.
    # Full force-directed convergence isn't attempted (see module
    # docstring) — this checks the approximate path produces valid output
    # for a graph at that scale without hanging.
    g = _random_graph(num_nodes=100_000, num_edges=200_000, seed=2)
    start = time.perf_counter()
    steps = list(force_directed_layout(g, iterations=3, seed=2))
    elapsed = time.perf_counter() - start
    assert steps[-1].positions.shape == (100_000, 2)
    assert np.all(np.isfinite(steps[-1].positions))
    assert elapsed < 30.0


def test_large_hyperedge_uses_linear_membership_storage():
    # Previously expanded to 12,502,500 Python pairs before the first step.
    g = Graph()
    for i in range(5001):
        g.add_node(i)
    g.add_hyperedge(list(range(5001)))
    step = next(force_directed_layout(g, iterations=1, seed=0))
    assert step.positions.shape == (5001, 2)
    assert np.isfinite(step.positions).all()
