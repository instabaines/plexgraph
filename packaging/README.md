# plexgraph

Interactive graph visualization for **multilayer**, **temporal** and **hypergraph** networks, from Python. Build a graph,
call `show()`, and explore it in a WebGL viewer, inline in Jupyter or in a browser tab. It stays responsive on graphs
with 100,000 nodes and hundreds of thousands of edges.

```sh
pip install plexgraph
pip install "plexgraph[jupyter]" # plus the notebook widget (anywidget)
pip install "plexgraph[all]"     # plus pandas, networkx and the notebook widget
```

```python
import plexgraph as pg

g = pg.Graph()
for i in range(60):
    g.add_node(i, team=f"t{i % 3}")
for i in range(60):
    g.add_edge(i, (i + 1) % 60)
    g.add_edge(i, (i * 7 + 3) % 60)

view = pg.show(g, node_color=pg.by_attribute("team", palette="tab10"),
               node_size=pg.size_by_degree((8, 18)), edge_curvature=0.15, return_handle=True)

view.style(node_shape="s")               # change the open viewer from Python
view.color_nodes([0, 1, 2], "crimson")   # paint specific nodes
```

Try it without writing code: `python -m plexgraph demo`.

## What it does

- **Three kinds of network as first-class data**: layers (multiplex), time (contact sequences or intervals) and
  hyperedges, in one graph model.
- **Loaders** for edge lists, pandas DataFrames, networkx, GEXF and GraphML, and for temporal `u v t` files
  (`pg.read_temporal_edgelist`) with ISO dates, custom date formats or Unix timestamps.
- **A layout that scales**: streaming force layout with bounded memory, an overview that turns into individual nodes as
  you zoom, and communities that collapse and expand.
- **networkx-style styling, live**: colors, sizes, shapes, outlines, opacity, curved edges and labels, from attributes,
  degree, weights, time buckets or your own lists and dicts, changed while the viewer is open. The same controls are in
  the viewer's Appearance panel.
- **Exploration tools**: search, filters, click-to-select, shortest path, time slider and time ribbon, and export to
  PNG, JPG, SVG, HTML or PDF.

## Documentation

**[plexgraph.readthedocs.io](https://plexgraph.readthedocs.io/)** — user guide and API reference. Also:

- [Tour notebook](https://github.com/instabaines/plexgraph/blob/master/examples/notebooks/tour.ipynb) with a runnable
  example of every feature
- [Changelog](https://github.com/instabaines/plexgraph/blob/master/CHANGELOG.md)

## Notes

- The package is pure Python and the viewer is bundled, so there is nothing to compile or build.
- `import plexgraph` is the supported entry point. It is implemented by two packages installed alongside it,
  `plexgraph_core` (graph model and loaders) and `plexgraph_bridge` (viewer and styling). Do not install this next to the
  separate `plexgraph-core` or `plexgraph-bridge` development packages, which provide the same modules.
- Status: alpha. The API may change before 1.0.

MIT licensed.
