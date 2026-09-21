"""Explicit 100K-node demo. The layout keeps approximate repulsion active;
the viewer starts in density overview. Search to inspect a neighborhood.
For bounded, measured runs use benchmarks/layout_audit.py instead."""

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
