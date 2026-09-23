# plexgraph

A graph/network visualization library built for complex network types — multiplex/multilayer
networks, hypergraphs, and temporal/dynamic networks — as first-class citizens, plus
GPU-accelerated rendering.

Existing tools (networkx/igraph + matplotlib, Gephi) treat these network types as afterthoughts
bolted onto a plain-graph model. plexgraph instead models every graph as a single unified
representation (see [Architecture](architecture/plan.md)) where "plain graph," "multiplex,"
"hypergraph," and "temporal" are variations of one structure, not separate systems.

## Install

```sh
pip install plexgraph
```

That installs everything, viewer included, with no Node.js needed. Two extras add optional
loaders and the notebook widget:

```sh
pip install "plexgraph[jupyter]"     # the notebook widget (anywidget) -- see "Jupyter notes" in the user guide
pip install pandas networkx          # only if you use the pandas/networkx loaders
```

## A first graph

```python
import plexgraph as pg

g = pg.Graph()
g.add_node("alice")
g.add_node("bob")
g.add_edge("alice", "bob", weight=2.0)
pg.show(g, node_color=pg.by_degree("plasma"))
```

`show()` opens a browser tab, or shows an inline widget if you're in a notebook.
`python -m plexgraph demo` opens a demo graph without writing any code.

## Where to go next

- **[User guide](user-guide.md)** -- building graphs (directed, layered, temporal, hypergraphs),
  styling, the interactive viewer, exporting, and reading data from other sources (networkx,
  pandas, GEXF/GraphML). Start here.
- **[API reference](api/index.md)** -- the graph model, every loader/writer, and every styling
  function, generated from the library's own docstrings.
- **[Architecture](architecture/plan.md)** -- the unified graph representation plexgraph is built
  on, and why.
- **[Changelog](changelog.md)** -- what changed in each release.

## Status

The layout and sliced renderer are still under evaluation for production use at very large graphs
(100K+ nodes) -- see the [measured limitations](https://github.com/instabaines/plexgraph/blob/master/benchmarks/README.md)
and the [evaluation lab](https://github.com/instabaines/plexgraph/blob/master/examples/notebooks/evaluation.ipynb)
in the repository.

## Source and license

plexgraph is developed on [GitHub](https://github.com/instabaines/plexgraph), under the
[MIT license](https://github.com/instabaines/plexgraph/blob/master/LICENSE). Contributions and
issues are welcome there; see [Releasing](releasing.md) for how new versions ship.
