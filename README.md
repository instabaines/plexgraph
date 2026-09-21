# plexgraph

A graph/network visualization library built for complex network types — multiplex/multilayer
networks, hypergraphs, and temporal/dynamic networks — as first-class citizens, plus
GPU-accelerated rendering, with large-graph scalability still under evaluation.

Existing tools (networkx/igraph + matplotlib, Gephi) treat these network types as afterthoughts
bolted onto a plain-graph model. `plexgraph` instead models every graph as a single unified
representation (see [`docs/architecture`](docs/architecture)) where "plain graph," "multiplex,"
"hypergraph," and "temporal" are variations of one structure, not separate systems.

**→ [Read the full user guide](docs/user-guide.md)** for how to build graphs (directed, layered,
temporal, hypergraphs), style them, use the viewer, export, and read data from other sources
(networkx, pandas, GEXF/GraphML). This README only covers one-time setup and the shortest path to
a graph on screen.

## Status

Early development. The package is ready to release but is **not yet published to PyPI**. Once it is, `pip install plexgraph`
installs everything, viewer included, with no Node.js needed. Separately, the layout and sliced renderer are not yet
validated for production use at 100K nodes: see the [evaluation lab](examples/notebooks/evaluation.ipynb) and the
[measured limitations](benchmarks/README.md). Releases are described in [docs/releasing.md](docs/releasing.md).

## Install

Until the first release is on PyPI, build the package and install the wheel. This produces exactly what users will get:

```sh
pip install build
python scripts/build_release.py               # needs Node.js and pnpm once, to build the viewer
pip install dist/plexgraph-0.1.0-py3-none-any.whl
pip install pandas networkx ipython           # optional: only for the pandas/networkx loaders and inline Jupyter display
```

```python
import plexgraph as pg

g = pg.Graph()
g.add_edge("alice", "bob", weight=2.0)
pg.show(g, node_color=pg.by_degree("plasma"))
```

`python -m plexgraph demo` opens a demo graph. The rest of this page is for working *on* plexgraph.

## Quick start

One-time setup: build the frontend, then create one Python environment for the whole repo (both
packages, the optional pandas/networkx loaders, tests, notebooks and benchmarks). A real release will ship
a single installable `plexgraph` package.

```sh
# 1. Frontend: install JS deps and build the static app the Python side serves.
pnpm install
pnpm --filter @plexgraph/app build

# 2. Python: one environment. Either uv ...
uv sync                                       # creates .venv with everything
# ... or plain pip:
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # .venv\Scripts\pip on Windows
```

Then, from the repo root, run any example with that environment's Python (`.venv/bin/python`, or
`.venv\Scripts\python.exe` on Windows; `uv run python ...` also works):

```sh
.venv/bin/python examples/python/hello_graph.py       # 60-node demo
.venv/bin/python examples/python/large_graph_100k.py  # 100K-node scale check
```

Each opens a browser tab rendering the graph, with the layout animating as it converges.

**What gets installed.** `plexgraph-core` needs `numpy` and `msgpack`; `plexgraph-bridge` adds
`websockets` (and `ipython` for inline Jupyter display, via its `jupyter` extra). `pandas` and `networkx`
are optional, needed only by the loaders that take those types: `pip install "plexgraph-core[pandas]"`,
`"plexgraph-core[networkx]"`, or `"plexgraph-core[all]"`. The notebooks use the extras above plus
`scipy`, `python-igraph` and `matplotlib` (see `benchmarks/requirements.txt`); `requirements-dev.txt` and
`uv sync` include all of it.

**Installing the packages elsewhere.** Build the frontend first, because the bridge wheel bundles it, so a
plain `pip install` can open the viewer (the build fails if the frontend is missing):

```sh
pnpm --filter @plexgraph/app build
pip wheel --no-deps packages/core packages/bridge -w dist-wheels
pip install dist-wheels/*.whl          # brings numpy, msgpack and websockets with it
```

To visualize your own graph:

```python
from plexgraph_bridge import show
from plexgraph_core import Graph

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
.venv/bin/python -m pytest packages           # core and bridge
pnpm --filter @plexgraph/viz-core test
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
