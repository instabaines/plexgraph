"""Streaming force layout with bounded working memory.

Small graphs use blocked exact repulsion. Larger graphs use a fixed spatial
mesh: distant cells act through their mass/centroid; the node's own cell is
sampled deterministically, excluding self. This approximation retains repulsion
at every size; it is not an exact or Barnes-Hut solver. Hyperedges attract their
members to their centroid in linear membership space. Components are packed
into stable, disjoint tiles for display. Simple path/cycle components receive
analytic layouts instead of relying on random force initialization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
import numpy as np
from hyperloom_core.model.ir import Graph

MAX_EXACT_REPULSION_NODES = 512


@dataclass(frozen=True, slots=True)
class LayoutStep:
    iteration: int
    positions: np.ndarray
    converged: bool


def _repulsion(pos: np.ndarray, k: float, exact: bool) -> np.ndarray:
    """At most 128×N exact pairs or 1024×64 mesh pairs in memory."""
    n = len(pos)
    result = np.zeros_like(pos)
    softening = max(k * .05, 1e-9) ** 2
    if exact:
        for start in range(0, n, 128):
            delta = pos[start:start + 128, None, :] - pos[None, :, :]
            squared = np.maximum(np.sum(delta * delta, axis=2), softening)
            result[start:start + 128] = np.sum(delta * (k * k / squared)[..., None], axis=1)
        return result
    low = pos.min(axis=0)
    span = np.maximum(np.ptp(pos, axis=0), 1e-9)
    grid = np.minimum(((pos - low) / span * 8).astype(np.int64), 7)
    cell = grid[:, 0] + grid[:, 1] * 8
    mass = np.bincount(cell, minlength=64)
    centers = np.column_stack([np.bincount(cell, weights=pos[:, d], minlength=64) for d in range(2)])
    centers /= np.maximum(mass, 1)[:, None]
    for start in range(0, n, 1024):
        stop = min(start + 1024, n)
        delta = pos[start:stop, None, :] - centers[None, :, :]
        squared = np.maximum(np.sum(delta * delta, axis=2), softening)
        weights = np.broadcast_to(mass, squared.shape).copy()
        weights[np.arange(stop - start), cell[start:stop]] = 0
        result[start:stop] = np.sum(delta * (k * k * weights / squared)[..., None], axis=1)
    # Stratified local samples avoid the singularity of treating a node's own
    # dense cell as a point mass. Working space is N×8, not N×N.
    order = np.argsort(cell, kind="stable")
    ranks = np.empty(n, dtype=np.int64)
    ranks[order] = np.arange(n)
    starts = np.cumsum(mass) - mass
    count = mass[cell]
    samples = np.minimum(count - 1, 8)
    for j in range(8):
        active = samples > j
        ids = np.flatnonzero(active)
        if not len(ids):
            break
        offsets = 1 + (j * (count[ids] - 1) // samples[ids])
        local_rank = ranks[ids] - starts[cell[ids]]
        target = order[starts[cell[ids]] + (local_rank + offsets) % count[ids]]
        delta = pos[ids] - pos[target]
        squared = np.maximum(np.sum(delta * delta, axis=1), softening)
        result[ids] += delta * (k * k * (count[ids] - 1) / samples[ids] / squared)[:, None]
    return result


def _components(n: int, edges: np.ndarray, hyperedges: list[np.ndarray]):
    parent = np.arange(n)
    size = np.ones(n, dtype=np.int64)
    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    def union(a, b):
        a, b = root(a), root(b)
        if a == b:
            return
        if size[a] < size[b]:
            a, b = b, a
        parent[b] = a
        size[a] += size[b]
    for a, b in edges:
        union(a, b)
    for members in hyperedges:
        for b in members[1:]:
            union(members[0], b)
    roots = np.array([root(i) for i in range(n)])
    _, labels, counts = np.unique(roots, return_inverse=True, return_counts=True)
    return labels, counts


def _tiles(counts):
    """Deterministic shelf packing; tile area follows component population."""
    widths = np.sqrt(counts.astype(float))
    gap = .35
    target = max(float(np.max(widths)), float(np.sqrt(np.sum((widths + gap) ** 2))))
    centers = np.zeros((len(counts), 2))
    x = y = row_height = 0.
    for c in np.argsort(-counts, kind="stable"):
        width = widths[c]
        if x and x + width > target:
            x = 0.
            y += row_height + gap
            row_height = 0.
        centers[c] = [x + width / 2, y + width / 2]
        x += width + gap
        row_height = max(row_height, width)
    return centers, widths


def _pack(pos, labels, counts, centers, widths, area):
    low = np.full((len(counts), 2), np.inf)
    high = np.full((len(counts), 2), -np.inf)
    np.minimum.at(low, labels, pos)
    np.maximum.at(high, labels, pos)
    span = np.maximum(np.max(high - low, axis=1), 1e-9)
    packed = (pos - ((low + high) / 2)[labels]) * (.88 * widths / span)[labels, None] + centers[labels]
    bounds_low = np.min(centers - widths[:, None] / 2, axis=0)
    bounds_high = np.max(centers + widths[:, None] / 2, axis=0)
    return (packed - (bounds_low + bounds_high) / 2) * (area * 1.6 / max(np.max(bounds_high - bounds_low), 1e-9))


def _analytic_components(pos, edges, hyperedges, labels, counts):
    """Resolve simple paths/cycles, ignoring direction only for placement."""
    adjacency = [[] for _ in range(len(pos))]
    for a, b in edges:
        adjacency[a].append(int(b))
        adjacency[b].append(int(a))
    blocked = set(int(labels[h[0]]) for h in hyperedges)
    fixed = np.zeros(len(pos), dtype=bool)
    groups = np.argsort(labels, kind="stable")
    start = 0
    for component, count in enumerate(counts):
        nodes = groups[start:start + count]
        start += count
        if component in blocked:
            continue
        if count == 1:
            pos[nodes] = 0
            fixed[nodes] = True
            continue
        degrees = [len(adjacency[i]) for i in nodes]
        ends = [int(i) for i in nodes if len(adjacency[i]) == 1]
        cycle = all(d == 2 for d in degrees) and count > 2
        if not (cycle or (len(ends) == 2 and all(d <= 2 for d in degrees))):
            continue
        current = int(nodes[0]) if cycle else ends[0]
        ordered, visited = [], set()
        while current not in visited:
            visited.add(current)
            ordered.append(current)
            next_nodes = [i for i in adjacency[current] if i not in visited]
            if not next_nodes:
                break
            current = next_nodes[0]
        if len(ordered) != count:
            continue
        if cycle:
            angle = np.arange(count) * 2 * np.pi / count
            pos[ordered] = np.column_stack([np.cos(angle), np.sin(angle)])
        else:
            pos[ordered] = np.column_stack([np.linspace(-1, 1, count), np.zeros(count)])
        fixed[ordered] = True
    return fixed


def force_directed_layout(
    graph: Graph, *, iterations: int = 200, area: float = 1.0,
    gravity: float = 0.01, convergence_threshold: float = 1e-4,
    seed: int | None = None,
    max_exact_repulsion_nodes: int = MAX_EXACT_REPULSION_NODES,
) -> Iterator[LayoutStep]:
    """Yield independent position snapshots; exhausted budget is not convergence.

    `max_exact_repulsion_nodes` chooses blocked exact vs spatial mesh forces.
    Repulsion remains enabled on either side. `area` controls display extent.
    Node weights/directions/layers do not change force strength in this solver.
    """
    if not isinstance(iterations, (int, np.integer)) or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    if not np.isfinite(area) or area <= 0:
        raise ValueError("area must be positive and finite")
    if not np.isfinite(gravity) or gravity < 0 or not np.isfinite(convergence_threshold) or convergence_threshold < 0:
        raise ValueError("gravity and convergence_threshold must be nonnegative and finite")
    if not isinstance(max_exact_repulsion_nodes, (int, np.integer)) or max_exact_repulsion_nodes < 0:
        raise ValueError("max_exact_repulsion_nodes must be a nonnegative integer")
    n = graph.num_nodes
    if n == 0:
        yield LayoutStep(0, np.zeros((0, 2)), True)
        return
    connectors = graph.connectors()
    edges = np.array([c.endpoints for c in connectors if len(c.endpoints) == 2], dtype=np.int64).reshape(-1, 2)
    # Self-loops carry no attractive force and do not affect connectivity.
    edges = edges[edges[:, 0] != edges[:, 1]]
    hyperedges = [np.asarray(c.endpoints, dtype=np.int64) for c in connectors if c.is_hyperedge]
    labels, counts = _components(n, edges, hyperedges)
    centers, widths = _tiles(counts)
    pos = np.random.default_rng(seed).uniform(-area / 2, area / 2, (n, 2))
    fixed = _analytic_components(pos, edges, hyperedges, labels, counts)
    if fixed.all():
        yield LayoutStep(1, _pack(pos, labels, counts, centers, widths, area), True)
        return
    k = area / np.sqrt(n)
    for it in range(1, iterations + 1):
        disp = _repulsion(pos, k, n <= max_exact_repulsion_nodes)
        if len(edges):
            u, v = edges.T
            delta = pos[u] - pos[v]
            force = delta * (np.linalg.norm(delta, axis=1) / k)[:, None]
            np.add.at(disp, u, -force)
            np.add.at(disp, v, force)
        for members in hyperedges:
            delta = pos[members] - pos[members].mean(axis=0)
            # One spring per incidence. No clique, no virtual node on the wire.
            force = delta * (np.linalg.norm(delta, axis=1) / k)[:, None]
            np.add.at(disp, members, -force)
        disp -= gravity * pos
        disp[fixed] = 0
        length = np.linalg.norm(disp, axis=1)
        # Linear schedule with a nonzero final cap. Test raw forces, so merely
        # cooling to tiny steps cannot report equilibrium.
        temperature = area * .08 * (1 - (it - 1) / iterations)
        step = disp * (np.minimum(length, temperature) / np.maximum(length, 1e-12))[:, None]
        pos += step
        converged = bool(np.max(length) < convergence_threshold)
        yield LayoutStep(it, _pack(pos, labels, counts, centers, widths, area), converged)
        if converged:
            return
