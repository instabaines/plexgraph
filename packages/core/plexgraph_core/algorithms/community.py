"""Community detection via generalized ("multislice") modularity maximization.

Mucha, Richardson, Macon, Porter & Onnela, "Community structure in time-dependent, multiscale, and
multiplex networks", Science 328:876-878 (2010). Every (node, layer) pair is its own "state node".
Edges within a layer contribute to modularity the ordinary way: weighted, compared against that
layer's own null model (so different layers' edge densities don't distort each other). A node's
state nodes across different layers are additionally coupled by a flat `omega` bonus with no null
model at all -- coupling isn't modeling a random process to compare against, it's a structural prior
that rewards a node's copies landing in the same community. `layer=None` (plexgraph's "always
present" backbone connectors) is treated as its own pseudo-layer, like any other.

A graph with no layers collapses to exactly one state node per node -- ordinary single-layer
Louvain modularity maximization (Blondel et al. 2008), with no coupling term at all.

This is local-moving Louvain (repeated greedy single-node moves to convergence), not the
multilevel/hierarchical variant -- matches the "Louvain-like locally-greedy algorithm" description
of multislice optimization in the literature, simpler to implement correctly, at some cost in
partition quality on very large graphs versus a full coarsen-and-recurse implementation.

Scope, stated plainly: edges are treated as undirected (direction doesn't affect modularity here,
matching common practice -- see e.g. Gephi's default), weight=None is treated as 1.0, and
self-loops are ignored. Hyperedges aren't modularity-native; run `bipartite_expansion()` first (see
hypergraph.py) if the graph has any -- every synthetic hyperedge node just becomes its own
community-eligible node, same as a real one.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from plexgraph_core.model.ir import Graph, LayerId, NodeId

StateNode = tuple[NodeId, "LayerId | None"]
Neighbors = list[list[tuple[int, float]]]
LayerTotals = dict["LayerId | None", float]


@dataclass(frozen=True, slots=True)
class CommunityResult:
    """`state_communities` is the raw partition this algorithm actually optimizes: one community id
    per (node, layer) pair, so a node *can* land in a different community in different layers --
    that's a real, literature-documented property of multislice modularity, not noise. `modularity`
    is the achieved Q, comparable to single-layer modularity's usual range (roughly -0.5 to 1).
    `state_degree` is each state node's intra-layer weighted degree, for `node_communities()`."""

    state_communities: dict[StateNode, int]
    modularity: float
    state_degree: dict[StateNode, float]

    def node_communities(self) -> dict[NodeId, int]:
        """Collapse to one community per node: whichever community holds the most of that node's
        total layer-degree (ties broken by the lowest community id, for determinism). For a node
        that is only ever in one layer (the common case for most graphs), this is exactly its
        state-node's community -- the collapse only matters when a node's layers disagree. A node
        with zero edges everywhere (every candidate community tied at weight 0) keeps its single
        state node's community."""
        weight_by_node: dict[NodeId, dict[int, float]] = {}
        for state, community in self.state_communities.items():
            node_id = state[0]
            bucket = weight_by_node.setdefault(node_id, {})
            bucket[community] = bucket.get(community, 0.0) + self.state_degree[state]
        return {
            node_id: min((c for c in counts if counts[c] == max(counts.values())))
            for node_id, counts in weight_by_node.items()
        }

    def apply_as_node_attribute(self, graph: Graph, *, attribute: str = "community") -> None:
        """Stamp `node_communities()` onto `graph` as a node attribute, in place -- so
        `show(g, node_color_by="community")` (or `by_attribute("community")`) picks it up directly
        through the existing attribute-coloring machinery, with no viewer changes needed: a
        legend, categorical colors, and attribute-based filtering all come for free. Values are
        stored as strings (`"0"`, `"1"`, ...) so they are always treated as categories, never
        mistaken for a continuous number to run through a colormap."""
        for node_id, community in self.node_communities().items():
            graph.set_node_attrs(node_id, **{attribute: str(community)})


