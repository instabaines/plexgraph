"""Force-directed layout, implemented as a generator so callers (the bridge
server) can stream intermediate positions to the frontend as the layout
converges, rather than blocking until it's done. This is the Phase A
default layout — see docs/architecture/plan.md section 6 for the note that
v1 does not aim for parity with mature igraph/Gephi layout libraries.

Hyperedges don't get their own attractive-force term (they have no single
"endpoints" pair) — instead each hyperedge's members are pulled together
via a clique of pairwise attractive forces between every pair of members,
same as plain edges. Without this, a hyperedge's members would be pulled
by nothing at all (silently ignored) and end up scattered arbitrarily,
making the frontend's convex-hull grouping around them visually
meaningless. This clique expansion exists purely to shape the layout; it
is not stored back onto the graph or sent over the wire.

Scalability note: the classic Fruchterman-Reingold repulsion term is O(n^2)
per iteration (every node repels every other node), which is fine up to a
few thousand nodes but is computationally infeasible at the 100K-node scale
this project targets (10 billion pairwise ops per iteration). Rather than
silently hang on large graphs, layouts above MAX_EXACT_REPULSION_NODES fall
back to edge-driven placement (attraction + mild gravity only, no pairwise
repulsion) — O(E) per iteration instead of O(n^2). This produces a worse
layout (unconnected components can overlap) but stays responsive. A proper
fix (Barnes-Hut/quadtree approximation, or GPU-side layout) is out of scope
for Phase A and tracked as an open risk in docs/architecture/plan.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from hyperloom_core.model.ir import Graph


MAX_EXACT_REPULSION_NODES = 5000


@dataclass(frozen=True, slots=True)
class LayoutStep:
    """One iteration's worth of node positions, keyed by internal node id."""

    iteration: int
    positions: np.ndarray  # shape (num_nodes, 2), float64
    converged: bool


def force_directed_layout(
    graph: Graph,
    *,
    iterations: int = 200,
    area: float = 1.0,
    gravity: float = 0.01,
    convergence_threshold: float = 1e-4,
    seed: int | None = None,
    max_exact_repulsion_nodes: int = MAX_EXACT_REPULSION_NODES,
) -> Iterator[LayoutStep]:
    """Fruchterman-Reingold-style force-directed layout.

    Yields a LayoutStep per iteration so callers can stream positions
    (e.g. over the bridge's WebSocket) as the layout visibly converges.

    Above `max_exact_repulsion_nodes`, pairwise repulsion is skipped (see
    module docstring) so the layout stays O(E) instead of O(n^2).
    """
    n = graph.num_nodes
    if n == 0:
        yield LayoutStep(iteration=0, positions=np.zeros((0, 2)), converged=True)
        return

    rng = np.random.default_rng(seed)
    pos = rng.uniform(-area / 2, area / 2, size=(n, 2))

    edges = [
        (c.endpoints[0], c.endpoints[1])
        for c in graph.connectors()
        if not c.is_hyperedge
    ]
    for c in graph.connectors():
        if c.is_hyperedge:
            members = c.endpoints
            edges.extend(
                (members[i], members[j])
                for i in range(len(members))
                for j in range(i + 1, len(members))
            )
    edge_array = np.array(edges, dtype=np.int64) if edges else np.zeros((0, 2), dtype=np.int64)

    use_exact_repulsion = n <= max_exact_repulsion_nodes
    k = area / np.sqrt(n)  # optimal spring length
    temperature = area / 10.0

    for it in range(1, iterations + 1):
        disp = np.zeros((n, 2))

        if use_exact_repulsion:
            # Repulsive force between every pair (O(n^2) — infeasible past
            # max_exact_repulsion_nodes, see module docstring).
            delta = pos[:, np.newaxis, :] - pos[np.newaxis, :, :]
            dist = np.linalg.norm(delta, axis=-1)
            np.fill_diagonal(dist, np.inf)
            repulsive = (k * k) / dist
            disp += np.sum((delta / dist[..., np.newaxis]) * repulsive[..., np.newaxis], axis=1)

        # Attractive force along edges.
        if len(edge_array):
            u, v = edge_array[:, 0], edge_array[:, 1]
            edge_delta = pos[u] - pos[v]
            edge_dist = np.linalg.norm(edge_delta, axis=-1)
            edge_dist = np.where(edge_dist == 0, 1e-9, edge_dist)
            attractive = (edge_dist * edge_dist) / k
            force = (edge_delta / edge_dist[:, np.newaxis]) * attractive[:, np.newaxis]
            np.add.at(disp, u, -force)
            np.add.at(disp, v, force)

        # Mild pull toward center so the layout doesn't drift off-screen.
        disp -= gravity * pos

        # Cap displacement by temperature (simulated annealing) and apply.
        disp_len = np.linalg.norm(disp, axis=-1)
        disp_len = np.where(disp_len == 0, 1e-9, disp_len)
        scale = np.minimum(disp_len, temperature) / disp_len
        step = disp * scale[:, np.newaxis]
        pos += step

        temperature *= 1 - it / iterations
        max_move = float(np.max(np.linalg.norm(step, axis=-1)))
        converged = max_move < convergence_threshold

        yield LayoutStep(iteration=it, positions=pos.copy(), converged=converged)

        if converged:
            return
