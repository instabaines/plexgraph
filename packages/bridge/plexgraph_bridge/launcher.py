"""`show(graph)` — the Phase A entry point: start the WebSocket bridge
server, serve the static frontend app, and display it — inline in the
notebook cell output when running under Jupyter, otherwise a browser tab.

This is the "local server + browser tab" transport from
docs/architecture/plan.md section 3 — the primary v1 path, chosen over a
desktop app for zero-install friction. The Jupyter case reuses the same
local server and just embeds it in an <iframe> rather than opening a
system browser tab; see the docstring on `show()` for why this is a
simpler stopgap rather than the "proper" anywidget integration the plan
describes, and what that would buy over this.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable

from plexgraph_bridge.color import parse_color
from plexgraph_bridge.server import BridgeServer
from plexgraph_bridge.style import STYLE_OPTIONS, StyleController
from plexgraph_core.model.ir import Graph

logger = logging.getLogger("plexgraph_bridge.launcher")

# Where the viewer's static files live, in order of preference:
#   1. plexgraph_bridge/static, the built app bundled into the wheel (an installed package);
#   2. packages/app/dist, the built app in a repo checkout (this file is packages/bridge/plexgraph_bridge/
#      launcher.py, so parents[2] is packages/);
#   3. packages/app/public, a placeholder usable before the frontend has been built.
_APP_BUNDLED = Path(__file__).resolve().parent / "static"
_APP_DIST = Path(__file__).resolve().parents[2] / "app" / "dist"
_APP_PUBLIC = Path(__file__).resolve().parents[2] / "app" / "public"


def _static_app_dir() -> Path:
    for candidate in (_APP_BUNDLED, _APP_DIST):
        if (candidate / "index.html").is_file():
            return candidate
    return _APP_PUBLIC


class _QuietRequestHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler logs every request straight to stderr
    (log_message() calls sys.stderr.write(), bypassing the logging module
    entirely) — harmless in a terminal, but in Jupyter that stderr is
    captured as cell output, so every asset request shows up as clutter
    under the graph. Route it through our own logger instead, at debug
    level, so it's silent by default but still available if needed."""

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


