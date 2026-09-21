// End-to-end styling: a real Python session (initial networkx-style options, then live handle calls) drives the real viewer.
// Colors are checked by reading canvas pixels, sizes/shapes/curves by the SVG export, and the panel by using it.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('..', import.meta.url));

const child = spawn(process.env.PYTHON_PATH || 'python3', ['-u', '-c', `
import json, sys, threading
import numpy as np
import hyperloom_bridge as hb
from hyperloom_bridge import show, RESET, by_attribute, by_degree, by_weight, by_time, by_time_bucket, size_by_degree, size_by_weight, shape_by_attribute
from hyperloom_core import Graph
g = Graph()
for i in range(60): g.add_node(i, team=['red', 'green', 'blue'][i % 3], score=float(i))
for t in range(100):
    u = t % 60; v = (t * 7 + 3) % 60
    if u != v: g.add_edge(u, v, t_start=float(t), t_end=float(t), weight=1.0 + t % 5, kind='a' if t % 2 else 'b')
for i in range(59): g.add_edge(i, i + 1, t_start=float(i), t_end=float(i), weight=2.0)
handle = show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=60,
              node_color=by_attribute('team', palette='tab10'), node_size=14, edge_curvature=0.2, edge_alpha=0.8)
print(json.dumps({'url': handle.url}), flush=True)
env = dict(globals())
for line in sys.stdin:
    request = json.loads(line)
    try:
        exec(request['code'], env)
        print(json.dumps({'ok': True}), flush=True)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}), flush=True)
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/packages/core:${root}/packages/bridge` }, stdio: ['pipe', 'pipe', 'pipe'] });
let stderr = ''; child.stderr.on('data', d => stderr += d);
const lines = createInterface({ input: child.stdout });
const waiting = []; let url;
lines.on('line', l => { let m; try { m = JSON.parse(l); } catch { return; } if (m.url) url = m.url; else waiting.shift()?.(m); });
const py = code => new Promise(res => { waiting.push(res); child.stdin.write(JSON.stringify({ code }) + '\n'); });
const pyOk = async code => { const r = await py(code); assert.ok(r.ok, `python failed: ${r.error}\n${stderr.slice(-400)}`); };
const pyFails = async (code, pattern) => { const r = await py(code); assert.ok(!r.ok, 'expected python to raise'); assert.match(r.error, pattern); };
await new Promise((res, rej) => { const t = setTimeout(() => rej(Error('python did not start: ' + stderr)), 30000); const poll = setInterval(() => { if (url) { clearTimeout(t); clearInterval(poll); res(); } }, 50); });

