// End-to-end: a contact sequence (`u v t` text file) loaded with read_temporal_edgelist and scrubbed in the viewer.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('..', import.meta.url));
async function scenario(style) {
const server = spawn(process.env.PYTHON_PATH || 'python3', ['-u', '-c', `
import json, tempfile, threading, os
from plexgraph_core import read_temporal_edgelist
from plexgraph_bridge import show
# One contact per integer time 0..99 between 20 people, in the plain "u v t" format datasets ship in.
path = os.path.join(tempfile.mkdtemp(), 'contacts.txt')
import datetime as dt
style = os.environ['STYLE']
base = dt.datetime(2020, 1, 1)
with open(path, 'w') as f:
    f.write('# u v t\\n')
    for t in range(100):
        stamp = (str(t) if style == 'numeric' else str(round(100 * (t / 100) ** 2, 4)) if style == 'skew'
                 else (base + dt.timedelta(days=t)).strftime('%Y-%m-%d %H:%M:%S'))
        f.write(f'{t % 20} {(3 * t + 1) % 20} {stamp}\\n')
g = read_temporal_edgelist(path)
h = show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=40)
print(json.dumps({'url': h.url}), flush=True)
try: threading.Event().wait()
finally: h.close()
`], { cwd: root, env: { ...process.env, STYLE: style, PYTHONPATH: `${root}/packages/core:${root}/packages/bridge` }, stdio: ['ignore', 'pipe', 'pipe'] });
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
  const edges = () => page.evaluate(() => window.__plexgraphHandle.getVisibleEdgeCount());
  const at = t => style === 'iso' ? 1577836800 + t * 86400 : t;   // 2020-01-01 UTC plus t days
  const setTime = async t => { await page.evaluate(v => { const s = document.getElementById('timeline-slider'); s.value = String(v); s.dispatchEvent(new Event('input', { bubbles: true })); }, t); await page.waitForTimeout(150); };
  await page.waitForFunction(() => window.__plexgraphHandle?.getVisibleEdgeCount?.() === 100);
  await page.getByLabel('Time selection').waitFor();

  if (style === 'skew') {
    // Activity that thins out over time: equal-time buckets are lopsided, equal-events buckets are not.
    const counts = async () => (await page.locator('#stack-legend .count').allTextContents()).map(Number);
    await page.getByRole('button', { name: 'Time ribbon' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#stack-legend .count').length === 6);
    let c = await counts();
    assert.equal(c.reduce((a, b) => a + b, 0), 100, 'every event appears in exactly one bucket');
    await page.getByLabel('Number of time buckets').fill('4'); await page.getByLabel('Number of time buckets').dispatchEvent('change');
    await page.waitForFunction(() => document.querySelectorAll('#stack-legend .count').length === 4);
    c = await counts();
    assert.equal(c.reduce((a, b) => a + b, 0), 100);
    assert.ok(Math.max(...c) >= 3 * Math.min(...c), `equal time should be lopsided for skewed data: ${c}`);
    await page.getByLabel('How to split time').selectOption('events');
    await page.waitForTimeout(600);
    c = await counts();
    assert.equal(c.length, 4); assert.equal(c.reduce((a, b) => a + b, 0), 100);
    assert.ok(c.every(n => n >= 24 && n <= 26), `equal events should be balanced: ${c}`);
    assert.deepEqual(errors, []);
    console.log(`PASS (skew): ribbon bucket count and equal-time vs equal-events split (${c})`);
    await browser.close(); server.kill('SIGTERM'); return;
  }

  // A contact sequence defaults to a trailing window, because point events are otherwise invisible between their instants.
  assert.equal(await page.getByLabel('Time selection').inputValue(), 'window');
  await page.getByRole('button', { name: 'All time', exact: true }).click();
  await page.getByLabel('Window length').fill('10');
  await setTime(at(50));
  assert.equal(await edges(), 11, 'window [40, 50] holds the events at t = 40..50');
  assert.match(await page.locator('#timeline-value').textContent(), style === 'iso' ? /^2020-02-20 00:00 \(last 10 d\)$/ : /^50 \(last 10\)$/);
  if (style === 'iso') assert.equal((await page.locator('#timeline-window-unit').textContent()).trim(), 'days', 'calendar windows are set in days');
  await setTime(at(30));
  assert.equal(await edges(), 11);

  await page.getByLabel('Time selection').selectOption('instant');
  await setTime(at(50));
  assert.equal(await edges(), 1, 'exactly one event happens at t = 50');
  await setTime(at(50.5));
  assert.equal(await edges(), 0, 'between events an instant view is empty; this is why the window mode exists');

  await page.getByLabel('Time selection').selectOption('cumulative');
  await setTime(at(50));
  assert.equal(await edges(), 51, 'events at t = 0..50');
  await setTime(at(99));
  assert.equal(await edges(), 100);

  await page.getByRole('button', { name: 'Show all', exact: true }).click();
  assert.equal(await edges(), 100, 'turning the filter off restores every event');

  // The ribbon renders the whole timeline; every panel is built without error.
  await page.getByRole('button', { name: 'Time ribbon' }).click();
  await page.waitForTimeout(600);
  if (style === 'iso') assert.match(await page.locator('#stack-legend').textContent(), /2020-01-01 00:00 → 2020-01-\d\d/, 'ribbon buckets are labelled with dates');
  const bucketCounts = (await page.locator('#stack-legend .count').allTextContents()).map(Number);
  assert.equal(bucketCounts.reduce((a, b) => a + b, 0), 100, `ribbon buckets should partition the events: ${bucketCounts}`);
  await page.screenshot({ path: `/tmp/plexgraph-temporal-${style}.png` });
  assert.deepEqual(errors, []);
  console.log(`PASS (${style}): u v t file loads; window / instant / cumulative time views; ribbon`);
} finally { await browser?.close(); server.kill('SIGTERM'); }
}

await scenario('numeric');
await scenario('iso');
await scenario('skew');
