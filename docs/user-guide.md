# User guide

A practical how-to for building graphs, visualizing them, styling them, and getting data in and
out. If you installed with `pip install plexgraph`, you're ready to go. Working *on* plexgraph itself
(building the frontend, running the test suites) instead needs the one-time setup in
[CONTRIBUTING.md](https://github.com/instabaines/plexgraph/blob/develop/CONTRIBUTING.md).
Either way, this guide assumes `from plexgraph_bridge import show` / `from plexgraph_core
import Graph` work in your shell or notebook.

All of this is also available from one import: `import plexgraph as pg` gives `pg.Graph`, `pg.show`, `pg.by_degree` and the rest
(the examples below import from `plexgraph_core` and `plexgraph_bridge`, the packages that implement it; `import plexgraph` exposes the same names and is the supported entry point). `python -m plexgraph info` shows the
installed version and whether the viewer is bundled.

## Contents

- [Building a graph](#building-a-graph)
- [Visualizing it](#visualizing-it)
- [Styling reference](#styling-reference)
- [Artistic styling: colors, sizes, shapes, live changes](#artistic-styling)
- [Interacting with the viewer](#interacting-with-the-viewer)
- [Exporting](#exporting)
- [Reading graphs from other sources](#reading-graphs-from-other-sources)
- [Community detection](#community-detection)
- [Centrality](#centrality)
- [Temporal paths](#temporal-paths)
- [Jupyter notes](#jupyter-notes)
- [Scale and performance](#scale-and-performance)
- [Known limitations](#known-limitations)

## Building a graph

Everything starts from `plexgraph_core.Graph` — one unified structure for plain graphs, directed
graphs, multiplex/multilayer graphs, temporal graphs, and hypergraphs (see
[`docs/architecture/plan.md`](architecture/plan.md) for why they're all the same underlying model).

```python
from plexgraph_core import Graph

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
from plexgraph_core import (from_temporal_edgelist, from_pandas_temporal_edgelist,
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
[`examples/notebooks/tour.ipynb`](https://github.com/instabaines/plexgraph/blob/develop/examples/notebooks/tour.ipynb)
for one example of each kind.

## Visualizing it

```python
from plexgraph_bridge import show

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

## Artistic styling

Beyond the single-color options above, `show()` takes networkx-style arguments, and the same arguments work
**live** on the handle while the viewer is open:

```python
import plexgraph_bridge as hb
from plexgraph_bridge import show, by_attribute, by_degree, by_time_bucket, size_by_degree, size_by_weight

h = show(g, node_color=by_degree("plasma"), node_size=size_by_degree((6, 26)),
         edgecolors="white", linewidths=1, edge_curvature=0.2, alpha=0.7, return_handle=True)

h.style(node_shape="s")                                # change something, keep the rest
h.style(edge_color=by_time_bucket(6), edge_width=2)    # color edges by time bucket
h.color_nodes(["alice", "bob"], "crimson")             # paint specific nodes
h.clear_colors()
h.style(node_size=hb.RESET)                            # restore one option's default
h.reset_style()                                        # back to the default look
h.get_style()                                          # what is applied now, as plain data
```

Invalid arguments (an unknown colormap, the wrong number of colors, coloring by time on a graph with no times, a
typo in an option name) raise in Python straight away and change nothing. A viewer opened later, or a second
browser tab, shows the current style from its first frame.

### The options

| Option | Accepts | Notes |
|---|---|---|
| `node_color`, `edge_color` | one color; a list/array (one per node or edge); a dict; an encoding | see below |
| `node_size`, `edge_width` | pixels: a number, list/array, dict, or `size_by_...()` | node size is the **diameter** |
| `node_shape` | `"circle"` `"square"` `"triangle"` `"diamond"` `"cross"` (or `o s ^ d +`); a list/dict; `shape_by_attribute(...)` | |
| `node_alpha`, `edge_alpha`, `alpha` | 0-1 (`alpha` sets both) | |
| `node_outline_color`, `node_outline_width` (`edgecolors`, `linewidths`) | color; pixels | |
| `edge_curvature` (or `connectionstyle="arc3,rad=0.2"`) | -2 to 2; about 0.2 is typical | edges bend into arcs; parallel edges in opposite directions bend apart |
| `edge_style` (or `style`) | `"solid"` `"dashed"` `"dotted"` `"dashdot"` (or matplotlib's `-` `--` `:` `-.`) | line pattern; `"solid"` clears an existing pattern |
| `arrow_scale` | 0-20 | scales arrowheads |
| `cmap`, `vmin`, `vmax` / `edge_cmap`, `edge_vmin`, `edge_vmax` | colormap name, range | used when colors come from numbers |
| `with_labels`, `label_mode`, `label_size` (`font_size`), `label_color` (`font_color`), `label_halo`, `label_attribute` | | `label_mode` is `"hover"`, `"all"` or `"none"` |
| `background_color` | color | the whole page follows it |

**One color, one per node, or a mapping.** A string, or a *tuple* of 3-4 numbers, is one color. A **list or NumPy
array has one entry per node** (or per edge, for `edge_color`); a dict maps node keys (or `(source, target)` pairs)
to values:

```python
show(g, node_color="tomato")                         # one color
show(g, node_color=["red", "#00f", (0, 1, 0), ...])  # one color per node
show(g, node_color=scores, cmap="viridis")           # one number per node, through a colormap (like matplotlib)
show(g, node_color={"alice": "red", "bob": 3.5})     # only the named nodes (numbers or colors, not mixed)
show(g, node_size={"alice": 30})                     # everything else keeps the default size
```

(For backward compatibility a plain list of 3-4 numbers is still read as one color, unless the graph has exactly
that many nodes.) Edge lists follow the graph's connector order, like `G.edges()` in networkx.

**Encodings** compute colors, sizes or shapes from the graph itself:

| Encoding | Meaning |
|---|---|
| `by_attribute("team", palette="tab10")` | one color per distinct value; a colormap if the attribute is a number with many values (`scale="continuous"` forces it) |
| `by_degree("viridis")` | nodes, by number of neighbours |
| `by_weight("Blues")` | edges, by weight |
| `by_time("plasma")` | a gradient by time: an edge's start, or a node's first activity |
| `by_time_bucket(6, split="events")` | split the time span into buckets, like the time ribbon, and color by bucket (`split="time"` gives equal durations, `"events"` equal numbers of events). Edges use their start time; nodes use `node_time="first"` or `"last"` activity. With no `cmap` the colors match the ribbon |
| `by_values(numbers, cmap)` | one number per node or edge (a list passed directly does the same) |
| `size_by_degree((6, 26))`, `size_by_weight((1, 6))`, `size_by_attribute("score", (4, 24), scale="sqrt")`, `size_by_time(...)` | map onto a pixel range; `scale` is `"linear"`, `"sqrt"` or `"log"` |
| `shape_by_attribute("team")` | one shape per value |

Every color encoding takes `reverse=True`, `vmin`/`vmax` (both, or neither) and `missing=` (the color for elements
with no value). Colormaps: `viridis plasma inferno magma cividis coolwarm RdBu Spectral Blues Greens Reds Oranges
Purples Greys YlOrRd`. Categorical palettes: `default tab10 Set1 Set2 Dark2 Paired Pastel1`
(`plexgraph_bridge.COLORMAPS`, `PALETTES`, `SHAPES`, `STYLE_OPTIONS` list them). Colors given by name accept all 148 CSS names.

### In the viewer

The right-hand **Appearance** panel does the same without code: color nodes and edges by one color, attribute,
degree, weight, time or time bucket (with palettes, colormaps, reverse, bucket count and split), node size, shape,
opacity and outline, edge width (optionally by weight), opacity, curvature, line style (solid/dashed/dotted/dash-dot)
and arrow size, labels (on hover, all, or none, with size and halo), and the page background. **Paint nodes** colors
the nodes you have selected. **Reset
appearance** goes back to the default look. The panel follows changes made from Python, and the legends show color
scales: swatches with counts for categories and time buckets, and a gradient bar for colormaps. The same operations
are on the JavaScript handle: `viewer.setStyle({...})`, `getStyle()`, `resetStyle()`, `paintNodes(ids, color)`,
`clearPaint()`.

### Exports and limits

- **SVG export matches the screen** for colors, sizes, shapes, outlines, opacity, curved edges, line style and
  labels — dashes export as a native `stroke-dasharray`, which is exact, unlike the on-screen approximation below.
  PNG, JPG, HTML and PDF come from the canvas.
- Above 20,000 drawn edges edges are one pixel wide, straight and solid (no curvature or dash), but each keeps its
  color. Below that, dashing applies regardless of edge count; curvature additionally turns itself off above 30,000
  edges to keep frames cheap. On screen, dashes measure distance along the straight line between an edge's
  endpoints, not the true length along a curved edge, so dashes on a curved edge stretch slightly; SVG export does
  not have this limitation.
- The stacked (layer atlas and time ribbon) views use node color, size and opacity but draw circles; shape and
  outline apply to the flat view. The density overview (above 5,000 nodes) shows each cell's dominant color.
- Styles are per session: a new graph starts from the options given to `show()`.

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

**From Python**, without touching the button: `handle.save("graph.svg")` writes the current view straight to a
file — the format is guessed from the extension (`.svg` or `.png`), or pass `format=` explicitly. `handle.export()`
returns the data instead of writing it (a string for SVG, bytes for PNG), if you want to do something else with it:

```python
handle = pg.show(g, return_handle=True)
handle.save("graph.svg")             # or "graph.png"
svg_text = handle.export("svg")      # same thing, returned instead of written
```

Both need a connected viewer — a browser tab that has loaded, or a notebook widget that has rendered — and ask it
for its *current* view, so they reflect any live style changes and however the layout has settled so far. They
raise `RuntimeError` if nothing is connected yet, and `TimeoutError` if the viewer doesn't answer within
`timeout=` seconds (10 by default). JPG, HTML and PDF (the button's other formats) aren't available from Python yet.

**In a notebook, prefer the `await`-based `export_async()`/`save_async()`** — Jupyter supports `await` directly in
a cell, no `async def` wrapper needed:

```python
await handle.save_async("graph.svg")
svg_text = await handle.export_async("svg")
```

The plain `save()`/`export()` block the thread they're called from until the viewer replies, which is always safe
for a browser tab (its own thread), but *not* reliably for the notebook widget: the reply can only be delivered by
the Jupyter kernel's own message handling, which sometimes runs on that exact same thread the call is blocking —
in that case `save()`/`export()` wait out the full timeout and raise `TimeoutError`, even though the viewer
answered correctly. `await` is a genuine yield, so it cannot cause this; `export_async()`/`save_async()` work
reliably over both routes. See "Known limitations" below.

## Reading graphs from other sources

```python
from plexgraph_core import from_edgelist, from_pandas_edgelist, from_networkx, from_gexf, from_graphml, read_gml

g = from_edgelist([("a", "b"), ("b", "c", 2.5)])                   # (u, v) or (u, v, weight) or (u, v, attrs_dict)
g = from_pandas_edgelist(df, source="src", target="dst", edge_attr=True)
g = from_networkx(nx_graph)                                         # any networkx Graph/DiGraph/MultiGraph/MultiDiGraph
g = from_gexf("network.gexf")                                        # via networkx's reader
g = from_graphml("network.graphml")                                  # via networkx's reader
g = read_gml("network.gml")                                          # native reader, no networkx needed
g = read_gexf("network.gexf")                                        # native reader, no networkx needed
g = read_graphml("network.graphml")                                  # native reader, no networkx needed
```

`from_networkx` is the highest-leverage one for everything not covered by plexgraph's own native readers:
since networkx already reads GEXF, GraphML, Pajek, and more (and ships dozens of graph generators),
converting *from* a networkx object reaches all of that without this project reimplementing every format
parser. `read_gml`/`read_gexf`/`read_graphml`, by contrast, are native — no networkx, no dependency at
all — part of a gradual move toward fewer external dependencies generally; see "Reading GML files",
"Reading GEXF files" and "Reading GraphML files" below for what that buys you over
`from_networkx(nx.read_gml(...))`/`from_gexf(...)`/`from_graphml(...)`. `networkx`/`pandas` are optional
dependencies — only imported when you actually call one of the networkx-backed functions, so you don't
need them installed otherwise.

### Reading GML files

`read_gml` is a native parser (no dependency, including no networkx) for GML (Graph Modelling Language),
the simple hierarchical key-value format used by Graphlet, Gephi, Cytoscape, igraph, yEd and others:

```python
from plexgraph_core import read_gml

g = read_gml("network.gml")
```

GML has no native concept of multiple edge-type layers or time-varying data — it predates
multilayer/temporal network research — so, as with `read_edgelist`, you name which custom GML edge
attribute holds that information:

```python
g = read_gml("network.gml", layer_key="relation", t_start_key="start", t_end_key="end")
```

A node's plexgraph key is its GML `id` by default (always present and unique, so never ambiguous);
`label_as_key=True` uses the `label` attribute instead, if your file has one on every node and they're all
distinct. `weight_key` (default `"weight"`) names the edge attribute read as plexgraph's `weight` — GML
itself has no single standard name for this across writers, so pass `weight_key="value"` if that's what
your file uses instead. Every other node/edge attribute is kept as-is, with its GML type (int, float or
text).

Parsed leniently, matching how real GML files in the wild actually look rather than only the strict
formal grammar: both of GML's real-world quoting conventions are read correctly (`&quot;`-style entity
escaping, the spec's own way, and `\"`-style backslash escaping, which at least one other graph tool
writes instead), a quoted value may contain a literal newline, and `_` is accepted in attribute names even
though the formal grammar is letters-and-digits only — the same leniency philosophy igraph's own GML
reader uses, since many files already deviate from the strict spec in exactly these ways.

### Reading GEXF files

`read_gexf` is a native parser (no dependency, including no networkx) for GEXF (Graph Exchange XML
Format), the XML format used by Gephi and others:

```python
from plexgraph_core import read_gexf

g = read_gexf("network.gexf")
```

Unlike GML, GEXF has genuine native support for time-varying structure, via `<spells>`: a node or edge
can exist over several disjoint time intervals, not just one. `read_gexf` reads each spell as its own
connector sharing the same endpoints — this IR already allows parallel connectors between the same pair
of nodes (the same thing a multigraph produces), so a 3-spell edge just becomes 3 connectors. A plain
`start`/`end` on an edge (no `<spells>` block) is the one-spell case. GEXF has no multiplex/layer concept
of its own, so there's no `layer_key` here the way there is for `read_gml`/`read_edgelist`.

Edge `weight` is read directly (GEXF makes it a first-class XML attribute, not a custom column like
GML's `weight_key`). Direction follows the file's own `defaultedgetype` (undirected if the file doesn't
say); `directed=True`/`False` overrides that default, though an edge with its own `type=` attribute
still wins either way — including `type="mutual"`, GEXF's shorthand for "both directions," read as two
directed connectors. As with `read_gml`, a node's plexgraph key is its GEXF `id` by default;
`label_as_key=True` switches to `label`.

Not read: node-level dynamics (a node's own `start`/`end`/`<spells>` — plexgraph nodes have no time
dimension, only connectors do) and dynamic attribute values (an `<attvalue>` that itself changes over
time via its own `start`/`end` — plexgraph attributes aren't time-varying). Both are real GEXF features;
this reader just doesn't reach for them yet.

### Reading GraphML files

`read_graphml` is a native parser (no dependency, including no networkx) for GraphML, the XML format
used by yEd, Gephi, Cytoscape and many others:

```python
from plexgraph_core import read_graphml

g = read_graphml("network.graphml")
```

GraphML has no native multiplex or temporal concept at all — no equivalent of GML's common
`layer`-style attributes and nothing like GEXF's `<spells>` — so, as with `read_gml`, you name which
declared key holds that information:

```python
g = read_graphml("network.graphml", layer_key="relation", t_start_key="start", t_end_key="end")
```

A node's plexgraph key is its GraphML `id` by default; `label_as_key=True` uses whichever key's
`attr.name` is `"label"` instead, if every node has one and they're all distinct. GraphML also has no
first-class `weight` XML attribute (GEXF does) — `weight_key` (default `"weight"`) names the declared
key read as plexgraph's `weight`. Every other declared key is kept as a plain attribute, coerced to its
declared type (boolean/int/long/float/double/string). A `<graph edgedefault="directed"|"undirected">`
sets the file's default direction; an individual `<edge directed="true"|"false">` still overrides that.

A GraphML document may legally contain more than one top-level `<graph>` element — only the first is
read here, matching `networkx.read_graphml`'s own behavior, rather than treating the others as
plexgraph layers: there's no real-world convention for using them that way (unlike GEXF's `<spells>`),
so inventing one would be guessing, not reading what the format means. For the same reason, a `<graph>`
nested inside a `<node>` (GraphML's hierarchical/compound-graph feature, e.g. yEd folder groups) is
simply not descended into. `<hyperedge>` elements aren't read either, matching networkx's own documented
limitation, and a `<data>` element holding XML content instead of a plain value (e.g. a yFiles
`<y:ShapeNode>` sub-schema) is skipped rather than mis-parsed as text.

### Plain edge-list files with properties

`from_edgelist`/`from_pandas_edgelist` take data already in memory. `read_edgelist` reads a delimited text file
directly — the same shape `nx.read_edgelist` reads, plus named property columns you can then use for color, size,
or anything else:

```python
from plexgraph_core import read_edgelist

# "alice bob 2.5 friend\nbob carol 1.0 coworker\n"
g = read_edgelist("edges.txt", attrs={"weight": 2, "kind": 3})   # column indexes: weight, then kind
show(g, edge_color=by_attribute("kind", palette="tab10"), edge_width=size_by_weight((1, 4)))
```

Delimiter, `comments`, a `header` row (auto-detects column names when `attrs` is left out and there is a header),
custom `columns=(source_col, target_col)`, and `directed=True` are all supported, matching `read_temporal_edgelist`.
`delimiter=None` (the default) splits on whitespace; an explicit single-character delimiter (`","`, `"\t"`, ...) is
read as real CSV (RFC 4180): a comma inside a `"quoted field"` doesn't split the column, a doubled `""` inside one
is a literal quote, and a quoted field can even span multiple lines. This also means a comment line is detected
from the raw line, not a field's unquoted value, so a node legitimately named `"#1"` in quotes is never mistaken
for a `#`-prefixed comment.

### Node attributes from a separate table

Edge lists only carry per-edge data. To color or size nodes by something not derivable from the graph itself (a
category, a score, a label from another system), enrich an already-built graph from its own file or DataFrame:

```python
from plexgraph_core import read_edgelist, read_node_attributes

g = read_edgelist("edges.csv", delimiter=",", header=True)
read_node_attributes(g, "node_info.csv", delimiter=",")   # id,category,importance header -> attrs auto-named
show(g, node_color=by_attribute("category"), node_size=size_by_attribute("importance", (6, 24)))
```

It mutates and returns the same graph, so it composes, and can be called more than once (e.g. two lookup tables
from different systems, each adding different attributes) without erasing what an earlier call set.
`read_pandas_node_attributes(graph, df, key="id")` does the same from a DataFrame already in memory.

### Saving and round-tripping

```python
from plexgraph_core import to_pandas_edgelist, write_edgelist, to_networkx, write_graphml, write_gexf

df = to_pandas_edgelist(g)              # a DataFrame, edge_attr=True by default
write_edgelist(g, "out.txt", attrs=["weight", "kind"])
nxg = to_networkx(g)                     # MultiGraph/MultiDiGraph, or Graph/DiGraph with multigraph=False
write_graphml(g, "out.graphml")          # via networkx
write_gexf(g, "out.gexf")                # via networkx
```

None of these plain-edge formats can represent a hyperedge (more than two endpoints) — networkx has the same
limitation — so hyperedges are skipped with a `UserWarning` rather than silently dropped or raising.

## Community detection

`detect_communities(graph)` finds groups of densely-connected nodes by maximizing generalized ("multislice")
modularity (Mucha, Richardson, Macon, Porter & Onnela, *Science* 328:876-878, 2010) — the standard extension of
ordinary single-layer modularity to multiplex and temporal networks:

```python
from plexgraph_core.algorithms.community import detect_communities

result = detect_communities(g)
result.modularity                                 # how good the partition is (roughly -0.5 to 1; higher is better)
result.apply_as_node_attribute(g)                  # stamps a "community" attribute onto every node, in place
show(g, node_color_by="community")                 # colors, legend and filtering all come from the usual attribute machinery
```

On a graph with no layers this is exactly ordinary Louvain community detection — one community per node. On a
multiplex graph, every `(node, layer)` pair is treated as its own "state node": a community can differ for the
same node across layers (a real, literature-documented property of this method, not a bug), and `omega`
controls how strongly a node's layers are pulled toward agreeing (`omega=0` detects each layer's communities
completely independently; higher values increasingly force a node's communities to match across the layers it
appears in). `result.state_communities` has the full per-layer partition if you want it; `node_communities()`
collapses it to one community per node, by whichever community holds the most of that node's total edge weight.
`resolution` (gamma) is the usual modularity resolution parameter: above 1 finds more, smaller communities;
below 1 finds fewer, larger ones.

**Temporal community detection** is the same algorithm with a different coupling topology — straight from the
paper's own two worked examples, which use different coupling for genuinely multiplex layers versus a
time-ordered sequence of slices. `coupling="categorical"` (the default) couples every pair of a node's
layers, right for edge *types* with no inherent order. `coupling="ordinal"` instead couples each layer only to
its nearest neighbor(s) among the layers a node actually appears in, in `add_layer()` declaration order — right
for a graph built as one layer per time slice, matching the paper's own Senate roll-call-voting application
(one layer per two-year Congress, coupled only to adjacent Congresses). There is no automatic time-to-layer
bucketing here; build the slices yourself, one `add_layer()` call per slice in chronological order, and bucket
each connector into the right one.

This only understands plain and multilayer edges — hyperedges aren't modularity-native, so `detect_communities`
raises if the graph has any; run `bipartite_expansion(graph)` first (`from
plexgraph_core.algorithms.hypergraph import bipartite_expansion`), which turns each hyperedge into a synthetic
node connected to its members, itself eligible for its own community like any other node.

This is local-moving Louvain (greedy single-node moves to convergence), not the multilevel/hierarchical
variant — matches how multislice modularity is optimized in the literature, and is simpler to implement
correctly, at some cost in partition quality on very large graphs. Not yet wired into the viewer: there is no
button for this yet, only the `node_color=` dict shown above.

## Centrality

"Who matters in this network" — two measures, both following De Domenico et al.'s standard construction for
multilayer centrality ("Centrality in Interconnected Multilayer Networks", arXiv:1311.2906): stack every
layer's adjacency into one combined ("supra-adjacency") structure, connect each node's copies across layers,
and run an ordinary single-layer centrality algorithm on that one structure. Both reduce exactly to their
textbook single-layer definition on a graph with no layers.

```python
from plexgraph_core.algorithms.centrality import eigenvector_centrality, pagerank_centrality

result = eigenvector_centrality(g)          # or: pagerank_centrality(g)
result.apply_as_node_attribute(g)            # stamps a numeric "centrality" attribute onto every node
show(g, node_size=size_by_attribute("centrality", (6, 24)), node_color_by="centrality")
```

`eigenvector_centrality` is the graph's leading eigenvector (power iteration): a node is central if it's
connected to other central nodes. It needs the graph (or at least the part being measured) to be connected,
and raises on a graph with no edges at all, since the leading eigenvector is undefined there.
`pagerank_centrality` is a damped random walk (`damping`, default 0.85, the usual value) with teleportation
spread uniformly across *every* (node, layer) pair, not just within a layer — so, unlike community detection's
`omega`, there's no way to make layers fully independent of each other: even with no edges at all between two
layers, they still share a little probability mass through teleportation (a real property of the method's own
formula, not a bug). Scores sum to 1 across every state node. PageRank honors edge direction
(`directed=True` edges are followed one-way); eigenvector centrality treats every edge as undirected, matching
its standard definition.

Both collapse to one score per node the same way: `node_centrality()` *sums* a node's score across every
layer it appears in (a node active in three layers accumulates three layers' worth of importance) — a
different convention from `detect_communities`'s `node_communities()`, which picks one layer's answer by
weight rather than adding them, since "which single community" and "how much total importance" are different
questions. `coupling_weight` (default 1.0) sets how strongly a node's layers are tied together; 0 removes
inter-layer coupling edges entirely (though PageRank's teleportation still crosses layers regardless, as
above). Hyperedges aren't supported directly, same as community detection: run `bipartite_expansion(graph)`
first.

## Temporal paths

Plain shortest-path algorithms get contact-sequence data wrong: if A contacted B at noon and B had already
contacted C that morning, "A-B-C" isn't a valid route — A can't ride a contact that already happened. A valid
route only uses contacts in non-decreasing time order — a *time-respecting path*. `temporal_reachability`
computes the earliest time every node can be reached this way, the temporal analogue of Dijkstra's shortest
path (arrival time plays the role of distance):

```python
from plexgraph_core.algorithms.temporal import temporal_reachability, temporal_closeness_centrality

reach = temporal_reachability(g, "alice", start_time=0)     # departing no earlier than t=0
reach.arrival                                                 # {node_id: earliest arrival time}
reach.path_to("carol")                                        # the actual route, or None if unreachable

closeness = temporal_closeness_centrality(g, start_time=0)   # {node_id: how easily it's reached}
for node_id, value in closeness.items():
    g.set_node_attrs(node_id, closeness=value)
show(g, node_color_by="closeness")
```

A connector is available for its whole `[t_start, t_end]` span (an instantaneous `(u, v, t)` event is the
common case, where `t_start == t_end`): you may depart along it at any moment that is both at or after your
own current arrival time and at or after the connector's `t_start`, as long as that moment is still within
`t_end` — waiting for a connector to open is allowed, missing its close is not. A connector with no explicit
time (`t_start=-inf`, `t_end=inf`, the static-graph default) is always available. Direction is honored.

`temporal_closeness_centrality` sums `1/(elapsed time)` over everything actually reachable from each node
(harmonic closeness, which stays well-defined even when the graph isn't fully reachable from every node — an
unreachable node just contributes 0 rather than making the whole score undefined). There is no
`apply_as_node_attribute()` helper here (unlike community detection and centrality) since the result is
already a plain `{node_id: value}` dict — the `set_node_attrs` loop above is the whole thing.

`temporal_reachability` specifically finds the *foremost* path (earliest arrival) between every pair, one of
three optimality notions the temporal-paths literature distinguishes — the other two are *shortest*
(fewest contacts) and *fastest* (least total travel time), each useful for different questions and not
interchangeable. Not yet implemented: temporal betweenness centrality, which needs enumerating -- not just
finding one -- optimal path between every pair. That's a real step up in complexity: counting foremost and
fastest paths is #P-hard, so the corresponding betweenness is intractable in general; only shortest-path
betweenness (and two special cases of foremost-path betweenness) have known polynomial algorithms ([Buss,
Molter, Niedermeier & Rymar, "Algorithmic Aspects of Temporal Betweenness",
2020/2024](https://arxiv.org/abs/2006.08668)). Hyperedges aren't supported directly: run
`bipartite_expansion(graph)` first.

## Where it runs

In a notebook, with [anywidget](https://anywidget.dev/) installed (`pip install "plexgraph[jupyter]"`), `show()` displays
the viewer as a **widget**: the graph and
its layout travel over the notebook's own connection, so no server or port is involved, nothing has to be reachable
from your browser, and there is nothing for another web page to connect to. Outside a notebook it opens a browser tab
served from a small local server. This is what has and has not been checked:

| Where you run it | Status |
|---|---|
| A script on your own computer (Linux desktop) | Verified: opens a browser tab |
| JupyterLab | Verified end to end in CI with a real JupyterLab and a real browser: the viewer renders in the widget, a style change from another cell arrives live, and it comes back after the page is reloaded. Checked on a 100,000-node graph too |
| Google Colab | **The widget does not work reliably and is off by default there** (see "Known issue" below); `show()` uses the older, port-based route automatically, which was verified by hand |
| Jupyter Notebook 7, VS Code notebooks, JupyterHub, Binder, Kaggle, Databricks, SageMaker, Azure ML | The widget is expected to work wherever anywidget does, but only JupyterLab has actually been checked; given the Colab result below, treat "expected" cautiously until one of these has been verified too |
| A local notebook on Windows or macOS | The automated tests cover the Python side there; nobody has looked at the viewer |
| An SSH session, a container or a server with no desktop | The address is printed. Forward both ports it names (`ssh -L`), then open it |

If the widget does not appear in your notebook, `show(g, widget=False)` uses the older route: a small local server with
the viewer in an `<iframe>`. That route only works where your browser can reach the machine running Python, so on a
hosted notebook it can leave a blank frame (`show()` warns on Kaggle, JupyterHub or Binder, and Databricks).

**Known issue: the widget does not reliably deliver the graph on Google Colab.** The widget itself loads correctly there
(the viewer's own script runs, gets a real WebGL context, and reports so), but the bulk data the kernel sends often
never arrives in the browser (`handle.diagnostics()["host"]` stays at `{}` or short of what
`handle.diagnostics()["kernel"]` shows was sent). Everything this project could test outside Colab itself works: the
same host script at its real size, relaying through its nested iframe, sending the same chunked pattern from an asyncio
background thread, all succeed there. What is actually different in Colab's own kernel-to-browser message delivery has
not been identified. Because of this, `show()` defaults to the port-based route on Colab (`widget=False`'s behavior)
regardless of whether anywidget is installed; pass `widget=True` to force the widget there anyway.

## Jupyter notes

- `show()` detects the notebook by itself; no code changes are needed compared with a script. It returns nothing, so
  that calling it as a cell's last line does not print anything under the graph. `show(g, return_handle=True)` returns a
  handle with `style()`, `color_nodes()`, `reset_style()`, `close()` and, in a notebook, `widget`, the
  `GraphWidget` itself, which you can place in a layout of your own.
- The widget needs `anywidget`, which is an extra: `pip install "plexgraph[jupyter]"` (in a notebook, `%pip install ...`
  and then restart the kernel). Without it `show()` uses the older iframe route; `show(g, widget=True)` insists on the
  widget and says how to install it. If `anywidget` is installed but cannot work with the notebook's own widget
  packages, `show()` warns and uses the iframe route instead of failing.
- **Hosted notebooks (Colab, Kaggle, Databricks) manage their own packages.** Installing `plexgraph[jupyter]` there
  adds only `anywidget` and `psygnal`; it does not touch IPython, ipywidgets, traitlets or numpy. The one package it can
  replace is `websockets`, if the environment ships a version older than 13 (plexgraph needs 13 or newer for the
  browser-tab viewer). Where you want no changes at all, `pip install --no-deps plexgraph anywidget psygnal` installs
  just those three: the widget does not import `websockets`, and the release checks run it that way.
- Running the cell again, or scrolling a notebook so the output is unloaded and loaded again, rebuilds the viewer, and
  the kernel streams the graph to it again with the current style. A saved notebook that is reopened while its kernel is
  still running does the same.
- With `widget=False` the viewer's socket is protected by a random secret that only its own URL carries, so another web
  page in your browser cannot read your graph from the port. If you write your own WebSocket client, connect to
  `handle.ws_url`. (The widget has no socket at all.)
- **Google Colab**, with `widget=False`: the kernel runs on a remote machine, so the server serves the viewer page and
  its WebSocket from a single port and shows it with Colab's `serve_kernel_port_as_iframe`. Colab treats a second
  forwarded port as a different origin and refuses the socket, which is why there is only one.
- Use `height` to set the widget's height in pixels (default 600); it is always as wide as the cell.
- `handle.close()` stops the viewer updating but leaves its picture in the cell, marked as closed. A notebook that
  closes the previous viewer when it shows the next (as the tour does) therefore keeps every earlier picture. A viewer
  closed before it had loaded stays empty, so give a viewer a moment before closing it.
- **If a viewer looks wrong** (blank, tiny, a single dot), keep the handle, wait a few seconds for the viewer to settle,
  and call `handle.diagnostics()`:

  ```python
  h = show(g, return_handle=True)
  # ... a few seconds later:
  h.diagnostics()   # canvas size, pixel ratio, WebGL renderer and whether its context was lost, level of detail, errors
  ```

  The viewer reports these itself, so it works where the browser's console is out of reach.
  `h.diagnostics()["kernel"]` says how far the round trip to the browser actually got, even if the viewer's own report
  never arrives: `hellosReceived` is 0 if the browser's "I'm listening" message never reached the kernel at all;
  `framesSent`/`bytesSent` staying 0 despite a hello means streaming did not start (`lastStreamingError` says why, if
  it raised). `h.diagnostics()["host"]` is what the widget's own host script has itself received and relayed into the
  viewer, independent of whether the viewer ever started; `host.hostReceived` below `kernel.framesSent` means messages
  the kernel believes it sent did not reach the browser, and `host.hostError` says why one that did arrive failed
  (a notebook frontend handing buffers over differently than JupyterLab's does, for instance). Please include the
  output in a bug report.

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
and first-frame aggregation cost. See [benchmark results](https://github.com/instabaines/plexgraph/blob/develop/benchmarks/README.md).

### Layer-aware layout (experimental)

On a multiplex graph, `show(g, layer_gravity=1.0)` adds one virtual "hub" per layer: every node with a
connector in that layer is pulled toward that layer's hub (and pulls it back — a real mutual attraction, not
a fixed anchor), and hubs repel each other. The effect is that each layer's nodes cluster together and
distinct layers spread apart; a node that belongs to several layers settles somewhere between their hubs
rather than fully inside any one of them. It is off by default (`layer_gravity=0`), reproduces today's
layout exactly when left off, and has no effect on a graph with no layers. `hub_inertia` (0-1, default 0.5)
damps how much a hub itself moves per iteration, to curb the oscillation a hub and the nodes pulled toward
it can otherwise drive into each other.

This is a new, experimental knob, not a finished "multilayer layout mode": there is no guidance yet on
picking a value (0.5-2 is a reasonable range to start experimenting in), it can only pull layers apart, not
choose how they're arranged, and a layer whose own members already form a strongly separated community may
show little additional effect, since the ordinary repulsion/attraction forces already find that structure
on their own. More importantly, **one hub represents a whole layer, not each cluster within it** — see
"Known limitations" below for a confirmed case where this makes a layout worse, not better. Try it on your
own graph and compare against the default before relying on it.

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

Not yet available in the viewer: choosing a layout algorithm, collapsing communities visually, and
pinning nodes. Individual nodes cannot be clicked in the density overview (above 5,000 visible nodes);
filter or search down to a subset first. Community *detection* itself is available from Python --
see [Community detection](#community-detection) -- it just isn't wired into the viewer's filter/color
controls yet; color a graph by `node_color=communities` (see below) to see it today.

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

- **Stacked views draw circles only**: node shape and outline apply to the flat view (see
  [Artistic styling](#artistic-styling)).
- **Hypergraphs and directed arrows aren't rendered in the stacked view** — switching to
  Stack: layers/time on a graph with hyperedges or directed edges will show plain edges only for
  those connectors.
- **General (non-categorical) multilayer coupling isn't implemented** — layers assume the same
  node id means the same entity across layers (the common case); Kivelä et al.'s general/ordinal
  coupling model isn't supported.
- **SVG/HTML export follows the same rule as the live stacked view**: arrows and hyperedge hulls
  only export in the flat view. The stacked view's own elements (planes, threads, per-slice edges)
  export correctly.
- **The notebook widget is verified in JupyterLab only.** Other notebook frontends (Colab, VS Code, Notebook 7 and the
  hosted services) should work but are listed above as not verified.
- **`handle.export()`/`.save()` can time out against the notebook widget even though the viewer answered
  correctly** — a Jupyter kernel threading quirk, not a plexgraph bug: the reply can only be delivered by the
  kernel's own message handling, which sometimes runs on the exact thread the call is blocking. Use
  `export_async()`/`save_async()` (`await`-based) instead; see "Exporting" above. The browser-tab route is not
  affected — it has no such thread to share.
- **`layer_gravity` pulls a layer's disconnected sub-communities together** (see [Layer-aware
  layout](#layer-aware-layout-experimental)) — it adds one hub per *layer*, not one per cluster, so a layer
  containing two or more structurally unrelated groups gets them pulled toward the same point instead of kept
  apart. Confirmed directly: on a layer with two disconnected sub-communities joined to the rest of the graph
  only through another layer, raising `layer_gravity` from 0 to 4 collapsed the distance between the two
  sub-communities' centroids from 1.0 to 0.1 and introduced edge crossings that a plain layout (`layer_gravity=0`)
  didn't have. Worth checking for before turning this on for a graph where a layer isn't known to be one
  cohesive community.

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
