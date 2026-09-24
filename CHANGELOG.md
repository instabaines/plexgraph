# Changelog

All notable changes to plexgraph are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Until 1.0 the API can change between minor versions.

## [Unreleased]

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

[Unreleased]: https://github.com/instabaines/plexgraph/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/instabaines/plexgraph/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/instabaines/plexgraph/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/instabaines/plexgraph/releases/tag/v0.1.0
