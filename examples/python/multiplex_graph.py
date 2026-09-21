"""Phase C example: a multiplex/multilayer graph. Opens with a layer
legend in the top-right — each layer gets its own edge color, and
unchecking a layer hides its edges. Edges with no layer (the "backbone"
here) always stay visible regardless of which layers are toggled.

Click "Stack: layers" (top-left) to switch to the stacked-slices view,
which draws each layer as its own separated plane instead of overlaying
them — the clearest way to see "layer" as an actual dimension."""

from plexgraph_bridge import show
from plexgraph_core import Graph


def main() -> None:
    g = Graph()
    for i in range(10):
        g.add_node(i)

    g.add_layer("friendship")
    g.add_layer("coworker")

    # A backbone with no layer — always visible.
    for i in range(10):
        g.add_edge(i, (i + 1) % 10)

    # Friendship layer: a sparser, more random-looking set of edges.
    for i in range(0, 10, 2):
        g.add_edge(i, (i + 3) % 10, layer="friendship")

    # Coworker layer: a different edge set over the same nodes.
    for i in range(1, 10, 2):
        g.add_edge(i, (i + 4) % 10, layer="coworker")

    show(g, seed=5)


if __name__ == "__main__":
    main()
