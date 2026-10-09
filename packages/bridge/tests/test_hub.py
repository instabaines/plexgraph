"""Direct tests of ClientHub -- the transport-independent core BridgeServer (WebSocket) and _WidgetBridge (the
notebook widget's comm channel) are both built on. These use a fake transport (no socket, no anywidget), so they
prove the shared logic itself is correct, decoupled from either concrete carrier. test_server.py and test_widget.py
cover each subclass's own wiring on top of this."""

from __future__ import annotations

import asyncio
import threading

import msgpack
import pytest

from plexgraph_bridge.hub import ClientHub
from plexgraph_bridge.style import StyleController
from plexgraph_core.model.ir import Graph


def _graph(n: int = 5) -> Graph:
    g = Graph()
    for i in range(n):
        g.add_node(i)
    for i in range(n):
        g.add_edge(i, (i + 1) % n)
        g.add_edge(i, (i * 7 + 3) % n)  # chords: a plain ring is laid out analytically, in one step
    return g


class ClosedError(Exception):
    """Stands in for whatever a real transport raises once the other end has gone."""


class FakeClient:
    """One fake viewer connection: records everything sent to it, and can be made to act as if it has disconnected."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.closed_after: int | None = None  # raise ClosedError on the Nth send (1-indexed), if set
        self.hangs = False  # if True, send() never returns (simulates a stalled/backgrounded tab)
        self._sent_count = 0

    async def send(self, data: bytes) -> None:
        self._sent_count += 1
        if self.closed_after is not None and self._sent_count >= self.closed_after:
            raise ClosedError("the tab is gone")
        if self.hangs:
            await asyncio.Event().wait()  # never completes
        self.received.append(data)

    def messages(self) -> list[dict]:
        return [msgpack.unpackb(m, raw=False) for m in self.received]

    def types(self) -> list[str]:
        return [m["type"] for m in self.messages()]


async def _serve(hub: ClientHub, client: FakeClient, *, run_until=None):
    """Runs serve_client for one fake client until `run_until` (an asyncio.Event) is set, or immediately returns
    once the initial stream + until_gone both complete on their own."""
    gone = run_until or asyncio.Event()

    async def until_gone():
        await gone.wait()

    return asyncio.create_task(hub.serve_client(client.send, until_gone, closed=(ClosedError,))), gone


@pytest.mark.asyncio
async def test_streams_the_graph_then_layout_steps_in_order():
    hub = ClientHub(_graph(), layout_iterations=5, seed=1)
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.2)
    gone.set()
    await task
    kinds = client.types()
    assert kinds[0] == "graph"
    assert kinds.count("layout_step") == 5
    assert client.messages()[0]["nodes"] and len(client.messages()[0]["nodes"]) == 5


@pytest.mark.asyncio
async def test_two_clients_each_get_their_own_independent_stream():
    hub = ClientHub(_graph(20), layout_iterations=3, seed=1)
    a, b = FakeClient(), FakeClient()
    gone_a, gone_b = asyncio.Event(), asyncio.Event()
    task_a, _ = await _serve(hub, a, run_until=gone_a)
    task_b, _ = await _serve(hub, b, run_until=gone_b)
    await asyncio.sleep(0.2)
    gone_a.set()
    gone_b.set()
    await task_a
    await task_b
    assert a.types()[0] == "graph" and b.types()[0] == "graph"
    assert a.types().count("layout_step") == 3
    assert b.types().count("layout_step") == 3
    assert len(hub._clients) == 0  # both cleaned up


@pytest.mark.asyncio
async def test_the_style_in_effect_before_connecting_is_replayed_first():
    g = _graph()
    controller = StyleController(g)
    controller.update(node_color="crimson")
    hub = ClientHub(g, layout_iterations=1, style=controller)
    hub._loop = asyncio.get_running_loop()
    controller.bind(hub.push_style)
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.2)
    gone.set()
    await task
    kinds = client.types()
    assert "style" in kinds
    assert kinds.index("style") < kinds.index("layout_step")
    style_msg = next(m for m in client.messages() if m["type"] == "style")
    assert style_msg["op"] == "replace"
    assert style_msg["spec"]["node"]["color"]["kind"] == "constant"
    assert style_msg["spec"]["node"]["color"]["color"][:3] == pytest.approx([0.863, 0.078, 0.235], abs=1e-3)  # crimson


@pytest.mark.asyncio
async def test_a_style_push_while_connected_reaches_the_client():
    g = _graph()
    controller = StyleController(g)
    hub = ClientHub(g, layout_iterations=1, style=controller)
    hub._loop = asyncio.get_running_loop()
    controller.bind(hub.push_style)
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)  # let it finish connecting (client.ready = True, writer started)
    controller.update(node_size=12)
    for _ in range(50):
        if "style" in client.types():
            break
        await asyncio.sleep(0.02)
    assert "style" in client.types()
    gone.set()
    await task


@pytest.mark.asyncio
async def test_a_style_push_reaches_every_connected_client():
    g = _graph()
    controller = StyleController(g)
    hub = ClientHub(g, layout_iterations=1, style=controller)
    hub._loop = asyncio.get_running_loop()
    controller.bind(hub.push_style)
    a, b = FakeClient(), FakeClient()
    gone_a, gone_b = asyncio.Event(), asyncio.Event()
    task_a, _ = await _serve(hub, a, run_until=gone_a)
    task_b, _ = await _serve(hub, b, run_until=gone_b)
    await asyncio.sleep(0.1)
    controller.update(node_color="blue")
    for _ in range(50):
        if "style" in a.types() and "style" in b.types():
            break
        await asyncio.sleep(0.02)
    assert "style" in a.types() and "style" in b.types()
    gone_a.set()
    gone_b.set()
    await task_a
    await task_b


@pytest.mark.asyncio
async def test_push_style_before_any_client_is_connected_is_safe():
    g = _graph()
    controller = StyleController(g)
    hub = ClientHub(g, layout_iterations=1, style=controller)
    hub._loop = asyncio.get_running_loop()
    controller.bind(hub.push_style)
    controller.update(node_color="green")  # no client exists yet
    await asyncio.sleep(0.05)
    # a client connecting afterwards still gets the current style via replay, not the (now-drained) push
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    gone.set()
    await task
    assert "style" in client.types()


@pytest.mark.asyncio
async def test_push_style_before_the_loop_is_known_is_a_no_op_not_a_crash():
    g = _graph()
    controller = StyleController(g)
    hub = ClientHub(g, layout_iterations=1, style=controller)
    controller.bind(hub.push_style)  # hub._loop was never set (no serve_client / start() called)
    controller.update(node_color="red")  # must not raise


@pytest.mark.asyncio
async def test_a_burst_of_pushes_past_the_incoming_limit_collapses_to_one_resync():
    from plexgraph_bridge import hub as hub_module

    g = _graph()
    controller = StyleController(g)
    hub = ClientHub(g, layout_iterations=1, style=controller)
    hub._loop = asyncio.get_running_loop()
    controller.bind(hub.push_style)
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    client.received.clear()  # only care about what arrives after this point
    for i in range(hub_module._MAX_INCOMING + 20):
        controller.update(node_size=8 + i % 5)
    await asyncio.sleep(0.2)
    style_msgs = [m for m in client.messages() if m["type"] == "style"]
    assert style_msgs and style_msgs[-1]["op"] == "replace"  # a resync snapshot, not hundreds of individual sets
    gone.set()
    await task


@pytest.mark.asyncio
async def test_a_client_whose_outbox_fills_up_gets_a_fresh_snapshot_instead_of_growing_forever():
    from plexgraph_bridge import hub as hub_module

    g = _graph()
    controller = StyleController(g)
    hub = ClientHub(g, layout_iterations=1, style=controller)
    hub._loop = asyncio.get_running_loop()
    controller.bind(hub.push_style)
    client = FakeClient()
    client.hangs = False
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    # Stall this one client's writer by making its send() hang, so its outbox queues up rather than draining.
    client.hangs = True
    for i in range(hub_module._MAX_OUTBOX + 30):
        controller.update(node_size=5 + i % 3)
    await asyncio.sleep(0.1)
    assert len(next(iter(hub._clients)).outbox) <= hub_module._MAX_OUTBOX
    client.hangs = False
    gone.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_a_send_that_raises_a_closed_exception_ends_serve_client_cleanly():
    hub = ClientHub(_graph(), layout_iterations=200, seed=1)
    client = FakeClient()
    client.closed_after = 1  # fail on the very first send (the graph message)
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.wait_for(task, timeout=5)  # returns on its own -- ClosedError is caught, not propagated
    assert task.exception() is None
    assert len(hub._clients) == 0


@pytest.mark.asyncio
async def test_a_send_that_raises_something_else_is_not_swallowed():
    hub = ClientHub(_graph(), layout_iterations=200, seed=1)

    class Client(FakeClient):
        async def send(self, data: bytes) -> None:
            raise RuntimeError("not a disconnection")

    client = Client()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    with pytest.raises(RuntimeError, match="not a disconnection"):
        await asyncio.wait_for(task, timeout=5)
    assert len(hub._clients) == 0  # still cleaned up, even though the error propagated


@pytest.mark.asyncio
async def test_until_gone_completing_normally_cleans_up_too():
    hub = ClientHub(_graph(), layout_iterations=1)
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    assert len(hub._clients) == 1
    gone.set()  # until_gone() returns "normally" -- the ordinary way a tab leaves
    await asyncio.wait_for(task, timeout=5)
    assert len(hub._clients) == 0


@pytest.mark.asyncio
async def test_the_writer_task_is_cancelled_when_a_client_leaves():
    hub = ClientHub(_graph(), layout_iterations=1)
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    writer = next(iter(hub._clients)).writer
    assert writer is not None and not writer.done()
    gone.set()
    await asyncio.wait_for(task, timeout=5)
    await asyncio.sleep(0)  # let the cancellation actually land
    assert writer.cancelled() or writer.done()


# -- request_export / resolve_export -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_export_sends_a_request_and_returns_the_viewers_reply():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)  # let it finish connecting

    async def act_as_the_viewer():
        # Wait for the request to actually arrive, then answer it -- exactly what a real viewer's export_response
        # does, decoded and handed to resolve_export the same way both transports do.
        for _ in range(50):
            if "export_request" in client.types():
                break
            await asyncio.sleep(0.02)
        request = next(m for m in client.messages() if m["type"] == "export_request")
        assert request["format"] == "svg"
        hub.resolve_export(request["id"], b"<svg>ok</svg>", None)

    viewer = asyncio.create_task(act_as_the_viewer())
    data = await hub.request_export("svg", timeout=2)
    await viewer
    assert data == b"<svg>ok</svg>"
    gone.set()
    await task


@pytest.mark.asyncio
async def test_request_export_matches_the_reply_to_its_own_request_id_among_several():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)

    async def answer_in_reverse_order():
        for _ in range(50):
            if len([m for m in client.types() if m == "export_request"]) >= 2:
                break
            await asyncio.sleep(0.02)
        requests = [m for m in client.messages() if m["type"] == "export_request"]
        # Resolve the second request first -- each caller must still get back only its own data.
        hub.resolve_export(requests[1]["id"], b"second", None)
        hub.resolve_export(requests[0]["id"], b"first", None)

    viewer = asyncio.create_task(answer_in_reverse_order())
    first, second = await asyncio.gather(hub.request_export("svg", timeout=2), hub.request_export("png", timeout=2))
    await viewer
    assert first == b"first" and second == b"second"
    gone.set()
    await task


@pytest.mark.asyncio
async def test_request_export_raises_when_no_viewer_is_connected():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    with pytest.raises(RuntimeError, match="no viewer is connected"):
        await hub.request_export("svg", timeout=1)


@pytest.mark.asyncio
async def test_request_export_times_out_when_nothing_answers():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    with pytest.raises(TimeoutError, match="did not respond"):
        await hub.request_export("svg", timeout=0.1)
    gone.set()
    await task


@pytest.mark.asyncio
async def test_request_export_raises_the_viewers_own_error():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)

    async def report_a_failure():
        for _ in range(50):
            if "export_request" in client.types():
                break
            await asyncio.sleep(0.02)
        request = next(m for m in client.messages() if m["type"] == "export_request")
        hub.resolve_export(request["id"], None, "canvas.toBlob returned null")

    viewer = asyncio.create_task(report_a_failure())
    with pytest.raises(RuntimeError, match="canvas.toBlob returned null"):
        await hub.request_export("png", timeout=2)
    await viewer
    gone.set()
    await task


@pytest.mark.asyncio
async def test_request_export_raises_clearly_when_the_viewer_disconnects_mid_request():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    client = FakeClient()
    gone = asyncio.Event()
    task, _ = await _serve(hub, client, run_until=gone)
    await asyncio.sleep(0.1)
    client.closed_after = 1  # the export request itself is the next send
    with pytest.raises(RuntimeError, match="disconnected"):
        await hub.request_export("svg", timeout=1)
    gone.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_resolve_export_for_an_unknown_or_already_resolved_id_is_a_no_op():
    hub = ClientHub(_graph(), layout_iterations=1)
    hub._loop = asyncio.get_running_loop()
    hub.resolve_export("no-such-request", b"data", None)  # must not raise
    await asyncio.sleep(0.05)


def test_resolve_export_is_safe_to_call_from_a_background_thread():
    async def scenario():
        hub = ClientHub(_graph(), layout_iterations=1)
        hub._loop = asyncio.get_running_loop()
        client = FakeClient()
        gone = asyncio.Event()
        task, _ = await _serve(hub, client, run_until=gone)
        await asyncio.sleep(0.1)

        async def request_it():
            return await hub.request_export("svg", timeout=2)

        request_task = asyncio.create_task(request_it())
        for _ in range(50):
            if "export_request" in client.types():
                break
            await asyncio.sleep(0.02)
        request = next(m for m in client.messages() if m["type"] == "export_request")

        def from_thread():
            hub.resolve_export(request["id"], b"<svg/>", None)

        t = threading.Thread(target=from_thread)
        t.start()
        t.join()
        assert await request_task == b"<svg/>"
        gone.set()
        await task

    asyncio.run(scenario())


def test_push_style_is_safe_to_call_from_a_background_thread():
    async def scenario():
        g = _graph()
        controller = StyleController(g)
        hub = ClientHub(g, layout_iterations=1, style=controller)
        hub._loop = asyncio.get_running_loop()
        controller.bind(hub.push_style)
        client = FakeClient()
        gone = asyncio.Event()
        task, _ = await _serve(hub, client, run_until=gone)
        await asyncio.sleep(0.1)

        def from_thread():
            controller.update(node_color="orange")

        t = threading.Thread(target=from_thread)
        t.start()
        t.join()
        # A synchronous time.sleep() here would block this coroutine's own event loop, which is exactly the loop
        # push_style's call_soon_threadsafe needs to run on to process the update -- await asyncio.sleep() instead.
        for _ in range(50):
            if "style" in client.types():
                break
            await asyncio.sleep(0.02)
        assert "style" in client.types()
        gone.set()
        await task

    asyncio.run(scenario())
