"""Phase A exit-criterion example: build a graph, call show(), watch it
render in a browser tab with the layout converging live."""

from plexgraph_bridge import show
from plexgraph_core import Graph


def main() -> None:
    g = Graph()
    for i in range(60):
        g.add_node(i)
    for i in range(59):
        g.add_edge(i, i + 1)
    # A few extra edges so it isn't just a bare path.
    for i in range(0, 59, 5):
        g.add_edge(i, (i + 13) % 60)

    show(g, seed=42)


if __name__ == "__main__":
    main()
