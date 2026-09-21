// End-to-end: real Python bridge, built app, density -> search -> neighborhood.
// PYTHON_PATH must have NumPy, msgpack and websockets installed.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('..',import.meta.url));
const server=spawn(process.env.PYTHON_PATH || 'python3',['-u','-c',`
import json, threading
from plexgraph_core import Graph
from plexgraph_bridge import show
g=Graph()
for i in range(6001): g.add_node('Node '+str(i), role='research' if i%2 else 'engineering')
for layer in ['Research','Engineering']: g.add_layer(layer)
for i in range(6001):
    g.add_edge('Node '+str(i),'Node '+str((i+1)%6001),layer='Research',t_start=i%12,t_end=i%12+2)
    g.add_edge('Node '+str(i),'Node '+str((i+37)%6001),layer='Engineering',directed=True)
h=show(g,open_browser=False,block=False,return_handle=True,seed=0,layout_iterations=10)
print(json.dumps({'url':h.url}),flush=True)
try: threading.Event().wait()
finally: h.close()
`],{cwd:root,env:{...process.env,PYTHONPATH:`${root}/packages/core:${root}/packages/bridge`},stdio:['ignore','pipe','pipe']});
let browser;
try {
  let stderr='';server.stderr.on('data',data=>stderr+=data);
  const url=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Bridge startup timeout: '+stderr)),15000);
    const lines=createInterface({input:server.stdout});
    lines.on('line',line=>{try{const msg=JSON.parse(line);if(msg.url){clearTimeout(timer);resolve(msg.url);}}catch{}});
    server.on('exit',code=>{clearTimeout(timer);reject(Error(`Bridge exited ${code}: ${stderr}`));});
  });
  browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/usr/bin/google-chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);
  await page.waitForFunction(()=>window.__plexgraphHandle?.searchNodes('Node 12').length>0);
  assert(await page.evaluate(()=>window.__plexgraphHandle.exportSVG().includes('Density overview')));
  await page.getByLabel('Find a node').fill('Node 12');
  await page.getByRole('button',{name:'Node 12',exact:true}).click();
  await page.waitForFunction(()=>window.__plexgraphHandle.getVisibleEdgeCount()===4);
  assert((await page.locator('#node-inspector').textContent()).includes('engineering'));
  assert(!(await page.evaluate(()=>window.__plexgraphHandle.exportSVG())).includes('Density overview'));
  await page.getByRole('button',{name:'Layer atlas',exact:true}).click();
  await page.screenshot({path:'/tmp/plexgraph-neighborhood.png'});
  await page.getByRole('button',{name:'Time ribbon',exact:true}).click();
  await page.getByRole('button',{name:'Fit view',exact:true}).click();
  await page.getByRole('button',{name:'Show all nodes',exact:true}).click();
  assert(await page.evaluate(()=>window.__plexgraphHandle.exportSVG().includes('Density overview')));
  // Empty and invalid searches must not destroy the current graph.
  await page.getByLabel('Find a node').fill('does-not-exist');
  await page.getByText('No matching nodes',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('PASS: density, real bridge, search, inspector, neighborhood, slice modes, fit, reset');
} finally {await browser?.close();server.kill('SIGTERM');}
