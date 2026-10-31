"""Entry point for plexgraph's desktop-app sidecar: a bare WebSocket server, with no HTTP/static
serving (the Tauri shell's own webview loads the frontend; this process only needs to talk
WebSocket) and a fixed, pre-agreed port rather than an OS-assigned one -- reasonable for a
single-user local desktop app, where there is no "someone else already has this port" concern in
practice.

Phase A (the current state): starts with a small hardcoded demo graph, same as `server.py`'s own
`if __name__ == "__main__":` block. There is no file-loading yet -- that is Phase B, which adds a
`"load_file"` wire message so a native Open-File dialog can ask a *running* sidecar to load a real
graph, without needing to restart this process.

Registered as the `plexgraph-sidecar` console script (see packages/bridge/pyproject.toml), so it
has a name Phase C's PyInstaller build can target directly, and so it can be run manually for
testing without `python -m`:

    plexgraph-sidecar --port 47300
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from plexgraph_bridge.server import demo_graph, run_server

DEFAULT_PORT = 47300


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost", help="interface to bind the WebSocket server to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="fixed port to listen on")
    parser.add_argument("--token", default=None, help="shared secret a viewer must present to connect")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_server(demo_graph(), host=args.host, port=args.port, token=args.token))


if __name__ == "__main__":
    main()
