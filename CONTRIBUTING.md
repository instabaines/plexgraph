# Contributing to plexgraph

This covers building the monorepo from source, running the example scripts, and running the test
suites. If you just want to *use* plexgraph, see [the README](README.md#install) instead —
`pip install plexgraph` is all that needs.

## Development setup

One-time setup: build the frontend, then create one Python environment for the whole repo (both
packages, the optional pandas/networkx loaders, tests, notebooks and benchmarks).

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

**What gets installed.** `plexgraph-core` needs `numpy` and `msgpack`; `plexgraph-bridge` adds
`websockets`, which the browser-tab viewer uses. The notebook widget needs `anywidget` (its `jupyter` extra) and no
`websockets`. `pandas` and `networkx`
are optional, needed only by the loaders that take those types: `pip install "plexgraph-core[pandas]"`,
`"plexgraph-core[networkx]"`, or `"plexgraph-core[all]"`. The notebooks use the extras above plus
`scipy`, `python-igraph` and `matplotlib` (see `benchmarks/requirements.txt`); `requirements-dev.txt` and
`uv sync` include all of it.

## Running the examples

From the repo root, run any example with that environment's Python (`.venv/bin/python`, or
`.venv\Scripts\python.exe` on Windows; `uv run python ...` also works):

```sh
.venv/bin/python examples/python/hello_graph.py       # 60-node demo
.venv/bin/python examples/python/large_graph_100k.py  # 100K-node scale check
```

Each opens a browser tab rendering the graph, with the layout animating as it converges. A worked
example of every feature (directed/layered/temporal/hypergraph graphs, styling, export, reading
from networkx/pandas/GEXF/GraphML) is in [`examples/notebooks/tour.ipynb`](examples/notebooks/tour.ipynb);
see [the user guide](docs/user-guide.md) for the written version.

## Building wheels to test elsewhere

Build the frontend first, because the bridge wheel bundles it, so a plain `pip install` can open
the viewer (the build fails if the frontend is missing):

```sh
pnpm --filter @plexgraph/app build
pip wheel --no-deps packages/core packages/bridge -w dist-wheels
pip install dist-wheels/*.whl          # brings numpy, msgpack and websockets with it
```

For an actual release build (the sdist/wheel that ships to PyPI) use `scripts/build_release.py` and
`scripts/check_release.py` instead — see [docs/releasing.md](docs/releasing.md).

## Running the test suites

```sh
.venv/bin/python -m pytest packages           # core and bridge
pnpm --filter @plexgraph/viz-core test
```

## Project layout

- `packages/core` — Python graph model (IR), algorithms, import/export
- `packages/bridge` — local WebSocket server bridging Python and the browser frontend
- The notebook widget (anywidget) lives in `packages/bridge` (`widget.py`, `widget.js`); it hosts the same viewer
  app in the notebook and feeds it over the notebook's own connection, so it needs no port.
- `packages/viz-core` — TypeScript rendering engine (WebGL/regl-based)
- `packages/app` — standalone browser app shell
- `docs/user-guide.md` — how to build, style, view, export, and import graphs
- `docs/architecture` — IR spec, wire protocol spec
- `docs/adr` — architecture decision records
- `mkdocs.yml`, `docs/api/` — the [hosted documentation site](https://plexgraph.readthedocs.io/)
  (user guide + generated API reference); `docs/releasing.md` has the "Documentation" section for
  previewing it locally
- `examples` — example scripts and notebooks

See [`docs/architecture/plan.md`](docs/architecture/plan.md) for the full v1 architecture plan, and
[docs/releasing.md](docs/releasing.md) for how versions are cut and published.
