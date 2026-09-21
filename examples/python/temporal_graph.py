"""Phase B example: a graph with temporal edges. Opens with the timeline
UI visible (the "All time" button + slider along the bottom) — click "All
time" to switch to scrubbing mode, then drag the slider to see only the
edges valid at that point in time. The ring backbone (always-present
edges) stays constant; the diagonal cross edges appear/disappear as you
scrub.

Click "Stack: time" (top-left) instead to see the whole timeline at once,
as a sequence of separated planes (bucketed, blue=early to orange=late)
rather than one moment at a time."""

from plexgraph_bridge import show
from plexgraph_core import Graph


def main() -> None:
    g = Graph()
    for i in range(12):
        g.add_node(i)

    # Always-present ring backbone (no t_start/t_end — visible at every time).
    for i in range(12):
        g.add_edge(i, (i + 1) % 12)

    # Each cross edge is only valid for a 3-unit window starting at i.
    for i in range(12):
        g.add_edge(i, (i + 5) % 12, t_start=i, t_end=i + 2)

    show(g, seed=3)


if __name__ == "__main__":
    main()
