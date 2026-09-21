# Changelog

All notable changes to plexgraph are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Until 1.0 the API can change between minor versions.

## [Unreleased]

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

[Unreleased]: https://github.com/instabaines/plexgraph/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/instabaines/plexgraph/releases/tag/v0.1.0
