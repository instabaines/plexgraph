import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
const rendererPath = '/@fs' + fileURLToPath(new URL('../packages/viz-core/src/render/renderer.ts', import.meta.url));
const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']});
try {
  const page = await browser.newPage({viewport: {width: 1280, height: 800}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/slice-check', route => route.fulfill({contentType: 'text/html', body: '<style>body{margin:0}canvas{width:100vw;height:100vh}</style><canvas id="canvas" width="1280" height="800"></canvas>'}));
  await page.goto('http://127.0.0.1:5173/slice-check');
  await page.evaluate(async (rendererPath) => {
    const {Renderer} = await import(rendererPath);
    const r = window.renderer = new Renderer(document.querySelector('canvas'), {onHover: id => window.hovered = id});
    r.loadGraph({type:'graph',schema_version:1,wire_version:1,
      nodes: Array.from({length:24}, (_,id)=>({id,key:`Node ${id}`,attrs:{}})),
      layers: ['Research','Engineering','Design'].map((key,id)=>({id,key,attrs:{}})),
      connectors: [{id:100,endpoints:[0,3,7,12],directed:false,layer_id:null,t_start:0,t_end:6,weight:null,attrs:{}}, ...Array.from({length:48},(_,id)=>({id,endpoints:[id%24,(id*7+3)%24],directed:true,layer_id:id%3,t_start:id%6,t_end:id%6+1,weight:null,attrs:{}}))]});
    const p = new Float32Array(Array.from({length:48},(_,i)=> i%2 ? Math.sin(Math.floor(i/2)*2.4)*0.7 : Math.cos(Math.floor(i/2)*2.4)*0.7));
    r.applyLayoutStep({type:'layout_step',iteration:1,converged:true,num_nodes:24,positions:new Uint8Array(p.buffer)});
  }, rendererPath);
  for (const [axis,layout] of [['layer','atlas'],['time','ribbon'],['layer','stack']]) {
    await page.evaluate(({axis,layout})=>window.renderer.setStackMode(axis,{layout,timeBuckets:6}),{axis,layout});
    await page.waitForTimeout(150);
    const result = await page.evaluate(()=>{
      const canvas = document.querySelector('canvas');
      const gl = canvas.getContext('webgl'); const pixels=new Uint8Array(canvas.width*canvas.height*4);
      gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
      let ink=0;for(let i=0;i<pixels.length;i+=4) if(pixels[i]<200 || pixels[i+1]<200 || pixels[i+2]<200) ink++;
      return {ink,svg:window.renderer.exportSVG()};
    });
    assert(result.ink>100, `${layout}: empty render`);
    assert(result.svg.includes('data-kind="hull"'), `${layout}: missing hyperedge hull`);
    assert(result.svg.includes('data-kind="arrow"'), `${layout}: missing directed arrow`);
    assert(result.svg.includes('<text'), `${layout}: missing labels`);
    assert(!result.svg.includes('NaN'), `${layout}: invalid geometry`);
    const target = await page.evaluate(() => {
      const r = window.renderer, g = r.getSliceGeometry();
      return r.worldToScreen(...g.point(0, r.stackNodeSliceCount - 1), 1280, 800);
    });
    await page.mouse.move(...target);
    await page.waitForFunction(() => window.hovered === 0);
    await page.mouse.move(2, 2);
    await page.waitForFunction(() => window.hovered === null);
    await page.screenshot({path:`/tmp/hyperloom-${layout}.png`});
    console.log(`${layout}: ${result.ink} graph pixels; SVG valid`);
  }
  await page.evaluate(() => {
    window.renderer.setStackMode(null);
    window.renderer.focusNeighborhood(0);
  });
  const focused = await page.evaluate(() => ({info:window.renderer.inspectNode(0), svg:window.renderer.exportSVG()}));
  assert.equal((focused.svg.match(/<circle /g)||[]).length,focused.info.neighbors+1);
  await page.evaluate(() => {
    const r=window.renderer, draw=r.drawNodes;
    window.drawCount=0; r.drawNodes=()=>{window.drawCount++;draw();};
    r.focusNeighborhood(null);
  });
  await page.waitForTimeout(100);
  const before=await page.evaluate(()=>window.drawCount);
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(()=>window.drawCount),before,'idle scene redraws unnecessarily');
  assert.deepEqual(errors,[]);
} finally { await browser.close(); }
