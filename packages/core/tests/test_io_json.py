import math

import pytest

from plexgraph_core.io.json_io import graph_from_dict, graph_to_dict, read_json, write_json
from plexgraph_core.model.ir import Graph


def test_a_plain_graph_round_trips_exactly(tmp_path):
    g = Graph()
    g.add_node("alice", age=30)
    g.add_node("bob", age=25)
    g.add_edge("alice", "bob", weight=2.5, relation="friend")

    data = graph_to_dict(g)
    g2 = graph_from_dict(data)

    assert g2.num_nodes == 2 and g2.num_edges == 1
    assert g2.node("alice").attrs == {"age": 30}
    assert g2.node("bob").attrs == {"age": 25}
    c = g2.connector(0)
    assert c.weight == 2.5
    assert c.attrs == {"relation": "friend"}
    assert not c.directed


def test_write_json_then_read_json_round_trips_through_a_real_file(tmp_path):
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b", directed=True, weight=1.0)

    path = tmp_path / "g.json"
    write_json(g, path)
    g2 = read_json(path)

    assert g2.num_nodes == 2 and g2.num_edges == 1
    assert g2.connector(0).directed


# ---- every IR field round-trips: layers, temporal bounds, hyperedges, roles, time_unit ------------


def test_layers_round_trip(tmp_path):
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_layer("friendship", color="blue")
    g.add_edge("a", "b", layer="friendship")

    g2 = graph_from_dict(graph_to_dict(g))
    assert len(g2.layers()) == 1
    assert g2.layers()[0].key == "friendship"
    assert g2.layers()[0].attrs == {"color": "blue"}
    assert g2.connector(0).layer_id == g2.layers()[0].id


def test_temporal_bounds_round_trip_including_always_present(tmp_path):
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_node("c")
    g.add_edge("a", "b", t_start=3, t_end=7)
    g.add_edge("b", "c")  # always present (-inf, +inf)

    g2 = graph_from_dict(graph_to_dict(g))
    assert g2.connector(0).t_start == 3 and g2.connector(0).t_end == 7
    assert g2.connector(1).is_always_present


def test_hyperedges_and_roles_round_trip(tmp_path):
    g = Graph()
    for k in ("a", "b", "c"):
        g.add_node(k)
    g.add_hyperedge(["a", "b", "c"], roles={"a": "source", "b": "target", "c": "cc"})

    g2 = graph_from_dict(graph_to_dict(g))
    c = g2.connector(0)
    assert c.is_hyperedge
    assert len(c.endpoints) == 3
    assert c.roles == {0: "source", 1: "target", 2: "cc"}


def test_time_unit_round_trips(tmp_path):
    g = Graph()
    g.time_unit = "epoch_seconds"
    g2 = graph_from_dict(graph_to_dict(g))
    assert g2.time_unit == "epoch_seconds"


def test_a_node_with_no_weight_round_trips_as_none(tmp_path):
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_edge("a", "b")
    g2 = graph_from_dict(graph_to_dict(g))
    assert g2.connector(0).weight is None


def test_integer_node_keys_round_trip(tmp_path):
    g = Graph()
    g.add_node(7)
    g.add_node(8)
    g.add_edge(7, 8)
    g2 = graph_from_dict(graph_to_dict(g))
    assert g2.node(7) is not None and g2.node(8) is not None


# ---- what this deliberately doesn't coerce ----------------------------------------------------------


def test_a_non_scalar_node_key_raises_clearly(tmp_path):
    g = Graph()
    g.add_node(("a", "b"))
    with pytest.raises(TypeError, match="not a JSON-native scalar"):
        graph_to_dict(g)


def test_the_output_of_graph_to_dict_is_actually_json_serializable(tmp_path):
    import json

    g = Graph()
    g.add_node("a", score=math.pi)
    g.add_node("b")
    g.add_edge("a", "b", weight=1.5)
    text = json.dumps(graph_to_dict(g))
    assert json.loads(text)["nodes"][0]["key"] == "a"
