# New Graph/Network Visualization Tool — v1 Architecture Plan

## Context

Current graph visualization tooling (networkx/igraph + matplotlib, Gephi) is dated and slow, and doesn't handle complex network types well — multiplex/multilayer networks, hypergraphs, and temporal/dynamic networks are all bolted onto tools designed around plain static graphs. This project starts fresh: a new graph visualization library that treats these complex network types as first-class citizens from the ground up, not afterthoughts, while also fixing the performance ceiling (100K+ node graphs) that limits current tools.

The working directory is currently empty — this is a greenfield build. v1 scope is a "full feature survey": design the core architecture and data model up front to natively accommodate all four target network types (plain, multiplex, hypergraph, temporal), then build out depth in phases.

**Confirmed decisions:**
- Primary interaction model: Python API (like networkx/igraph) for graph construction/algorithms, paired with an interactive web frontend for visual exploration.
- Priority network types (all first-class): multiplex/multilayer, hypergraphs, temporal/dynamic, large-scale static (100K+ nodes).
- Python API: fully original design, not networkx-compatible — free to design around the new data model without inheriting networkx's dict-of-dict internals or Graph/DiGraph/MultiGraph/MultiDiGraph class hierarchy.
- License: MIT.
- Layout: streams incrementally to the frontend as it converges ("watch it converge," Gephi-style), not snapshot-only — built into the wire protocol from Phase A.

## 1. Unified Data Model (the core design problem)

The trap to avoid: modeling "plain graph" as the base case with multiplex/hyper/temporal bolted on as special cases — each extension changes a *different axis* and they need to compose (a temporal multiplex hypergraph should be representable without a distinct concrete class for it).

**Design: a single relation primitive — the "connector" — parameterized along three independent axes (layer, time, arity), with plain graphs as the degenerate case where all three axes are unused.**

- **Node**: `id`, `attrs: dict`. Layer/time-agnostic by default — no per-layer node duplication (avoids the "node-layer tuple as first-class citizen" trap that pymnet/multinet fall into, which makes plain-graph algorithms awkward). Layer/time membership is inferred from edge participation by default, with an optional sparse `NodeLayerPresence` overlay table `(node_id, layer_id, t_start, t_end)` for explicit cases.

