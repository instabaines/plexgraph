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


def test_integers_a_browser_cannot_hold_exactly_travel_as_text():
    huge = 24811812513198111524  # larger than any 64-bit integer; a real subreddit name
    beyond_double = 2**60  # fits MessagePack but would round to a different number in the browser
    g = Graph()
    g.add_node(huge)
    g.add_node(beyond_double, big=beyond_double)
    g.add_node(7, count=2**40)
    g.add_edge(huge, 7, note=huge)
    msg = decode(encode_graph(g))
    assert [n["key"] for n in msg["nodes"]] == [str(huge), str(beyond_double), 7]
    assert msg["nodes"][1]["attrs"]["big"] == beyond_double or msg["nodes"][1]["attrs"]["big"] == str(beyond_double)
    assert msg["nodes"][2]["attrs"]["count"] == 2**40  # exact in a double, left alone
    assert msg["connectors"][0]["attrs"]["note"] == str(huge)


def test_loader_keeps_names_that_only_look_like_numbers(tmp_path):
    from hyperloom_core import read_temporal_edgelist
    f = tmp_path / "e.txt"
    f.write_text("24811812513198111524 007 1\n5 -3 2\n")
    keys = [n.key for n in read_temporal_edgelist(f).nodes()]
    assert keys == ["24811812513198111524", "007", 5, -3]


def test_time_unit_travels_with_the_graph():
    g = Graph()
    g.add_node("a"); g.add_node("b"); g.add_edge("a", "b", t_start=1, t_end=1)
    assert decode(encode_graph(g))["time_unit"] is None
    g.time_unit = "epoch_seconds"
    assert decode(encode_graph(g))["time_unit"] == "epoch_seconds"
