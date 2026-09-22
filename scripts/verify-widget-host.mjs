// Fast, browser-only checks of widget.js's own message handling: no Jupyter/kernel needed, since we drive it with a
// fake anywidget `model`. This is where a message from the kernel is turned into something the iframe understands,
// and it is the one place a delivery problem (a widget manager that does not hand buffers over the way JupyterLab's
// does, say) would otherwise be completely invisible -- so it is tested directly, with adversarial input.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const template = readFileSync(new URL('../packages/bridge/plexgraph_bridge/widget.js', import.meta.url), 'utf8');
const source = template.replace('__APP_HTML__', JSON.stringify('<html><head></head><body>stub</body></html>'));

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  const result = await page.evaluate(async (src) => {
    const mod = await import(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
    const handlers = {};
    const sent = [];
    const model = {
      _height: 600,
      get(key) { return key === 'height' ? this._height : key === 'viewer_query' ? '?ws=parent' : undefined; },
      on(event, fn) { handlers[event] = fn; },
      off() {},
      send(content, buffers) { sent.push({ content, buffers: (buffers || []).length }); },
    };
    const el = document.createElement('div');
    document.body.appendChild(el);
    const cleanup = mod.default.render({ model, el });
    const onKernel = handlers['msg:custom'];

    const bytes = (n) => new Uint8Array(n).fill(7);
    onKernel({ type: 'frame', stream: 1, id: 1, index: 0, count: 1 }, [bytes(5)]);                    // normal, typed-array view
    onKernel({ type: 'frame', stream: 1, id: 2, index: 0, count: 1 }, [new ArrayBuffer(4)]);           // normal, raw ArrayBuffer
    onKernel({ type: 'frame', stream: 1, id: 3, index: 0, count: 1 }, []);                             // no buffer at all
    onKernel({ type: 'frame', stream: 1, id: 4, index: 0, count: 1 }, [{ not: 'a buffer' }]);          // unrecognised shape
    onKernel({ type: 'frame', stream: 1, id: 5, index: 0, count: 2 }, [bytes(3)]);                     // one half of a 2-piece frame (never completes)
    cleanup();

    return { sent, iframeCount: el.querySelectorAll('iframe').length };
  }, source);

  assert.equal(result.iframeCount, 1, 'render() creates the viewer iframe');

  // The frame payload itself travels by postMessage straight to the iframe, not through model.send; hostRelayed
  // (checked below) is what confirms the two well-formed messages actually got relayed.
  const errorReports = result.sent.filter(m => m.content.type === 'report' && m.content.kind === 'error');
  assert.equal(errorReports.length, 2, 'exactly the two malformed messages were reported: ' + JSON.stringify(result.sent));
  assert.match(errorReports[0].content.data.message, /no buffer attached/);
  assert.match(errorReports[1].content.data.message, /unexpected buffer type/);

  const hostReports = result.sent.filter(m => m.content.type === 'report' && m.content.kind === 'host');
  assert.equal(hostReports.length, 6, 'a host report on every message (5) plus one more from cleanup: ' + hostReports.length);
  const last = hostReports[hostReports.length - 1].content.data;
  assert.equal(last.hostReceived, 5, 'all 5 attempts were counted, including the malformed ones');
  assert.equal(last.hostRelayed, 2, 'only the 2 well-formed single-piece frames were relayed');
  assert.match(last.hostError, /unexpected buffer type/, 'the most recent error is kept');

  const closeReport = result.sent.filter(m => m.content.type === 'report' && m.content.kind === 'host').at(-1);
  assert.ok(closeReport, 'a host report was sent on cleanup too');

  assert.deepEqual(errors, [], 'no uncaught page errors');
  console.log('PASS: widget.js counts and reports what it receives from the kernel, and survives malformed messages');
} catch (error) {
  console.log('FAIL:', error.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