def _serve_static(directory: Path, host: str, port: int) -> http.server.ThreadingHTTPServer:
    handler = partial(_QuietRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _notebook_kind() -> str | None:
    """"jupyter" inside a running Jupyter kernel (notebook, JupyterLab, or the VS Code/Cursor notebook UI),
    "colab" inside Google Colab, None in a plain script or REPL, where there's no cell output to embed
    into and blocking the calling thread is fine.

    Colab has its own shell class (`google.colab._shell.Shell`, not ZMQInteractiveShell), and its kernel runs on
    a remote machine, so it needs a different way of reaching the viewer."""
    try:
        from IPython import get_ipython
    except ImportError:
        return None
    shell = get_ipython()
    if shell is None:
        return None
    if type(shell).__module__.startswith("google.colab"):
        return "colab"
    return "jupyter" if shell.__class__.__name__ == "ZMQInteractiveShell" else None


def _in_jupyter() -> bool:
    return _notebook_kind() is not None


def _display_inline(url: str, *, width: int, height: int) -> None:
    from IPython.display import IFrame, display

    display(IFrame(src=url, width=width, height=height))


def _colab_viewer_path(style: dict[str, Any]) -> str:
    """The path (with query) the Colab iframe opens. One port serves both the viewer page and its WebSocket, so the
    two share an origin: a second proxied port is a different origin and Colab refuses the socket. The viewer is
    told `ws=same-origin` and works out the address for itself, because the proxied host name is Colab's to choose."""
    path = "/?ws=same-origin"
    if style:
        path += f"&style={urllib.parse.quote(json.dumps(style))}"
    return path


def _display_colab(port: int, path: str, *, height: int) -> None:
    """Show the viewer in the cell output through Colab's own port-forwarding call. Other ways of building the
    address (`google.colab.kernel.proxyPort`) point at a different proxy that does not carry WebSockets."""
    from google.colab import output  # type: ignore[import-not-found]

    output.serve_kernel_port_as_iframe(port, path=path, width="100%", height=str(height))


# Python-friendly (snake_case) names for the subset of viz-core's
# RendererOptions users most commonly want to customize from show(),
# mapped to their camelCase wire names. Anything not listed here can't be
# set from Python yet — notably node shape, which needs real new rendering
# work beyond a color/size knob.
_SIMPLE_STYLE_KEYS = {
    "node_radius_px": "nodeRadiusPx",
    "edge_width_px": "edgeWidthPx",
    "background_color": "backgroundColor",
    "arrow_color": "arrowColor",
    "arrow_length": "arrowLength",
    "arrow_width": "arrowWidth",
    "arrow_t": "arrowT",
    "hull_padding": "hullPadding",
}

# Colors among the simple keys above — run through parse_color so callers
# can pass a name ("red"), a hex string ("#ff6347"), or an [r, g, b, a]
# array; everything else in _SIMPLE_STYLE_KEYS is a plain number.
_COLOR_STYLE_KEYS = {"background_color", "arrow_color"}

# node_color/edge_color/node_color_by/edge_color_by/node_label are handled
# separately below (_resolve_node_color etc.) since each can take several
# different shapes (a single color, a dict of overrides, an attribute
# name, ...) that _SIMPLE_STYLE_KEYS' one-value-one-transform model can't
# express.
_SPECIAL_STYLE_KEYS = {
    "node_color",
    "edge_color",
    "node_color_by",
    "edge_color_by",
    "node_label",
}


def _resolve_node_color(value: object) -> tuple[str, Any]:
    """node_color is either a single color (-> nodeColor, applied to every
    node) or a dict of {node_key: color} overrides (-> nodeColorOverrides,
    keyed by String(node_key) to match how the renderer looks nodes up)."""
    if isinstance(value, dict):
        return "nodeColorOverrides", {str(k): parse_color(v) for k, v in value.items()}
    return "nodeColor", parse_color(value)


def _resolve_edge_color(value: object) -> tuple[str, Any]:
    """edge_color is either a single color (-> edgeColor) or a dict of
    {(source, target): color} overrides (-> edgeColorOverrides, keyed by
    "sourceKey||targetKey" to match the renderer's lookup — see
    Renderer.resolveEdgeColor in viz-core)."""
    if isinstance(value, dict):
        overrides: dict[str, Any] = {}
        for k, v in value.items():
            if not (isinstance(k, (tuple, list)) and len(k) == 2):
                raise TypeError(
                    f"edge_color dict keys must be a (source, target) pair, got {k!r}"
                )
            u, w = k
            overrides[f"{u}||{w}"] = parse_color(v)
        return "edgeColorOverrides", overrides
    return "edgeColor", parse_color(value)


def _resolve_node_label(value: object) -> Any:
    """node_label is True (label by node key), an attribute name string
    (label by that attribute's value), or a dict of {node_key: text} for
    fully custom per-node text — see viz-core's NodeLabelSpec."""
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return value


def _build_style_dict(style_kwargs: dict[str, Any]) -> dict[str, Any]:
    known = set(_SIMPLE_STYLE_KEYS) | _SPECIAL_STYLE_KEYS
    unknown = set(style_kwargs) - known
    if unknown:
        raise TypeError(f"unknown style option(s): {sorted(unknown)}; supported: {sorted(known)}")

    result: dict[str, Any] = {}
    for k, v in style_kwargs.items():
        if v is None:
            continue
        if k == "node_color":
            wire_key, wire_value = _resolve_node_color(v)
        elif k == "edge_color":
            wire_key, wire_value = _resolve_edge_color(v)
        elif k == "node_color_by":
            wire_key, wire_value = "nodeColorBy", v
        elif k == "edge_color_by":
            wire_key, wire_value = "edgeColorBy", v
        elif k == "node_label":
            wire_key, wire_value = "nodeLabel", _resolve_node_label(v)
        else:
            wire_key = _SIMPLE_STYLE_KEYS[k]
            wire_value = parse_color(v) if k in _COLOR_STYLE_KEYS else v
        result[wire_key] = wire_value
    return result


def _viewer_url(host: str, http_port: int, ws_port: int, style: dict[str, Any]) -> str:
    url = f"http://{host}:{http_port}/?ws={ws_port}"
    if style:
        url += f"&style={urllib.parse.quote(json.dumps(style))}"
    return url


@dataclass(frozen=True, slots=True)
class ShowHandle:
    """Handle to a running show() session, for programmatic/notebook use
    (block=False) where the caller wants the bound ports without the call
    blocking the current thread."""

    ws_port: int
    http_port: int | None
    thread: threading.Thread
    _stop: Callable[[], None] = field(repr=False)
    url: str | None = None
    _style: StyleController | None = field(default=None, repr=False)

    def close(self) -> None:
        """Stop HTTP/WebSocket servers and release the session (idempotent)."""
        self._stop()

    def style(self, **options: Any) -> None:
        """Change how the open viewer looks, live. Takes the same networkx-style options as show() (`node_color=`,
        `node_size=`, `edge_width=`, `edge_curvature=` ...); fields you leave out stay as they are, and
        `RESET` restores one to its default. Invalid arguments raise here and change nothing."""
        self._controller().update(**options)

    def color_nodes(self, nodes: Any, color: Any = None) -> None:
        """Give specific nodes (by key) a color of their own, over any color scheme.

        `handle.color_nodes(["alice", "bob"], "crimson")`, or `handle.color_nodes({"alice": "red", "bob": "#00f"})`;
        color=None removes it."""
        self._controller().paint(nodes, color)

    def clear_colors(self) -> None:
        """Remove every color set with color_nodes()."""
        self._controller().clear_paint()

    def reset_style(self) -> None:
        """Back to the default look: forget every style change and every painted node."""
        self._controller().reset()

    def get_style(self) -> dict[str, Any]:
        """The style changes currently applied, as plain data."""
        return self._controller().spec()

    def _controller(self) -> StyleController:
        if self._style is None:
            raise RuntimeError("this session has no style controller")
        return self._style

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def show(
    graph: Graph,
    *,
    host: str = "localhost",
    ws_port: int = 0,
    http_port: int = 0,
    layout_iterations: int = 200,
    seed: int | None = None,
    open_browser: bool = True,
    block: bool | None = None,
    width: int = 900,
    height: int = 600,
    node_color: str | list[float] | dict[Any, str | list[float]] | None = None,
    node_color_by: str | None = None,
    node_radius_px: float | None = None,
    node_label: bool | str | dict[Any, str] | None = None,
    edge_color: str | list[float] | dict[tuple[Any, Any], str | list[float]] | None = None,
    edge_color_by: str | None = None,
    edge_width_px: float | None = None,
    background_color: str | list[float] | None = None,
    arrow_color: str | list[float] | None = None,
    arrow_length: float | None = None,
    arrow_width: float | None = None,
    arrow_t: float | None = None,
    hull_padding: float | None = None,
    return_handle: bool = False,
    **style_options: Any,
) -> ShowHandle | None:
    """Render `graph` interactively.

    Starts the WebSocket bridge on its own thread/event loop and serves the
    static frontend app over plain HTTP. Where it's displayed depends on
    the environment:

    - **Inside Jupyter** (notebook, JupyterLab, VS Code notebooks): embeds
      the viewer inline in the cell output via an `<iframe>`, and never
      blocks the kernel (block defaults to False here regardless of the
      `block` argument).
    - **Everywhere else**: opens a system browser tab (unless
      open_browser=False), and blocks the calling thread by default so the
      process stays alive to keep serving — pass block=False to run the
      server without blocking (e.g. from your own event loop or another
      thread).

    Note on the Jupyter path: this is a pragmatic reuse of the same local
    WebSocket+HTTP server embedded in an iframe, not a "real" anywidget
    integration (bidirectional comm channel, works over remote/hosted
    Jupyter like Colab/JupyterHub without port-forwarding, survives
    notebook reopening without rerunning the cell). That's the more robust
    design docs/architecture/plan.md describes for the `widget` package and
    is a reasonable future upgrade; this iframe approach ships today with
    no new transport code.

    Sizing:
    - `width`/`height` control the rendered viewer's pixel size (the
      Jupyter iframe's size, or a hint for the browser-tab case — the
      canvas itself always fills whatever it's embedded in, so a browser
      tab is already as big as the window and doesn't need these). Default
      900x600.
    - `node_radius_px` sets node dot size (default 5); `edge_width_px` sets
      edge line thickness (default 1.5). Both are in screen pixels and
      stay constant regardless of zoom.

    Style options (node_color, edge_color, edge_width_px, etc. — see
    viz-core's RendererOptions for the full semantics of each) are passed
    through to the renderer via a URL parameter. Colors accept a name
    ("tomato"), a hex string ("#ff6347" / "#f63"), or an [r, g, b, a] /
    [r, g, b] array (0-1 floats, or 0-255 ints — auto-detected).

    node_color/edge_color also accept a dict for highlighting specific
    nodes/edges, on top of the plain-color form:

        show(g, node_color={"alice": "red", "bob": "blue"})       # other nodes keep the default
        show(g, edge_color={("alice", "bob"): "tomato"})           # dict keys are (source, target) pairs

    node_color_by/edge_color_by instead auto-color every distinct value of
    an existing node/connector attribute (added via add_node(..., **attrs)
    or add_edge(..., **attrs)), with a legend the app shows automatically —
    the more scalable way to "visualize specific groups" at any graph size:

        g.add_node("alice", group="A")
        g.add_node("bob", group="B")
        show(g, node_color_by="group")

    A node_color/node_color_by conflict resolves in favor of the explicit
    override for any node it names; same for edge_color/edge_color_by.

    node_label draws text next to each node — True (label by node key), an
    attribute name (label by that attribute's value), or a dict of
    {node_key: text} for fully custom text. Off by default.

    **networkx-style styling.** Beyond the options above, show() takes the
    options in `plexgraph_bridge.style.STYLE_OPTIONS`; the same options work
    live on the returned handle (`handle.style(...)`):

        show(g, node_color=[...numbers...], cmap="viridis")       # one number per node -> colormap
        show(g, node_color=["red", "#00f", ...])                   # one color per node
        show(g, node_size=size_by_degree((6, 24)))                 # pixel diameters, from degree
        show(g, node_shape="s", edgecolors="white", linewidths=1)  # squares with an outline
        show(g, edge_color=by_weight("Blues"), edge_curvature=0.2, alpha=0.6)
        show(g, edge_color=by_time_bucket(6))                      # color edges by time bucket

    A tuple of 3-4 numbers is one color; a list or array has one entry per
    node (or edge); a dict maps node keys (or (source, target) pairs) to
    values. Mistakes (unknown colormap, wrong count, coloring by time on a
    graph with no times) raise here, before anything opens.

    Returns None by default — like matplotlib's plt.show() or plotly's
    fig.show(), not the session handle — specifically so that calling
    show(g) bare as a notebook cell's last line doesn't trigger Jupyter's
    automatic display of the return value (which would otherwise print the
    ShowHandle's repr, including internal details like its Thread object,
    right below the graph). Pass return_handle=True if you actually need
    the bound ports for programmatic use (e.g. in tests).
    """
    # Colors, sizes, shapes and the other networkx-style options are checked now (so mistakes raise before anything
    # opens) and sent over the WebSocket, because per-node arrays would not fit in a URL.
    controller = StyleController(graph)
    options = dict(style_options)
    if node_color is not None:
        options["node_color"] = node_color
    if edge_color is not None:
        options["edge_color"] = edge_color
    controller.update(**options)

    style = _build_style_dict(
        {
            "node_color_by": node_color_by,
            "node_radius_px": node_radius_px,
            "node_label": node_label,
            "edge_color_by": edge_color_by,
            "edge_width_px": edge_width_px,
            "background_color": background_color,
            "arrow_color": arrow_color,
            "arrow_length": arrow_length,
            "arrow_width": arrow_width,
            "arrow_t": arrow_t,
            "hull_padding": hull_padding,
        }
    )

    notebook = _notebook_kind()
    in_jupyter = notebook is not None
    if in_jupyter:
        block = False
        open_browser = False
    elif block is None:
        block = True

    app_dir = _static_app_dir()
    single_port = notebook == "colab" and app_dir.exists()  # see _colab_viewer_path
    if single_port and host == "localhost":
        # Colab's port proxy reaches the kernel from outside the loopback interface: a server bound to localhost
        # answers its requests with a 500. The machine is a private, throwaway VM, so listening on every interface
        # is what the proxy needs (it is also what Colab's own examples do).
        host = "0.0.0.0"

    ready = threading.Event()
    bound_ws_port: list[int] = []
    stop_bridge: list[Callable[[], Any]] = []

    def _run_bridge_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _main() -> None:
            server = BridgeServer(
                graph,
                host=host,
                port=ws_port,
                layout_iterations=layout_iterations,
                seed=seed,
                style=controller,
                static_dir=app_dir if single_port else None,
            )
            port = await server.start()
            controller.bind(server.push_style)
            stop_bridge.append(lambda: loop.call_soon_threadsafe(server.close))
            bound_ws_port.append(port)
            ready.set()
            await server.wait_closed()

        try:
            loop.run_until_complete(_main())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    bridge_thread = threading.Thread(target=_run_bridge_loop, daemon=True)
    bridge_thread.start()
    ready.wait(timeout=10)
    if not bound_ws_port:
        raise RuntimeError("bridge server failed to start")
    actual_ws_port = bound_ws_port[0]

    if not app_dir.exists():
        logger.warning(
            "no static frontend found at %s, %s or %s; the bridge is running but there is "
            "nothing to serve yet. From a checkout, build it with `pnpm --filter @plexgraph/app build`.",
            _APP_BUNDLED,
            _APP_DIST,
            _APP_PUBLIC,
        )
        httpd = None
        actual_http_port = None
    elif single_port:
        httpd = None
        actual_http_port = actual_ws_port
    else:
        httpd = _serve_static(app_dir, host, http_port)
        actual_http_port = httpd.server_address[1]

    url = None
    if actual_http_port is not None:
        if notebook == "colab":
            url = _colab_viewer_path(style)  # relative to Colab's proxy, which only Colab can name
            logger.info("displaying in Colab: %s", url)
            _display_colab(actual_ws_port, url, height=height)
        else:
            url = _viewer_url(host, actual_http_port, actual_ws_port, style)
        if notebook == "jupyter":
            logger.info("displaying inline: %s", url)
            _display_inline(url, width=width, height=height)
        elif open_browser:
            logger.info("opening %s", url)
            webbrowser.open(url)
    else:
        logger.info(
            "bridge ready at ws://%s:%d (no frontend to open)", host, actual_ws_port
        )

    stopped = threading.Event()
    def stop() -> None:
        if stopped.is_set():
            return
        stopped.set()
        if bridge_thread.is_alive() and stop_bridge:
            stop_bridge[0]()
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if threading.current_thread() is not bridge_thread:
            bridge_thread.join(timeout=10)

    handle = ShowHandle(ws_port=actual_ws_port, http_port=actual_http_port, thread=bridge_thread, _stop=stop, url=url, _style=controller)

    if block:
        try:
            bridge_thread.join()
        except KeyboardInterrupt:
            handle.close()

    return handle if return_handle else None
