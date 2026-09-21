// Renderer-only diagnostic. Start the app Vite server first. Synthetic positions
// deliberately isolate GPU/upload/interaction costs from Python layout quality.
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { writeFile } from 'node:fs/promises';
import os from 'node:os';
const args = Object.fromEntries(process.argv.slice(2).map(arg => arg.replace(/^--/, '').split('=')));
const sizes = (args.sizes ?? '1000,10000').split(',').map(Number);
if (sizes.some(n => !Number.isInteger(n) || n < 1 || n > 100000)) throw Error('sizes must be integers from 1 to 100000');
const output = args.output ?? '/tmp/plexgraph-browser.json';
const url = args.url ?? 'http://127.0.0.1:5173';
const rendererPath = '/@fs' + fileURLToPath(new URL('../packages/viz-core/src/render/renderer.ts', import.meta.url));
const report = {node: process.version, platform: `${os.platform()} ${os.release()}`, cpu: os.cpus()[0]?.model,
  backend: 'Chrome ANGLE SwiftShader (software rendering)', frameScenario: 'continuous camera pan', viewport: [1280,800], results: []};
for (const n of sizes) {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--js-flags=--max-old-space-size=1024']});
  let watchdog;
  let timedOut = false;
  try {
    watchdog = setTimeout(() => { timedOut = true; browser.close().catch(() => {}); }, 60000);
    const page = await browser.newPage({viewport: {width:1280,height:800}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/render-benchmark', route => route.fulfill({contentType:'text/html', body:'<style>body{margin:0}canvas{width:100vw;height:100vh}</style><canvas width="1280" height="800"></canvas>'}));
    await page.goto(url + '/render-benchmark');
    const rows = await page.evaluate(async ({n, rendererPath}) => {
      const {Renderer} = await import(rendererPath);
      const canvas = document.querySelector('canvas');
      const renderer = new Renderer(canvas);
      const nodes = Array.from({length:n},(_,id)=>({id,key:String(id),attrs:{}}));
      const connectors = Array.from({length:2*n},(_,id)=>({id,endpoints:[id%n,(id*31+17)%n],directed:false,
        layer_id:id%6,t_start:id%12,t_end:id%12+1,weight:null,attrs:{}}));
      let start = performance.now();
      renderer.loadGraph({type:'graph',schema_version:1,wire_version:1,nodes,connectors,layers:Array.from({length:6},(_,id)=>({id,key:String(id),attrs:{}}))});
      const graphUploadMs = performance.now()-start;
      const positions = new Float32Array(n*2);
      const side = Math.ceil(Math.sqrt(n));
      for(let i=0;i<n;i++) {positions[i*2]=(i%side)/side*1.6-.8; positions[i*2+1]=Math.floor(i/side)/side*1.6-.8;}
      const step = {type:'layout_step',iteration:1,converged:true,num_nodes:n,positions:new Uint8Array(positions.buffer)};
      start = performance.now(); renderer.applyLayoutStep(step);
      const initialLayoutUploadMs = performance.now()-start;
      const quantile = (a,q) => [...a].sort((a,b)=>a-b)[Math.floor((a.length-1)*q)];
      const rows=[];
      for(const mode of ['overview','atlas','ribbon']) {
        start=performance.now();
        renderer.setStackMode(mode==='overview'?null:mode==='atlas'?'layer':'time',{layout:mode==='atlas'?'atlas':'ribbon',timeBuckets:6});
        const modeBuildMs=performance.now()-start;
        start=performance.now(); renderer.applyLayoutStep(step);
        const layoutUpdateMs=performance.now()-start;
        // Warm up shaders and buffers before measuring frame intervals.
        for(let i=0;i<5;i++) await new Promise(requestAnimationFrame);
        const intervals=[]; let previous=performance.now();
        for(let i=0;i<45;i++) {
          renderer.camera.x += .002;
          await new Promise(requestAnimationFrame);
          const now=performance.now();intervals.push(now-previous);previous=now;
        }
        // Measure the actual hover routine at a known node; private access is
        // intentional benchmark instrumentation, not public application API.
        const point= mode==='overview' ? [positions[0],positions[1]] : renderer.getSliceGeometry().point(0,0);
        const [x,y]=renderer.worldToScreen(...point,1280,800);
        canvas.dispatchEvent(new PointerEvent('pointermove',{clientX:x,clientY:y}));
        const hover=[];
        for(let i=0;i<20;i++) {
          start=performance.now();
          if(!renderer.densityMode) { if(mode==='overview') renderer.updateHover(); else renderer.updateSliceHover(); }
          hover.push(performance.now()-start);
        }
        rows.push({nodes:n,edges:connectors.length,mode,densityOverview:renderer.densityMode,hoverAvailable:!renderer.densityMode,drawnNodeMarks:renderer.densityMode?renderer.densityNodes.length/3:(mode==='overview'?n:n*6),
          graphUploadMs,initialLayoutUploadMs,modeBuildMs,layoutUpdateMs,
          frameP50Ms:quantile(intervals,.5),frameP95Ms:quantile(intervals,.95),
          hoverP95Ms:renderer.densityMode?null:quantile(hover,.95),jsHeapMB:performance.memory?.usedJSHeapSize/1048576 ?? null,
          frameBudgetMet:quantile(intervals,.95)<=33,hoverBudgetMet:renderer.densityMode?null:quantile(hover,.95)<=100});
        canvas.dispatchEvent(new PointerEvent('pointerleave'));
      }
      renderer.dispose();
      return rows;
    }, {n,rendererPath});
    report.results.push(...rows.map(row=>({...row,status:errors.length?'browser_error':'ok',errors})));
    console.log(JSON.stringify(rows));
  } catch (error) {
    report.results.push({nodes:n,status:timedOut?'timeout':'error',error:String(error)});
  } finally { clearTimeout(watchdog);await browser.close(); }
  await writeFile(output,JSON.stringify(report,null,2)+'\n');
}
console.log(output);
