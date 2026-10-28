"""Centrality for multilayer and temporal networks, via the supra-adjacency matrix.

De Domenico, Sole-Ribalta, Omodei, Gomez & Arenas, "Centrality in Interconnected Multilayer
Networks" (arXiv:1311.2906), and the earlier "Mathematical Formulation of Multilayer Networks"
(De Domenico et al., Phys. Rev. X 3, 041022, 2013): stack every layer's adjacency into one
block-diagonal "supra-adjacency" matrix, connect each node's copies across layers with inter-layer
coupling edges, and run an ordinary single-layer centrality algorithm on that one matrix. This is
the field's standard construction -- "versatility" in De Domenico's terminology -- and the same
supra-graph idea `community.py` already builds for modularity, just with coupling edges that are
genuine weighted edges here rather than a bonus term outside the adjacency.

Two centralities, both reducing exactly to their well-known single-layer definition when the graph
has no layers:

- `eigenvector_centrality`: the leading eigenvector of the supra-adjacency matrix (power iteration,
  L2-normalized -- same convention as networkx's `eigenvector_centrality`). Requires the graph (or
  at least the component being measured) to be connected for the result to be meaningful and
  unique, same as ordinary eigenvector centrality.
- `pagerank_centrality`: PageRank's damped random walk, generalized to the supra-adjacency matrix,
  with uniform teleportation across *every* (node, layer) pair -- paper's Eq. 3. Dangling state
  nodes (no outgoing edges) teleport with probability 1, the standard PageRank convention, which
  the paper's own Eq. 3 reduces to for such nodes.

Both: `layer=None` (plexgraph's "always present" backbone connectors) is its own pseudo-layer, like
`community.py`. Hyperedges aren't supported directly; run `bipartite_expansion()` first (see
hypergraph.py). Direction is honored for PageRank (the random walk follows edge direction; an
undirected connector contributes to both directions); eigenvector centrality treats every edge as
undirected, matching the standard definition (the directed generalization, HITS-style, is out of
scope here).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from plexgraph_core.model.ir import Graph, LayerId, NodeId

StateNode = tuple[NodeId, "LayerId | None"]
Neighbors = list[list[tuple[int, float]]]


@dataclass(frozen=True, slots=True)
class CentralityResult:
    """`state_centrality` is one score per (node, layer) state node -- a node can be more central
    in one layer than another, a real property of multilayer centrality, not noise."""

    state_centrality: dict[StateNode, float]

    def node_centrality(self) -> dict[NodeId, float]:
        """Collapse to one score per node by summing across its layers (the paper's own
        aggregation: overall importance is the total centrality held across every layer a node
        appears in). For a node in only one layer, this is exactly its state node's score."""
        totals: dict[NodeId, float] = {}
        for (node_id, _layer), value in self.state_centrality.items():
            totals[node_id] = totals.get(node_id, 0.0) + value
        return totals

    def apply_as_node_attribute(self, graph: Graph, *, attribute: str = "centrality") -> None:
        """Stamp `node_centrality()` onto `graph` as a numeric node attribute, in place -- so
        `show(g, node_color_by="centrality")` or `node_size_by="centrality"` (`size_by_attribute`)
        picks it up directly through the existing attribute machinery, with no viewer changes."""
        for node_id, value in self.node_centrality().items():
            graph.set_node_attrs(node_id, **{attribute: value})


def eigenvector_centrality(
    graph: Graph,
    *,
    coupling_weight: float = 1.0,
    max_iterations: int = 1000,
    tolerance: float = 1e-8,
    seed: int | None = None,
) -> CentralityResult:
    """Leading eigenvector of the supra-adjacency matrix, L2-normalized. `coupling_weight` is the
    weight of each inter-layer edge connecting a node's copies across layers (default 1.0, same
    scale as an unweighted real edge). Raises if the supra-graph has no edges at all (the leading
    eigenvector is undefined) or doesn't converge within `max_iterations`.

    Power iteration starts from a random vector (seeded by `seed`), not a uniform one: a uniform
    start is exactly degenerate for some small symmetric graphs (e.g. a star), landing exactly on
    the boundary between the dominant eigenvector and others, which can 2-cycle forever instead of
    converging. A random start avoids that with probability 1.
    """
    if not np.isfinite(coupling_weight) or coupling_weight < 0:
        raise ValueError("coupling_weight must be nonnegative and finite")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be positive and finite")

    state_index, neighbors = _build_supra_adjacency(graph, coupling_weight, directed_ok=False)
    n = len(state_index)
    if n == 0:
        return CentralityResult({})
    if not any(neighbors):
        raise ValueError("eigenvector_centrality is undefined on a graph with no edges")

    # A bipartite graph/component has its two dominant eigenvalues at exactly +lambda and
    # -lambda -- equal magnitude, so plain power iteration can never amplify one over the other
    # (a structural property, not fixable by the starting vector: it 2-cycles between two fixed
    # vectors forever). Shifting every diagonal entry by a safe upper bound on the spectral radius
    # (the total edge weight -- Perron-Frobenius puts the true radius well inside that) turns
    # (+lambda, -lambda) into (+lambda+shift, -lambda+shift), unambiguously separated, without
    # changing the eigenvectors at all -- the dominant eigenvector of A+shift*I is exactly that
    # of A, just with every eigenvalue offset by the same constant.
    shift = sum(w for row in neighbors for _j, w in row)
    x = np.random.default_rng(seed).uniform(0.1, 1.0, n)
    x /= np.linalg.norm(x)
    for _ in range(max_iterations):
        nxt = shift * x
        for i in range(n):
            for j, w in neighbors[i]:
                nxt[i] += w * x[j]
        norm = np.linalg.norm(nxt)
        if norm == 0:
            raise ValueError("eigenvector_centrality collapsed to zero -- the graph likely has a component with no edges")
        nxt /= norm
        if np.max(np.abs(nxt - x)) < tolerance:
            x = nxt
            break
        x = nxt
    else:
        raise ValueError(f"eigenvector_centrality did not converge within {max_iterations} iterations")

    x = np.abs(x)  # canonical sign: the Perron-Frobenius eigenvector is unique only up to sign
    return CentralityResult({state: float(x[idx]) for state, idx in state_index.items()})


