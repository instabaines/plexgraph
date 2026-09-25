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
      // AnyModel.send is (content, callbacks, buffers) -- exactly this shape is what caught, in this fake, a real
      // bug where widget.js called it as (content, buffers): the buffers silently landed in the callbacks position
      // and never reached Python. Recording both positions (not just buffers.length) is what makes that visible.
      send(content, callbacks, buffers) { sent.push({ content, callbacksIsBuffers: Array.isArray(callbacks), buffers: (buffers || []).length }); },
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

    window.__plexgraphTest = { sent, cleanup };
    return { iframeCount: el.querySelectorAll('iframe').length };
  }, source);

  assert.equal(result.iframeCount, 1, 'render() creates the viewer iframe');

  // The viewer (in the iframe) answering an export request (ShowHandle.export/.save) -- the one message type that
  // travels iframe -> host -> kernel, the opposite direction from everything above. Simulated from inside the real
  // iframe Playwright sees, so event.source lines up exactly as it would for the real viewer posting to its host.
  const viewerFrame = page.frames().find((fr) => fr !== page.mainFrame());
  assert.ok(viewerFrame, 'the viewer iframe is a real, separate frame Playwright can drive');
  await viewerFrame.waitForLoadState(); // the srcdoc assignment navigates the iframe; wait for that to settle first
  await viewerFrame.evaluate(() => {
    const buf = new Uint8Array([1, 2, 3]).buffer;
    window.parent.postMessage({ plexgraph: 'export', id: 'req-ok', format: 'svg', error: null, data: buf }, '*', [buf]);
    window.parent.postMessage({ plexgraph: 'export', id: 'req-failed', format: 'png', error: 'canvas.toBlob returned null', data: null }, '*');
  });
  await page.waitForTimeout(200); // postMessage delivery is asynchronous even within one process

  const beforeCleanup = await page.evaluate(() => window.__plexgraphTest.sent);
  const exportSent = beforeCleanup.filter((m) => m.content.type === 'export');
  assert.equal(exportSent.length, 2, 'both export replies were relayed to the kernel: ' + JSON.stringify(beforeCleanup));
  const [ok, failed] = exportSent;
  assert.deepEqual([ok.content.id, ok.content.format, ok.content.error], ['req-ok', 'svg', null]);
  assert.equal(ok.buffers, 1, 'the data buffer reached model.send in the buffers position, not the callbacks one');
  assert.equal(ok.callbacksIsBuffers, false, 'callbacks and buffers are not swapped');
  assert.deepEqual([failed.content.id, failed.content.format, failed.content.error], ['req-failed', 'png', 'canvas.toBlob returned null']);
  assert.equal(failed.buffers, 0, 'a failed export carries no data');
  console.log('ok: an export reply from the viewer is relayed to the kernel with the buffer intact');

  await page.evaluate(() => window.__plexgraphTest.cleanup());
  const sent = await page.evaluate(() => window.__plexgraphTest.sent); // includes cleanup's own final host report

  // The frame payload itself travels by postMessage straight to the iframe, not through model.send; hostRelayed
  // (checked below) is what confirms the two well-formed messages actually got relayed.
  const errorReports = sent.filter(m => m.content.type === 'report' && m.content.kind === 'error');
  assert.equal(errorReports.length, 2, 'exactly the two malformed messages were reported: ' + JSON.stringify(sent));
  assert.match(errorReports[0].content.data.message, /no buffer attached/);
  assert.match(errorReports[1].content.data.message, /unexpected buffer type/);

  const hostReports = sent.filter(m => m.content.type === 'report' && m.content.kind === 'host');
  assert.equal(hostReports.length, 6, 'a host report on every message (5) plus one more from cleanup: ' + hostReports.length);
  const last = hostReports[hostReports.length - 1].content.data;
  assert.equal(last.hostReceived, 5, 'all 5 attempts were counted, including the malformed ones');
  assert.equal(last.hostRelayed, 2, 'only the 2 well-formed single-piece frames were relayed');
  assert.match(last.hostError, /unexpected buffer type/, 'the most recent error is kept');

  const closeReport = hostReports.at(-1);
  assert.ok(closeReport, 'a host report was sent on cleanup too');

  assert.deepEqual(errors, [], 'no uncaught page errors');
  console.log('PASS: widget.js counts and reports what it receives from the kernel, and survives malformed messages');
} catch (error) {
  console.log('FAIL:', error.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
