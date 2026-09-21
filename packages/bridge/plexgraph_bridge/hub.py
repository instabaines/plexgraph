"""What every viewer connection needs, whatever carries its bytes.

`ClientHub` holds one live graph and the tabs (or notebook views) looking at it: it streams the graph and the layout
to each, keeps them in step with style changes made from Python, and never lets a slow one stall the rest. A subclass
supplies the transport: `BridgeServer` speaks WebSocket, `GraphWidget` speaks the notebook's comm channel.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
from typing import TYPE_CHECKING, Awaitable, Callable

from plexgraph_bridge.session import Session
from plexgraph_core.model.ir import Graph

if TYPE_CHECKING:
    from plexgraph_bridge.style import StyleController

logger = logging.getLogger("plexgraph_bridge.hub")

# A tab that stops reading (backgrounded, or its page is busy) must never stall Python, and must not make the
# server queue an unbounded backlog. Past this many queued messages the backlog is replaced by one fresh snapshot.
_MAX_OUTBOX = 64
# Changes made faster than the event loop can hand them out are collapsed into one snapshot past this many.
_MAX_INCOMING = 200



Send = Callable[[bytes], Awaitable[None]]


class _Client:
    """One connected tab.

    Sends are serialised by a lock. Style messages go through an outbox drained by a writer task, so pushing a
    change never waits on a slow tab and always arrives in order. A change that arrives while the tab is still being
    brought up to date is held until it is ready."""

    def __init__(self, send: Send, closed: tuple[type[BaseException], ...] = ()) -> None:
        self._send = send
        self.closed = closed  # what the transport raises when the other end has gone
        self.lock = asyncio.Lock()
        self.ready = False
        self.pending: list[tuple[int, list[bytes]]] = []
        self.outbox: collections.deque[bytes] = collections.deque()
        self.wake = asyncio.Event()
        self.writer: asyncio.Task | None = None

    async def send(self, data: bytes) -> None:
        async with self.lock:
            await self._send(data)

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
                except self.closed:
                    return




class ClientHub:
    def __init__(
        self,
        graph: Graph,
        *,
        layout_iterations: int = 200,
        seed: int | None = None,
        style: "StyleController | None" = None,
    ) -> None:
        self.graph = graph
        self.style = style
        self._clients: set[_Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._incoming: collections.deque[tuple[int, list[bytes]]] = collections.deque()
        self._incoming_lock = threading.Lock()
        self._drain_scheduled = False
        self._resync_needed = False
        self.layout_iterations = layout_iterations
        self.seed = seed

    async def serve_client(
        self,
        send: Send,
        until_gone: Callable[[], Awaitable[None]],
        closed: tuple[type[BaseException], ...] = (),
    ) -> None:
        """Bring one viewer up to date and keep it there until `until_gone()` returns (the viewer left).
        `closed` is what `send` raises once the viewer has gone, which is normal and not an error."""
        session = Session(self.graph, layout_iterations=self.layout_iterations, seed=self.seed)
        client = _Client(send, closed)
        # Registered before anything is sent so that no style update can slip past this viewer while it connects.
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
            await until_gone()
        except closed:
            pass  # the viewer was closed or reloaded, possibly mid-layout: normal, and not worth a traceback
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
