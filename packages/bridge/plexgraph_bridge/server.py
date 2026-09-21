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
import collections
import logging
import mimetypes
import secrets
import threading
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from plexgraph_bridge.session import Session
from plexgraph_core.model.ir import Graph

if TYPE_CHECKING:
    from plexgraph_bridge.style import StyleController

logger = logging.getLogger("plexgraph_bridge.server")


# A tab that stops reading (backgrounded, or its page is busy) must never stall Python, and must not make the
# server queue an unbounded backlog. Past this many queued messages the backlog is replaced by one fresh snapshot.
_MAX_OUTBOX = 64
# Changes made faster than the event loop can hand them out are collapsed into one snapshot past this many.
_MAX_INCOMING = 200


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


class _Client:
    """One connected tab.

    Sends are serialised by a lock. Style messages go through an outbox drained by a writer task, so pushing a
    change never waits on a slow tab and always arrives in order. A change that arrives while the tab is still being
    brought up to date is held until it is ready."""

    def __init__(self, websocket: ServerConnection) -> None:
        self.websocket = websocket
        self.lock = asyncio.Lock()
        self.ready = False
        self.pending: list[tuple[int, list[bytes]]] = []
        self.outbox: collections.deque[bytes] = collections.deque()
        self.wake = asyncio.Event()
        self.writer: asyncio.Task | None = None

    async def send(self, data: bytes) -> None:
        async with self.lock:
            await self.websocket.send(data)

    def offer(self, version: int, messages: list[bytes], resync, snapshot: bool = False) -> None:
        """Called on the event loop for every style change. A snapshot (the whole current style) replaces anything
        still queued, since it supersedes it."""
        if not self.ready:
            self.pending.append((version, messages))
            return
        if snapshot:
            self.outbox.clear()
        self.outbox.extend(messages)
        if len(self.outbox) > _MAX_OUTBOX and resync is not None:
            self.outbox.clear()
            self.outbox.extend(resync()[1])
        self.wake.set()

    def start_writer(self) -> None:
        self.writer = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.outbox:
                try:
                    await self.send(self.outbox.popleft())
                except websockets.ConnectionClosed:
                    return


class BridgeServer:
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
        self.graph = graph
        self.static_dir = static_dir
        self.token = token
        self.style = style
        self._clients: set[_Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._incoming: collections.deque[tuple[int, list[bytes]]] = collections.deque()
        self._incoming_lock = threading.Lock()
        self._drain_scheduled = False
        self._resync_needed = False
        self.host = host
        self.port = port
        self.layout_iterations = layout_iterations
        self.seed = seed
        self._server: Server | None = None

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        session = Session(
            self.graph, layout_iterations=self.layout_iterations, seed=self.seed
        )
        client = _Client(websocket)
        # Registered before anything is sent so that no style update can slip past this tab while it connects.
        self._clients.add(client)

        async def bring_style_up_to_date() -> None:
            if self.style is not None:
                version, messages = self.style.replay()
                for message in messages:
                    await client.send(message)
                # Updates that arrived meanwhile and are newer than the replay; the check-and-set below has no
                # await in it, so nothing can be added between "empty" and "ready".
                while client.pending:
                    newer, batch = client.pending.pop(0)
                    if newer > version:
                        for message in batch:
                            await client.send(message)
            client.ready = True
            client.start_writer()

        try:
            await session.stream_to(client.send, after_graph=bring_style_up_to_date)

            # Placeholder message loop for future control-plane traffic
            # (hover/selection events flowing frontend -> Python).
            async for _message in websocket:
                pass
        except websockets.ConnectionClosed:
            pass  # the tab was closed or reloaded, possibly mid-layout: normal, and not worth a traceback
        finally:
            self._clients.discard(client)
            if client.writer is not None:
                client.writer.cancel()

    def _drain_incoming(self) -> None:
        """On the event loop: hand every queued change to every tab."""
        with self._incoming_lock:
            batches = list(self._incoming)
            self._incoming.clear()
            resync, self._resync_needed = self._resync_needed, False
            self._drain_scheduled = False
        resync_fn = self.style.replay if self.style is not None else None
        clients = list(self._clients)
        if resync and resync_fn is not None:
            version, messages = resync_fn()
            for client in clients:
                client.offer(version, messages, resync_fn, snapshot=True)
            return
        for version, messages in batches:
            for client in clients:
                client.offer(version, messages, resync_fn)

    def push_style(self, version: int, messages: list[bytes]) -> None:
        """Queue style messages for every connected tab. Safe to call from any thread; never waits on a tab, and
        messages reach each tab in the order they were pushed. However fast changes are made, at most one wake-up of
        the event loop is ever pending."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._incoming_lock:
            self._incoming.append((version, messages))
            if len(self._incoming) > _MAX_INCOMING:
                self._incoming.clear()
                self._resync_needed = True
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        loop.call_soon_threadsafe(self._drain_incoming)

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
