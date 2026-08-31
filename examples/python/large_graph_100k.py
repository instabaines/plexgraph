"""Phase A scale check: a 100K-node / 200K-edge random graph, per
docs/architecture/plan.md's exit criterion. Above ~5000 nodes the layout
falls back to edge-driven placement (no pairwise repulsion) rather than
full force-directed convergence — see hyperloom_core.algorithms.layout's
module docstring for why full O(n^2) repulsion isn't feasible at this
scale in Phase A."""

import numpy as np

from hyperloom_bridge import show
from hyperloom_core import Graph


def main() -> None:
    rng = np.random.default_rng(0)
    n = 100_000
    g = Graph()
    for i in range(n):
        g.add_node(i)
    for u, v in rng.integers(0, n, size=(200_000, 2)):
        if u != v:
            g.add_edge(int(u), int(v))

    print(f"built graph: {g.num_nodes} nodes, {g.num_edges} edges")
    show(g, seed=0, layout_iterations=60)


if __name__ == "__main__":
    main()
