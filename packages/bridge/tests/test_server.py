import msgpack
import pytest
import websockets

from hyperloom_bridge.server import BridgeServer
from hyperloom_core.model.ir import Graph


def _small_graph() -> Graph:
    g = Graph()
    for i in range(5):
        g.add_node(i)
    for i in range(4):
        g.add_edge(i, i + 1)
    return g


@pytest.mark.asyncio
async def test_client_receives_graph_then_layout_steps():
    server = BridgeServer(_small_graph(), host="localhost", port=0, layout_iterations=5, seed=1)
    port = await server.start()
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            first = msgpack.unpackb(await ws.recv(), raw=False)
            assert first["type"] == "graph"
            assert len(first["nodes"]) == 5
            assert len(first["connectors"]) == 4

            second = msgpack.unpackb(await ws.recv(), raw=False)
            assert second["type"] == "layout_step"
            assert second["num_nodes"] == 5
            assert second["iteration"] == 1
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_layout_streams_until_converged_or_max_iterations():
    server = BridgeServer(_small_graph(), host="localhost", port=0, layout_iterations=5, seed=1)
    port = await server.start()
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await ws.recv()  # initial graph snapshot
            steps = []
            while True:
                msg = msgpack.unpackb(await ws.recv(), raw=False)
                steps.append(msg)
                if msg["converged"] or msg["iteration"] >= 5:
                    break
            assert len(steps) <= 5
    finally:
        server.close()
        await server.wait_closed()
