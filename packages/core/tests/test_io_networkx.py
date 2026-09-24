import networkx as nx
import pytest

from plexgraph_core.io import from_networkx


def test_from_networkx_undirected_graph():
    nxg = nx.Graph()
    nxg.add_node("a", label="Alice")
    nxg.add_node("b", label="Bob")
    nxg.add_edge("a", "b", weight=2.0)

    g = from_networkx(nxg)
    assert g.num_nodes == 2
    assert g.num_edges == 1
    edge = g.connector(0)
    assert not edge.directed
    assert edge.weight == 2.0
    assert g.node("a").attrs["label"] == "Alice"


def test_from_networkx_digraph_produces_directed_connectors():
    nxg = nx.DiGraph()
    nxg.add_edge("a", "b")
    g = from_networkx(nxg)
    assert g.connector(0).directed


def test_from_networkx_preserves_edge_attrs_besides_weight():
    nxg = nx.Graph()
    nxg.add_edge("a", "b", kind="friend", weight=1.5)
    g = from_networkx(nxg)
    edge = g.connector(0)
    assert edge.weight == 1.5
    assert edge.attrs["kind"] == "friend"


def test_from_networkx_multigraph_keeps_parallel_edges():
    nxg = nx.MultiGraph()
    nxg.add_edge("a", "b")
    nxg.add_edge("a", "b")
    g = from_networkx(nxg)
    assert g.num_edges == 2


# ---- to_networkx ------------------------------------------------------------

from plexgraph_core.io import to_networkx, write_gexf, write_graphml  # noqa: E402
from plexgraph_core.model.ir import Graph  # noqa: E402


def test_to_networkx_basic_multigraph():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, weight=2.0, kind="friend")
    nxg = to_networkx(g)
    assert isinstance(nxg, nx.MultiGraph) and not isinstance(nxg, nx.MultiDiGraph)
    assert set(nxg.nodes()) == {"a", "b"}
    data = nxg.get_edge_data("a", "b")[0]
    assert data["weight"] == 2.0 and data["kind"] == "friend"


def test_to_networkx_preserves_node_attrs():
    g = Graph()
    g.add_node("a", label="Alice")
    nxg = to_networkx(g)
    assert nxg.nodes["a"]["label"] == "Alice"


def test_to_networkx_directed_graph_becomes_multidigraph():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, directed=True)
    nxg = to_networkx(g)
    assert isinstance(nxg, nx.MultiDiGraph)
    assert list(nxg.edges()) == [("a", "b")]


def test_to_networkx_undirected_connector_in_a_directed_graph_gets_both_directions():
    g = Graph()
    a, b, c = g.add_node("a"), g.add_node("b"), g.add_node("c")
    g.add_edge(a, b, directed=True)
    g.add_edge(b, c, directed=False)
    nxg = to_networkx(g)
    assert isinstance(nxg, nx.MultiDiGraph)
    assert set(nxg.edges()) == {("a", "b"), ("b", "c"), ("c", "b")}


def test_to_networkx_multigraph_false_collapses_parallel_edges():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, weight=1.0)
    g.add_edge(a, b, weight=2.0)
    single = to_networkx(g, multigraph=False)
    assert isinstance(single, nx.Graph) and not isinstance(single, nx.MultiGraph)
    assert single.number_of_edges() == 1


def test_to_networkx_layer_key_not_internal_id_becomes_an_attribute():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_layer("friendship")
    g.add_edge(a, b, layer="friendship")
    nxg = to_networkx(g)
    assert nxg.get_edge_data("a", "b")[0]["layer"] == "friendship"


def test_to_networkx_always_present_connector_has_no_time_attrs():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b)
    nxg = to_networkx(g)
    data = nxg.get_edge_data("a", "b")[0]
    assert "t_start" not in data and "t_end" not in data


