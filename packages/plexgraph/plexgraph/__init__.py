"""plexgraph: interactive graph visualization for multilayer, temporal and hypergraph networks.

    import plexgraph as pg

    g = pg.Graph()
    g.add_node("alice")
    g.add_node("bob")
    g.add_edge("alice", "bob", weight=2.0)
    pg.show(g, node_color=pg.by_degree("plasma"))

Everything you normally need is at the top level. The graph model and loaders come from `plexgraph_core`, and the
viewer and styling from `plexgraph_bridge`. Those two are installed alongside this package, but `import plexgraph` is the
supported entry point.
"""

from typing import TYPE_CHECKING, Any

from plexgraph._version import __version__
from plexgraph_bridge import (
    COLORMAPS,
    PALETTES,
    RESET,
    SHAPES,
    STYLE_OPTIONS,
    by_attribute,
    by_degree,
    by_time,
    by_time_bucket,
    by_values,
    by_weight,
    shape_by_attribute,
    show,
    size_by_attribute,
    size_by_degree,
    size_by_time,
    size_by_weight,
)
from plexgraph_bridge.launcher import ShowHandle
from plexgraph_core import (
    Connector,
    Graph,
    Layer,
    Node,
    from_edgelist,
    from_gexf,
    from_graphml,
    from_networkx,
    from_pandas_edgelist,
    from_pandas_temporal_edgelist,
    from_temporal_edgelist,
    read_temporal_edgelist,
)

if TYPE_CHECKING:
    from plexgraph_bridge import BridgeServer


def __getattr__(name: str) -> Any:
    # Needs the `websockets` package, which the notebook widget does not; imported only when asked for.
    if name == "BridgeServer":
        from plexgraph_bridge import BridgeServer

        return BridgeServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    # graph model
    "Graph", "Node", "Connector", "Layer",
    # loading data
    "from_edgelist", "from_pandas_edgelist", "from_networkx", "from_gexf", "from_graphml",
    "from_temporal_edgelist", "from_pandas_temporal_edgelist", "read_temporal_edgelist",
    # showing it
    "show", "ShowHandle", "BridgeServer",
    # styling
    "RESET", "COLORMAPS", "PALETTES", "SHAPES", "STYLE_OPTIONS",
    "by_attribute", "by_degree", "by_weight", "by_time", "by_time_bucket", "by_values",
    "size_by_attribute", "size_by_degree", "size_by_weight", "size_by_time", "shape_by_attribute",
]
