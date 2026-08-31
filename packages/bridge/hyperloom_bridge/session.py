"""A per-connection live session: sends the initial graph snapshot, then
streams layout updates as they converge (confirmed v1 requirement — the
frontend watches the layout settle rather than waiting for a final result).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from hyperloom_core.algorithms.layout import force_directed_layout
from hyperloom_core.model.ir import Graph
from hyperloom_core.wire.protocol import encode_graph, encode_layout_step

Sender = Callable[[bytes], Awaitable[None]]


class Session:
    def __init__(
        self,
        graph: Graph,
        *,
        layout_iterations: int = 200,
        seed: int | None = None,
    ) -> None:
        self.graph = graph
        self.layout_iterations = layout_iterations
        self.seed = seed

    async def stream_to(self, send: Sender) -> None:
        await send(encode_graph(self.graph))
        for step in force_directed_layout(
            self.graph, iterations=self.layout_iterations, seed=self.seed
        ):
            await send(encode_layout_step(step))
            # Yield control between iterations so the event loop can still
            # service incoming control-plane messages (hover/selection,
            # once implemented) and so a slow/disconnected client applies
            # backpressure instead of the layout running unboundedly ahead.
            await asyncio.sleep(0)