def detect_communities(
    graph: Graph,
    *,
    resolution: float = 1.0,
    omega: float = 1.0,
    seed: int | None = None,
    max_passes: int = 100,
) -> CommunityResult:
    """Maximize generalized modularity over `graph`, returning the partition and its score.

    `resolution` (gamma) scales the null model within every layer: above 1 favors more, smaller
    communities; below 1 favors fewer, larger ones. `omega` is the flat bonus for a node's state
    nodes across layers sharing a community: 0 makes every layer's partition fully independent
    (as if each layer were community-detected on its own); large values increasingly force a
    node's communities to agree across all the layers it appears in.
    """
    if not np.isfinite(resolution) or resolution < 0:
        raise ValueError("resolution must be nonnegative and finite")
    if not np.isfinite(omega) or omega < 0:
        raise ValueError("omega must be nonnegative and finite")
    if not isinstance(max_passes, int) or max_passes < 1:
        raise ValueError("max_passes must be a positive integer")

    state_index, layer_of, neighbors, deg, two_m = _build_state_graph(graph)
    n = len(state_index)
    if n == 0:
        return CommunityResult({}, 0.0, {})

    siblings = _siblings(state_index)
    community = _local_moving_louvain(n, layer_of, neighbors, siblings, deg, two_m, resolution, omega, seed, max_passes)
    modularity = _modularity(n, layer_of, neighbors, siblings, deg, two_m, community, resolution, omega)

    # Relabel to compact, deterministic ids (ordered by each community's smallest state-node index).
    order = sorted(set(community.tolist()), key=lambda c: min(i for i in range(n) if community[i] == c))
    relabel = {old: new for new, old in enumerate(order)}
    state_communities = {state: relabel[community[idx]] for state, idx in state_index.items()}
    state_degree = {state: float(deg[idx]) for state, idx in state_index.items()}
    return CommunityResult(state_communities, modularity, state_degree)


def _build_state_graph(
    graph: Graph,
) -> tuple[dict[StateNode, int], np.ndarray, Neighbors, np.ndarray, LayerTotals]:
    state_index: dict[StateNode, int] = {}

    def index_of(node_id: NodeId, layer_id: "LayerId | None") -> int:
        key = (node_id, layer_id)
        if key not in state_index:
            state_index[key] = len(state_index)
        return state_index[key]

    # Every node gets at least one state node, even with zero connectors, so it still gets a
    # (singleton) community -- the usual convention for isolated nodes.
    for node in graph.nodes():
        index_of(node.id, None)

    adjacency: dict[int, dict[int, float]] = {}

    def add_edge(a: int, b: int, weight: float) -> None:
        if a == b:
            return  # self-loops don't affect which community a node should join
        adjacency.setdefault(a, {})[b] = adjacency.setdefault(a, {}).get(b, 0.0) + weight
        adjacency.setdefault(b, {})[a] = adjacency.setdefault(b, {}).get(a, 0.0) + weight

    for connector in graph.connectors():
        if connector.is_hyperedge:
            raise ValueError(
                "detect_communities does not support hyperedges directly -- run "
                "plexgraph_core.algorithms.hypergraph.bipartite_expansion(graph) first"
            )
        weight = 1.0 if connector.weight is None else float(connector.weight)
        u, v = connector.endpoints
        a = index_of(u, connector.layer_id)
        b = index_of(v, connector.layer_id)
        add_edge(a, b, weight)

    n = len(state_index)
    layer_of = np.empty(n, dtype=object)
    for (_node_id, layer_id), idx in state_index.items():
        layer_of[idx] = layer_id

    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    deg = np.zeros(n)
    for a, row in adjacency.items():
        for b, w in row.items():
            neighbors[a].append((b, w))
            deg[a] += w

    two_m: dict["LayerId | None", float] = {}
    for idx in range(n):
        two_m[layer_of[idx]] = two_m.get(layer_of[idx], 0.0) + deg[idx]

    return state_index, layer_of, neighbors, deg, two_m


