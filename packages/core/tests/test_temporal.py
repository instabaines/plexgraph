import pytest

from plexgraph_core.algorithms.hypergraph import bipartite_expansion
from plexgraph_core.algorithms.temporal import temporal_closeness_centrality, temporal_reachability
from plexgraph_core.model.ir import Graph


def test_non_transitivity_a_later_contact_cannot_be_reached_through_an_earlier_one():
    # The defining property of temporal paths: A-B at t=5 can't connect to a B-C contact that
    # already happened at t=3 -- "A is connected to B is connected to C" in the plain-graph sense
    # is not enough; a plain shortest-path algorithm would get this wrong.
    g = Graph()
    a, b, c = g.add_node("a"), g.add_node("b"), g.add_node("c")
    g.add_edge(a, b, t_start=5, t_end=5)
    g.add_edge(b, c, t_start=3, t_end=3)
    reach = temporal_reachability(g, a, start_time=0)
    assert b in reach.arrival and reach.arrival[b] == 5
    assert c not in reach.arrival


def test_a_later_contact_in_the_right_order_is_reachable():
    g = Graph()
    a, b, c = g.add_node("a"), g.add_node("b"), g.add_node("c")
    g.add_edge(a, b, t_start=5, t_end=5)
    g.add_edge(b, c, t_start=7, t_end=7)
    reach = temporal_reachability(g, a, start_time=0)
    assert reach.arrival[c] == 7
    path = reach.path_to(c)
    assert path.nodes == (a, b, c)
    assert path.times == (0, 5, 7)


def test_path_to_an_unreached_node_is_none():
    g = Graph()
    a, b, c = g.add_node("a"), g.add_node("b"), g.add_node("c")
    g.add_edge(a, b, t_start=5, t_end=5)
    g.add_edge(b, c, t_start=3, t_end=3)
    reach = temporal_reachability(g, a, start_time=0)
    assert reach.path_to(c) is None


def test_waiting_inside_an_availability_window():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, t_start=3, t_end=10)

    assert temporal_reachability(g, a, start_time=5).arrival[b] == 5  # already inside the window
    assert temporal_reachability(g, a, start_time=1).arrival[b] == 3  # waits for it to open
    assert b not in temporal_reachability(g, a, start_time=11).arrival  # window already closed


def test_static_edges_are_always_available():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b)  # no explicit time -> t_start=-inf, t_end=inf
    reach = temporal_reachability(g, a, start_time=42)
    assert reach.arrival[b] == 42


def test_direction_is_honored():
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, directed=True, t_start=0, t_end=0)
    assert b in temporal_reachability(g, a, start_time=-1).arrival
    assert a not in temporal_reachability(g, b, start_time=-1).arrival


def test_self_loops_are_ignored():
    g = Graph()
    a = g.add_node("a")
    g.add_edge(a, a, t_start=0, t_end=0)
    reach = temporal_reachability(g, a, start_time=-1)
    assert reach.arrival == {a: -1}


def test_source_maps_to_start_time():
    g = Graph()
    a = g.add_node("a")
    reach = temporal_reachability(g, a, start_time=3.5)
    assert reach.arrival[a] == 3.5


def test_hyperedges_raise_without_expansion():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3])
    with pytest.raises(ValueError, match="hyperedge"):
        temporal_reachability(g, 0)


def test_hyperedges_work_after_bipartite_expansion():
    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_hyperedge([0, 1, 2, 3], t_start=0, t_end=0)
    expanded = bipartite_expansion(g)
    reach = temporal_reachability(expanded, 0, start_time=-1)
    assert len(reach.arrival) == 5  # 4 original members + the synthetic hyperedge node


def test_temporal_closeness_decreases_with_distance_along_a_chronological_path():
    g = Graph()
    nodes = [g.add_node(f"n{i}") for i in range(4)]
    for i in range(3):
        g.add_edge(nodes[i], nodes[i + 1], directed=True, t_start=i, t_end=i)
    closeness = temporal_closeness_centrality(g, start_time=-1)
    assert closeness[nodes[0]] > closeness[nodes[1]] > closeness[nodes[2]] > closeness[nodes[3]] == 0.0


def test_temporal_closeness_of_an_isolated_node_is_zero():
    g = Graph()
    a = g.add_node("a")
    g.add_node("isolated")
    g.add_edge(a, g.add_node("b"), t_start=0, t_end=0)
    closeness = temporal_closeness_centrality(g, start_time=-1)
    assert closeness[g.node("isolated").id] == 0.0
