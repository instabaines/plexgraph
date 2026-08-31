# hyperloom

A graph/network visualization library built for complex network types — multiplex/multilayer
networks, hypergraphs, and temporal/dynamic networks — as first-class citizens, plus
GPU-accelerated rendering for large (100K+ node) graphs.

Existing tools (networkx/igraph + matplotlib, Gephi) treat these network types as afterthoughts
bolted onto a plain-graph model. `hyperloom` instead models every graph as a single unified
representation (see [`docs/architecture`](docs/architecture)) where "plain graph," "multiplex,"
"hypergraph," and "temporal" are variations of one structure, not separate systems.

**→ [Read the full user guide](docs/user-guide.md)** for how to build graphs (directed, layered,
temporal, hypergraphs), style them, use the viewer, export, and read data from other sources
(networkx, pandas, GEXF/GraphML). This README only covers one-time setup and the shortest path to
a graph on screen.

## Status

Early development (Phase A: core spine — IR, Python API, WebGL renderer, streaming layout).
Not yet published.

## Quick start

One-time setup — build the frontend and set up the two Python packages (each has its own venv
for now; a real release will ship a single installable `hyperloom` package):

```sh
# 1. Frontend: install JS deps and build the static app the Python side serves.
pnpm install
pnpm --filter @hyperloom/app build

# 2. Python core: the graph model, algorithms, wire protocol.
cd packages/core
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # .venv/bin/pip on macOS/Linux
cd ../..

# 3. Python bridge: the WebSocket server + browser launcher. Depends on core,
#    installed from the local path since this isn't published yet.
cd packages/bridge
python -m venv .venv
.venv/Scripts/pip install -e ../core -e ".[dev]"   # .venv/bin/pip on macOS/Linux
cd ../..
```

Then, from the repo root, run any example with the bridge's venv:

```sh
packages/bridge/.venv/Scripts/python.exe examples/python/hello_graph.py       # 60-node demo
packages/bridge/.venv/Scripts/python.exe examples/python/large_graph_100k.py  # 100K-node scale check
```

Each opens a browser tab rendering the graph, with the layout animating as it converges.

To visualize your own graph:

```python
from hyperloom_bridge import show
from hyperloom_core import Graph

g = Graph()
a = g.add_node("alice")
b = g.add_node("bob")
g.add_edge(a, b, weight=1.0)

show(g)  # opens a browser tab (or renders inline if you're in Jupyter)
```

For everything else — directed/layered/temporal/hypergraph graphs, styling (colors, sizes, edge
width), the stacked layer/time view, exporting, reading from networkx/pandas/GEXF/GraphML, and
Jupyter specifics — see **[the user guide](docs/user-guide.md)**. A worked example of every
feature is also in [`examples/notebooks/tour.ipynb`](examples/notebooks/tour.ipynb).

Run the test suites with:

```sh
packages/core/.venv/Scripts/python.exe -m pytest packages/core/tests
packages/bridge/.venv/Scripts/python.exe -m pytest packages/bridge/tests
pnpm --filter @hyperloom/viz-core test
```

## Project layout

- `packages/core` — Python graph model (IR), algorithms, import/export
- `packages/bridge` — local WebSocket server bridging Python and the browser frontend
- `packages/widget` — reserved for a future "real" Jupyter (anywidget) integration; not built yet.
  The current Jupyter support (inline iframe display) lives in `packages/bridge` — see Quick start.
- `packages/viz-core` — TypeScript rendering engine (WebGL/regl-based)
- `packages/app` — standalone browser app shell
- `docs/user-guide.md` — how to build, style, view, export, and import graphs
- `docs/architecture` — IR spec, wire protocol spec
- `docs/adr` — architecture decision records
- `examples` — example scripts and notebooks

See [`docs/architecture/plan.md`](docs/architecture/plan.md) for the full v1 architecture plan.

## License

MIT — see [LICENSE](LICENSE).
