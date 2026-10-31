"""Save/reopen a full plexgraph "project" -- the graph plus enough state to reproduce how it
looked -- as one JSON file.

Scope: this is a **reproducible** snapshot, not a pixel-exact one. It captures the graph (every IR
field, losslessly, via `plexgraph_core.io.json_io`), the style (both the StyleController spec and
any individually painted nodes), and the layout's own seed/parameters (`layer_gravity`,
`hub_inertia`, `layout_iterations`). Reopening re-runs that same deterministic layout with the same
seed and lands back on the same positions -- but it does not capture a node's literal (x, y): the
viewer has no drag-to-reposition feature yet and never reports positions back to Python (the
server-side `force_directed_layout()` streams them one-way, fresh, each time a tab connects), so
there is nothing else to save. If that changes later, a saved project's `"layout"` section is the
natural place to add captured positions without breaking this version's files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from plexgraph_core.io.json_io import graph_from_dict, graph_to_dict
from plexgraph_core.model.ir import Graph

PROJECT_SCHEMA_VERSION = 1


def build_project_dict(
    graph: Graph,
    *,
    layout: dict[str, Any] | None = None,
    style_spec: dict[str, Any] | None = None,
    painted: dict[Any, tuple[float, ...]] | None = None,
    initial_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a project file's contents from its raw pieces -- no `ShowHandle` required, so a caller
    with just a graph and whatever style/layout state it already has on hand (the desktop sidecar's
    `"save_project"` wire message handler, which has no notebook/browser session at all) can save a
    project the same way `save_project` does for one."""
    return {
        "plexgraph_project": PROJECT_SCHEMA_VERSION,
        "graph": graph_to_dict(graph),
        "layout": dict(layout or {}),
        "style": {
            "initial": dict(initial_style or {}),
            "spec": style_spec or {},
            "painted": [[key, list(rgba)] for key, rgba in (painted or {}).items()],
        },
    }


def save_project(handle: Any, path: str | os.PathLike[str]) -> None:
    """Save `handle`'s graph, style and layout parameters to `path` as one JSON file -- see the
    module docstring for exactly what this does and does not capture. Usually called as
    `handle.save_project(path)` rather than directly."""
    controller = handle._controller()
    data = build_project_dict(
        controller._graph,
        layout=handle._layout_params,
        style_spec=controller.spec(),
        painted=controller.painted(),
        initial_style=handle._initial_style,
    )
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def open_project(path: str | os.PathLike[str], **show_overrides: Any) -> Any:
    """Reopen a project saved with `save_project`/`handle.save_project`: rebuilds the graph,
    re-runs the layout with its original seed/parameters so it settles back into the same shape,
    and reapplies the saved style. Extra keyword arguments (`host`, `port`, `block`, ...) are
    passed through to `show()`, the same as calling it directly; `return_handle` defaults to True
    here (rather than show()'s own False) since there would otherwise be no way to apply the saved
    style back onto the reopened session."""
    from plexgraph_bridge.launcher import show  # deferred: avoids a module-import cycle with launcher

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    graph = graph_from_dict(data["graph"])
    layout = data.get("layout", {})
    style = data.get("style", {})

    defaults = {
        "seed": layout.get("seed"),
        "layer_gravity": layout.get("layer_gravity", 0.0),
        "hub_inertia": layout.get("hub_inertia", 0.5),
        "layout_iterations": layout.get("layout_iterations", 200),
        "_initial_style_override": style.get("initial") or {},
        "return_handle": True,
    }
    handle = show(graph, **{**defaults, **show_overrides})  # overrides win over the saved defaults
    if handle is not None:
        controller = handle._controller()
        spec = style.get("spec")
        if spec:
            controller.load_spec(spec)
        painted = style.get("painted")
        if painted:
            controller.paint({key: rgba for key, rgba in painted})
    return handle
