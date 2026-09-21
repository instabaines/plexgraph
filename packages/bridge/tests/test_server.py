import asyncio
import http.client

import msgpack
import pytest
import websockets

from plexgraph_bridge.server import BridgeServer
from plexgraph_core.model.ir import Graph


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


@pytest.fixture
def viewer_dir(tmp_path):
    root = tmp_path / "viewer"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<title>viewer</title>")
    (root / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("private")
    return root


async def _get(port: int, path: str):
    def fetch():
        connection = http.client.HTTPConnection("localhost", port, timeout=10)
        connection.request("GET", path)  # http.client sends the path as written, so ".." reaches the server
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()
    return await asyncio.get_running_loop().run_in_executor(None, fetch)


@pytest.mark.asyncio
async def test_one_port_serves_the_viewer_and_the_websocket(viewer_dir):
    server = BridgeServer(_small_graph(), host="localhost", port=0, layout_iterations=2, seed=1, static_dir=viewer_dir)
    port = await server.start()
    try:
        assert await _get(port, "/") == (200, "text/html", b"<title>viewer</title>")
        status, kind, body = await _get(port, "/assets/app.js?cache=1")
        assert (status, body) == (200, b"console.log(1)") and kind == "text/javascript"
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            assert msgpack.unpackb(await ws.recv(), raw=False)["type"] == "graph"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/nothing.js", "/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"])
async def test_static_serving_stays_inside_the_viewer_directory(viewer_dir, path):
    server = BridgeServer(_small_graph(), host="localhost", port=0, layout_iterations=2, static_dir=viewer_dir)
    port = await server.start()
    try:
        status, _, body = await _get(port, path)
        assert status == 404 and b"private" not in body
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_without_a_static_dir_plain_http_is_not_served():
    server = BridgeServer(_small_graph(), host="localhost", port=0, layout_iterations=2)
    port = await server.start()
    try:
        status, _, _ = await _get(port, "/")
        assert status != 200
    finally:
        server.close()
        await server.wait_closed()
