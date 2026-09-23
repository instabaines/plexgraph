# API reference

Everything here is available from the one top-level import:

```python
import plexgraph as pg
```

(`plexgraph` re-exports the graph model and loaders from `plexgraph_core`, and the viewer and
styling helpers from `plexgraph_bridge` -- those two packages are installed alongside it, but
`plexgraph` is the supported entry point. See the [user guide](../user-guide.md) for narrative,
example-driven docs; this section is the reference for each function and class.)

- **[Graph model](graph.md)** -- `Graph`, `Node`, `Connector`, `Layer`: building and inspecting a
  graph directly.
- **[Loading & saving graphs](io.md)** -- edge lists, pandas, networkx, GEXF/GraphML, and node
  attribute tables, in both directions.
- **[Viewing & styling](viewer.md)** -- `show()`, the live style handle, and the `by_*`/`size_by_*`
  encoding functions.
