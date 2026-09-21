import math

import pytest

from plexgraph_core.model.ir import Graph


def test_plain_graph_add_and_read():
    g = Graph()
    a = g.add_node("a", label="Alice")
    b = g.add_node("b", label="Bob")
    e = g.add_edge(a, b, weight=1.5)

    assert g.num_nodes == 2
    assert g.num_edges == 1

    edge = g.edge(e)
    assert edge.endpoints == (a, b)
    assert edge.weight == 1.5
    assert not edge.is_hyperedge
    assert edge.is_always_present


def test_default_node_keys_are_ids():
    g = Graph()
    n0 = g.add_node()
    n1 = g.add_node()
    assert g.node(n0).key == n0
    assert g.node(n1).key == n1


def test_duplicate_node_key_raises():
    g = Graph()
    g.add_node("a")
    with pytest.raises(ValueError):
        g.add_node("a")


def test_unknown_node_raises():
    g = Graph()
    with pytest.raises(KeyError):
        g.node("missing")


def test_hyperedge():
    g = Graph()
    ids = [g.add_node(k) for k in ("a", "b", "c")]
    hid = g.add_hyperedge(["a", "b", "c"])
    c = g.connector(hid)
    assert c.is_hyperedge
    assert c.endpoints == tuple(ids)


def test_hyperedge_requires_at_least_two_endpoints():
    g = Graph()
    g.add_node("a")
    with pytest.raises(ValueError):
        g.add_hyperedge(["a"])


def test_temporal_connector_defaults_to_always_present():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    e = g.add_edge("a", "b")
    c = g.connector(e)
    assert c.t_start == float("-inf")
    assert c.t_end == float("inf")
    assert c.is_always_present


def test_temporal_event_and_interval():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    event = g.add_edge("a", "b", t_start=5.0, t_end=5.0)
    interval = g.add_edge("a", "b", t_start=1.0, t_end=3.0)
    assert g.connector(event).is_event
    assert not g.connector(interval).is_event


def test_t_start_after_t_end_raises():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    with pytest.raises(ValueError):
        g.add_edge("a", "b", t_start=5.0, t_end=1.0)


def test_snapshot_view_filters_by_time():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b", t_start=0.0, t_end=10.0)
    g.add_edge("a", "b", t_start=20.0, t_end=30.0)

    snap = g.snapshot(5.0)
    assert snap.num_edges == 1


def test_window_view_filters_overlapping():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b", t_start=0.0, t_end=2.0)
    g.add_edge("a", "b", t_start=10.0, t_end=12.0)

    win = g.window(1.0, 5.0)
    assert win.num_edges == 1


def test_layer_and_layer_view():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_layer("friendship")
    g.add_layer("coworker")
    g.add_edge("a", "b", layer="friendship")
    g.add_edge("a", "b", layer="coworker")
    g.add_edge("a", "b")  # no layer

    friend_view = g.layer_view("friendship")
    assert friend_view.num_edges == 1


def test_connector_arrays_columnar_export():
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b", weight=2.0)
    g.add_edge("a", "b", t_start=0.0, t_end=1.0)

    arrays = g.connector_arrays()
    assert arrays["weight"].shape == (2,)
    assert arrays["weight"][0] == 2.0
    assert math.isnan(arrays["weight"][1])
    assert arrays["t_start"][1] == 0.0
