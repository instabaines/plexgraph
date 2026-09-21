// End-to-end: real Python bridge + built app. Exercises style, filter, select/path and zoom controls.
// PYTHON_PATH must have NumPy, msgpack and websockets installed. Build the app first.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('..', import.meta.url));
const server = spawn(process.env.PYTHON_PATH || 'python3', ['-u', '-c', `
import json, threading
from hyperloom_core import Graph
from hyperloom_bridge import show
g = Graph()
for i in range(60): g.add_node(i, team=['red','green','blue'][i % 3], score=i * 1.5)
for i in range(49): g.add_edge(i, i + 1)
for hub in (10, 20, 30): g.add_edge(0, hub)
g.add_edge(55, 56)
h = show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=80)
print(json.dumps({'url': h.url}), flush=True)
try: threading.Event().wait()
finally: h.close()
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/packages/core:${root}/packages/bridge` }, stdio: ['ignore', 'pipe', 'pipe'] });
let browser;
try {
  let stderr = ''; server.stderr.on('data', d => stderr += d);
  const url = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(Error('bridge startup timeout: ' + stderr)), 20000);
    createInterface({ input: server.stdout }).on('line', l => { try { const m = JSON.parse(l); if (m.url) { clearTimeout(t); resolve(m.url); } } catch {} });
    server.on('exit', c => { clearTimeout(t); reject(Error(`bridge exited ${c}: ${stderr}`)); });
  });
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message)); page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  const H = fn => page.evaluate(fn);
  await page.waitForFunction(() => window.__hyperloomHandle?.getVisibleNodeCount?.() === 60 && window.__hyperloomHandle.getNodeAttributes().length === 2);
  await page.waitForTimeout(1500);

  // Style: colour by a categorical attribute produces a legend; size by degree is recorded.
  await page.getByLabel('Color by').selectOption('team');
  await page.waitForFunction(() => document.querySelectorAll('#node-color-legend .row').length === 3);
  await page.getByLabel('Size by').selectOption('degree');
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleNodeCount()), 60);
  assert.deepEqual(await page.getByLabel('Size by').locator('option').allTextContents(), ['Uniform', 'Degree', 'score']);
  await page.getByLabel('Size by').selectOption('');   // large marks overlap on this tight chain, and the one on top would take the click

  // Filter by categorical value: 20 nodes are red; edges need both endpoints visible.
  await page.getByLabel('Filter attribute').selectOption('team');
  await page.locator('#tools .values label', { hasText: 'red' }).locator('input').check();
  await page.waitForFunction(() => window.__hyperloomHandle.getVisibleNodeCount() === 20);
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleEdgeCount()), 1, 'red nodes are 0,3,6,...; only the hub link 0-30 joins two of them');
  // Filter by degree: hub 0 (deg 4) and 10/20/30 (deg 3) have degree >= 3.
  await page.getByRole('button', { name: 'Reset filter' }).click();
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleNodeCount()), 60);
  await page.getByLabel('Minimum degree').fill('3'); await page.getByLabel('Minimum degree').dispatchEvent('change');
  await page.waitForFunction(() => window.__hyperloomHandle.getVisibleNodeCount() === 4);
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleEdgeCount()), 3);
  // Numeric range filter on score (0..88.5): keep score <= 15 -> nodes 0..10.
  await page.getByLabel('Minimum degree').fill('0'); await page.getByLabel('Minimum degree').dispatchEvent('change');
  await page.getByLabel('Filter attribute').selectOption('score');
  await page.getByLabel('score maximum').fill('15'); await page.getByLabel('score maximum').dispatchEvent('change');
  await page.waitForFunction(() => window.__hyperloomHandle.getVisibleNodeCount() === 11);
  await page.getByRole('button', { name: 'Reset filter' }).click();
  await page.waitForFunction(() => window.__hyperloomHandle.getVisibleNodeCount() === 60);

  // Select two nodes by clicking them on the canvas, then find the route between them.
  const clickNode = async id => {
    const [x, y] = await page.evaluate(i => window.__hyperloomHandle.getNodeScreenPosition(i), id);
    const box = await page.locator('canvas#canvas').boundingBox();
    await page.mouse.move(box.x + x, box.y + y); await page.waitForTimeout(150);
    await page.mouse.click(box.x + x, box.y + y); await page.waitForTimeout(150);
  };
  await clickNode(5); await clickNode(25);
  await page.getByRole('button', { name: 'Unselect 5' }).waitFor();
  await page.getByRole('button', { name: 'Unselect 25' }).waitFor();
  await page.getByRole('button', { name: 'Find path' }).click();
  await page.getByText('11 hops:', { exact: false }).waitFor();
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleNodeCount()), 12);
  await page.getByRole('button', { name: 'Show all nodes' }).click();
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleNodeCount()), 60);
  // Unreachable pair reports honestly and leaves the graph as it was.
  await page.getByRole('button', { name: 'Clear selection' }).click();
  await H(() => window.__hyperloomHandle.focusNeighborhood(null));
  await clickNode(5); await clickNode(59);
  await page.getByRole('button', { name: 'Find path' }).click();
  await page.getByText('No route between 5 and 59.').waitFor();
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleNodeCount()), 60);

  // Zoom buttons scale distances between nodes by 1.5.
  const gap = async () => { const [a, b] = await page.evaluate(() => [0, 20].map(i => window.__hyperloomHandle.getNodeScreenPosition(i))); return Math.hypot(a[0] - b[0], a[1] - b[1]); };
  const before = await gap();
  await page.getByRole('button', { name: 'Zoom in' }).click(); await page.waitForTimeout(100);
  assert.ok(Math.abs(await gap() / before - 1.5) < 0.02, 'zoom in should scale by 1.5');
  await page.getByRole('button', { name: 'Zoom out' }).click(); await page.waitForTimeout(100);
  assert.ok(Math.abs(await gap() / before - 1) < 0.02, 'zoom out should restore the scale');
  // Selection also works from search results (needed where nodes are not individually clickable).
  await page.getByRole('button', { name: 'Clear selection' }).click();
  await page.getByLabel('Find a node').fill('37');
  await page.getByRole('button', { name: 'Select 37', exact: true }).click();
  await page.getByRole('button', { name: 'Unselect 37' }).waitFor();
  await page.getByRole('button', { name: 'Show neighbourhood' }).click();
  await page.getByText('Showing 37 and its neighbours').waitFor();
  assert.equal(await H(() => window.__hyperloomHandle.getVisibleNodeCount()), 3);
  await page.getByRole('button', { name: 'Show all nodes' }).click();
  await page.screenshot({ path: '/tmp/hyperloom-tools.png' });
  assert.deepEqual(errors, []);
  console.log('PASS: colour/size by attribute, value/range/degree filters, click-select, shortest path, unreachable path, zoom, search-select');
} finally { await browser?.close(); server.kill('SIGTERM'); }