def test_to_networkx_bounded_connector_has_time_attrs():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, t_start=1.0, t_end=5.0)
    nxg = to_networkx(g)
    data = nxg.get_edge_data("a", "b")[0]
    assert data["t_start"] == 1.0 and data["t_end"] == 5.0


def test_to_networkx_skips_hyperedges_with_a_warning():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_edge(0, 1)
    g.add_hyperedge([0, 1, 2, 3])
    with pytest.warns(UserWarning, match="skipped 1 hyperedge"):
        nxg = to_networkx(g)
    assert nxg.number_of_edges() == 1


def test_to_networkx_directedness_ignores_a_skipped_hyperedge():
    # The only directed connector is a hyperedge, which gets skipped -- the resulting (empty) graph must not become
    # a spurious MultiDiGraph purely because of content that never actually appears in the output.
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3], directed=True)
    with pytest.warns(UserWarning):
        nxg = to_networkx(g)
    assert not isinstance(nxg, nx.MultiDiGraph)


def test_to_networkx_does_not_confuse_a_node_key_with_another_nodes_internal_id():
    # b's internal id is 1; c is deliberately given the explicit key 1, colliding with it. The exported edge must
    # still connect "a" to "b", not to c -- see the identical test in test_io_edgelist.py for the full story.
    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_node(1)  # key 1 == b's internal id
    g.add_edge("a", "b")
    nxg = to_networkx(g)
    assert set(nxg.edges()) == {("a", "b")}


def test_to_networkx_round_trips_through_from_networkx():
    from plexgraph_core.io import from_networkx

    g = Graph()
    a, b = g.add_node("alice", role="staff"), g.add_node("bob", role="guest")
    g.add_edge(a, b, weight=1.5, kind="friend")
    back = from_networkx(to_networkx(g))
    assert back.num_nodes == 2 and back.num_edges == 1
    assert back.node("alice").attrs["role"] == "staff"
    edge = back.connector(0)
    assert edge.weight == 1.5 and edge.attrs["kind"] == "friend"


def test_to_networkx_missing_networkx_raises_a_clear_error(monkeypatch):
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "networkx":
            raise ImportError("simulated: not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "networkx", None)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    g = Graph()
    g.add_node("a")
    with pytest.raises(ImportError, match="to_networkx.*requires networkx"):
        to_networkx(g)


# ---- write_graphml / write_gexf ---------------------------------------------


def test_write_graphml_round_trips(tmp_path):
    from plexgraph_core.io import from_graphml

    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, weight=2.5, kind="friend")
    path = tmp_path / "out.graphml"
    write_graphml(g, str(path))
    assert path.exists()
    back = from_graphml(str(path))
    assert back.num_nodes == 2 and back.num_edges == 1
    edge = back.connector(0)
    assert edge.weight == 2.5 and edge.attrs["kind"] == "friend"


def test_write_gexf_round_trips(tmp_path):
    from plexgraph_core.io import from_gexf

    g = Graph()
    a, b = g.add_node("a", label="Alice"), g.add_node("b", label="Bob")
    g.add_edge(a, b, weight=1.0)
    path = tmp_path / "out.gexf"
    write_gexf(g, str(path))
    assert path.exists()
    back = from_gexf(str(path))
    assert back.num_nodes == 2 and back.num_edges == 1
    assert back.connector(0).weight == 1.0


def test_write_graphml_missing_networkx_raises_a_clear_error(monkeypatch):
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "networkx":
            raise ImportError("simulated: not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "networkx", None)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    g = Graph()
    g.add_node("a")
    with pytest.raises(ImportError, match="write_graphml.*requires networkx"):
        write_graphml(g, "unused.graphml")


def test_write_gexf_missing_networkx_raises_a_clear_error(monkeypatch):
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "networkx":
            raise ImportError("simulated: not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "networkx", None)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    g = Graph()
    g.add_node("a")
    with pytest.raises(ImportError, match="write_gexf.*requires networkx"):
        write_gexf(g, "unused.gexf")
