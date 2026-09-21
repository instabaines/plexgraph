import asyncio
import threading

import msgpack
import numpy as np
import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect  # the new client, which every supported version has

from plexgraph_bridge import RESET, by_degree, show
from plexgraph_bridge.style import merge_style
from plexgraph_core import Graph


def graph() -> Graph:
    g = Graph()
    for i in range(6):
        g.add_node(f"n{i}", team="a" if i % 2 else "b")
    for i in range(5):
        g.add_edge(f"n{i}", f"n{i + 1}")
    g.add_edge("n0", "n3")  # cycles, so the force layout runs (a plain path is laid out in one analytic step)
    g.add_edge("n1", "n4")
    return g


def start(**kwargs):
    return show(graph(), open_browser=False, block=False, return_handle=True, layout_iterations=4, seed=0, **kwargs)


async def connect(handle):
    return await ws_connect(handle.ws_url, max_size=None)


async def collect(ws, until_layout_steps=0, timeout=5.0):
    """Messages up to (and including) the requested number of layout steps."""
    out, steps = [], 0
    while True:
        message = msgpack.unpackb(await asyncio.wait_for(ws.recv(), timeout), raw=False)
        out.append(message)
        steps += message["type"] == "layout_step"
        if steps >= until_layout_steps and (until_layout_steps or message["type"] == "graph"):
            return out


async def styles_for(ws, seconds=0.6):
    """Every style message that arrives within `seconds` (layout steps are skipped)."""
    out = []
    end = asyncio.get_running_loop().time() + seconds
    while (left := end - asyncio.get_running_loop().time()) > 0:
        try:
            message = msgpack.unpackb(await asyncio.wait_for(ws.recv(), left), raw=False)
        except asyncio.TimeoutError:
            break
        if message["type"] == "style":
            out.append(message)
    return out


