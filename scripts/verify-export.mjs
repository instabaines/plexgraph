// End-to-end check of ShowHandle.export()/.save(): a Python session asks the real, connected browser viewer to
// export itself (over the same WebSocket the graph/style traffic already uses) and gets the SVG/PNG bytes back --
// no click, no GUI. Covers both directions of the new request/response wire messages (export_request, going out
// the same way style pushes already do; export_response, the first message type a viewer ever sends back).
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('..', import.meta.url));
const workdir = mkdtempSync(join(tmpdir(), 'plexgraph-verify-export-'));

const child = spawn(process.env.PYTHON_PATH || 'python3', ['-u', '-c', `
import json, sys
from plexgraph_bridge import show
from plexgraph_core import Graph
g = Graph()
for i in range(20): g.add_node(i, team=['red', 'green'][i % 2])
for i in range(20): g.add_edge(i, (i + 1) % 20)
handle = show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=30,
              node_color='crimson')
print(json.dumps({'url': handle.url}), flush=True)
env = dict(globals())
for line in sys.stdin:
    request = json.loads(line)
    try:
        env['__result__'] = None
        exec(request['code'], env)
        print(json.dumps({'ok': True, 'result': env.get('__result__')}), flush=True)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}), flush=True)
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/packages/core:${root}/packages/bridge` }, stdio: ['pipe', 'pipe', 'pipe'] });
let stderr = ''; child.stderr.on('data', d => stderr += d);
const lines = createInterface({ input: child.stdout });
const waiting = []; let url;
lines.on('line', l => { let m; try { m = JSON.parse(l); } catch { return; } if (m.url) url = m.url; else waiting.shift()?.(m); });
const py = code => new Promise(res => { waiting.push(res); child.stdin.write(JSON.stringify({ code }) + '\n'); });
const pyOk = async code => { const r = await py(code); assert.ok(r.ok, `python failed: ${r.error}\n${stderr.slice(-400)}`); return r.result; };
const pyFails = async (code, pattern) => { const r = await py(code); assert.ok(!r.ok, 'expected python to raise'); assert.match(r.error, pattern); };
await new Promise((res, rej) => { const t = setTimeout(() => rej(Error('python did not start: ' + stderr)), 30000); const poll = setInterval(() => { if (url) { clearTimeout(t); clearInterval(poll); res(); } }, 50); });

let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message)); page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  await page.waitForFunction(() => window.__plexgraphHandle?.getVisibleNodeCount?.() === 20);
  await page.waitForTimeout(1500); // let the layout settle so exportSVG() has real positions, not the initial scatter

  // 1. export("svg") returns the same vector geometry the "Export ▾" button produces.
  const viaHandle = await page.evaluate(() => window.__plexgraphHandle.exportSVG());
  const svg = await pyOk("__result__ = handle.export('svg')");
  assert.ok(svg.startsWith('<svg'), 'export("svg") returns real SVG text');
  assert.equal((svg.match(/<circle /g) || []).length, 20, 'one <circle> per node, same as the button’s export');
  assert.equal(svg, viaHandle, 'export("svg") is byte-identical to the viewer’s own exportSVG()');

  // 2. export("png") returns real PNG bytes (a raster capture of the canvas, the same path the button’s PNG export uses).
  const pngB64 = await pyOk("import base64; __result__ = base64.b64encode(handle.export('png')).decode()");
  const png = Buffer.from(pngB64, 'base64');
  assert.deepEqual(png.subarray(0, 8), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), 'a real PNG signature');
  assert.ok(png.length > 500, `a real capture, not a blank/tiny image (${png.length} bytes)`);

  // 3. save() writes the file directly -- no separate export() call needed.
  const svgPath = join(workdir, 'graph.svg');
  await pyOk(`handle.save(${JSON.stringify(svgPath)})`); // format guessed from the .svg extension
  assert.ok(existsSync(svgPath));
  assert.equal(readFileSync(svgPath, 'utf8'), svg, 'save() writes exactly what export() returns');

  const pngPath = join(workdir, 'graph.png');
  await pyOk(`handle.save(${JSON.stringify(pngPath)}, format='png')`); // format given explicitly, extension ignored
  assert.deepEqual(readFileSync(pngPath).subarray(0, 8), png.subarray(0, 8));

  // 4. mistakes are caught in Python, clearly, before anything is sent to the browser.
  await pyFails("handle.export('bmp')", /format must be 'svg' or 'png'/);
  await pyFails(`handle.save(${JSON.stringify(join(workdir, 'graph.bmp'))})`, /can't tell the format/);

  // 5. a late tab (opened after the graph settled) can export too -- exercises a second, independent connection.
  const late = await browser.newPage({ viewport: { width: 700, height: 500 } });
  await late.goto(url);
  await late.waitForFunction(() => window.__plexgraphHandle?.getVisibleNodeCount?.() === 20);
  await late.waitForTimeout(500);
  const lateSvg = await pyOk("__result__ = handle.export('svg')");
  assert.ok(lateSvg.startsWith('<svg') && (lateSvg.match(/<circle /g) || []).length === 20);
  await late.close();

  assert.deepEqual(errors, []);
  console.log('PASS: export("svg")/("png") and save() round-trip through the real browser viewer, matching the button’s own export, and reject bad input in Python');
} finally { await browser?.close(); child.kill('SIGTERM'); }
