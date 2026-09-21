"""`python -m plexgraph`: version, installation info, and a demo viewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _info() -> int:
    import plexgraph
    import plexgraph_bridge.launcher as launcher

    static = launcher._static_app_dir()
    viewer = (static / "index.html").is_file()
    print(f"plexgraph {plexgraph.__version__}")
    print(f"python    {sys.version.split()[0]}")
    print(f"location  {Path(plexgraph.__file__).parent}")
    print(f"viewer    {'bundled' if viewer else 'MISSING'} ({static})")
    optional = {"pandas": "", "networkx": "", "IPython": "",
                "anywidget": ' (the notebook widget; without it notebooks use a local server: pip install "plexgraph[jupyter]")'}
    for name in ("numpy", "msgpack", "websockets", *optional):
        try:
            module = __import__(name)
            print(f"{name:<9} {getattr(module, '__version__', 'installed')}")
        except ImportError:
            print(f"{name:<9} not installed" + (optional[name] if name in optional else "  <-- required"))
    return 0 if viewer else 1


def _demo(open_browser: bool) -> int:
    import plexgraph as pg

    g = pg.Graph()
    for i in range(60):
        g.add_node(i, group=f"g{i % 3}")
    for i in range(60):
        g.add_edge(i, (i + 1) % 60)
        g.add_edge(i, (i * 7 + 3) % 60)
    print("Opening a demo graph; press Ctrl+C to stop.")
    pg.show(g, seed=1, layout_iterations=80, node_color=pg.by_attribute("group", palette="tab10"),
            node_size=pg.size_by_degree((8, 18)), edge_curvature=0.1, open_browser=open_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plexgraph", description="plexgraph graph visualization")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("info", help="show the version, where it is installed, and whether the viewer is bundled")
    demo = commands.add_parser("demo", help="open a small demo graph in the viewer")
    demo.add_argument("--no-browser", action="store_true", help="serve the viewer without opening a browser tab")
    args = parser.parse_args(argv)
    if args.version:
        from plexgraph import __version__

        print(__version__)
        return 0
    if args.command == "info":
        return _info()
    if args.command == "demo":
        return _demo(open_browser=not args.no_browser)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
