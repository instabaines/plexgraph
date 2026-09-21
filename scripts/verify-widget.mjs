// End-to-end for the notebook widget: a real JupyterLab, a real kernel and a real browser. A first cell calls show() and
// the viewer must render inside the widget's iframe; a second cell changes the style live and the pixels must change; the
// page is then reloaded and the viewer must come back. Nothing may listen on a port for the viewer.
//   PYTHON_PATH=/path/to/venv/bin/python node scripts/verify-widget.mjs      (the venv needs jupyterlab and anywidget)
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import assert from 'node:assert/strict';

const python = process.env.PYTHON_PATH || 'python3';
const jupyter = /[\\/]/.test(python) ? join(dirname(python), process.platform === 'win32' ? 'jupyter.exe' : 'jupyter') : 'jupyter'; // a bare `python` means the one on PATH
const dir = mkdtempSync(join(tmpdir(), 'plexgraph-widget-'));
const port = 8900 + Math.floor(Math.random() * 500), token = 'verify';

const cell = source => ({ cell_type: 'code', metadata: {}, execution_count: null, outputs: [], source, id: Math.random().toString(16).slice(2, 10) });
writeFileSync(join(dir, 'widget.ipynb'), JSON.stringify({
  cells: [
    cell(`import json
import plexgraph as pg
g = pg.Graph()
for i in range(60): g.add_node(i, team=['a', 'b', 'c'][i % 3])
for i in range(60):
    g.add_edge(i, (i + 1) % 60)
    g.add_edge(i, (i * 7 + 3) % 60)
h = pg.show(g, seed=0, layout_iterations=30, node_color=pg.by_attribute('team', palette='tab10'), node_size=12, return_handle=True)`),
    cell(`h.style(node_color='crimson')`),
    cell(`print('PORTS', h.ws_port, h.http_port, h.url, type(h.widget).__name__)
print('DIAGNOSTICS', json.dumps(h.diagnostics()))`),
    cell(`h.close()`),
  ],
  metadata: { kernelspec: { name: 'python3', display_name: 'Python 3', language: 'python' } }, nbformat: 4, nbformat_minor: 5,
}));

const lab = spawn(jupyter, ['lab', '--no-browser', `--port=${port}`, '--ServerApp.port_retries=0', `--ServerApp.token=${token}`,
  `--ServerApp.root_dir=${dir}`, '--expose-app-in-browser', '--ServerApp.disable_check_xsrf=True'], { stdio: ['ignore', 'pipe', 'pipe'] });
