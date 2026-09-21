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
        steps = force_directed_layout(
            self.graph, iterations=self.layout_iterations, seed=self.seed
        )
        while True:
            # Advance one step at a time off the event-loop thread. Awaiting
            # send preserves backpressure; no unbounded producer queue.
            step = await asyncio.to_thread(next, steps, None)
            if step is None:
                break
            await send(encode_layout_step(step))
