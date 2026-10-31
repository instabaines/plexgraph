"""Local WebSocket server. One BridgeServer serves one live graph; each
browser tab that connects gets its own Session (so reconnecting re-streams
the current graph + a fresh layout run).

Incoming messages from a tab: export replies (ShowHandle.export/.save; see ClientHub.request_export),
and -- for the desktop app's native Open-File/Save-Project dialogs -- `"load_file"`/`"save_project"`,
each acknowledged with an `encode_ack` reply on the same connection. `"load_file"` swaps `self.graph`
in place and does no hot-swap-mid-stream of its own: the desktop frontend reacts to a successful ack
by closing and reopening its WebSocket, which lands in `_handle_connection` again and streams the
new graph through the ordinary fresh-Session path below. Hover/selection events flowing frontend ->
Python (docs/architecture/plan.md section 3) are not implemented yet.

Python can change the viewer's style while it is open (`push_style`): the change goes to every connected tab, and a
tab that connects later is brought up to date right after it receives the graph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import secrets
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from plexgraph_bridge.hub import ClientHub, Send
from plexgraph_bridge.project import build_project_dict
from plexgraph_core.io.auto import read_graph
from plexgraph_core.model.ir import Graph
from plexgraph_core.wire.protocol import decode, encode_ack

if TYPE_CHECKING:
    from plexgraph_bridge.style import StyleController

logger = logging.getLogger("plexgraph_bridge.server")


# Some systems map these to the wrong type (Windows reads them from the registry), and a browser refuses a script
# served as text/plain.
_CONTENT_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
                  ".json": "application/json", ".svg": "image/svg+xml", ".map": "application/json"}


def _static_response(root: Path, request_path: str) -> Response:
    """Answer a plain HTTP request for a file under `root` (the built viewer). Nothing outside `root` is served."""
    relative = urllib.parse.unquote(request_path.split("?", 1)[0].split("#", 1)[0]).lstrip("/") or "index.html"
    root = root.resolve()
    target = (root / relative).resolve()
    if target.is_dir():
        target = target / "index.html"
    if not target.is_relative_to(root) or not target.is_file():
        body = b"not found"
        return Response(404, "Not Found", Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]), body)
    body = target.read_bytes()
    kind = _CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(200, "OK", Headers([("Content-Type", kind), ("Content-Length", str(len(body)))]), body)


class BridgeServer(ClientHub):
    def __init__(
        self,
        graph: Graph,
        *,
        host: str = "localhost",
        port: int = 0,
        layout_iterations: int = 200,
        seed: int | None = None,
        layer_gravity: float = 0.0,
        hub_inertia: float = 0.5,
        style: "StyleController | None" = None,
        static_dir: Path | None = None,
        token: str | None = None,
    ) -> None:
        """With a `token`, only a viewer that presents it (`ws://host:port/?token=...`) is served the graph. The
        server listens on a port that any web page open in the same browser can try to connect to, and a browser
        lets a page do that whatever its origin, so without a secret only the port number stands between a
        stranger's page and your data. `show()` always sets one.

        With `static_dir`, plain HTTP requests on this same port are answered with the viewer's files, so one
        port serves both the page and the WebSocket. That is what a remote notebook needs: it can forward a single
        port, and the page and the socket then share an origin."""
        super().__init__(graph, layout_iterations=layout_iterations, seed=seed,
                          layer_gravity=layer_gravity, hub_inertia=hub_inertia, style=style)
        self.static_dir = static_dir
        self.token = token
        self.host = host
        self.port = port
        self._server: Server | None = None

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        # One lock for every write to this connection: the graph/layout stream and style pushes
        # already serialise through ClientHub's own per-client lock, but an ack reply (below) sits
        # outside that path, and the websockets library requires concurrent sends on one connection
        # to be externally synchronised -- unsynchronised concurrent writes can interleave at the
        # transport level and corrupt the frame stream.
        send_lock = asyncio.Lock()

        async def send(data: bytes) -> None:
            async with send_lock:
                await websocket.send(data)

        async def until_gone() -> None:
            async for message in websocket:
                await self._handle_incoming(message, send)

        await self.serve_client(send, until_gone, closed=(websockets.ConnectionClosed,))

    async def _handle_incoming(self, message: str | bytes, respond: Send) -> None:
        if not isinstance(message, bytes):
            return  # the protocol is binary-only; a text frame is not one of ours
        try:
            payload = decode(message)
        except Exception:
            logger.warning("could not decode a message from a viewer", exc_info=True)
            return
        msg_type = payload.get("type")
        if msg_type == "export_response" and isinstance(payload.get("id"), str):
            self.resolve_export(payload["id"], payload.get("data"), payload.get("error"))
        elif msg_type == "load_file" and isinstance(payload.get("path"), str):
            await self._load_file(payload["path"], respond)
        elif msg_type == "save_project" and isinstance(payload.get("path"), str):
            await self._save_project(payload["path"], payload.get("style"), respond)

    async def _load_file(self, path: str, respond: Send) -> None:
        """Read `path` with the format its extension names and make it the live graph -- the desktop app's
        native Open-File dialog, relayed by the frontend (see the module docstring)."""
        try:
            graph = read_graph(path)
        except Exception as exc:
            logger.warning("load_file failed for %r", path, exc_info=True)
            await respond(encode_ack("load_file", False, str(exc)))
            return
        self.graph = graph
        await respond(encode_ack("load_file", True))

    async def _save_project(self, path: str, style_spec: object, respond: Send) -> None:
        """Write the live graph (plus layout parameters and, if given, the frontend's current style
        spec) to `path` as a project file -- the desktop app's native Save-Project dialog. There is
        no server-side `StyleController` in the bare desktop sidecar, so the style spec travels in
        the request itself, read straight off the frontend's own `getStyle()`."""
        try:
            data = build_project_dict(
                self.graph,
                layout={
                    "seed": self.seed, "layer_gravity": self.layer_gravity,
                    "hub_inertia": self.hub_inertia, "layout_iterations": self.layout_iterations,
                },
                style_spec=style_spec if isinstance(style_spec, dict) else None,
            )
            Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("save_project failed for %r", path, exc_info=True)
            await respond(encode_ack("save_project", False, str(exc)))
            return
        await respond(encode_ack("save_project", True))

    async def start(self) -> int:
        """Start listening and return the bound port."""
        self._loop = asyncio.get_running_loop()
        # `serve` from websockets.asyncio.server, not `websockets.serve`: before version 14 the latter is the older
        # implementation, which has a different request hook and would not work with this code.
        self._server = await serve(self._handle_connection, self.host, self.port, process_request=self._process_request)
        bound_port = self._server.sockets[0].getsockname()[1]
        self.port = bound_port
        logger.info("bridge server listening on ws://%s:%d", self.host, bound_port)
        return bound_port

    def _authorised(self, request_path: str) -> bool:
        if self.token is None:
            return True
        supplied = urllib.parse.parse_qs(urllib.parse.urlsplit(request_path).query).get("token", [""])[0]
        return secrets.compare_digest(supplied.encode(), self.token.encode())

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            if not self._authorised(request.path):
                body = b"forbidden"
                return Response(403, "Forbidden", Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]), body)
            return None  # a viewer connecting: carry on with the WebSocket handshake
        if self.static_dir is None:
            return None  # not a WebSocket and nothing to serve: the library answers "upgrade required"
        return _static_response(self.static_dir, request.path)

    async def wait_closed(self) -> None:
        if self._server is None:
            raise RuntimeError("server has not been started")
        await self._server.wait_closed()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()


async def run_server(graph: Graph, **kwargs) -> None:
    server = BridgeServer(graph, **kwargs)
    await server.start()
    await server.wait_closed()


def demo_graph() -> Graph:
    """A small path graph, used wherever a stand-in graph is needed with no real data at hand --
    this bare-server demo, and the desktop sidecar's own Phase A placeholder."""
    g = Graph()
    for i in range(20):
        g.add_node(i)
    for i in range(19):
        g.add_edge(i, i + 1)
    return g


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_server(demo_graph(), port=8765))
