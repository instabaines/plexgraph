// Real viewer, real bridge, real datasets: screenshots + UI inventory per size.
// node scripts/viewer-gallery.mjs --datasets=karate,sbm5000 --out=benchmarks/results/quality
// PYTHON_PATH must have numpy, scipy, networkx and the bridge dependencies.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const arg = (k, d) => (process.argv.find(a => a.startsWith(`--${k}=`)) ?? `=${d}`).split('=').slice(1).join('=');
const datasets = arg('datasets', 'karate,sbm1000,sbm5000,sbm20000').split(',');
const out = arg('out', '/tmp/plexgraph-viewer'); mkdirSync(out, { recursive: true });
const root = fileURLToPath(new URL('..', import.meta.url));

async function serve(name) {
  const child = spawn(process.env.PYTHON_PATH || 'python3', ['-u', 'benchmarks/serve_case.py', name], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = ''; child.stderr.on('data', d => stderr += d);
  const url = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(Error('startup timeout: ' + stderr)), 180000);
    createInterface({ input: child.stdout }).on('line', l => { try { const m = JSON.parse(l); if (m.url) { clearTimeout(t); resolve(m.url); } } catch {} });
    child.on('exit', c => { clearTimeout(t); reject(Error(`server exited ${c}: ${stderr.slice(-800)}`)); });
  });
  return { url, child };
}

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const report = [];
try {
  for (const name of datasets) {
    const row = { dataset: name };
    let server;
    try {
      server = await serve(name);
      const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
      page.on('requestfailed', r => errors.push('requestfailed ' + r.url()));
      page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
      await page.goto(server.url);
      await page.waitForFunction(() => window.__plexgraphHandle?.getVisibleEdgeCount?.() > 0, null, { timeout: 120000 });
      await page.waitForTimeout(5000);
      await page.screenshot({ path: `${out}/viewer-${name}.png` });
      row.controls = await page.evaluate(() => ({
        buttons: [...document.querySelectorAll('button')].map(b => b.getAttribute('aria-label') || b.textContent.trim()).filter(Boolean),
        inputs: [...document.querySelectorAll('input,select')].map(i => `${i.type || i.tagName}:${i.getAttribute('aria-label') || i.placeholder || i.id || ''}`),
        methods: Object.keys(window.__plexgraphHandle).sort(),
        caption: [...document.querySelectorAll('div,span')].map(e => e.textContent.trim()).find(t => /density overview/i.test(t) && t.length < 200) || null,
      }));
      row.pixels = await page.evaluate(() => {
        // The page background is opaque, so a correctly blended canvas has alpha 255 everywhere.
        // Translucent pixels holding non-premultiplied colours composite brighter than they should (white edges).
        const c = document.getElementById('canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
        const px = new Uint8Array(c.width * c.height * 4); gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
        let translucent = 0, invalidPremultiplied = 0, ink = 0;
        for (let i = 0; i < px.length; i += 4) {
          const a = px[i + 3];
          if (a < 250) translucent++;
          if (Math.max(px[i], px[i + 1], px[i + 2]) > a + 2) invalidPremultiplied++;
          if (Math.abs(px[i] - 250) > 10 || Math.abs(px[i + 1] - 250) > 10 || Math.abs(px[i + 2] - 250) > 10) ink++;
        }
        return { translucentPixels: translucent, invalidPremultipliedPixels: invalidPremultiplied, inkPixels: ink, totalPixels: c.width * c.height };
      });
      row.visibleEdges = await page.evaluate(() => window.__plexgraphHandle.getVisibleEdgeCount());
      row.nodes = await page.evaluate(() => window.__plexgraphHandle.getVisibleNodeCount());
      row.lod = { initial: await page.evaluate(() => window.__plexgraphHandle.getLodState()) };
      if (row.nodes > 5000) {   // can a person get from the overview to individual nodes?
        await page.evaluate(() => window.__plexgraphHandle.zoomBy(12));
        await page.waitForTimeout(1200);
        row.lod.zoomed = await page.evaluate(() => window.__plexgraphHandle.getLodState());
        await page.screenshot({ path: `${out}/viewer-${name}-zoom.png` });
        await page.evaluate(() => window.__plexgraphHandle.fitView());
        await page.waitForTimeout(1200);
      }
      // Restyle the viewer (colormap and size by degree, outlines, translucent edges): how long does it take, and how does it look?
      row.styleMs = await page.evaluate(() => {
        const start = performance.now();
        window.__plexgraphHandle.setStyle({ node: { color: { kind: 'degree', colormap: 'plasma' }, size: { kind: 'degree', range: [4, 18], scale: 'sqrt' }, outline: { color: '#ffffff', width: 1 } }, edge: { opacity: 0.4 } });
        return Math.round(performance.now() - start);
      });
      await page.waitForTimeout(900);
      await page.screenshot({ path: `${out}/viewer-${name}-styled.png` });
      await page.evaluate(() => window.__plexgraphHandle.resetStyle());
      await page.waitForTimeout(400);
      const hit = await page.evaluate(() => window.__plexgraphHandle.searchNodes('1')[0]?.id ?? null);
      if (hit !== null) {
        await page.evaluate(id => window.__plexgraphHandle.focusNeighborhood(id), hit);
        await page.waitForTimeout(1500);
        row.focusInfo = await page.evaluate(id => window.__plexgraphHandle.inspectNode(id), hit);
        row.focusVisibleEdges = await page.evaluate(() => window.__plexgraphHandle.getVisibleEdgeCount());
        await page.screenshot({ path: `${out}/viewer-${name}-focus.png` });
      }
      row.errors = [...new Set(errors)];
      row.status = 'ok';
      await page.close();
    } catch (e) { row.status = 'error'; row.error = String(e.message ?? e).slice(0, 500); }
    finally { server?.child.kill('SIGTERM'); }
    console.log(name, row.status, row.error ?? '', row.errors?.length ? row.errors : '');
    report.push(row);
  }
} finally { await browser.close(); }
writeFileSync(`${out}/viewer-gallery.json`, JSON.stringify(report, null, 2) + '\n');
console.log(`${out}/viewer-gallery.json`);