let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message)); page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  await page.waitForFunction(() => window.__hyperloomHandle?.getVisibleNodeCount?.() === 60 && window.__hyperloomHandle.getStyle().node);
  await page.waitForTimeout(3500);
  const H = (fn, arg) => page.evaluate(fn, arg);
  const settle = () => page.waitForTimeout(500);

  // canvas colour at a node's centre, as 0-255 rgb (reads the WebGL buffer, so this is what is really drawn)
  const colorAt = id => H(i => {
    const h = window.__hyperloomHandle, c = document.getElementById('canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
    const [x, y] = h.getNodeScreenPosition(i), dpr = c.width / c.clientWidth;
    const px = new Uint8Array(4); gl.readPixels(Math.round(x * dpr), Math.round(c.height - y * dpr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    return Array.from(px.slice(0, 3));
  }, id);
  const near = (a, b, tol = 30) => a.every((v, i) => Math.abs(v - b[i]) <= tol);
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  // how many of these nodes show the expected colour at their centre (some centres are covered by neighbours)
  const share = async (ids, expected) => (await Promise.all(ids.map(colorAt))).filter((c, i) => near(c, expected(ids[i]))).length / ids.length;
  const svg = () => H(() => window.__hyperloomHandle.exportSVG());
  const legendRows = () => page.locator('#node-color-legend .row').count();

  // 1. The style given to show() arrives before the layout moves, and the legend and pixels show it.
  const start = await H(() => window.__hyperloomHandle.getStyle());
  assert.deepEqual(start.node.size, 14); assert.equal(start.edge.curvature, 0.2); assert.equal(start.edge.opacity, 0.8);
  assert.equal(await legendRows(), 3, 'tab10 legend has one row per team');
  const tab10 = ['#1f77b4', '#ff7f0e', '#2ca02c'];   // red -> first seen -> tab10[0]; nodes go red, green, blue
  const all = Array.from({ length: 60 }, (_, i) => i);
  assert.ok(await share(all, i => hex(tab10[i % 3])) >= 0.7, 'nodes are drawn in their tab10 team colours');
  let s = await svg();
  assert.equal((s.match(/<circle /g) || []).length, 60);
  assert.ok(/r="7\.0"/.test(s), 'size 14 px means radius 7');
  assert.ok(s.includes('<path d="M') && s.includes(' Q'), 'curved edges export as quadratic curves');

  // 2. Live changes from Python.
  await pyOk('handle.style(node_color=by_degree("viridis"), node_size=size_by_degree((6, 26)))');
  await settle();
  assert.equal(await page.locator('#node-color-legend .gradient').count(), 1, 'a colormap legend appears');
  assert.match(await page.locator('#node-color-legend .title').textContent(), /degree/);
  const radii = [...(await svg()).matchAll(/<circle [^>]*r="([\d.]+)"/g)].map(m => +m[1]);
  assert.ok(Math.min(...radii) < 5 && Math.max(...radii) > 10, `sizes follow degree (radii ${Math.min(...radii)}-${Math.max(...radii)})`);

  await pyOk('handle.style(node_size=12)');
  await pyOk('handle.color_nodes([5, 6, 7], "crimson")');
  await settle();
  assert.ok(await share([5, 6, 7], () => [220, 20, 60]) >= 0.66, 'painted nodes are crimson on screen');
  await pyOk('handle.clear_colors()');
  await settle();
  assert.ok(await share([5, 6, 7], () => [220, 20, 60]) <= 0.34, 'clearing removes the paint');

  await pyOk('handle.style(node_shape="s", edgecolors="#ffffff", linewidths=1.5)');
  await settle();
  s = await svg();
  assert.equal((s.match(/<rect x=/g) || []).length >= 60, true, 'square nodes export as rects');
  assert.ok(s.includes('stroke-width="1.5"'), 'the outline is exported');
  await pyOk('handle.style(node_shape=shape_by_attribute("team", ["triangle", "diamond", "cross"]))');
  await settle();
  assert.ok((await svg()).match(/<polygon /g).length >= 60, 'shapes by attribute export as polygons');
  await pyOk('handle.style(node_shape="o")');

  await pyOk('handle.style(edge_color=by_time_bucket(5), edge_width=2.5, edge_curvature=0)');
  await settle();
  const rows = await page.locator('#edge-color-legend .row').allTextContents();
  assert.equal(rows.length, 5, 'one legend row per time bucket');
  const total = (await page.locator('#edge-color-legend .count').allTextContents()).reduce((n, c) => n + Number(c.replace(/,/g, '')), 0);
  assert.equal(total, 159, 'every edge is in exactly one bucket (100 random + 59 chain)');
  s = await svg();
  const strokes = new Set([...s.matchAll(/<line [^>]*stroke="([^"]+)" stroke-width="2.5"/g)].map(m => m[1].replace(/,[\d.]+\)$/, ')')));
  assert.ok(strokes.size >= 4, `edges take several bucket colours (${strokes.size})`);
  await pyOk('handle.style(edge_color=by_weight("Blues"), edge_width=size_by_weight((1, 6)))');
  await settle();
  assert.match(await page.locator('#edge-color-legend .title').textContent(), /weight/);

  await pyOk('handle.style(background_color="#0f172a")');
  await settle();
  assert.equal(await H(() => getComputedStyle(document.body).backgroundColor), 'rgb(15, 23, 42)', 'the whole page follows the background');
  await pyOk('handle.style(background_color=RESET)');
  await settle();

  // 3. Mistakes are caught in Python and leave the viewer alone.
  const before = await H(() => JSON.stringify(window.__hyperloomHandle.getStyle()));
  await pyFails('handle.style(node_color=[1, 2, 3, 4, 5], cmap="viridis")', /entries but the graph has 60 nodes/);
  await pyFails('handle.style(node_color=by_attribute("nope"))', /no node has an attribute named 'nope'/);
  await pyFails('handle.style(edge_color=by_degree())', /degree is a node property/);
  await pyFails('handle.style(node_colour="red")', /unknown style option/);
  await settle();
  assert.equal(await H(() => JSON.stringify(window.__hyperloomHandle.getStyle())), before);

  // 4. A tab opened later sees the current style straight away.
  await pyOk('handle.style(node_color=by_attribute("score", cmap="plasma"), node_size=16)');
  await pyOk('handle.color_nodes({0: "gold", 1: "#00ff00"})');
  await settle();
  const late = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  await late.goto(url);
  await late.waitForFunction(() => window.__hyperloomHandle?.getVisibleNodeCount?.() === 60 && window.__hyperloomHandle.getStyle().node?.size === 16);
  await late.waitForTimeout(1500);
  assert.deepEqual(await late.evaluate(() => JSON.stringify(window.__hyperloomHandle.getStyle())), await H(() => JSON.stringify(window.__hyperloomHandle.getStyle())));
  await late.close();

  // 5. The panel controls the same style, and follows changes made from Python.
  await pyOk('handle.reset_style()');
  await settle();
  assert.deepEqual(await H(() => window.__hyperloomHandle.getStyle()), {});
  assert.equal(await page.getByLabel('Node size').inputValue(), '10');
  await pyOk('handle.style(node_size=22, edge_curvature=0.35, node_alpha=0.6)');
  await settle();
  assert.equal(await page.getByLabel('Node size').inputValue(), '22', 'the panel follows Python');
  assert.equal(await page.getByLabel('Edge curvature').inputValue(), '0.35');
  assert.equal(await page.getByLabel('Node opacity').inputValue(), '0.6');

  for (const title of ['Edges', 'Labels & page', 'Paint nodes']) await page.locator('#appearance summary', { hasText: title }).click();   // sections start collapsed
  const setSlider = (label, value) => page.getByLabel(label, { exact: true }).evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })); }, value);
  await setSlider('Node size', 18);
  await setSlider('Edge width', 3);
  await settle();
  const fromPanel = await H(() => window.__hyperloomHandle.getStyle());
  assert.equal(fromPanel.node.size, 18); assert.equal(fromPanel.edge.width, 3);
  assert.equal(await page.getByLabel('Node size', { exact: true }).inputValue(), '18');

  await page.getByLabel('Node color mode').selectOption('attr:team');
  await page.getByLabel('Node palette').selectOption('Set2');
  await settle();
  assert.deepEqual((await H(() => window.__hyperloomHandle.getStyle())).node.color, { kind: 'attribute', attribute: 'team', scale: 'categorical', palette: 'Set2' });
  assert.equal(await legendRows(), 3);
  assert.equal(await page.getByLabel('Color by', { exact: true }).first().inputValue(), 'team', 'the quick Color by control follows too');

  await page.getByLabel('Node color mode').selectOption('attr:score');
  await page.getByLabel('Node colormap', { exact: true }).selectOption('magma');
  await page.getByLabel('Reverse node colormap').check();
  await settle();
  assert.deepEqual((await H(() => window.__hyperloomHandle.getStyle())).node.color, { kind: 'attribute', attribute: 'score', scale: 'continuous', colormap: 'magma', reverse: true });
  await page.getByLabel('Node color mode').selectOption('timeBucket');
  await settle();
  assert.equal(await legendRows(), 6, 'coloring nodes by time bucket');
  await page.getByLabel('Node shape').selectOption('diamond');
  await page.getByLabel('Labels', { exact: true }).selectOption('all');
  await page.getByLabel('Label halo').check();
  await settle();
  s = await svg();
  assert.ok(s.includes('<polygon') && s.includes('<text'), 'diamond nodes and labels appear in the export');
  assert.ok(s.includes('paint-order="stroke"'), 'the label halo is exported');
  await page.getByLabel('Labels', { exact: true }).selectOption('hover');

  await setSlider('Node size', 8);   // small marks, so a neighbour cannot cover the pixel we read
  await page.getByLabel('Find a node').fill('12');
  await page.getByRole('button', { name: 'Select 12', exact: true }).click();
  await page.getByLabel('Paint color').evaluate(el => { el.value = '#ff00ff'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.getByRole('button', { name: 'Paint selected nodes' }).click();
  await settle();
  const painted = await colorAt(12);   // node opacity is still 0.6 here, so it blends with what is behind it
  assert.ok(painted[0] > 170 && painted[2] > 170 && painted[1] < 140, `painting a selected node from the panel (pixel ${painted})`);
  await page.getByRole('button', { name: 'Clear painting' }).click();
  await page.getByRole('button', { name: 'Reset appearance' }).click();
  await settle();
  assert.deepEqual(await H(() => window.__hyperloomHandle.getStyle()), {});
  assert.equal(await page.locator('#tools select[aria-label="Color by"]').inputValue(), '');

  assert.deepEqual(errors, []);
  console.log('PASS: initial and live Python styling, pixels, SVG parity, time buckets, validation, late tabs, panel');
} finally { await browser?.close(); child.kill('SIGTERM'); }
