import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE||'/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const base=process.env.CLOUDBIM_WORKBENCH_URL||'http://127.0.0.1:8767';
const catalog=await (await fetch(base+'/api/catalog')).json();
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM||'/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1920,height:1080}}),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
 const recovery=process.env.CLOUDBIM_PRELOAD_FAILURE==='1';let rejected=false;
 if(recovery)await page.route('**/runs/**/result.json',route=>{
   if(!rejected){rejected=true;return route.fulfill({status:503,json:{error:'simulated download failure'}});}
   return route.continue();
 });
 const selected=catalog.bars.at(-1).ifcGlobalId,start=performance.now();
 await page.goto(base+'/?bar='+encodeURIComponent(selected),{waitUntil:'domcontentloaded'});
 if(recovery){
   await page.waitForFunction(()=>!document.querySelector('#preloadRetry').hidden,null,{timeout:240000});
   assert.match(await page.locator('#preloadMessage').textContent(),/simulated download failure/);
   assert.equal(await page.locator('#app').evaluate(el=>el.inert),true);
   assert.equal(await page.locator('#barSelect').isDisabled(),true);
   await page.locator('#preloadRetry').click();
 }
 await page.waitForFunction(()=>window.scanBimWorkbench?.preload.complete,null,{timeout:240000});
 const loadingMs=performance.now()-start;
 await mkdir('.cloudbim/preload-all',{recursive:true});
 if(!recovery)await page.screenshot({path:'.cloudbim/preload-all/ready.png'});
 assert.equal(await page.locator('#preloadOverlay').isVisible(),false);
 assert.equal(await page.locator('#barSelect').isDisabled(),false);
 const state=await page.evaluate(()=>({...window.scanBimWorkbench.preload,selected:window.scanBimWorkbench.result.ifcGlobalId}));
 assert.equal(state.loaded,catalog.bars.length);assert.equal(state.cached,catalog.bars.length);assert.equal(state.geometries,catalog.bars.length);
 assert.equal(state.selected,selected,'Preloading must restore the requested bar');
 const beforeRequests=requests.length;
 await page.context().setOffline(true);
 const session=await page.context().newCDPSession(page);await session.send('Emulation.setCPUThrottlingRate',{rate:4});
 const times=[];
 // Alternate distant bars so a small adjacent cache cannot make this pass.
 const ids=catalog.bars.map(b=>b.ifcGlobalId),order=[];
 while(ids.length){order.push(ids.shift());if(ids.length)order.push(ids.pop());}
 for(const id of order){
   times.push(await page.evaluate(async id=>{
     const start=performance.now(),select=document.querySelector('#barSelect');select.value=id;select.dispatchEvent(new Event('change'));
     while(document.querySelector('#run').disabled||window.scanBimWorkbench.result.ifcGlobalId!==id){
       if(!document.querySelector('#error').hidden)throw Error(document.querySelector('#error').textContent);
       if(performance.now()-start>2000)throw Error('Offline navigation stalled');
       await Promise.resolve();
     }
     const {scene,result}=window.scanBimWorkbench,unit=document.querySelector('#unitSelect').value;
     const mesh=scene.children[0].children.find(o=>o.isMesh),part=result.mesh.parts.find(p=>p.unitId===unit);
     if(part&&(mesh.geometry.index.count!==part.indices.length||mesh.geometry.index.array.some((v,i)=>v!==part.indices[i])))throw Error('Wrong cached mesh');
     return performance.now()-start;
   },id));
 }
 assert.equal(requests.length,beforeRequests,'All navigation must work without making any network requests');
 assert.deepEqual(errors,[]);
 const sorted=[...times].sort((a,b)=>a-b),p95=sorted[Math.floor(sorted.length*.95)];
 assert.ok(p95<100,`Offline 4x CPU navigation P95 ${p95} ms exceeds 100 ms`);
 await mkdir('.cloudbim/preload-all',{recursive:true});
 const heap=await session.send('Runtime.getHeapUsage');
 const evidence={passed:true,recovery,loadingMs,bars:catalog.bars.length,cpuSlowdown:4,p95Ms:p95,maxMs:Math.max(...times),meanMs:times.reduce((a,b)=>a+b,0)/times.length,navigationRequests:requests.length-beforeRequests,heapUsedMiB:heap.usedSize/1048576,state,errors};
 await writeFile(`.cloudbim/preload-all/${recovery?'recovery':'checks'}.json`,JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));
}finally{await browser.close();}
