# Changelog

All notable changes to plexgraph are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Until 1.0 the API can change between minor versions.

## [Unreleased]

### Added
- **`handle.export_async()`/`handle.save_async()`**: `await`-based equivalents of `export()`/`save()` (added in
  0.3.0), for use directly in a notebook cell (Jupyter supports top-level `await`). Recommended over the plain
  `export()`/`save()` for the notebook widget specifically -- see "Fixed" below for why.
- **`show(g, layer_gravity=...)`** (experimental, off by default): on a multiplex graph, adds one virtual "hub"
  particle per layer that layer members are pulled toward (and pull back -- a real mutual attraction, not a
  fixed anchor), with hubs repelling each other so distinct layers spread apart; a node in several layers
  settles between their hubs. `hub_inertia` (default 0.5) damps hub movement to curb oscillation. See "Layer-aware
  layout" in the user guide -- confirmed to make a layout worse, not better, on a layer with multiple
  disconnected sub-communities, since one hub represents the whole layer rather than each cluster within it.
- **`plexgraph_core.algorithms.community.detect_communities()`**: community detection via generalized
  ("multislice") modularity maximization (Mucha, Richardson, Macon, Porter & Onnela, *Science* 2010), the
  standard extension of modularity to multiplex and temporal networks. A graph with no layers is exactly
  ordinary single-layer Louvain; on a multiplex graph a node's community can differ per layer, with `omega`
  controlling how strongly its layers are pulled toward agreement. `result.apply_as_node_attribute(g)` stamps
  the partition onto the graph as a node attribute, so `show(g, node_color_by="community")` colors, legends
  and filters it through the viewer's existing attribute machinery -- no viewer changes needed. See "Community
  detection" in the user guide; there is still no dedicated "detect communities" button in the viewer itself.
  `coupling="ordinal"` (alongside the default `"categorical"`) couples each layer only to its nearest
  neighbors in declaration order instead of every pair -- temporal community detection, matching the same
  paper's own Senate roll-call-voting application (one layer per time slice, coupled only to adjacent slices).
- **`plexgraph_core.algorithms.centrality.eigenvector_centrality()`/`pagerank_centrality()`**: multilayer
  centrality via the standard supra-adjacency construction (De Domenico et al., arXiv:1311.2906) -- stack
  every layer's adjacency into one structure, connect a node's copies across layers, run the ordinary
  single-layer algorithm on that. Both reduce exactly to their textbook definition on a graph with no layers;
  both verified against a from-scratch dense eigendecomposition (not just another library) to double-precision
  accuracy, including a regression test for a star graph (two dominant eigenvalues of equal magnitude, the
  classic case where naive power iteration never converges -- fixed with a diagonal shift). Unlike
  `detect_communities`, a node only gets one state node per layer it actually has an edge in, not an extra
  zero-degree one for every layer -- harmless for community detection's partition, but would otherwise have
  quietly diluted every other node's share of PageRank's teleportation probability. `result.apply_as_node_attribute(g)`
  works the same way as community detection's. See "Centrality" in the user guide.
- **`plexgraph_core.algorithms.temporal.temporal_reachability()`/`temporal_closeness_centrality()`**:
  time-respecting ("foremost") paths for temporal networks -- the earliest time every node can be reached
  departing no earlier than a given start time, honoring the non-transitivity real contact sequences have
  (a contact can only be followed by a later one; "A contacted B, B had already contacted C" is not a valid
  two-hop route, something a plain shortest-path algorithm gets wrong on this kind of data). The temporal
  analogue of Dijkstra's shortest path, with arrival time playing the role of distance; `reach.path_to(node)`
  reconstructs the actual route. `temporal_closeness_centrality` builds on it (harmonic closeness over
  elapsed time). See "Temporal paths" in the user guide for the exact availability-window semantics and what
  isn't built yet (temporal betweenness, a real step up in complexity: counting optimal paths is #P-hard in
  general, unlike finding just one).
