import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1920,height:1080}});
let posts=0;const errors=[];
page.on('request',r=>{if(r.method()==='POST')posts++;});page.on('pageerror',e=>errors.push(e.message));
try {
  await page.goto((process.env.CLOUDBIM_WORKBENCH_URL||'http://127.0.0.1:8767')+'/?bar=2o97Tbskf2JPl1QtzNqS0X');
  await page.waitForFunction(()=>window.scanBimWorkbench?.result&&!document.querySelector('#run').disabled);
  const session=await page.context().newCDPSession(page);
  await session.send('Emulation.setCPUThrottlingRate',{rate:4});
  const timings=[];
  for(let step=0;step<12;step++) {
    const state=await page.evaluate(()=>{
      const {result:r,scene}=window.scanBimWorkbench;
      const base=scene.children[0],mesh=base.children.find(o=>o.isMesh),scan=base.children.find(o=>o.isPoints&&o.material.vertexColors);
      const versions=[mesh.geometry.attributes.color.version,scan.geometry.attributes.color.version];
      const start=performance.now();document.querySelector('#nextUnit').click();const elapsed=performance.now()-start;
      const id=document.querySelector('#unitSelect').value;
      return {elapsed,versions,after:[mesh.geometry.attributes.color.version,scan.geometry.attributes.color.version],
        mesh:Array.from(mesh.geometry.index.array),expected:r.mesh.parts.find(p=>p.unitId===id).indices,
        scan:Array.from(scan.geometry.index.array),expectedScan:r.scan.unitIds.map((unit,i)=>unit===id?i:-1).filter(i=>i>=0)};
    });
    assert.deepEqual(state.mesh,state.expected,'Per-segment mesh must retain original global triangle indices');
    assert.deepEqual(state.scan,state.expectedScan,'Per-segment scan must retain every original point index');
    assert.deepEqual(state.after,state.versions,'Switching segments must not re-upload unchanged full-bar colors');
    timings.push(state.elapsed);
  }
  const sorted=[...timings].sort((a,b)=>a-b),p90=sorted[Math.floor(sorted.length*.9)];
  assert.ok(p90<50,`Segment click blocks the main thread for ${p90} ms (4x CPU slowdown; budget 50 ms)`);
  assert.equal(posts,0,'Segment navigation must not trigger computation');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,cpuSlowdown:4,handlerMs:timings,p90,posts,errors}));
} finally {await browser.close();}
