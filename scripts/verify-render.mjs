// Loads a running app URL in headless Chromium and checks that it actually
// rendered something: no console/page errors, and the canvas has pixels
// that differ from the background clear color (not just a blank/near-blank
// canvas). Use this after any viz-core/app change that touches rendering —
// type-checking and unit tests do not catch WebGL runtime errors (wrong
// buffer type, missing extensions, etc.), which only surface in a real
// browser. See docs/architecture/plan.md for context on why this exists.
//
// Usage: node scripts/verify-render.mjs <url> [screenshot.png]
//
// Requires a running bridge server to point at — start one with, e.g.:
//   packages/bridge/.venv/Scripts/python.exe -c "
//     from graphviz_bridge import show
//     from graphviz_core import Graph
//     g = Graph()
//     g.add_node('a'); g.add_node('b'); g.add_edge('a', 'b')
//     show(g, open_browser=False, block=True)
//   "
// then pass http://localhost:<http_port>/?ws=<ws_port> (printed if you use
// ShowHandle instead of block=True) as the URL argument here.
//
// Uses Chrome from CHROME_PATH, else /usr/bin/google-chrome when present, else the browser from
// `npx playwright install chromium`.

import { chromium } from "playwright";
import { existsSync } from "node:fs";

const url = process.argv[2];
if (!url) {
  console.error("usage: node scripts/verify-render.mjs <url> [screenshot.png]");
  process.exit(1);
}
const screenshotPath = process.argv[3] ?? null;

const chrome = process.env.CHROME_PATH || (existsSync("/usr/bin/google-chrome") ? "/usr/bin/google-chrome" : undefined);
const browser = await chromium.launch({
  executablePath: chrome,
  args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

await page.goto(url, { waitUntil: "load" });
// Give the WebSocket time to connect, stream the graph, and render a few
// animation frames / layout steps.
await page.waitForTimeout(3000);

const statusText = await page.locator("#status").textContent();

if (screenshotPath) {
  await page.screenshot({ path: screenshotPath });
}

const pixelCheck = await page.evaluate(() => {
  const canvas = document.getElementById("canvas");
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  if (!gl) return { error: "no WebGL context found on canvas" };
  const w = canvas.width;
  const h = canvas.height;
  const pixels = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
  let nonBackground = 0;
  const bg = [250, 250, 250]; // ~0.98 * 255, the default backgroundColor
  for (let i = 0; i < pixels.length; i += 4) {
    const dr = Math.abs(pixels[i] - bg[0]);
    const dg = Math.abs(pixels[i + 1] - bg[1]);
    const db = Math.abs(pixels[i + 2] - bg[2]);
    if (dr > 10 || dg > 10 || db > 10) nonBackground++;
  }
  return { width: w, height: h, nonBackgroundPixels: nonBackground, totalPixels: w * h };
});

console.log("status text:", JSON.stringify(statusText));
console.log("console errors:", consoleErrors.length ? consoleErrors : "none");
console.log("pixel check:", JSON.stringify(pixelCheck));

await browser.close();

if (consoleErrors.length > 0) {
  console.log("RESULT: FAIL (console errors present)");
  process.exit(1);
}
if (pixelCheck.error) {
  console.log("RESULT: FAIL (" + pixelCheck.error + ")");
  process.exit(1);
}
if (pixelCheck.nonBackgroundPixels === 0) {
  console.log("RESULT: FAIL (canvas is entirely background color — nothing rendered)");
  process.exit(1);
}
console.log("RESULT: PASS");