@pytest.mark.asyncio
async def test_the_style_from_show_arrives_after_the_graph_and_before_the_layout_moves():
    handle = start(node_color=by_degree("plasma"), node_size=12, edge_curvature=0.2)
    try:
        async with await connect(handle) as ws:
            messages = await collect(ws, until_layout_steps=1)
        types = [m["type"] for m in messages]
        assert types[0] == "graph"
        assert types[1] == "style" and types.index("layout_step") > 1
        assert messages[1]["op"] == "replace"
        assert messages[1]["spec"] == {
            "node": {"color": {"kind": "degree", "colormap": "plasma"}, "size": 12.0},
            "edge": {"curvature": 0.2},
        }
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_no_style_message_is_sent_when_nothing_was_styled():
    handle = start()
    try:
        async with await connect(handle) as ws:
            messages = await collect(ws, until_layout_steps=2)
        assert [m["type"] for m in messages if m["type"] != "layout_step"] == ["graph"]
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_a_live_change_reaches_every_open_tab_and_late_tabs_get_the_current_style():
    handle = start(node_size=10)
    try:
        async with await connect(handle) as first, await connect(handle) as second:
            await collect(first, until_layout_steps=4)
            await collect(second, until_layout_steps=4)
            handle.style(node_color=["red", "blue", "green", "gold", "black", "white"], edge_width=3)
            handle.color_nodes(["n1"], "crimson")
            got_first, got_second = await styles_for(first), await styles_for(second)
            for got in (got_first, got_second):
                assert [m["op"] for m in got] == ["set", "paint"]
                assert got[0]["spec"]["edge"] == {"width": 3.0}
                assert list(np.frombuffer(got[1]["ids"], dtype=np.uint32)) == [1]
            handle.style(node_size=20)
            handle.style(node_size=RESET)
            assert [m["spec"] for m in await styles_for(first)] == [{"node": {"size": 20.0}}, {"node": {"size": None}}]

            # a tab that opens now is brought fully up to date before its layout starts
            async with await connect(handle) as late:
                replay = [m for m in await collect(late, until_layout_steps=1) if m["type"] == "style"]
            assert replay[0]["op"] == "replace"
            assert replay[0]["spec"]["edge"] == {"width": 3.0}
            assert replay[0]["spec"]["node"]["color"]["kind"] == "colors" and "size" not in replay[0]["spec"]["node"]
            assert replay[1]["op"] == "paint" and replay[1]["color"][:3] == pytest.approx([220 / 255, 20 / 255, 60 / 255])
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_reset_clears_what_late_tabs_see():
    handle = start(node_size=10)
    try:
        handle.color_nodes(["n0"], "red")
        handle.reset_style()
        assert handle.get_style() == {}
        async with await connect(handle) as ws:
            messages = await collect(ws, until_layout_steps=1)
        assert [m["type"] for m in messages if m["type"] == "style"] == []
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_invalid_updates_raise_in_python_and_send_nothing():
    handle = start()
    try:
        async with await connect(handle) as ws:
            await collect(ws, until_layout_steps=4)
            with pytest.raises(ValueError, match="unknown colormap"):
                handle.style(node_color=[1, 2, 3, 4, 5, 6], cmap="nope")
            with pytest.raises(TypeError, match="unknown style option"):
                handle.style(node_colour="red")
            with pytest.raises(KeyError):
                handle.color_nodes(["missing"], "red")
            assert await styles_for(ws, 0.4) == []
            assert handle.get_style() == {}
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_updates_arrive_in_the_order_they_were_made():
    handle = start()
    try:
        async with await connect(handle) as ws:
            await collect(ws, until_layout_steps=4)
            for i in range(1, 31):
                handle.style(node_size=float(i))
            got = await styles_for(ws, 1.0)
            assert [m["spec"]["node"]["size"] for m in got] == [float(i) for i in range(1, 31)]
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_a_tab_that_connects_while_the_style_is_changing_never_misses_an_update():
    """Each tab must end up with the same style as Python, however its connection interleaves with the changes."""
    handle = start()
    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set():
            i += 1
            handle.style(node_size=float(i % 50 + 1), edge_width=float(i % 7 + 1))

    worker = threading.Thread(target=churn, daemon=True)
    try:
        worker.start()
        clients = []
        for _ in range(8):
            clients.append(await connect(handle))
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        stop.set()
        worker.join()
        final = handle.get_style()
        for ws in clients:
            state: dict = {}
            while True:
                try:
                    m = msgpack.unpackb(await asyncio.wait_for(ws.recv(), 0.4), raw=False)
                except asyncio.TimeoutError:
                    break  # nothing more is coming
                if m["type"] == "style":
                    state = m["spec"] if m["op"] == "replace" else merge_style(state, m["spec"])
            assert state == final
        for ws in clients:
            await ws.close()
    finally:
        stop.set()
        handle.close()


@pytest.mark.asyncio
async def test_style_calls_never_wait_on_a_tab_that_stopped_reading():
    """A backgrounded browser tab must not freeze the notebook: calls return at once, and when the tab wakes up it
    still ends up with the current style (the backlog is replaced by one fresh snapshot, not queued forever)."""
    import time

    handle = start()
    try:
        async with await connect(handle) as ws:
            await collect(ws, until_layout_steps=4)
            started = time.perf_counter()
            for i in range(1, 2001):
                handle.style(node_size=float(i % 40 + 1), node_color=np.arange(6, dtype=float) * i)
            assert time.perf_counter() - started < 5, "style() should return immediately, not wait for the tab"
            # now the tab wakes up and reads whatever was kept for it
            state: dict = {}
            while True:
                try:
                    m = msgpack.unpackb(await asyncio.wait_for(ws.recv(), 0.5), raw=False)
                except asyncio.TimeoutError:
                    break
                if m["type"] == "style":
                    state = m["spec"] if m["op"] == "replace" else merge_style(state, m["spec"])
            final = handle.get_style()
            assert state["node"]["size"] == final["node"]["size"]
            sent = np.frombuffer(state["node"]["color"]["values"]["$data"], dtype=np.float64)
            assert list(sent) == list(final["node"]["color"]["values"])
    finally:
        handle.close()