def _siblings(state_index: dict[StateNode, int]) -> list[list[int]]:
    by_node: dict[NodeId, list[int]] = {}
    for (node_id, _layer), idx in state_index.items():
        by_node.setdefault(node_id, []).append(idx)
    siblings: list[list[int]] = [[] for _ in range(len(state_index))]
    for idxs in by_node.values():
        for idx in idxs:
            siblings[idx] = [other for other in idxs if other != idx]
    return siblings


def _local_moving_louvain(
    n: int,
    layer_of: np.ndarray,
    neighbors: Neighbors,
    siblings: list[list[int]],
    deg: np.ndarray,
    two_m: LayerTotals,
    resolution: float,
    omega: float,
    seed: int | None,
    max_passes: int,
) -> np.ndarray:
    community = np.arange(n)
    sigma_tot: dict[tuple[int, "LayerId | None"], float] = {}
    for idx in range(n):
        key = (int(community[idx]), layer_of[idx])
        sigma_tot[key] = sigma_tot.get(key, 0.0) + deg[idx]

    rng = np.random.default_rng(seed)
    order = np.arange(n)
    for _pass in range(max_passes):
        rng.shuffle(order)
        moved_any = False
        for v in order:
            lay = layer_of[v]
            old_c: int = int(community[v])
            sigma_tot[(old_c, lay)] -= deg[v]

            weight_to: dict[int, float] = {}
            for u, w in neighbors[v]:
                cu = int(community[u])
                weight_to[cu] = weight_to.get(cu, 0.0) + w
            sibling_communities = [int(community[s]) for s in siblings[v]]
            candidates = set(weight_to) | set(sibling_communities) | {old_c}

            m2 = two_m.get(lay, 0.0)
            best_c: int = old_c
            best_gain = -np.inf
            for c in candidates:
                edge_term = weight_to.get(c, 0.0)
                null_term = (resolution * deg[v] * sigma_tot.get((c, lay), 0.0) / m2) if m2 > 0 else 0.0
                coupling_term = omega * sum(1 for sc in sibling_communities if sc == c)
                gain = edge_term - null_term + coupling_term
                if gain > best_gain:
                    best_gain, best_c = gain, c

            community[v] = best_c
            sigma_tot[(best_c, lay)] = sigma_tot.get((best_c, lay), 0.0) + deg[v]
            if best_c != old_c:
                moved_any = True
        if not moved_any:
            break
    return community


def _modularity(
    n: int,
    layer_of: np.ndarray,
    neighbors: Neighbors,
    siblings: list[list[int]],
    deg: np.ndarray,
    two_m: LayerTotals,
    community: np.ndarray,
    resolution: float,
    omega: float,
) -> float:
    two_mu = sum(two_m.values())
    # Each node's ordered sibling pairs (s != r) contribute omega once per pair when co-located.
    sibling_pair_total = sum(len(s) for s in siblings) * omega
    two_mu += sibling_pair_total
    if two_mu <= 0:
        return 0.0

    raw = 0.0
    for v in range(n):
        lay = layer_of[v]
        for u, w in neighbors[v]:
            if community[u] == community[v]:
                raw += w
    # Subtract the per-layer null model, once per (community, layer) combination.
    sigma_tot: dict[tuple[int, "LayerId | None"], float] = {}
    for v in range(n):
        key = (community[v], layer_of[v])
        sigma_tot[key] = sigma_tot.get(key, 0.0) + deg[v]
    for (c, lay), total_deg in sigma_tot.items():
        m2 = two_m.get(lay, 0.0)
        if m2 > 0:
            raw -= resolution * (total_deg * total_deg) / m2
    for v in range(n):
        for s in siblings[v]:
            if community[s] == community[v]:
                raw += omega
    return raw / two_mu