- **`read_gml()`**: a native GML (Graph Modelling Language) reader -- no dependency, including no networkx,
  the first format plexgraph reads without delegating to it, part of a gradual move toward fewer external
  dependencies generally. Parsed leniently rather than only to the strict formal grammar, matching how real
  GML files in the wild actually look (the same philosophy igraph's own lenient GML reader uses): both of
  GML's real-world quoting conventions are read correctly (`&quot;`-style entity escaping, the spec's own
  way, and `\"`-style backslash escaping, which at least one other graph tool writes instead, despite
  conflicting with the spec), a quoted value may contain a literal newline, and `_` is accepted in
  attribute names even though the formal grammar is letters-and-digits only. `layer_key=`/`t_start_key=`/
  `t_end_key=` name a custom GML edge attribute to read as plexgraph's layer/t_start/t_end, the same
  convention-based approach `read_edgelist` already uses for arbitrary extra columns, since GML itself has
  no native concept of either (it predates multilayer/temporal network research). Cross-validated against a
  real GML file written by networkx (not just hand-constructed examples): identical node/edge counts,
  identical edge set, attributes preserved. See "Reading GML files" in the user guide.
- **`read_gexf()`**: a native GEXF (Graph Exchange XML Format) reader -- no dependency, including no
  networkx, built on the standard library's `xml.etree.ElementTree`. Unlike GML, GEXF has genuine native
  support for time-varying structure via `<spells>`: a node or edge may exist over several disjoint time
  intervals rather than just one. Each spell becomes its own connector sharing the same endpoints --
  parallel connectors between the same node pair are already native to this IR (the same mechanism a
  multigraph uses), so no model change was needed. A plain `start`/`end` on an edge (no `<spells>`) is the
  one-spell case. Edge `weight` is read directly (a first-class GEXF XML attribute, unlike GML's
  configurable `weight_key`); direction follows the file's own `defaultedgetype`, overridable with
  `directed=`, with a per-edge `type=` (including `"mutual"`, read as two directed connectors) always
  taking precedence either way. `<attributes>` declarations are read for type coercion and defaults; an
  `<attvalue>` referencing an undeclared attribute raises. GEXF has no native multiplex/layer concept, so
  there is no `layer_key` here. Node-level dynamics and dynamic (time-varying) attribute values are not
  read -- plexgraph's `Node`/attribute model has no time dimension of its own yet. Cross-validated against
  a real GEXF file written by networkx. See "Reading GEXF files" in the user guide.
- **`read_graphml()`**: a native GraphML reader -- no dependency, including no networkx, built on the
  standard library's `xml.etree.ElementTree`. GraphML has no native multiplex or temporal concept at all
  (no equivalent of GML's common `layer`-style attributes, nothing like GEXF's `<spells>`), so
  `layer_key=`/`t_start_key=`/`t_end_key=` name a declared key to read as plexgraph's layer/t_start/
  t_end, the same convention `read_gml` already uses. `weight_key` (default `"weight"`) does the same
  for `weight`, since GraphML -- unlike GEXF -- has no first-class `weight` XML attribute either. A
  `<graph edgedefault=...>` sets the file's default direction, overridable per-edge via its own
  `directed=` attribute. `<key>` declarations are read for type coercion (boolean/int/long/float/double/
  string) and defaults, with a `for="all"` key visible to both nodes and edges; a `<data>` referencing an
  undeclared key raises. A GraphML document may legally hold more than one top-level `<graph>` element --
  only the first is read, matching `networkx.read_graphml`'s own behavior, rather than guessing that the
  others are meant as layers (there's no real-world convention for that, unlike GEXF's `<spells>`). Also
  out of scope, for the same reason: `<graph>` nested inside a `<node>` (hierarchical/compound graphs,
  e.g. yEd folder groups) and `<hyperedge>` elements, the latter matching networkx's own documented
  limitation. A `<data>` element holding XML content rather than a plain value (e.g. a yFiles
  `<y:ShapeNode>` sub-schema) is skipped rather than mis-parsed. Cross-validated against a real GraphML
  file written by networkx. See "Reading GraphML files" in the user guide.
