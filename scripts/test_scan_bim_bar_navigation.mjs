import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const base=process.env.CLOUDBIM_WORKBENCH_URL||'http://127.0.0.1:8767';
const catalog=await (await fetch(base+'/api/catalog')).json();
const runs=await (await fetch(base+'/api/runs')).json();
const sameParameters=params=>Object.keys(catalog.defaults).every(key=>params[key]===catalog.defaults[key]);
const prepared=new Set(runs.filter(run=>run.sessionId===catalog.sessionId&&sameParameters(run.parameters)).map(run=>run.ifcGlobalId));
assert.deepEqual(catalog.bars.filter(bar=>!prepared.has(bar.ifcGlobalId)).map(bar=>bar.ifcGlobalId),[],
  'Every bar needs a current default result; navigation must not fall back to live computation');
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM||'/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1920,height:1080}});
 const errors=[],times=[];let posts=0;
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/run',route=>{posts++;return route.abort();});
 await page.goto(base+'/?bar='+encodeURIComponent(catalog.bars[0].ifcGlobalId));
 await page.waitForFunction(()=>window.scanBimWorkbench?.result&&!document.querySelector('#run').disabled);
 for(const bar of catalog.bars.slice(1)){
   const timing=await page.evaluate(async expected=>{
     const start=performance.now();document.querySelector('#nextBar').click();
     while(document.querySelector('#run').disabled||window.scanBimWorkbench.result.ifcGlobalId!==expected){
       if(!document.querySelector('#error').hidden)throw Error(document.querySelector('#error').textContent);
       if(performance.now()-start>10000)throw Error('Bar navigation timed out: '+expected);
       await new Promise(requestAnimationFrame);
     }
     await new Promise(requestAnimationFrame);
     return performance.now()-start;
   },bar.ifcGlobalId);
   times.push(timing);
 }
 assert.equal(posts,0,'Visiting every bar must not compute any result');assert.deepEqual(errors,[]);
 const sorted=[...times].sort((a,b)=>a-b),p95=sorted[Math.floor(sorted.length*.95)];
 assert.ok(p95<500,`New-bar navigation P95 ${p95} ms exceeds 500 ms`);
 const evidence={passed:true,bars:catalog.bars.length,prepared:prepared.size,posts,p95Ms:p95,maxMs:Math.max(...times),meanMs:times.reduce((a,b)=>a+b,0)/times.length,times,errors};
 await mkdir('.cloudbim/bar-navigation-performance',{recursive:true});
 await writeFile('.cloudbim/bar-navigation-performance/journey.json',JSON.stringify(evidence,null,2));
 console.log(JSON.stringify(evidence));
}finally{await browser.close();}
