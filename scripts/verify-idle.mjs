// Render-on-demand: the frame loop must stop scheduling itself once a converged, unchanging graph has nothing left
// to draw, and must wake back up on the things that actually change what's on screen (style, hover, wheel, drag).
// This is a real-browser check (a real requestAnimationFrame clock), not a unit test, because the mechanism it
// verifies -- the loop actually stopping -- only exists once the browser drives real animation frames.
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
for i in range(40): g.add_node(i)
for i in range(40): g.add_edge(i, (i + 1) % 40)
h = show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=20)
print(json.dumps({'url': h.url}), flush=True)
try: threading.Event().wait()
finally: h.close()
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/packages/core:${root}/packages/bridge` }, stdio: ['ignore', 'pipe', 'pipe'] });
let browser;
try {
  let stderr = ''; server.stderr.on('data', d => stderr += d);
  const url = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('Bridge startup timeout: ' + stderr)), 15000);
    const lines = createInterface({ input: server.stdout });
    lines.on('line', line => { try { const msg = JSON.parse(line); if (msg.url) { clearTimeout(timer); resolve(msg.url); } } catch {} });
    server.on('exit', code => { clearTimeout(timer); reject(Error(`Bridge exited ${code}: ${stderr}`)); });
  });
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto(url);
  await page.waitForFunction(() => window.__plexgraphHandle?.getVisibleNodeCount?.() === 40);

  // Count real rAF callbacks fired on the page (not just this renderer's), over fixed windows, via the browser's own
  // clock -- this is what "the loop stopped" or "the loop is running" actually means, observed from the outside.
  const countRafOver = (ms) => page.evaluate((ms) => new Promise((resolve) => {
    let n = 0;
    const tick = () => { n++; requestAnimationFrame(tick); };
    const id = requestAnimationFrame(tick);
    setTimeout(() => { cancelAnimationFrame(id); resolve(n); }, ms);
  }), ms);
  // this counts ALL rAF activity on the page (including the probe's own tick), so it is a baseline, not a
  // renderer-specific count; what matters is the DIFFERENCE once the renderer is confirmed idle vs active below.

  await page.waitForTimeout(2000);  // let the 20-iteration layout stream finish and settle
  const idleFor = await page.evaluate(() => new Promise((resolve) => {
    let stillScheduled = false;
    const orig = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { stillScheduled = true; return orig(cb); };
    setTimeout(() => { window.requestAnimationFrame = orig; resolve(stillScheduled); }, 600);
  }));
  assert.equal(idleFor, false, 'requestAnimationFrame was still being called 600ms after the graph settled -- the loop never went idle');
  console.log('ok: the loop goes idle once the graph settles (no requestAnimationFrame calls for 600ms)');

  // A style change must wake it (one or more frames), then it must go idle again.
  const wokeForStyle = await page.evaluate(() => new Promise((resolve) => {
    let called = false;
    const orig = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { called = true; return orig(cb); };
    window.__plexgraphHandle.setNodeColorBy(null);  // a real style call, not a synthetic poke
    setTimeout(() => { window.requestAnimationFrame = orig; resolve(called); }, 200);
  }));
  assert.ok(wokeForStyle, 'a style change did not wake the loop');
  await page.waitForTimeout(700);
  let stillIdle = await page.evaluate(() => new Promise((resolve) => {
    let called = false;
    const orig = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { called = true; return orig(cb); };
    setTimeout(() => { window.requestAnimationFrame = orig; resolve(called); }, 400);
  }));
  assert.equal(stillIdle, false, 'the loop did not settle back to idle after the style change');
  console.log('ok: a style change wakes the loop, which settles back to idle');

  // A wheel/zoom event (handled by Camera, not the renderer's own listeners) must also wake it. The override has to
  // be installed BEFORE the event, since wake() calls requestAnimationFrame synchronously from the event handler.
  await page.mouse.move(500, 350);
  await page.evaluate(() => {
    window.__rafCalled = false;
    window.__origRaf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { window.__rafCalled = true; return window.__origRaf(cb); };
  });
  await page.mouse.wheel(0, -100);
  await page.waitForTimeout(100);
  const wokeForWheel = await page.evaluate(() => { window.requestAnimationFrame = window.__origRaf; return window.__rafCalled; });
  assert.ok(wokeForWheel, 'a wheel/zoom event did not wake the loop');
  console.log('ok: a wheel/zoom event wakes the loop');

  // Drag-panning goes through a different path: Camera's own pointermove listener moves the camera, and the
  // renderer's separate pointermove listener sets pointerDirty -- both fire off the same native events, with no
  // onChange callback involved for this one. It must wake the loop too, and settle back to idle once released.
  await page.mouse.move(500, 350);
  await page.evaluate(() => {
    window.__rafCount = 0;
    window.__origRaf2 = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { window.__rafCount++; return window.__origRaf2(cb); };
  });
  await page.mouse.down();
  await page.mouse.move(560, 380, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(100);
  const dragWoke = await page.evaluate(() => { window.requestAnimationFrame = window.__origRaf2; return window.__rafCount; });
  assert.ok(dragWoke > 0, 'a drag-pan did not wake the loop');
  console.log('ok: drag-panning wakes the loop (' + dragWoke + ' frame(s))');
  await page.waitForTimeout(700);
  const idleAfterDrag = await page.evaluate(() => new Promise((resolve) => {
    let called = false;
    const orig = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { called = true; return orig(cb); };
    setTimeout(() => { window.requestAnimationFrame = orig; resolve(called); }, 400);
  }));
  assert.equal(idleAfterDrag, false, 'the loop did not settle back to idle after the drag ended');
  console.log('ok: the loop settles back to idle after a drag ends');

  // A real window resize must also wake an idle renderer: loop() only notices its canvas's drawing-buffer size by
  // polling at its own top, which does not run while idle, so nothing else would ever catch a resize that happens
  // after the graph settled -- the canvas (and the camera's aspect ratio) would silently stay at the old size.
  const before = await page.evaluate(() => { const c = document.getElementById('canvas'); return { w: c.width, h: c.height }; });
  await page.setViewportSize({ width: 700, height: 500 });
  await page.waitForTimeout(500);
  const after = await page.evaluate(() => { const c = document.getElementById('canvas'); return { w: c.width, h: c.height }; });
  assert.notDeepEqual(before, after, 'the canvas did not resize after a real window resize: ' + JSON.stringify({ before, after }));
  const glViewport = await page.evaluate(() => {
    const c = document.getElementById('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    return Array.from(gl.getParameter(gl.VIEWPORT));
  });
  assert.deepEqual(glViewport.slice(2), [after.w, after.h],
    'the WebGL viewport does not match the new canvas size -- the renderer never redrew after the resize: ' + JSON.stringify({ glViewport, after }));
  console.log('ok: a window resize wakes the idle renderer, which redraws at the new size');

  await page.screenshot({ path: '/tmp/plexgraph-idle.png' });
  assert.deepEqual(errors, [], 'no page errors');
  console.log('PASS: render-on-demand -- the loop idles when settled and wakes on style changes and camera input');
} finally {
  try { await browser?.close(); } catch {}
  server.kill('SIGTERM');
}
