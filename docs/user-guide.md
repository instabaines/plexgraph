# User guide

A practical how-to for building graphs, visualizing them, styling them, and getting data in and
out. For the one-time setup (venvs, building the frontend), see the [README](../README.md#quick-start)
first — this guide assumes that's done and `from hyperloom_bridge import show` / `from hyperloom_core
import Graph` work in your shell or notebook.

## Contents

- [Building a graph](#building-a-graph)
- [Visualizing it](#visualizing-it)
- [Styling reference](#styling-reference)
- [Interacting with the viewer](#interacting-with-the-viewer)
- [Exporting](#exporting)
- [Reading graphs from other sources](#reading-graphs-from-other-sources)
- [Jupyter notes](#jupyter-notes)
- [Scale and performance](#scale-and-performance)
- [Known limitations](#known-limitations)

## Building a graph

Everything starts from `hyperloom_core.Graph` — one unified structure for plain graphs, directed
graphs, multiplex/multilayer graphs, temporal graphs, and hypergraphs (see
[`docs/architecture/plan.md`](architecture/plan.md) for why they're all the same underlying model).

```python
from hyperloom_core import Graph

g = Graph()
alice = g.add_node("alice", role="engineer")   # extra kwargs become node attrs
bob = g.add_node("bob")
g.add_edge(alice, bob, weight=1.0)
```

`add_node(key=None, **attrs)` — `key` is any hashable value (a string, an int, a tuple, ...); if
omitted, the node's own integer id is used as its key. Both `add_node`'s return value and the key
you passed can be used interchangeably anywhere a node is expected (`add_edge`, `add_hyperedge`,
etc.).

`add_edge(u, v, *, directed=False, layer=None, t_start=None, t_end=None, weight=None, **attrs)`:

```python
g.add_edge("alice", "bob", directed=True)                        # arrowhead alice -> bob
g.add_edge("alice", "bob", layer="friendship")                    # see multiplex, below
g.add_edge("alice", "bob", t_start=0, t_end=5)                    # see temporal, below
g.add_edge("alice", "bob", weight=2.5, kind="coworker")           # weight + arbitrary attrs
```

**Multiplex/multilayer** — declare layers, then tag edges with one:

```python
g.add_layer("friendship")
g.add_layer("coworker")
g.add_edge("alice", "bob", layer="friendship")
g.add_edge("alice", "bob", layer="coworker")
g.add_edge("alice", "carol")   # no layer — always visible, the "backbone"
```

**Temporal** — give edges a validity interval; omit both bounds for "always present":

```python
g.add_edge("alice", "bob", t_start=0, t_end=5)     # only valid in [0, 5]
g.add_edge("alice", "bob", t_start=3, t_end=3)      # an instantaneous event (t_start == t_end)
g.add_edge("alice", "carol")                         # always present (no bounds)
```

**Hypergraphs** — `add_hyperedge` takes a list of 3+ members instead of exactly 2:

```python
g.add_hyperedge(["alice", "bob", "carol"])          # one hyperedge spanning all three
g.add_hyperedge(["alice", "bob"], layer="friendship", t_start=0, t_end=5)  # can combine with layer/time too
```

(A 2-member `add_hyperedge` call is just an ordinary edge — hyperedge status requires >2 members.)

Everything composes freely: a hyperedge can have a layer and a time window at once, an edge can be
directed and layered and temporal simultaneously, and so on — see
[`examples/notebooks/tour.ipynb`](../examples/notebooks/tour.ipynb) for one example of each kind.

## Visualizing it

```python
from hyperloom_bridge import show

show(g)
```

That's the whole API surface for the common case. What happens depends on where you call it from:

- **In Jupyter** (notebook, JupyterLab, VS Code): renders inline in the cell output, doesn't block
  the kernel.
- **Everywhere else** (plain script, REPL): opens a browser tab, and blocks the process so the
  server stays alive — pass `block=False` if you don't want that (e.g. to keep scripting after).

`show()` returns `None` by default (so calling it bare as a cell's last line doesn't trigger
Jupyter's automatic display of a return value). If you need the bound ports for programmatic use,
pass `return_handle=True` and capture the result:

```python
handle = show(g, return_handle=True, block=False)
print(handle.ws_port, handle.http_port)
```

## Styling reference

Pass any of these as keyword arguments to `show()`. All are optional; unset ones use the defaults
below.

| Parameter | Default | Meaning |
|---|---|---|
| `node_color` | `"#298CF2"` (blue) | Node fill color |
| `node_radius_px` | `5` | Node dot radius, in screen pixels (constant regardless of zoom) |
| `edge_color` | gray, 50% alpha | Default edge color (layered edges use their layer's color instead — see below) |
| `edge_width_px` | `1.5` | Edge line thickness, in screen pixels (constant regardless of zoom) |
| `background_color` | near-white | Canvas background |
| `arrow_color` | dark gray, 90% alpha | Arrowhead color for directed edges |
| `arrow_length` | `0.025` | Arrowhead length, in world units (scales with zoom, unlike the pixel-based options above) |
| `arrow_width` | `0.012` | Arrowhead half-width, in world units |
| `arrow_t` | `0.92` | Where along the edge (0-1, source to target) the arrowhead tip sits — less than 1 so it doesn't sit under the target node's dot |
| `hull_padding` | `0.035` | How far a hyperedge's hull polygon extends beyond its member nodes, in world units |
| `width` / `height` | `900` / `600` | Rendered viewer's pixel size (the Jupyter iframe's size; a browser tab already fills the window and ignores these) |

**Colors** accept three forms, freely mixed:

```python
show(g, node_color="tomato")                        # any of the 148 CSS3 named colors
show(g, node_color="#ff6347")                        # hex — "#rgb", "#rrggbb", or "#rrggbbaa" (with alpha)
show(g, node_color=[1.0, 0.39, 0.28, 1.0])            # [r, g, b, a] or [r, g, b], 0-1 floats or 0-255 ints (auto-detected)
```

A combined example:

```python
show(
    g,
    node_color="tomato",
    node_radius_px=10,
    edge_color="steelblue",
    edge_width_px=3,
    background_color="#101018",
)
```

**What styling can't do yet**: only one uniform color/size for all nodes and all (non-layered)
edges — there's no "color nodes by this attribute's value" or per-node/per-edge styling, and no
node shape (circles only). Both need real new rendering/API work, not just a wider style dict; see
[Known limitations](#known-limitations).

## Interacting with the viewer

- **Pan**: click and drag. **Zoom**: scroll wheel.
- **Hover** a node to see its id in the top-left corner (otherwise empty — no permanent
  connection/debug clutter).
- **Layer legend** (top-right, multiplex graphs only): one checkbox + color swatch per layer.
  Unchecking a layer hides its edges; edges with no layer always stay visible.
- **Timeline** (bottom, temporal graphs only): click "All time" to start scrubbing, then drag the
  slider to see only edges valid at that point (`t_start <= t <= t_end`).
- **View mode** (top-left, shown when the graph has layers and/or temporal data): switch between
  **Flat** (the default overlay — everything on one plane, color-coded) and **Stack: layers** /
  **Stack: time**, which instead draw each layer (or each time-bucket) as its own separated plane,
  all sharing the same node layout, with faint threads connecting each node's copy from one plane
  to the next. This is the clearest way to see "layer" or "time" as an actual dimension rather than
  just a color or filter. Stacked-time bucketing defaults to 6 buckets across the graph's temporal
  span.

## Exporting

Click **Export ▾** (top-left) for:

| Format | What it is |
|---|---|
| PNG / JPG | A direct capture of what's on screen, whichever view mode is active |
| SVG | Real vector geometry (circles/lines/polygons rebuilt from the same node/edge/hull/arrow state the renderer draws from) — not a rasterized image, so it stays crisp at any zoom and is editable in a vector tool |
| HTML | That same SVG wrapped in a standalone page |
| PDF | A single-page PDF embedding a JPEG raster (a true vector PDF is a possible future upgrade, not done yet) |

## Reading graphs from other sources

```python
from hyperloom_core import from_edgelist, from_pandas_edgelist, from_networkx, from_gexf, from_graphml

g = from_edgelist([("a", "b"), ("b", "c", 2.5)])                   # (u, v) or (u, v, weight) or (u, v, attrs_dict)
g = from_pandas_edgelist(df, source="src", target="dst", edge_attr=True)
g = from_networkx(nx_graph)                                         # any networkx Graph/DiGraph/MultiGraph/MultiDiGraph
g = from_gexf("network.gexf")                                        # via networkx's reader
g = from_graphml("network.graphml")                                  # via networkx's reader
```

`from_networkx` is the highest-leverage one: since networkx already reads GEXF, GraphML, GML,
Pajek, and more (and ships dozens of graph generators), converting *from* a networkx object reaches
all of that without this project reimplementing any format parser. `networkx`/`pandas` are optional
dependencies — only imported when you actually call one of these functions, so you don't need them
installed otherwise.

## Jupyter notes

- `show()` auto-detects the kernel; no code changes needed versus a plain script.
- This is a pragmatic reuse of the same local WebSocket+HTTP server embedded in an `<iframe>`, not
  a "real" [anywidget](https://anywidget.dev/) integration — it won't survive notebook reopening
  without rerunning the cell, and won't work over remote/hosted Jupyter (Colab, JupyterHub) without
  port-forwarding. A real anywidget integration is a reasonable future upgrade (see
  [`docs/architecture/plan.md`](architecture/plan.md)) but isn't built yet.
- Use `width`/`height` to control the iframe's size (defaults 900x600).

## Scale and performance

- Force-directed layout uses exact pairwise repulsion up to 5,000 nodes; above that it falls back
  to edge-driven placement only (no repulsion), trading layout quality for staying responsive at
  100K+-node scale.
- Edge rendering similarly has a size-based tradeoff: below ~20,000 edges, edges get full
  per-edge color and adjustable width; above that, edges fall back to a cheap uniform-color,
  fixed-width path so very large graphs (100K+ nodes, 200K+ edges) stay responsive to load and to
  layout updates.
- Both thresholds are about *degrading gracefully*, not failing — a 100K-node graph still loads,
  renders, and updates live; it just looks cruder than a 50-node graph would.

## Known limitations

Worth knowing about rather than discovering by surprise:

- **No per-node/per-edge attribute-driven styling** (e.g. "color nodes by this attribute's value",
  "width by weight") and **no node shape** (circles only) — see [Styling reference](#styling-reference).
- **Hypergraphs and directed arrows aren't rendered in the stacked view** — switching to
  Stack: layers/time on a graph with hyperedges or directed edges will show plain edges only for
  those connectors.
- **General (non-categorical) multilayer coupling isn't implemented** — layers assume the same
  node id means the same entity across layers (the common case); Kivelä et al.'s general/ordinal
  coupling model isn't supported.
- **SVG/HTML export follows the same rule as the live stacked view**: arrows and hyperedge hulls
  only export in the flat view. The stacked view's own elements (planes, threads, per-slice edges)
  export correctly.
- **Jupyter integration is the iframe stopgap** described above, not a full anywidget integration.
