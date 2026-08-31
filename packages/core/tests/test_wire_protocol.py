import time

import numpy as np

from hyperloom_core.algorithms.layout import LayoutStep
from hyperloom_core.model.ir import Graph
from hyperloom_core.wire.protocol import decode, encode_graph, encode_layout_step


def test_encode_decode_plain_graph_roundtrip():
    g = Graph()
    a = g.add_node("a", label="Alice")
    b = g.add_node("b", label="Bob")
    g.add_edge(a, b, weight=1.5)

    decoded = decode(encode_graph(g))
    assert decoded["type"] == "graph"
    assert len(decoded["nodes"]) == 2
    assert len(decoded["connectors"]) == 1
    assert decoded["connectors"][0]["weight"] == 1.5
    assert decoded["connectors"][0]["t_start"] is None  # always-present sentinel
    assert decoded["connectors"][0]["t_end"] is None


def test_encode_decode_carries_temporal_and_layer_fields():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_layer("friendship")
    g.add_edge("a", "b", layer="friendship", t_start=1.0, t_end=2.0)

    decoded = decode(encode_graph(g))
    conn = decoded["connectors"][0]
    assert conn["layer_id"] == 0
    assert conn["t_start"] == 1.0
    assert conn["t_end"] == 2.0
    assert len(decoded["layers"]) == 1


def test_encode_decode_hyperedge_endpoint_count():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])

    decoded = decode(encode_graph(g))
    assert len(decoded["connectors"][0]["endpoints"]) == 4


def test_encode_graph_stays_fast_at_100k_node_scale():
    # Phase A's scale target (docs/architecture/plan.md exit criterion).
    # A prior manual check measured ~0.7s / ~18MB for 100K nodes / 200K
    # edges on this machine — assert a generous ceiling so a real
    # regression (e.g. accidentally O(n^2) serialization) still fails
    # this test without making it flaky across machines.
    rng = np.random.default_rng(0)
    g = Graph()
    n = 100_000
    for i in range(n):
        g.add_node(i)
    for u, v in rng.integers(0, n, size=(200_000, 2)):
        if u != v:
            g.add_edge(int(u), int(v))

    start = time.perf_counter()
    payload = encode_graph(g)
    elapsed = time.perf_counter() - start

    assert elapsed < 10.0
    assert len(payload) > 0


def test_encode_layout_step_roundtrip():
    step = LayoutStep(
        iteration=3,
        positions=np.array([[0.0, 1.0], [2.0, 3.0]]),
        converged=False,
    )
    decoded = decode(encode_layout_step(step))
    assert decoded["type"] == "layout_step"
    assert decoded["iteration"] == 3
    assert decoded["num_nodes"] == 2
    positions = np.frombuffer(decoded["positions"], dtype=np.float32).reshape(-1, 2)
    np.testing.assert_allclose(positions, [[0.0, 1.0], [2.0, 3.0]], rtol=1e-5)
