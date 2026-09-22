# Evaluation, not just rendering

Start with [`evaluation.ipynb`](../examples/notebooks/evaluation.ipynb). The
original `tour.ipynb` demonstrates features; it does not establish scalability.
Large viewers and stress runs are opt-in in both notebooks.

## Reproduce

Use Python 3.11+ with NumPy installed. The subprocess memory limiter uses POSIX
`resource` (Linux/macOS), and the memory ceiling limits virtual address space,
not just resident memory. Each case gets a fresh subprocess; failed workers are
recorded rather than silently dropped. Do not compare timings across machines
as if they were algorithm-only measurements.

```sh
python benchmarks/layout_audit.py --suite quick --output /tmp/layout-quick.json
python benchmarks/layout_audit.py --suite scale --timeout 20 --memory-mb 1024 --output /tmp/layout-scale.json
pnpm --filter @plexgraph/app dev --host 127.0.0.1
# Separate terminal:
node scripts/benchmark-render.mjs --sizes=1000,10000,100000 --output=/tmp/browser.json
```

Set `CHROME_PATH` for a Chrome installation outside `/usr/bin/google-chrome`.
Browser runs use software rendering, a 1280×800 viewport, six slices, two edges
per node, and synthetic grid positions. Each size has a 60-second browser
watchdog and a 1 GiB JavaScript old-space ceiling (not a whole-browser memory
limit). JavaScript heap estimates exclude GPU allocations and are not peak RSS.
Current frame samples are taken after shader warmup during continuous camera pan;
the historical baseline used a stationary camera. Hover routine timing
is measured separately. These are short diagnostics, not sustained-load tests.

Python stress graphs have approximately three edges per node and run ten
iterations. Quick fixtures run up to sixty iterations over three seeds. Metrics
include build time, first-step latency, total layout time, peak worker RSS,
attraction-pair count, screen-grid crowding, and edge/random distance ratio.

## Historical baseline findings

Checked-in JSON captures the tested working tree, not a released version.
`results/source-hashes.json` identifies the evaluated layout/renderer files;
`results/python-environment.txt` records Python dependencies.

- All 24 small fixtures produced finite coordinates. Existing layout tests
  passed too, despite the failures below.
- 3K nodes reached about 595 MiB peak RSS. At 5K nodes the exact-repulsion
  allocation failed under the 1 GiB virtual-memory ceiling.
- 5,001 nodes completed because the implementation disables repulsion above
  5K. This is an algorithm discontinuity, not a sudden improvement in scale.
- A hyperedge with 5,001 members requires 12,502,500 attractive pairs and failed
  allocation under the same ceiling.
- 100K ordinary nodes completed ten layout iterations, but about 97% of nodes
  shared already occupied 8px screen cells after viewport fitting. This is a
  crowding proxy, not an exact overlap percentage or a quality score.
- The stopping flag can reflect cooling rather than a satisfactory equilibrium:
  all seed-zero quick cases stopped at iteration 27 despite differing topology.
- At 10K nodes the renderer missed a proposed p95 33ms frame budget in overview,
  atlas, and ribbon on software Chrome. Target hardware must be tested separately.
- The 100K browser run did not complete within the per-size watchdog window;
  it has no successful frame/hover result. Do not interpret Python completion
  as evidence that the browser can handle that workload.

## Visual inspection

[Seed-zero structural layouts](results/layout-diagnostics.png) were generated
from the notebook's plotting cell. The 120-node path remains folded and crossing;
one pair of disconnected planted groups substantially overlaps. The star is
recognizable. These are specific visual observations, not a general score.
The hyperedge plot shows positions only; it does not depict the renderer's hull.

## Historical implementation priorities

1. **Layout engine:** bounded-memory repulsion, scalable approximation rather
   than dropping forces, non-quadratic hyperedge attraction, disconnected
   component packing, and stopping criteria separated from cooling.
2. **Rendering:** viewport culling, aggregation/level of detail, selective node
   copies, indexed picking, and render-on-demand. Test interaction while layout
   updates stream, not only after they stop.
3. **Analytical tools:** key search, neighbor isolation, attribute inspector,
   persistent linked selection, fit/reset, and temporal difference views.
4. **Lifecycle and delivery:** stop/dispose notebook sessions, repeated loads,
   reconnect, export parity, and long-running memory measurements.

No new cosmetic arrangement fixes these issues by itself. Before claiming a
novel layout, compare readable structure and task performance against established
baselines on the same datasets and seeds. There is no novelty claim here.

## Coverage limits

This audit does not yet cover real-world datasets, hardware GPU profiling,
long-running leak detection, bridge throughput, user task completion studies,
layout comparisons with external engines, or quantitative edge crossings.
Small graph WebGL correctness is covered separately by `verify-slices.mjs`.
The notebook's optional visual inspection cells require matplotlib, and its
optional viewer requires the bridge dependencies and built frontend.


