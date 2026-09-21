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

**Loading temporal data.** Temporal networks are usually published as *contact sequences*, one row
per event: `u v t` (u and v interacted at time t). Load them directly; you do not build
`t_start`/`t_end` by hand:

```python
from hyperloom_core import (from_temporal_edgelist, from_pandas_temporal_edgelist,
                            read_temporal_edgelist)

g = read_temporal_edgelist("events.txt")                 # whitespace-separated u v t (# comments ok)
g = read_temporal_edgelist("events.csv", delimiter=",", header=True, columns=(2, 3, 0))  # pick columns
g = from_temporal_edgelist([("a", "b", 1), ("b", "c", 5)])           # (u, v, t) tuples
g = from_pandas_temporal_edgelist(df, "src", "dst", "when", edge_attr=["kind"])   # DataFrame
g = from_temporal_edgelist(rows, intervals=True)                     # (u, v, start, end) rows
g = from_temporal_edgelist(rows, duration=30)                        # each contact lasts 30 time units
```

Each event becomes its own connector, so a repeated pair keeps every occurrence. A plain `(u, v, t)`
event is instantaneous (`t_start == t_end == t`).

**Time formats.** Every temporal loader takes the same options, so the format is never hardcoded:

| Your timestamps | What to pass | Result |
|---|---|---|
| Datetime objects, `numpy.datetime64`, pandas datetimes | nothing | Stored as Unix seconds (naive times are UTC); the viewer shows dates |
| ISO 8601 text: `2013-12-31 16:39:18`, `2020-01-01T10:00:00Z`, `2020-01-01 10:00+02:00`, `2020-01-01` | nothing | Same |
| Text in another layout: `12/31/2013`, `31.12.2013 16:39` | `time_format="%m/%d/%Y"` (a `strptime` pattern) | Same |
| Unix numbers | `time_unit="epoch_seconds"`, `"epoch_milliseconds"`, `"epoch_microseconds"` or `"epoch_nanoseconds"` | Converted to seconds; the viewer shows dates |
| Any other numbers (day 0, 1, 2, ...; simulation steps) | nothing | Kept as given; the axis shows the numbers |

Numbers that all look like Unix time (at least 1e9) with no `time_unit` produce a warning naming the unit
they most likely are, because otherwise the axis would show raw numbers such as 1388507958 and
millisecond values would be off by a factor of a thousand. `duration=` is in seconds once times are
converted. Times are shown in UTC.

Do not pass `(u, v, t)` rows to `from_edgelist`: its third value is a **weight**, so timestamps would
load silently as weights.

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
  slider. The selector chooses what the slider shows: **At this moment** (`t_start <= t <= t_end`,
  right for intervals), **Trailing window** (events in the last N time units, the default for
  contact sequences because a point event is otherwise visible only at its exact instant), or
  **Everything so far** (all events up to t). The ribbon view splits the timeline into buckets; an
  event on a bucket boundary appears in one bucket only.
- **View mode** (top-left, shown when the graph has layers and/or temporal data): switch between
  **Flat** (the default overlay — everything on one plane, color-coded) and **Stack: layers** /
  **Stack: time**, which instead draw each layer (or each time-bucket) as its own separated plane,
  all sharing the same node layout, with faint threads connecting each node's copy from one plane
  to the next. This is the clearest way to see "layer" or "time" as an actual dimension rather than
  just a color or filter. In the **Time ribbon**, the controls at the bottom set the number of buckets
  (2-24, default 6) and how time is split: **Equal time** gives every bucket the same duration, while
  **Equal number of events** gives every bucket about the same number of events, so busy periods get
  narrow buckets. Equal time is faithful to the clock but can leave one panel nearly empty and another
  crowded when activity is uneven (the Reddit hyperlink network grows from 67K events in its first
  bucket to 121K in its last). The legend shows how many events each bucket holds. An event exactly on a
  bucket boundary is counted in one bucket only, and every event appears in exactly one bucket unless
  it is an interval that spans several.

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

The layout uses blocked exact repulsion up to 512 nodes and a spatial-mesh
approximation above that. It never disables repulsion. The approximation uses
cell masses for distant forces and deterministic local samples within a cell;
it is not an exact or Barnes–Hut solver. Large hyperedges use one centroid
attraction per membership. Paths and cycles receive analytic layouts, and
components occupy separate, stable tiles. Exhausting the iteration budget no
longer means convergence. Direction and edge weight do not currently change
force strength.

Above 5,000 visible nodes, the viewer uses a **density overview**: spatial cells
represent node populations. In sliced views only participating nodes contribute
to each panel. Connections are deduplicated between cells; panels with over
1,000 aggregate connections omit them and say so. Direction, hyperedge hulls,
and individual picking are available in detailed neighborhoods, not density
mode. This is deliberate aggregation, not a claim that all individual nodes
remain readable. Search for a key and select a result to inspect its actual
incident relationships. A neighborhood larger than 5,000 nodes stays aggregated.