let labLog = ''; lab.stdout.on('data', d => labLog += d); lab.stderr.on('data', d => labLog += d);
const base = `http://localhost:${port}`;
let browser;
const stop = async () => { try { await browser?.close(); } catch {} lab.kill('SIGTERM'); };
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch(`${base}/api/status?token=${token}`)).ok) break; } catch {}
    if (i > 120) throw new Error('JupyterLab did not start:\n' + labLog.slice(-1500));
    await new Promise(r => setTimeout(r, 250));
  }
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1300, height: 950 }, deviceScaleFactor: Number(process.env.DPR || 1) });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // JupyterLab unloads the output of a cell that scrolls out of view, which rebuilds the widget (and is exercised on
  // purpose by the reload step below). For the live-update steps the outputs are kept in place.
  const setting = await fetch(`${base}/lab/api/settings/@jupyterlab/notebook-extension:tracker?token=${token}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raw: JSON.stringify({ windowingMode: 'none' }) }) });
  assert.ok(setting.ok, 'could not set the notebook windowing mode: ' + setting.status);
  const open = async () => {
    await page.goto(`${base}/lab/tree/widget.ipynb?token=${token}&reset`);
    await page.waitForFunction(() => window.jupyterapp?.commands && document.querySelector('.jp-Notebook'), null, { timeout: 60000 });
    await page.waitForTimeout(2500);
  };
  const runCell = () => page.evaluate(() => window.jupyterapp.commands.execute('notebook:run-cell-and-select-next'));
  const viewerFrame = async () => {
    for (let i = 0; i < 240; i++) {
      // the widget's iframe is the one frame that holds the viewer (its address is not one Playwright reports consistently)
      for (const fr of page.frames()) {
        if (fr === page.mainFrame()) continue;
        if (await fr.evaluate(() => Boolean(window.__plexgraphHandle)).catch(() => false)) return fr;
      }
      await page.waitForTimeout(250);
    }
    const outputs = (await page.locator('.jp-OutputArea').allTextContents()).map(t => t.slice(0, 1200));
    await page.screenshot({ path: join(tmpdir(), 'plexgraph-widget-failure.png') });
    throw new Error('the widget did not create a viewer iframe. Cell output: ' + JSON.stringify(outputs) + ' | page errors: ' + JSON.stringify(errors));
  };
  const ready = async frame => {
    await frame.waitForFunction(() => window.__plexgraphHandle?.getVisibleNodeCount?.() === 60, null, { timeout: 60000 });
    await frame.waitForTimeout(3500);
  };
  const colorAt = (frame, id) => frame.evaluate(i => {
    const h = window.__plexgraphHandle, c = document.getElementById('canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
    const [x, y] = h.getNodeScreenPosition(i), dpr = c.width / c.clientWidth;
    const px = new Uint8Array(4); gl.readPixels(Math.round(x * dpr), Math.round(c.height - y * dpr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    return Array.from(px.slice(0, 3));
  }, id);
  const near = (a, b, tol = 40) => a.every((v, i) => Math.abs(v - b[i]) <= tol);
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const share = async (frame, expected) => {
    const ids = Array.from({ length: 60 }, (_, i) => i), colors = await Promise.all(ids.map(i => colorAt(frame, i)));
    return colors.filter((c, i) => near(c, expected(i))).length / 60;
  };
  const tab10 = ['#1f77b4', '#ff7f0e', '#2ca02c'].map(hex);

  // 1. show() in a notebook renders the viewer inside the widget, with the style it was given.
  await open();
  await runCell();
  let frame = await viewerFrame();
  await ready(frame);
  {
    const sample = await Promise.all([0, 1, 2, 3, 4, 5].map(i => colorAt(frame, i)));
    const shareNow = await share(frame, i => tab10[i % 3]);
    assert.ok(shareNow >= 0.6, `the nodes are drawn in their team colours (share ${shareNow}; nodes 0-5 read ${JSON.stringify(sample)}, expected ${JSON.stringify([0, 1, 2, 0, 1, 2].map(i => tab10[i]))})`);
  }
  const info = await frame.evaluate(() => ({ nodes: window.__plexgraphHandle.getVisibleNodeCount(), status: document.getElementById('status')?.textContent || '' }));
  assert.equal(info.nodes, 60); assert.ok(!/disconnected|error/.test(info.status), `status: ${info.status}`);
  console.log('ok: the viewer renders in the widget with the given style');

  // 2. A change made from another cell reaches the open viewer.
  await runCell();
  await frame.waitForFunction(() => window.__plexgraphHandle.getStyle().node?.color !== undefined, null, { timeout: 30000 });
  await frame.waitForTimeout(1500);
  assert.ok(await share(frame, () => hex('#dc143c')) >= 0.6, 'the nodes turned crimson');
  console.log('ok: a style change from Python arrives live');

  // 3. There is no port for the viewer: no address, no server.
  await runCell();
  const printed = await page.locator('.jp-OutputArea-output pre').filter({ hasText: 'PORTS' }).first().textContent({ timeout: 30000 });
  assert.match(printed, /PORTS None None None GraphWidget/, printed);
  console.log('ok: the widget uses no port');
  // ...and the viewer has told the kernel what it sees, so a misbehaving one can be diagnosed from Python.
  const reported = JSON.parse((await page.locator('.jp-OutputArea-output pre').filter({ hasText: 'DIAGNOSTICS' }).first().textContent()).split('DIAGNOSTICS')[1]);
  assert.equal(reported.state.graph.nodes, 60, 'the viewer reported its graph');
  assert.ok(reported.state.canvas.pixels[0] > 100 && reported.state.canvas.pixels[1] > 100, 'the viewer reported a real canvas size');
  assert.equal(reported.state.gl.lost, false); assert.deepEqual(reported.errors, []);
  assert.ok(reported.state.gl.viewport[2] > 100, 'and a viewport that matches it: ' + JSON.stringify(reported.state.gl.viewport));
  console.log('ok: the viewer reports its state to the kernel');

  // 4. Reloading the page brings the viewer back. The kernel is still running with the graph and its style, so the
  // widget only has to ask it to stream again. (The notebook is saved first: JupyterLab redraws outputs from the file.)
  await page.evaluate(() => window.jupyterapp.commands.execute('docmanager:save'));
  await page.waitForTimeout(1500);
  await page.goto(`${base}/lab/tree/widget.ipynb?token=${token}`);
  await page.waitForFunction(() => window.jupyterapp?.commands && document.querySelector('.jp-Notebook'), null, { timeout: 60000 });
  frame = await viewerFrame();
  await ready(frame);
  assert.ok(await share(frame, () => hex('#dc143c')) >= 0.6, 'after a reload the viewer is back, with the live style');
  console.log('ok: the viewer survives a page reload');

  // 5. Closing a viewer stops it updating but leaves its picture in the cell (a notebook that closes the previous viewer
  // when it shows the next must not lose the earlier ones).
  await page.evaluate(() => { const nb = window.jupyterapp.shell.currentWidget.content; nb.activeCellIndex = 3; return window.jupyterapp.commands.execute('notebook:run-cell'); });
  await frame.waitForFunction(() => /closed/.test(document.getElementById('status')?.textContent || ''), null, { timeout: 30000 });
  assert.equal(await frame.evaluate(() => window.__plexgraphHandle.getVisibleNodeCount()), 60, 'the closed viewer still holds its graph');
  assert.ok(await share(frame, () => hex('#dc143c')) >= 0.6, 'and still shows it');
  console.log('ok: a closed viewer keeps its picture');

  assert.deepEqual(errors.filter(e => !/ResizeObserver|favicon/.test(e)), [], 'no page errors');
  console.log('PASS: notebook widget renders, restyles live, uses no port, survives a reload, and keeps its picture when closed');
} catch (error) {
  console.log('FAIL:', error.message, '\n--- jupyter log tail ---\n' + labLog.slice(-1200));
  process.exitCode = 1;
} finally {
  await stop();
}