## Fixes and verification

The `*-fixed.json` files and `layout-diagnostics-fixed.png` record the subsequent
implementation. Keep the historical files for comparison.

- Exact forces use bounded blocks; large graphs use a spatial-mesh approximation
  with deterministic local samples. Repulsion never switches off.
- Centroid springs take linear hyperedge membership storage. A 5,001-member
  hyperedge now needs 5,001 attraction terms instead of 12,502,500 pairs.
- Analytic paths/cycles and component packing correct the audited path tangling
  and disconnected-component overlap. Cooling no longer implies equilibrium.
- The viewer uses density overview above 5,000 visible nodes. It aggregates
  populations into at most 1,024 cells per slice, deduplicates cell links, and
  hides links if a slice has over 1,000 aggregate connections. Search opens
  the exact incident neighborhood; very large neighborhoods remain aggregated.
- Search, attribute inspection, persistent neighborhood focus, fit/reset, and
  `ShowHandle.close()` are implemented. Streaming layout computation runs off
  the server event loop with client backpressure.

Under the same 1 GiB worker limit, all eight scale cases complete. The 5K-node
case uses about 57 MiB peak RSS; the 5,001-member hyperedge about 46 MiB.
100K nodes retain repulsion and take roughly 3.2 seconds for ten iterations
on the measured machine, versus the faster but repulsion-free historical path.

## Rendering: render-on-demand and hover cost

**Render-on-demand, the one item in "Historical implementation priorities"
above that was not yet done, is now implemented.** The frame loop used to
call `requestAnimationFrame` unconditionally forever, regardless of whether
anything was dirty — a converged, unchanging graph sitting on screen still
ran the browser's compositor at the display refresh rate indefinitely.
Confirmed with a real browser clock (`scripts/verify-idle.mjs`, in CI): a
settled graph now produces zero `requestAnimationFrame` calls over a 5-second
window (previously about 300, at 60Hz), and the loop correctly wakes for a
style change, a wheel/zoom, or a drag, then settles back to idle afterward.
Camera changes from wheel/drag now reach the renderer through a callback
(`Camera.onChange`) rather than being polled every frame; anything that
directly mutates `renderer.camera.x/y/zoom` instead of going through a real
input event (as `scripts/benchmark-render.mjs` used to) will no longer wake
the loop — the benchmark itself was updated to dispatch real wheel/pointer
events for this reason, not synthesize camera movement directly.

**Hovering in the flat (non-stacked) view does a full extra GPU render pass
plus a synchronous pixel readback (`regl.read`) to pick what's under the
cursor**, versus the stacked (atlas/ribbon) views' plain CPU distance check.
Profiled in isolation this readback alone costs single-digit milliseconds;
under sandboxed software rendering with other load on the machine it was
observed ranging from about 7ms to over 150ms for the same call — GPU
readback is a known-noisy operation to benchmark and this project's SwiftShader
CI backend is not representative of real hardware here (see the caveats
above). Whatever the true per-call cost, running it on every dirty frame
during continuous camera movement is wasteful: it now runs immediately on
real pointer movement (hover stays instant while genuinely hovering) but is
debounced to once per ~50ms of quiet while only the camera is moving
(`markHoverStale`), cutting how often the expensive path runs during a
wheel-zoom or programmatic pan without changing what a stationary hover
sees. Not yet explored: an async/non-blocking readback path, or replacing
GPU picking with CPU-side hit-testing for the flat view the way the stacked
view already does (deliberately not attempted here — GPU picking was chosen
specifically to get overlapping-node depth order right; a CPU nearest-node
check would need to reproduce that correctly, which is more than this pass
addressed).

A second, unrelated waste found in the same pass: `loadGraph` allocated the
node-position `Float32Array` twice in a row, discarding the first copy
unread (nothing between the two allocations writes positions) — one fewer
800KB allocation per load at 100K nodes.

At 100K nodes the viewer completes continuous-pan checks at approximately
19–24ms p95 frame intervals on software Chrome. These results are for aggregated
populations, not full individual-node rendering. Density cells have no individual
node hover, so hover timings/budgets are not applicable. The old and new browser
runs use different representations and camera scenarios; they are not an
apples-to-apples rendering-speed comparison. Aggregation preparation and panel
construction can still create noticeable first-frame pauses.

The real bridge/app smoke test is `scripts/verify-explore.mjs` (`PYTHON_PATH`
selects the test Python). It checks density, key search, exact neighborhood
connectivity, attributes, layer/time views, fit, and clearing focus.
`verify-slices.mjs` additionally checks small-graph SVG/hover correctness,
focused SVG node counts, and absence of unnecessary idle graph redraws.