The renderer redraws when data, the camera, filters, or focus changes; pointer
movement updates interaction overlays without redrawing the graph. Remaining
large-graph limitations include force-approximation quality, dense neighborhoods,
and first-frame aggregation cost. Hardware GPU and sustained streaming results
still need broader validation. See [benchmark results](../benchmarks/README.md).

### Search and neighborhood inspection

**Find a node** searches keys (first 20 substring matches). Select a result to
isolate its incident connectors and their members, including complete hyperedge
memberships. The inspector shows attributes, neighbor count, and incident
connector count across the full graph. Overview layer/time filters still apply;
sliced views compare all slices of that neighborhood. **Show all nodes** clears
focus and **Fit view** reframes the current content. This is incident-neighborhood
exploration, not shortest-path analysis or an induced-neighbor subgraph.

JavaScript clients use the same public API:

```ts
const matches = viewer.searchNodes("alice");
if (matches.length) {
  const info = viewer.inspectNode(matches[0].id);
  viewer.focusNeighborhood(matches[0].id);
}
viewer.focusNeighborhood(null);
viewer.fitView();
```

### Style, filter, select and path tools

The right-hand panel of the viewer has these controls (all keyboard-accessible):

- **Color by / Size by**: color nodes by any categorical attribute (up to 200 distinct values) and
  size them by degree or a numeric attribute. In the density overview each cell takes its most common
  color.
- **Filter**: keep nodes by attribute value, numeric range and minimum degree. A connector stays visible
  only if all its endpoints do, and layer/time filters and neighbourhood focus still apply. Node
  positions do not change, so the filtered view keeps the same map. Reset filter clears it.
- **Select and path**: click nodes (or use the + button beside a search result) to select up to eight.
  **Find path** shows the fewest-hop route between two selected nodes (direction ignored; a hyperedge is
  one hop) or says there is none. **Show neighbourhood** works with one selected node.
- **Zoom**: + and - buttons in addition to the mouse wheel.

The same operations are on the JavaScript handle:

```ts
viewer.getNodeAttributes();                      // names, kinds, value counts, numeric ranges
viewer.setNodeFilter({ attribute: "team", values: ["red"], minDegree: 2 });   // returns nodes shown
viewer.setNodeFilter(null);
viewer.setNodeColorBy("team");
viewer.setNodeSizeBy("degree");                  // or a numeric attribute, or null
const route = viewer.focusPath(a, b);            // { nodes, connectors, hops } or null
viewer.setHighlightedNodes([a, b]);
viewer.zoomBy(1.5);
```

Not yet available: choosing a layout algorithm from the viewer, collapsing communities, and pinning
nodes. Individual nodes cannot be clicked in the density overview (above 5,000 visible nodes); filter
or search down to a subset first.

### Closing notebook sessions

Request a handle and close it before replacing a long-running viewer:

```python
handle = show(g, return_handle=True)
# later, before rerunning the viewer:
handle.close()  # idempotent; releases HTTP and WebSocket servers
```

Outside Jupyter, use `block=False` to receive the handle immediately. Handles
also support a `with` block. Layout steps run off the server event loop and
remain backpressured by the client.

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

### Aligned slice layouts

The viewer's **Layer atlas** places multiplex layers in separate grid panels.
**Time ribbon** places six equal-duration time windows from left to right.
Both keep the same node positions in every panel, making connectivity changes
comparable without overlapping planes or a thread for every node. Panel numbers
match reading order. Unassigned edges receive a panel only when present.

JavaScript users can choose the arrangement independently of the slicing axis:

```ts
viewer.setStackMode("layer", { layout: "atlas" });
viewer.setStackMode("time", { layout: "ribbon", timeBuckets: 8 });
viewer.setStackMode("layer", { layout: "stack" }); // original depth view
viewer.setStackMode(null); // overview
```

`timeBuckets` must be positive and finite; fractions are floored and counts are
capped at 64. Time windows include edges whose lifetimes intersect the window,
including its boundaries. A ribbon is a sequence of interval summaries, not
individual snapshots. Pan and zoom work in every arrangement; entering a slice
view fits its panels. SVG exports use the same geometry and panel headings.
Directed edges retain arrowheads, and hyperedges appear as translucent hulls on
all matching panels. Hover a node to reveal its key and highlight its copies across
panels; the existing `onHover(nodeId)` callback works in these views. Configured
node labels appear on each panel. The frontmost panel takes priority when picking
in the overlapping stack arrangement. These are arrangements based on aligned
small multiples, not a claim of research novelty.

To run the WebGL smoke check, start the app's Vite server on port 5173, then run
`node scripts/verify-slices.mjs`. Set `CHROME_PATH` if Chrome is installed somewhere
other than `/usr/bin/google-chrome`. Screenshots are written to `/tmp`.
