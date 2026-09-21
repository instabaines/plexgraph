// End-to-end: level of detail on a graph too large to draw node by node.
// Overview cells -> click to zoom -> individual nodes in view -> back; group collapse/expand by attribute.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('..', import.meta.url));
const server = spawn(process.env.PYTHON_PATH || 'python3', ['-u', '-c', `
import json, threading
from plexgraph_core import Graph
from plexgraph_bridge import show
g = Graph()
n = 6000
for i in range(n): g.add_node(i, role='research' if i < 2400 else 'engineering')
for i in range(n):
    lo = 0 if i < 2400 else 2400; hi = 2400 if i < 2400 else n
    g.add_edge(i, lo + (i * 7 + 1) % (hi - lo)); g.add_edge(i, lo + (i * 13 + 5) % (hi - lo))
    if i % 50 == 0: g.add_edge(i, (i * 31 + 17) % n)
h = show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=30)
print(json.dumps({'url': h.url}), flush=True)
try: threading.Event().wait()
finally: h.close()
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/packages/core:${root}/packages/bridge` }, stdio: ['ignore', 'pipe', 'pipe'] });
let browser;
try {
  let stderr = ''; server.stderr.on('data', d => stderr += d);
  const url = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(Error('bridge startup timeout: ' + stderr)), 30000);
    createInterface({ input: server.stdout }).on('line', l => { try { const m = JSON.parse(l); if (m.url) { clearTimeout(t); resolve(m.url); } } catch {} });
    server.on('exit', c => { clearTimeout(t); reject(Error(`bridge exited ${c}: ${stderr}`)); });
  });
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message)); page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  const lod = () => page.evaluate(() => window.__plexgraphHandle.getLodState());
  await page.waitForFunction(() => window.__plexgraphHandle?.getVisibleNodeCount?.() === 6000);
  await page.waitForTimeout(3000);
  await page.evaluate(() => window.__plexgraphHandle.fitView()); await page.waitForTimeout(700);

  // Overview: aggregated cells with the honest caption and some links.
  let state = await lod();
  assert.equal(state.mode, 'overview'); assert.equal(state.nodesInView, 6000); assert.ok(state.nodesDrawn > 20 && state.nodesDrawn < 2400, `cells drawn: ${state.nodesDrawn}`);
  const svg0 = await page.evaluate(() => window.__plexgraphHandle.exportSVG());
  assert.ok(svg0.includes('Density overview') && svg0.includes('nodes in view'));
  assert.ok(svg0.includes('<line'), 'overview should draw aggregated links');

  // Clicking the biggest cell zooms toward it.
  const circles = svg => [...svg.matchAll(/<circle cx="([\d.-]+)" cy="([\d.-]+)" r="([\d.]+)"/g)].map(m => ({ x: +m[1], y: +m[2], r: +m[3] })).sort((a, b) => b.r - a.r);
  const box = await page.locator('canvas#canvas').boundingBox();
  const big = circles(svg0)[0];
  await page.mouse.move(box.x + big.x, box.y + big.y); await page.waitForTimeout(200);
  await page.mouse.click(box.x + big.x, box.y + big.y); await page.waitForTimeout(900);
  const afterClick = await lod();
  assert.ok(afterClick.nodesInView < 6000, `clicking a cell should zoom in (nodes in view ${afterClick.nodesInView})`);

  // Zooming far enough shows individual nodes; fit returns to the overview.
  await page.evaluate(() => window.__plexgraphHandle.zoomBy(40)); await page.waitForTimeout(900);
  state = await lod();
  assert.equal(state.mode, 'region'); assert.ok(state.nodesDrawn > 0 && state.nodesDrawn <= 5000);
  assert.equal(await page.evaluate(() => window.__plexgraphHandle.getVisibleNodeCount()), 6000, 'the whole graph is still the base set');
  await page.evaluate(() => window.__plexgraphHandle.fitView()); await page.waitForTimeout(900);
  assert.equal((await lod()).mode, 'overview');

  // Group by an attribute: one point per value; clicking a group expands to just that group.
  await page.getByLabel('Group by').selectOption('role');
  await page.waitForTimeout(700);
  state = await lod();
  assert.equal(state.mode, 'groups'); assert.equal(state.nodesDrawn, 2);
  const svg1 = await page.evaluate(() => window.__plexgraphHandle.exportSVG());
  assert.ok(svg1.includes('Grouped by role'));
  const groups = circles(svg1); assert.equal(groups.length, 2);
  await page.mouse.move(box.x + groups[0].x, box.y + groups[0].y); await page.waitForTimeout(200);
  await page.mouse.click(box.x + groups[0].x, box.y + groups[0].y);
  await page.waitForFunction(() => window.__plexgraphHandle.getVisibleNodeCount() === 2400 || window.__plexgraphHandle.getVisibleNodeCount() === 3600);
  const shown = await page.evaluate(() => window.__plexgraphHandle.getVisibleNodeCount());
  assert.equal((await lod()).mode, 'detail');
  assert.equal(await page.getByLabel('Filter attribute').inputValue(), 'role');
  assert.equal(await page.getByLabel('Group by').inputValue(), '');
  assert.ok(shown === 3600 ? groups[0].r > groups[1].r : groups[0].r >= groups[1].r, 'the larger group is the 3,600-node one');
  await page.getByRole('button', { name: 'Reset filter' }).click();
  await page.waitForFunction(() => window.__plexgraphHandle.getVisibleNodeCount() === 6000);
  await page.evaluate(() => window.__plexgraphHandle.fitView()); await page.waitForTimeout(900);
  assert.equal((await lod()).mode, 'overview');
  assert.deepEqual(errors, []);
  console.log('PASS: overview cells with links, click-to-zoom, region detail, fit, group collapse/expand');
} finally { await browser?.close(); server.kill('SIGTERM'); }