Still open: temporal difference analysis, viewport-adaptive density resolution,
large-neighborhood drill-down, weighted/directed force semantics, hardware GPU
profiling, external-layout comparisons, and sustained-streaming performance.


## Quality and viewer bench (added after the first fixes)

The sections above measure whether layouts *run*. These measure whether they are *good*
and whether the shipped viewer is *usable*; see sections 2-4 and 12 of
[`evaluation.ipynb`](../examples/notebooks/evaluation.ipynb).

```sh
pip install -r benchmarks/requirements.txt
python benchmarks/quality_report.py --out benchmarks/results/quality      # ~4 min
pnpm --filter @plexgraph/app build
PYTHON_PATH=$(which python) node scripts/viewer-gallery.mjs --out=benchmarks/results/quality
```

`quality_report.py` compares plexgraph with igraph (FR, DrL, Kamada-Kawai) and networkx spring
layouts on karate, Les Miserables, a tree, a grid, planted-community graphs (1K/5K/20K), a
scale-free graph and the SNAP Facebook network, using stress, neighbourhood precision, community
silhouette, edge-length uniformity, crossing density and node overlap. `viewer-gallery.mjs` opens the
real viewer through the real bridge, screenshots it, checks the canvas for translucent pixels and the
console for errors, and inventories the UI controls and `ViewerHandle` methods.

Findings recorded by the first run (tiny gaps are noise; see the notebook scorecard):

- Layout quality is competitive with igraph-FR and networkx on stress, neighbourhood precision and
  crossings, and better than igraph-FR at separating planted communities at 5K-20K nodes.
- Weak on regular structure: the grid and tree score far below Kamada-Kawai (grid stress 0.19 vs
  0.012). It is also 10-25x slower than igraph-FR (5.5 s vs 0.24 s for 100 iterations at 20K nodes).
- Viewer, fixed: edges rendered white and cut through nodes at every detailed size because the draw
  commands wrote translucent colours without blending into the premultiplied canvas. All draw commands
  now blend with `OPAQUE_CANVAS_BLEND`; the gallery's translucent-pixel check guards it. The overview
  (over 5,000 visible nodes) now colours each cell by its dominant node colour, and the 404 (missing
  favicon) is gone. Since then the overview is viewport-adaptive: cells regroup as you zoom, links are weighted and the
  strongest 1,500 are drawn, clicking a cell zooms in, and zooming far enough shows the individual nodes in
  view (`getLodState()` reports which). Grouping by an attribute collapses communities into one point each;
  clicking a group expands it. On software Chrome the flat overview's p95 frame time is now about 28-38 ms
  (was 17-20 ms) with an unchanged median; atlas and ribbon are back to baseline. The benchmark's synthetic
  grid fills every cell, which is a worst case, and the cause has not been isolated.
- Exploration tools: the first run found 9 of 15 capabilities missing. Attribute/range/degree filters,
  colour and size by attribute, click and search selection, shortest path and zoom buttons now exist
  (`scripts/verify-tools.mjs` tests them end to end; at 20K nodes each takes about 30 ms or less). Still
  missing: choosing a layout algorithm and pinning nodes. Community collapse now exists (Group by).

- Temporal data: `(u, v, t)` contact sequences could not be loaded (and `from_edgelist` read the timestamp as a
  weight). `from_temporal_edgelist`, `from_pandas_temporal_edgelist` and `read_temporal_edgelist` now load them;
  the timeline has a trailing-window mode for point events; ribbon buckets no longer duplicate boundary events.
  `scripts/verify-temporal.mjs` covers the whole path from a `u v t` file. Not yet covered: datetime formatting
  on the time axis (it shows epoch seconds) and very large event streams.

- Styling: `scripts/verify-style.mjs` drives a real Python session end to end: options given to `show()`, then live
  `handle.style()`, `color_nodes()` and `reset_style()` calls, checking the colors actually drawn (canvas pixels), sizes,
  shapes and curves in the SVG export, time-bucket legends whose counts add up to the edge count, errors raised in
  Python without touching the viewer, a second tab receiving the current style, and the Appearance panel following
  Python. On the 572K-event Reddit graph a node-only restyle takes about 20-60 ms and an edge color change about
  250-350 ms (edges are drawn as one-pixel lines in per-color groups). The viewer gallery also restyles every dataset
  and fails the scorecard if that takes over 3 seconds.
- Fixed while building it: zooming into a large graph while its layout was still streaming stayed stuck in the
  overview, because each layout step postponed re-evaluating the level of detail; and hovering used the first node
  drawn where nodes overlap instead of the one on top.