- **Connector** (generalizes "edge"): the one relation primitive for edges *and* hyperedges.
  - `endpoints: list[node_id]` — length 2 for ordinary edges, length N for hyperedges (HyperNetX's incidence-based approach — no separate `Hyperedge` class).
  - `directed: bool`, optional `roles: dict[node_id, str]` for directed/role-based hyperedges.
  - `layer_id: LayerRef | None` — `None` = single-layer graph, zero multiplex overhead in the common case.
  - `t_start, t_end: float | None` — `None, None` = always present (static default). `t_start == t_end` represents instantaneous events; unequal bounds represent interval/duration edges. Both event-graph and interval-graph temporal semantics are representable.
  - `weight: float | None`, `attrs: dict`.

- **Layer**: `id`, `attrs`. Default coupling semantics: **categorical** (same node id across layers is implicitly the same entity — covers the majority real-world multiplex case, e.g. one social network with multiple relation types). General/ordinal coupling (Kivelä et al.'s taxonomy) is supported via an explicit `CouplingEdge` escape hatch, not the default path, and is **not implemented/exercised until v2** — flagged explicitly rather than left as an implicit gap.

- **Time is an attribute, not a wrapper type**: every connector (and optionally node-layer presence) carries a validity interval. This is the key move that lets temporal compose freely with multiplex and hypergraphs instead of needing a `TemporalMultiplexHypergraph` class. "Snapshot at time T," "aggregate over window," and "flatten layers to single-layer weighted graph" are all **computed views**, not stored state.

- **Storage**: columnar (struct-of-arrays, Arrow-backed) rather than adjacency-list-of-objects — necessary for 100K+ node performance and for efficient serialization to the frontend (typed arrays, not per-node JSON dicts). Filtering by layer/time becomes a columnar predicate.

**Reference points to ground the design doc in**: Kivelä et al. 2014 multilayer network taxonomy (coupling semantics); HyperNetX's incidence-based hyperedge model; Holme & Saramäki's temporal-network survey framing (edge stream + snapshot view).

## 2. Rendering Technology

**WebGL2 as the v1 rendering baseline**, with the geometry/buffer abstraction kept internal so a later WebGPU migration is a renderer-internals change, not an API change. Canvas2D is used only for the label overlay (text-in-WebGL is a known pain point; text-as-synced-Canvas2D-overlay is the standard trick — deck.gl, Sigma.js, MapLibre all do this) and static image export, never for graph geometry itself.

Why WebGL over Canvas2D/SVG: 100K+ node rendering rules out SVG (DOM death) and pure Canvas2D (draw-call overhead); WebGL instanced rendering is the proven approach (Sigma.js, Cosmos, deck.gl). Temporal animation at 60fps favors GPU-buffer interpolation over per-frame Canvas2D redraws. Hypergraph hulls and multiplex layer-planes are both expressible as additional WebGL geometry — a data-flow (IR→geometry) problem, not a rendering-tech problem.

Why not WebGPU yet: browser support/tooling maturity and team learning curve make it a premature v1 bet. Revisit once the internal abstraction proves out and WebGPU adoption matures.

**Build on regl** (thin WebGL wrapper, no scene-graph opinions to fight) rather than adopting Sigma.js/PIXI/deck.gl wholesale — none of them natively model hyperedges or multiplex layers, so 3 of the 4 priority network types would mean fighting their abstractions. Sigma.js's renderer internals are worth reading as prior art, not as a dependency. **Phase A includes a timeboxed spike** (100K nodes, pan/zoom/pick, 60fps position animation) to confirm regl vs. an alternative (e.g. luma.gl, if WebGPU-migration ease outweighs minimalism) before committing.

## 3. Python ↔ Frontend Bridge

**Local WebSocket server + browser tab as the primary v1 transport** (Bokeh/Plotly-Dash-style) — zero-install-friction for the target user (already living in a terminal/Jupyter/IDE), and gives a real devtools-based dev loop.

**Jupyter support via anywidget**, built as a second thin transport shim over the *same* wire protocol and the *same* `viz-core` renderer bundle — not a parallel frontend implementation. anywidget's use of standard ESM is what makes this reuse possible.

**Electron/Tauri desktop app: explicitly deferred**, not in v1 — doesn't match the target persona (scripting Python user, not "double-click an app icon" user).

**Protocol**: local WebSocket (not HTTP polling) — required for the confirmed streaming-layout requirement (Python pushes incremental position updates as force-directed layout converges; frontend pushes hover/selection events back). Wire format: prototype with MessagePack + typed arrays in Phase A; evaluate Arrow IPC once real payload sizes from the 100K-node spike are known — don't pre-commit to Arrow's dependency weight before that data exists. The wire schema carries `layer_id`/`t_start`/`t_end`/hyperedge-endpoint-count fields from Phase A onward even though only plain-graph fields are exercised at first, so later phases don't require a protocol-breaking change.

## 4. Project Structure (monorepo)

```
hyperloom/
  packages/
    core/                  # Python: IR, graph model, algorithms, IO
      hyperloom_core/
        model/              # Node/Connector/Layer/Graph, IR schema + version field
        algorithms/         # layout (force-directed etc.), centrality, community detection
        io/                 # import/export: networkx/igraph interop, edge lists, GEXF, GraphML
        wire/               # serialization to the bridge wire protocol (streaming-aware)
      pyproject.toml
      tests/

    bridge/                 # Python: local WebSocket server, session/state management
      hyperloom_bridge/
        server.py            # WebSocket server (FastAPI/uvicorn)
        session.py            # per-graph live session, incremental/streaming diffs
        launcher.py           # browser-tab launch orchestration
      pyproject.toml

    widget/                  # Python + JS: anywidget integration (thin shim, reuses viz-core)
      hyperloom_widget/
      js/
      pyproject.toml

    viz-core/                # TypeScript: framework-agnostic rendering engine
      src/
        ir/                   # TS mirror of the Python IR + wire decode
        render/                # regl/WebGL geometry generation per network-type
        layout/                 # optional client-side layout, streaming-update consumer
        interaction/            # pan/zoom/pick/select/hover, timeline scrubber
        transport/               # WebSocket client + anywidget client (shared interface)
      package.json

    app/                    # TS: standalone browser app shell (imports viz-core)
      src/
      index.html
      package.json

  docs/
    architecture/            # IR spec, wire protocol spec
    adr/                      # architecture decision records

  examples/
    python/
    notebooks/

  LICENSE (MIT)
  README.md
```

Tooling: pnpm workspaces + Turborepo for the TS side (shared `viz-core` consumed by both `app/` and `widget/js/`); `uv` workspaces for the Python side (`bridge` and `widget` both depend on `core`).

## 5. Phased Build Order

**Phase A — Core spine.** Full IR schema (all fields present; only plain-graph fields exercised), original Python API for plain graphs, WebGL/regl renderer (instanced nodes/edges, pan/zoom, GPU color-buffer picking for hover/selection), streaming force-directed layout over the WebSocket bridge, browser-tab launch. Includes the rendering-tech spike (100K nodes, 60fps animation) to de-risk the highest-uncertainty bet before building three more layers on it. Exit criterion: constructing a 100K-node graph in Python and calling `show()` opens a browser tab rendering it interactively, with layout visibly converging in real time.

**Phase B — Temporal.** Least structurally invasive extension (interval fields already exist on connectors from Phase A) — validates that the IR's "independent axes" design actually holds before tackling the two rendering-invasive extensions. Deliverables: timeline scrubber, snapshot/window-aggregation views, GPU-interpolated transitions between snapshots, small-multiples view.

**Phase C — Multiplex/multilayer.** Categorical coupling only (per §1) — layer filtering/flattening views, stacked-2.5D and small-multiples rendering modes, edge-type-encoded flattened mode. Mostly reuses Phase A/B node/edge geometry per-layer rather than requiring new visual grammar.

**Phase D — Hypergraphs.** Deliberately last: needs genuinely new geometry (convex-hull/contour grouping, or bipartite-expansion rendering with synthetic hyperedge-nodes) and new interaction semantics (hover/select on a hyperedge). By this point the IR and streaming protocol are already validated by three prior phases, so this phase is purely a rendering + algorithm problem. Includes bipartite-expansion utility in `core/algorithms`, reused for both hypergraph layout and existing centrality/community algorithms.

**Cross-cutting**: Phase A's wire protocol always carries the fields later phases need (§3), avoiding a protocol break at each phase boundary.

## 6. Open Risks to Track (not blocking, but worth revisiting)

- **regl vs. luma.gl vs. fully custom** — resolved by the Phase A spike, not decided up front.
- **Per-network-type layout algorithms** (temporal frame-to-frame stability, multiplex shared-vs-per-layer positions, hypergraph layout) are each open problems — v1 scopes one reasonable default per type, not parity with mature igraph/Gephi layout libraries.
- **General/non-categorical multilayer coupling** — IR has the escape hatch (`CouplingEdge`) but it's unimplemented until v2; keep documentation honest about this gap.
- **Wire format** (MessagePack vs. Arrow IPC) — deferred to real measurement in Phase A rather than decided now.

## Verification

Phase A's exit criterion is directly testable end-to-end: run a Python script that builds a 100K-node graph, call the show/render entry point, confirm a browser tab opens, the graph renders and is pan/zoom/hover interactive, and the force-directed layout visibly animates as it converges. Each subsequent phase (B/C/D) should ship with an equivalent example script in `examples/python/` demonstrating its network type end-to-end, plus unit tests in `packages/core/tests/` for the IR/algorithm additions and a manual interaction check in the browser for the renderer additions.
