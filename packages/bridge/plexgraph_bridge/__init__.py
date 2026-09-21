from typing import TYPE_CHECKING, Any

from plexgraph_bridge.launcher import show
from plexgraph_bridge.style import (
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
    size_by_attribute,
    size_by_degree,
    size_by_time,
    size_by_weight,
)

if TYPE_CHECKING:
    from plexgraph_bridge.server import BridgeServer


def __getattr__(name: str) -> Any:
    # The WebSocket server needs the `websockets` package, which the notebook widget does not. It is imported when it is
    # asked for, so that `import plexgraph` and the widget work in an environment that has not installed it.
    if name == "BridgeServer":
        from plexgraph_bridge.server import BridgeServer

        return BridgeServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "show",
    "BridgeServer",
    "RESET",
    "COLORMAPS",
    "PALETTES",
    "SHAPES",
    "STYLE_OPTIONS",
    "by_attribute",
    "by_degree",
    "by_time",
    "by_time_bucket",
    "by_values",
    "by_weight",
    "shape_by_attribute",
    "size_by_attribute",
    "size_by_degree",
    "size_by_time",
    "size_by_weight",
]
