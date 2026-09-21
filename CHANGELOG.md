# Changelog

All notable changes to plexgraph are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Until 1.0 the API can change between minor versions.

## [Unreleased]

## [0.1.1]

### Security
- The viewer's WebSocket accepted a connection from any web page open in the same browser: a browser lets a page connect
  to `localhost:<port>` whatever its origin, so any site you visited while a viewer was open could read the graph
  (including node names and attributes) from a guessed port. Each `show()` session now has a random secret token
  that its own viewer URL carries, and connections without it are refused (HTTP 403). `handle.ws_url` gives the
  address, with the token, for your own WebSocket clients.

### Fixed
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
- `show()` in a hosted notebook where the viewer cannot be reached (Kaggle, JupyterHub or Binder, Databricks) now
  warns instead of leaving a blank frame. The user guide has a new "Where it runs" table.
- `read_temporal_edgelist`: a byte-order mark at the start of a file (as Excel writes) became part of the first node's
  name, and in a file with an explicit delimiter a trailing empty field was dropped, so a valid row was rejected as
  having too few columns.
- `ShowHandle.url` is `None` in Colab, where there is no address to give, instead of a path that looked like one.
- The bridge declared `websockets>=12`, but it uses the `websockets.asyncio` API, which needs 13 or newer.
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

[Unreleased]: https://github.com/instabaines/plexgraph/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/instabaines/plexgraph/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/instabaines/plexgraph/releases/tag/v0.1.0