def pagerank_centrality(
    graph: Graph,
    *,
    damping: float = 0.85,
    coupling_weight: float = 1.0,
    max_iterations: int = 1000,
    tolerance: float = 1e-10,
) -> CentralityResult:
    """PageRank on the supra-adjacency matrix: with probability `damping`, follow a random
    out-edge (weighted); otherwise (or from a dangling state node with no out-edges) teleport
    uniformly to any of the N*L state nodes -- paper's Eq. 3. Scores sum to 1 over all state nodes
    (so `node_centrality()`'s per-node sum is a share of that total, like ordinary PageRank)."""
    if not np.isfinite(damping) or not (0.0 <= damping <= 1.0):
        raise ValueError("damping must be between 0 and 1")
    if not np.isfinite(coupling_weight) or coupling_weight < 0:
        raise ValueError("coupling_weight must be nonnegative and finite")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be positive and finite")

    state_index, neighbors = _build_supra_adjacency(graph, coupling_weight, directed_ok=True)
    n = len(state_index)
    if n == 0:
        return CentralityResult({})

    out_total = np.zeros(n)
    for i in range(n):
        out_total[i] = sum(w for _j, w in neighbors[i])
    dangling = out_total == 0

    p = np.full(n, 1.0 / n)
    for _ in range(max_iterations):
        nxt = np.full(n, (1.0 - damping) / n)
        nxt += damping * p[dangling].sum() / n  # dangling mass redistributed uniformly
        for i in range(n):
            if out_total[i] == 0:
                continue
            share = damping * p[i] / out_total[i]
            for j, w in neighbors[i]:
                nxt[j] += share * w
        if np.max(np.abs(nxt - p)) < tolerance:
            p = nxt
            break
        p = nxt
    else:
        raise ValueError(f"pagerank_centrality did not converge within {max_iterations} iterations")

    return CentralityResult({state: float(p[idx]) for state, idx in state_index.items()})


def _build_supra_adjacency(
    graph: Graph, coupling_weight: float, *, directed_ok: bool
) -> tuple[dict[StateNode, int], Neighbors]:
    state_index: dict[StateNode, int] = {}

    def index_of(node_id: NodeId, layer_id: "LayerId | None") -> int:
        key = (node_id, layer_id)
        if key not in state_index:
            state_index[key] = len(state_index)
        return state_index[key]

    edges: list[tuple[int, int, float, bool]] = []  # (a, b, weight, directed)
    for connector in graph.connectors():
        if connector.is_hyperedge:
            raise ValueError(
                "multilayer centrality does not support hyperedges directly -- run "
                "plexgraph_core.algorithms.hypergraph.bipartite_expansion(graph) first"
            )
        weight = 1.0 if connector.weight is None else float(connector.weight)
        u, v = connector.endpoints
        a = index_of(u, connector.layer_id)
        b = index_of(v, connector.layer_id)
        if a == b:
            continue  # self-loops don't inform centrality
        edges.append((a, b, weight, directed_ok and connector.directed))

    # A node with zero connectors still gets exactly one (edgeless) state node, so it's
    # represented -- but a node that already has a real state node from some layer does not also
    # get a phantom layer=None one: that would dilute every other node's PageRank teleportation
    # share for no modeling reason (unlike community.py, where an extra zero-degree candidate
    # community is harmless -- here it's an extra node sharing in a fixed probability budget).
    nodes_with_a_state = {node_id for node_id, _layer in state_index}
    for node in graph.nodes():
        if node.id not in nodes_with_a_state:
            index_of(node.id, None)

    n = len(state_index)
    neighbors: Neighbors = [[] for _ in range(n)]
    for a, b, weight, directed in edges:
        neighbors[a].append((b, weight))
        if not directed:
            neighbors[b].append((a, weight))

    by_node: dict[NodeId, list[int]] = {}
    for (node_id, _layer), idx in state_index.items():
        by_node.setdefault(node_id, []).append(idx)
    if coupling_weight > 0:
        for idxs in by_node.values():
            for i, a in enumerate(idxs):
                for b in idxs[i + 1:]:
                    neighbors[a].append((b, coupling_weight))
                    neighbors[b].append((a, coupling_weight))

    return state_index, neighbors
