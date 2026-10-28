"""Time-respecting paths and reachability for temporal networks.

A time-respecting ("temporal") path may only use connectors in non-decreasing departure time --
Holme & Saramaki's "A map of approaches to temporal networks" (arXiv:2103.13615) and the
foundational literature they survey. Even if u->v and v->w are both edges in the ordinary sense,
if the u->v contact happens *after* the v->w contact, the two-hop path u->v->w is not valid -- you'd
arrive at v too late to catch the v->w connection. Plain (non-temporal) shortest-path algorithms get
this wrong on contact-sequence data; this is the reason a dedicated temporal path algorithm exists
at all.

`temporal_reachability(graph, source, start_time=...)` computes, from one source departing no
earlier than `start_time`, the EARLIEST time every other node can be reached -- the direct temporal
analogue of Dijkstra's shortest path, with "arrival time" playing the role of distance (and, since
arrival time is never less than departure time along any connector, the same non-decreasing-weight
assumption that makes Dijkstra correct holds here too). This is the one building block closeness
(and, in future work, betweenness -- Pan & Saramaki 2011; Buss, Molter, Niedermeier & Rymar,
"Algorithmic aspects of temporal betweenness", 2024) are defined on top of.

A connector is available for its whole span [t_start, t_end] (default -inf/+inf: "always
available", the degenerate static-graph case). You may depart along it at any moment that is both
>= your current arrival time at its source and >= its own t_start, as long as that moment is <=
t_end, and you arrive at the other endpoint at that same moment -- the connector itself is
instantaneous once its availability window has let you in; the span only constrains *when* you may
enter it. A plain (u, v, t) event (t_start == t_end) is the common case and works exactly as
expected: depart and arrive both at t.

Direction is honored: a directed connector can only be departed from its source toward its target;
an undirected one either way. Hyperedges aren't supported directly; run `bipartite_expansion()`
first (see hypergraph.py).
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from plexgraph_core.model.ir import ConnectorId, Graph, NodeId


@dataclass(frozen=True, slots=True)
class TemporalPath:
    """`nodes[0]` is the source, `nodes[-1]` the target; `times[i]` is the arrival time at
    `nodes[i]` (`times[0]` is the search's own `start_time`, not a real arrival)."""

    nodes: tuple[NodeId, ...]
    times: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TemporalReachability:
    """`arrival[node]` is the earliest time `node` can be reached; a node absent from `arrival` was
    never reached departing at or after `start_time`. `source` itself maps to `start_time`."""

    source: NodeId
    start_time: float
    arrival: dict[NodeId, float]
    _predecessor: dict[NodeId, tuple[NodeId, ConnectorId]] = field(repr=False)

    def path_to(self, target: NodeId) -> "TemporalPath | None":
        """The actual time-respecting path achieving `arrival[target]`, or None if unreached."""
        if target not in self.arrival:
            return None
        nodes = [target]
        times = [self.arrival[target]]
        current = target
        while current != self.source:
            prev, _connector = self._predecessor[current]
            nodes.append(prev)
            times.append(self.arrival.get(prev, self.start_time))
            current = prev
        nodes.reverse()
        times.reverse()
        return TemporalPath(tuple(nodes), tuple(times))


def temporal_reachability(graph: Graph, source: object, *, start_time: float = float("-inf")) -> TemporalReachability:
    """Earliest-arrival reachability from `source`, departing no earlier than `start_time` (a key
    or internal id, like the rest of the Graph API). Raises if `source` has hyperedge connectors
    incident to it or anywhere in the graph -- run `bipartite_expansion()` first in that case.
    """
    src = graph.node(source).id
    adjacency = _build_temporal_adjacency(graph)

    arrival: dict[NodeId, float] = {src: start_time}
    predecessor: dict[NodeId, tuple[NodeId, ConnectorId]] = {}
    heap: list[tuple[float, NodeId]] = [(start_time, src)]
    while heap:
        time, node = heapq.heappop(heap)
        if time > arrival.get(node, float("inf")):
            continue  # a stale, already-superseded heap entry
        for neighbor, t_start, t_end, connector_id in adjacency.get(node, ()):
            depart = max(time, t_start)
            if depart > t_end:
                continue  # the connector's availability window has already closed
            if depart < arrival.get(neighbor, float("inf")):
                arrival[neighbor] = depart
                predecessor[neighbor] = (node, connector_id)
                heapq.heappush(heap, (depart, neighbor))

    return TemporalReachability(src, start_time, arrival, predecessor)


def temporal_closeness_centrality(graph: Graph, *, start_time: float = float("-inf")) -> dict[NodeId, float]:
    """Harmonic closeness over temporal distance (elapsed time, not absolute arrival clock time):
    for each node, the sum of 1/(time to reach each other node) that is actually reachable. Like
    ordinary harmonic closeness, this handles a graph that isn't fully (temporally) connected
    gracefully -- an unreachable node just contributes 0, rather than the whole score being
    undefined -- and a node with nothing reachable from it scores 0.
    """
    scores: dict[NodeId, float] = {}
    for node in graph.nodes():
        reach = temporal_reachability(graph, node.id, start_time=start_time)
        total = 0.0
        for other, arrival in reach.arrival.items():
            if other == node.id:
                continue
            elapsed = arrival - start_time
            if elapsed > 0:
                total += 1.0 / elapsed
            # elapsed == 0 (reached at the same instant departure was allowed, e.g. start_time
            # itself is a contact time) contributes nothing -- an infinite 1/0 term would swamp
            # every other node's real, finite travel time.
        scores[node.id] = total
    return scores


def _build_temporal_adjacency(graph: Graph) -> dict[NodeId, list[tuple[NodeId, float, float, ConnectorId]]]:
    adjacency: dict[NodeId, list[tuple[NodeId, float, float, ConnectorId]]] = {}

    def add(a: NodeId, b: NodeId, t_start: float, t_end: float, connector_id: ConnectorId) -> None:
        adjacency.setdefault(a, []).append((b, t_start, t_end, connector_id))

    for connector in graph.connectors():
        if connector.is_hyperedge:
            raise ValueError(
                "temporal reachability does not support hyperedges directly -- run "
                "plexgraph_core.algorithms.hypergraph.bipartite_expansion(graph) first"
            )
        u, v = connector.endpoints
        if u == v:
            continue  # a self-loop can never be the fastest way to reach anywhere
        add(u, v, connector.t_start, connector.t_end, connector.id)
        if not connector.directed:
            add(v, u, connector.t_start, connector.t_end, connector.id)

    return adjacency
