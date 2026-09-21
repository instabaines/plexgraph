"""Local WebSocket server. One BridgeServer serves one live graph; each
browser tab that connects gets its own Session (so reconnecting re-streams
the current graph + a fresh layout run).

Phase A does not yet handle incoming control-plane messages from the
frontend (hover/selection events) — the message loop after the initial
stream is a placeholder for that, per docs/architecture/plan.md section 3.

Python can change the viewer's style while it is open (`push_style`): the change goes to every connected tab, and a
tab that connects later is brought up to date right after it receives the graph.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import secrets
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from plexgraph_bridge.hub import ClientHub
from plexgraph_core.model.ir import Graph

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
        super().__init__(graph, layout_iterations=layout_iterations, seed=seed, style=style)
        self.static_dir = static_dir
        self.token = token
        self.host = host
        self.port = port
        self._server: Server | None = None

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        async def until_gone() -> None:
            # Placeholder message loop for future control-plane traffic (hover/selection events flowing
            # frontend -> Python).
            async for _message in websocket:
                pass

        await self.serve_client(websocket.send, until_gone, closed=(websockets.ConnectionClosed,))

    async def start(self) -> int:
        """Start listening and return the bound port."""
        self._loop = asyncio.get_running_loop()
        self._server = await websockets.serve(self._handle_connection, self.host, self.port,
                                              process_request=self._process_request)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo = Graph()
    for i in range(20):
        demo.add_node(i)
    for i in range(19):
        demo.add_edge(i, i + 1)
    asyncio.run(run_server(demo, port=8765))