- **`read_dot()`**: a native DOT (the Graphviz language) reader -- no dependency, including no networkx.
  DOT's own grammar is large, so this covers a practical subset rather than the whole language: node/
  edge statements, edge chains, multiple trailing attribute-list groups, `node`/`edge [...]`
  default-attribute statements (applying to whatever's declared afterward in that scope, not
  retroactively, matching the spec's own "a subgraph receives its parent's attribute settings at the
  time of its definition" wording), and subgraphs, flattened into the main graph rather than modeled as
  nested (the same treatment `read_graphml` gives its own hierarchical nested graphs). A node mentioned
  more than once merges attributes; one appearing only as an edge endpoint is auto-created. Ports are
  stripped (cosmetic only -- no topology meaning) and an HTML-like label is kept as one opaque string
  rather than parsed as HTML. Not supported, raising a clear error rather than guessing: a subgraph used
  as an edge endpoint (DOT's cross-product "connect every node on one side to every node on the other").
  `strict` is accepted but not enforced; top-level assignments and `graph [...]` attribute statements are
  parsed but discarded (pure rendering metadata). `layer_key`/`t_start_key`/`t_end_key` name a custom
  attribute the same way `read_gml`/`read_graphml` do, since DOT has no native multiplex or temporal
  concept of its own; unlike those formats, DOT attribute values are always plain strings, so
  `weight_key`/`t_start_key`/`t_end_key` are coerced to `float` here, raising clearly if that fails.
  Cross-validated against Graphviz's own canonical cluster example. See "Reading DOT files" in the user
  guide.
- **`read_pajek()`**: a native Pajek `.net` reader -- no dependency, including no networkx. Unlike
  `read_gml`/`read_graphml`/`read_dot`, this has **genuine native multiplex support**: a `.net` file can
  hold more than one relation section (`*Arcs`/`*Edges`/`*Arcslist`/`*Edgeslist`/`*Matrix`), and when it
  does, each one becomes its own plexgraph layer automatically -- no `layer_key` configuration needed. A
  file with only one relation section stays a flat graph, matching every other reader's "nothing
  configured" behavior. `*Arcs`/`*Arcslist` sections are directed, `*Edges`/`*Edgeslist` undirected, and
  `*Matrix` always directed (even when symmetric) -- per section, overridable file-wide via `directed=`.
  Other bundled sections (`*Partition`/`*Vector`/`*Permutation`/`*Cluster`/`*Hierarchy` -- clustering
  results, not graph structure) are skipped gracefully, fixing a real bug in networkx's own reader: its
  edge-reading loop has no check for a section boundary, so a second relation section or a trailing
  `*Partition` block gets silently misread as bogus edges (confirmed directly against a real file).
  `t_start_key`/`t_end_key` name a custom trailing attribute, the same convention `read_gml` uses, since
  Pajek has no native temporal concept; there's no `weight_key`, since an edge/arc line's weight is a
  fixed position, not a named attribute, and is always read directly. Cross-validated against a real
  `.net` file written by networkx. See "Reading Pajek files" in the user guide.
- `handle.export()`/`.save()` could time out against the notebook widget even though the viewer answered
  correctly. They block the calling thread until the reply arrives, which is always safe for a browser tab (its
  own, unrelated thread), but not for the notebook widget: the reply can only be delivered by the Jupyter kernel's
  own message handling, which sometimes runs on the exact thread the call is blocking -- confirmed directly, by
  instrumenting a real run and tracing where the reply actually got processed (immediately after the blocking
  wait gave up and freed that thread, not before). `export_async()`/`save_async()` do not have this problem:
  `await` is a genuine cooperative yield, so it cannot starve the kernel's own message handling the way a thread
  block can. The plain `export()`/`save()` remain unchanged (still correct for a browser tab, and still usable
  against the widget -- just not guaranteed to succeed within their timeout); their docstrings and the user guide
  now say so.

### Fixed
- `read_edgelist`/`read_temporal_edgelist`/`read_node_attributes` with an explicit delimiter (`","`, `"\t"`) split
  rows with plain `str.split()`, not real CSV parsing -- a quoted field containing the delimiter (`"Smith,
  John",...`) was silently mis-split into extra columns instead of read as one value. Now parsed with the
  standard library's `csv` module (no new dependency): proper quoting, doubled-quote escaping, and multi-line
  quoted fields all work, and comment-line detection still looks at the raw line rather than an unquoted field
  value, so a node legitimately named `"#1"` is never mistaken for a comment. A multi-character delimiter, which
  used to silently "work" via `str.split()` but can't be expressed as real CSV, now raises a clear error instead.

## [0.3.0]

### Added
- **`handle.export()`/`handle.save()`**: get or write the viewer's current view straight from Python, no click, no
  GUI -- `handle.save("graph.svg")` (format guessed from the extension, or given explicitly; `"svg"` or `"png"`).
  Needs a connected viewer (a loaded browser tab or a rendered notebook widget); raises `RuntimeError` if none is
  connected and `TimeoutError` if it doesn't answer within `timeout=` seconds. Works over both viewer transports
  (the WebSocket bridge and the notebook widget), via a new two-way wire message (`export_request`/
  `export_response`) -- the first message type a viewer ever sends back to Python; every prior message flowed
  only from Python to the viewer.

### Fixed
- The notebook widget's host script (`widget.js`) called anywidget's `model.send(content, buffers)` -- but
  `AnyModel.send` is `(content, callbacks, buffers)`, three arguments, matching the underlying ipywidgets model;
  passing buffers as the second argument silently drops them into the callbacks position instead, so they never
  reach Python. Found while building `handle.export()`'s notebook-widget path, which is the first thing in this
  codebase to send binary data from the browser back to the kernel; nothing before it would have exposed this.

## [0.2.0]

### Added
- **Plain edge-list files with property columns.** `read_edgelist(path, attrs={"weight": 2, "kind": 3})` loads an
  edge list where extra columns carry arbitrary per-edge properties (usable for color, size, or anything else),
  alongside the existing temporal loader. Delimiter, comments, a header row and custom source/target columns are all
  configurable, matching `read_temporal_edgelist`'s conventions.
- **Node attribute tables.** `read_node_attributes(graph, path)` and `read_pandas_node_attributes(graph, df)` enrich
  an already-loaded graph's nodes from a separate file or DataFrame keyed by node id — a distinct, composable step
  from loading edges, so a graph can be built from one file and colored/sized from another. `Graph.set_node_attrs`
  merges attributes into an existing node directly.
- **Export and round-trip.** `to_pandas_edgelist`, `write_edgelist`, `to_networkx`, `write_graphml` and `write_gexf`
  save a graph back out, mirroring the load-side functions. Hyperedges (more than two endpoints) can't be represented
  in any of these plain-edge formats — networkx has the same limitation — so they're skipped with a `UserWarning`
  rather than silently dropped or raising.
- **Dashed and dotted edges.** `edge_style="dashed"` (also `"dotted"`, `"dashdot"`, and matplotlib's `"-"`/`"--"`/
  `":"`/`"-."`) draws edges with a line pattern instead of solid, matching networkx's `style=` parameter (`style=` is
  accepted as an alias). Available from `show()`, `handle.style()`, and the viewer's Appearance panel ("Line style"),
  and exported exactly via `stroke-dasharray` in `exportSVG()`.

### Fixed
- **`to_pandas_edgelist`, `write_edgelist` and `to_networkx` could export the wrong source/target/layer.** A node or
  layer given an explicit integer key equal to another node/layer's *internal* id (e.g. a node keyed `1` when some
  other node happens to be the graph's 2nd node) made these exporters resolve a connector's endpoint to the wrong
  node, silently — no exception, just wrong data in the output. They now index nodes/layers directly by internal id
  instead of going through key resolution a second time.
- `to_pandas_edgelist(edge_attr=["t_end"])` (or `["t_start"]`) silently produced neither column, or both regardless
  of which was asked for — `t_start`/`t_end` selection is now independent, as the other `edge_attr` fields already were.
- `write_edgelist`: a connector whose *last* requested attribute was present but happened to be an empty string
  (`color=""`) was indistinguishable from one where that attribute was entirely absent, so it was silently trimmed
  from the row — shortening it, and raising a confusing `ValueError` if the file was read back with a fixed column
  layout. A present empty value and an absent one are now told apart correctly.
- `write_edgelist`'s default `attrs=["weight"]` (when a graph has any weighted connector) and `to_networkx`'s choice
  of `DiGraph`/`Graph` could be decided by a hyperedge that then gets skipped from the actual output. Both now look
  only at the connectors that are actually exported.
- `read_edgelist`/`read_temporal_edgelist`: a node id with two or more leading minus signs (e.g. `"--5"`) crashed
  with an uncaught `ValueError` instead of staying text as documented; the true minimum 64-bit integer (`-2**63`)
  was incorrectly rejected and kept as text due to an asymmetric range check.
- `read_pandas_node_attributes` didn't apply the same int-node-id coercion as `read_node_attributes`/`read_edgelist`,
  so a text id column (e.g. read with `dtype=str`) that should match an existing int-keyed node instead silently
  created a duplicate node.
- A non-numeric `weight` (e.g. a malformed "weight" column read as text) was silently accepted and stored with the
  wrong type, surfacing later as a confusing, far-away crash; `Graph.add_edge`/`add_hyperedge` now reject it
  immediately with a clear error.

### Changed
- **Render-on-demand.** The viewer's frame loop used to run forever at the display refresh rate, even sitting on a
  converged, unchanging graph. It now stops scheduling itself once nothing is dirty and nothing is pending, and wakes
  on the things that actually change what's on screen (style changes, hover, wheel/zoom, drag). Verified with a real
  browser clock: zero frames over 5 seconds of true idle, versus about 300 before. See `benchmarks/README.md` for the
  full writeup, including a real gap this exposed: code that mutates the camera directly instead of through a real
  input event no longer wakes the loop (`scripts/benchmark-render.mjs` did this and was fixed).
- Hovering over the flat (non-stacked) view no longer runs its GPU pick-and-readback on every frame while only the
  camera is moving (wheel-zoom, a programmatic pan) — it still updates immediately on real pointer movement. This
  read is a known-expensive, noisy-to-benchmark GPU operation; see `benchmarks/README.md` for what was and was not
  measured.
- `loadGraph` no longer allocates the node-position array twice in a row on every graph load (one was discarded
  unread) — one fewer 800KB allocation per load at 100K nodes.

### Known issues
- The notebook widget does not reliably deliver the graph on Google Colab: it loads and runs correctly there, but the
  bulk data the kernel sends often never reaches the browser, for a reason not identified. `show()` defaults to the
  older, port-based route on Colab because of this (`widget=True` forces the widget anyway). See "Where it runs" in
  the user guide.

## [0.1.1]

### Added
- **A notebook widget.** In a notebook, `show()` now displays the viewer as a widget (built on anywidget), and the graph,
  the layout and every style change travel over the notebook's own connection. No server is started and no port is
  opened, so it does not depend on your browser being able to reach the machine that runs Python, which is what broke
  in Colab and would break on any hosted notebook. `show(widget=False)` keeps the older local-server route, and it is
  what `show()` falls back to when `anywidget` is not installed, and the default on Google Colab specifically (see
  "Known issues" below). `show(g, return_handle=True).widget` is the `GraphWidget`, for placing in a layout of your own.
- `anywidget` is an optional extra, `pip install "plexgraph[jupyter]"`, not a requirement: hosted notebooks manage their
  own IPython and ipywidgets, and installing plexgraph must not change them. The widget does not import `websockets`, so
  `pip install --no-deps plexgraph anywidget psygnal` works where nothing at all may be replaced.
- `handle.diagnostics()` (and `GraphWidget.diagnostics`) returns what the notebook viewer reports about itself: its canvas
  and window size, pixel ratio, WebGL renderer and whether the context was lost, the level of detail it chose, and any
  errors it hit; and, under `kernel`, counters for how far the round trip to the browser got even if the viewer never
  reports anything at all (hellos received, frames and bytes sent, and the last error hit while streaming); and, under
  `host`, what the widget's own host script has itself received and relayed, independent of whether the viewer started
  -- catching a notebook frontend that does not hand comm messages/buffers over the way JupyterLab's does, which was
  previously invisible everywhere, including to the kernel. For working out why a viewer is blank or drawn wrongly on a
  platform where the browser console is out of reach.
- `scripts/verify-widget.mjs` runs the widget in a real JupyterLab in a real browser (in CI): it renders, a style change
  arrives live, nothing listens on a port, and the viewer comes back after a page reload.

### Security
- The viewer's WebSocket accepted a connection from any web page open in the same browser: a browser lets a page connect
  to `localhost:<port>` whatever its origin, so any site you visited while a viewer was open could read the graph
  (including node names and attributes) from a guessed port. Each `show()` session now has a random secret token
  that its own viewer URL carries, and connections without it are refused (HTTP 403). `handle.ws_url` gives the
  address, with the token, for your own WebSocket clients.

### Fixed
- **If the embedded viewer never starts at all** (its script is blocked by the page — a strict
  Content-Security-Policy with no `unsafe-inline` for scripts is one real way that happens — or the browser has no
  usable WebGL), the cell used to stay silently blank forever, with nothing in `handle.diagnostics()` either, because the
  reporting code lived inside the thing that never ran. The cell now shows a message after a few seconds, and Python
  gets a report too, from code that runs outside the possibly-blocked part.
- Sending many widget messages back to back, with nothing awaited between them, is the one thing every reproduction of
  a viewer stuck with no graph had in common (a small graph's rapid burst of layout_step messages; a large graph's
  single graph message once it needs several chunks). The widget now pauses briefly (15ms) between chunks sent to the
  browser. This did not come from a confirmed root cause -- it is the most consistent pattern across every case
  investigated -- so it may not be the whole answer for every notebook frontend.
- Closing a widget (`handle.close()`) destroyed the notebook connection and blanked its output, so a notebook that closes
  the previous viewer when it shows the next lost every earlier picture. It now stops the viewer updating and leaves the
  picture on screen, marked as closed.
- The viewer could draw nothing at all, in a 1x1 pixel area, when its canvas was created before its container had a
  size (which happens inside a notebook) and then resized. The renderer now tells its graphics library when the canvas
  changes size, and the app follows the canvas with a `ResizeObserver`.
- `show()` in Google Colab blocked forever and displayed nothing: Colab was not recognised as a notebook (its shell
  is not a `ZMQInteractiveShell`), so the call waited on a browser that a remote machine cannot open. Colab is now
  detected, `show()` returns immediately, and the viewer is shown through Colab's `serve_kernel_port_as_iframe`. One port
  serves both the viewer page and its WebSocket there, because Colab treats a second forwarded port as a different
  origin and refuses it. The server listens on all interfaces there (unless `host=` is given), because Colab's proxy
  cannot reach one bound to `localhost`.
- Closing or reloading a viewer tab while its layout was still streaming logged a "connection handler failed"
  traceback, which appears as red output in a notebook.
- `show()` in a script on a machine with no browser (SSH, a container, a server) waited forever and printed nothing.
  It now prints the address and the `ssh -L` command that forwards the ports.
- On the older server route (`widget=False`, or when `anywidget` is missing), `show()` in a hosted notebook where the
  viewer cannot be reached (Kaggle, JupyterHub or Binder, Databricks) now warns instead of leaving a blank frame. The
  user guide has a new "Where it runs" table.
- `read_temporal_edgelist`: a byte-order mark at the start of a file (as Excel writes) became part of the first node's
  name, and in a file with an explicit delimiter a trailing empty field was dropped, so a valid row was rejected as
  having too few columns.
- `ShowHandle.url` is `None` in Colab, where there is no address to give, instead of a path that looked like one.
- The bridge declared `websockets>=12`, but it uses the `websockets.asyncio` API, which needs 13 or newer. It also
  called `websockets.serve`, which before version 14 is the older implementation and does not work with this code; it now
  uses `websockets.asyncio.server.serve`, and versions 13.1 and 17 are both tested.
- The first example in the README and in the package docstring called `add_edge` on nodes that did not exist and would
  have raised `KeyError`. They are fixed, and a test now runs the first example of each README and the docstring.
- `python -m plexgraph info` reports whether `anywidget` is installed.
- The viewer accepts `?ws=same-origin` (the server that served the page) or a full `ws://`/`wss://` address, not only a
  port number, and its `disconnected`/`connection error` messages now say which address it tried.

## [0.1.0]

First release, as a single installable package: `pip install plexgraph`.

### Added
- **One package.** `import plexgraph as pg` exposes the graph model, loaders, `show()` and the styling helpers; the viewer
  is bundled, so no Node.js is needed. The implementation packages `plexgraph_core` and `plexgraph_bridge` are installed alongside it; `plexgraph` is the supported entry point. `python -m plexgraph`
  prints the version and installation info, and `python -m plexgraph demo` opens a demo graph.
- **Graph model** for multilayer, temporal and hypergraph networks, with loaders for edge lists, pandas, networkx, GEXF
  and GraphML.
- **Temporal loaders** for `u v t` contact sequences (`from_temporal_edgelist`, `from_pandas_temporal_edgelist`,
  `read_temporal_edgelist`): ISO dates, custom formats (`time_format`) and Unix timestamps (`time_unit`).
- **Scalable layout**: bounded-memory force layout with active repulsion at every size, linear-cost hyperedges, analytic
  layouts for paths and cycles, and component packing.
- **Viewer**: WebGL rendering with a viewport-adaptive overview that becomes individual nodes as you zoom, community
  grouping, search, attribute/degree filters, click-to-select, shortest path, time slider and time ribbon (equal-time or
  equal-events buckets), and export to PNG, JPG, SVG, HTML and PDF.
- **networkx-style styling**, live from Python and in the viewer's Appearance panel: colors, sizes, shapes, outlines,
  opacity, curved edges and labels, driven by attributes, degree, weight, time buckets or explicit lists and dicts.
  `handle.style()`, `color_nodes()` and `reset_style()` restyle an open viewer, and tabs that connect later see the
  current style.
- **Evaluation**: layout-quality comparison against igraph and networkx, browser benchmarks, and end-to-end tests that
  drive the real viewer.

### Fixed
- `edge_attr=False` in `from_pandas_edgelist` and `from_pandas_temporal_edgelist` raised a `TypeError`; it now means no
  extra attributes, like `None`.
- Node names that are integers too large for JavaScript (or for MessagePack) no longer crash the connection; they are sent
  as text.
- Hovering picked the first node drawn where nodes overlap instead of the one on top.
- Zooming into a large graph while its layout was still streaming stayed stuck in the overview.

[Unreleased]: https://github.com/instabaines/plexgraph/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/instabaines/plexgraph/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/instabaines/plexgraph/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/instabaines/plexgraph/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/instabaines/plexgraph/releases/tag/v0.1.0
