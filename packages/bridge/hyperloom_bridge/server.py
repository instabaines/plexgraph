"""Local WebSocket server. One BridgeServer serves one live graph; each
browser tab that connects gets its own Session (so reconnecting re-streams
the current graph + a fresh layout run).

Phase A does not yet handle incoming control-plane messages from the
frontend (hover/selection events) — the message loop after the initial
stream is a placeholder for that, per docs/architecture/plan.md section 3.
"""

from __future__ import annotations

import asyncio
import logging

import websockets
from websockets.asyncio.server import ServerConnection

from hyperloom_bridge.session import Session
from hyperloom_core.model.ir import Graph

logger = logging.getLogger("hyperloom_bridge.server")


class BridgeServer:
    def __init__(
        self,
        graph: Graph,
        *,
        host: str = "localhost",
        port: int = 0,
        layout_iterations: int = 200,
        seed: int | None = None,
    ) -> None:
        self.graph = graph
        self.host = host
        self.port = port
        self.layout_iterations = layout_iterations
        self.seed = seed
        self._server: websockets.asyncio.server.Server | None = None

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        session = Session(
            self.graph, layout_iterations=self.layout_iterations, seed=self.seed
        )

        async def send(data: bytes) -> None:
            await websocket.send(data)

        await session.stream_to(send)

        # Placeholder message loop for future control-plane traffic
        # (hover/selection events flowing frontend -> Python).
        async for _message in websocket:
            pass

    async def start(self) -> int:
        """Start listening and return the bound port."""
        self._server = await websockets.serve(self._handle_connection, self.host, self.port)
        bound_port = self._server.sockets[0].getsockname()[1]
        self.port = bound_port
        logger.info("bridge server listening on ws://%s:%d", self.host, bound_port)
        return bound_port

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
