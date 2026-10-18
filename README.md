# plexgraph

A graph/network visualization library built for complex network types — multiplex/multilayer
networks, hypergraphs, and temporal/dynamic networks — as first-class citizens, plus
GPU-accelerated rendering, with large-graph scalability still under evaluation.

Existing tools (networkx/igraph + matplotlib, Gephi) treat these network types as afterthoughts
bolted onto a plain-graph model. `plexgraph` instead models every graph as a single unified
representation (see [`docs/architecture`](docs/architecture)) where "plain graph," "multiplex,"
"hypergraph," and "temporal" are variations of one structure, not separate systems.

**→ [Full documentation](https://plexgraph.readthedocs.io/)** (user guide and API reference), also
readable in-repo at [docs/user-guide.md](docs/user-guide.md), for how to build graphs (directed,
layered, temporal, hypergraphs), style them, use the viewer, export, and read data from other
sources (networkx, pandas, GEXF/GraphML). This README only covers one-time setup and the shortest
path to a graph on screen.

## Status

Early development, published on PyPI. The layout and sliced renderer are not yet validated for production use at
100K nodes: see the [evaluation lab](examples/notebooks/evaluation.ipynb) and the
[measured limitations](benchmarks/README.md). Releases are described in [docs/releasing.md](docs/releasing.md).

## Install

```sh
pip install plexgraph
pip install "plexgraph[jupyter]"            # optional: the notebook widget (anywidget); see "Where it runs" in the user guide
pip install pandas networkx                   # optional: only for the pandas/networkx loaders
```

```python
import plexgraph as pg

g = pg.Graph()
g.add_node("alice")
g.add_node("bob")
g.add_edge("alice", "bob", weight=2.0)
pg.show(g, node_color=pg.by_degree("plasma"))
```

`python -m plexgraph demo` opens a demo graph.

For everything else — directed/layered/temporal/hypergraph graphs, styling (colors, sizes, edge
width), the stacked layer/time view, exporting, reading from networkx/pandas/GEXF/GraphML, and
Jupyter specifics — see **[the user guide](docs/user-guide.md)**. A worked example of every
feature is also in [`examples/notebooks/tour.ipynb`](examples/notebooks/tour.ipynb).

Building plexgraph from source, running its example scripts and test suites, and the monorepo's
project layout are in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
