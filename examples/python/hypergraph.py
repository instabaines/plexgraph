"""Phase D example: a hypergraph. Each hyperedge (a connector spanning
more than 2 nodes) renders as a translucent convex-hull polygon wrapping
its member nodes, instead of a line — see docs/architecture/plan.md
section 5. The layout pulls each hyperedge's members together (via
pairwise clique attraction — see hyperloom_core.algorithms.layout) so the
hulls end up visually coherent rather than enclosing scattered nodes."""

from hyperloom_bridge import show
from hyperloom_core import Graph


def main() -> None:
    g = Graph()
    for i in range(12):
        g.add_node(i)

    # A few overlapping hyperedges of different sizes.
    g.add_hyperedge([0, 1, 2, 3])
    g.add_hyperedge([3, 4, 5])
    g.add_hyperedge([6, 7, 8, 9, 10])
    g.add_hyperedge([1, 6, 11])

    show(g, seed=2)


if __name__ == "__main__":
    main()
