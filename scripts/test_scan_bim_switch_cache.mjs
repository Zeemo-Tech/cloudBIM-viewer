import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const base=process.env.CLOUDBIM_WORKBENCH_URL || 'http://127.0.0.1:8767';
const page=await browser.newPage({viewport:{width:1920,height:1080}});
let posts=0, downloads=0;
const resultRequests=new Map();
const errors=[], measurements=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(r.method()==='POST'&&r.url()===base+'/api/run')posts++;if(/\/runs\/.*\/result.json$/.test(r.url())){downloads++;resultRequests.set(r.url(),(resultRequests.get(r.url())||0)+1);}});
async function ready(bar,windowScale=1){
  await page.waitForFunction(({bar,windowScale})=>window.scanBimWorkbench?.result?.ifcGlobalId===bar&&window.scanBimWorkbench.result.parameters.windowScale===windowScale&&!document.querySelector('#run').disabled,{bar,windowScale},{timeout:60000});
  assert.equal(await page.locator('#error').isVisible(),false,await page.locator('#error').textContent());
  return page.evaluate(()=>window.scanBimWorkbench.result.runId);
}
async function select(bar,windowScale=1){await page.selectOption('#barSelect',bar);return ready(bar,windowScale);}
try{
  const catalog=await (await fetch(base+'/api/catalog')).json();
  const bars=catalog.bars.filter(b=>b.pointCount>0).slice(0,5);
  assert.ok(bars.length>=5,'Need five real bars for eviction coverage');
  const a=bars[0].ifcGlobalId,b=bars[1].ifcGlobalId;
  await page.goto(base+'/?bar='+encodeURIComponent(a));
  const first=await ready(a);
  await select(b);
  const firstUrl=base+'/runs/'+first+'/result.json';
  const before={posts,downloads:resultRequests.get(firstUrl)}, start=performance.now();
  assert.equal(await select(a),first,'Switching back must reuse the same run');
  measurements.push({case:'warm switch',elapsedMs:performance.now()-start});
  assert.ok(measurements[0].elapsedMs<1000,`Warm switch took ${Math.round(measurements[0].elapsedMs)} ms (budget: 1000 ms)`);
  assert.equal(posts,before.posts,'Switching back must not recompute');
  assert.equal(resultRequests.get(firstUrl),before.downloads,'Warm switch must not download its full JSON again');
  const nextBar=await page.locator('#barSelect').evaluate(el=>el.options[el.selectedIndex+1].value);
  await page.locator('#nextBar').click();await ready(nextBar);
  const beforePrevious=posts;
  await page.locator('#prevBar').click();assert.equal(await ready(a),first);
  assert.equal(posts,beforePrevious,'Previous/next navigation must also reuse results');
  await page.locator('#windowScale').fill('2');
  await select(b,2);
  const variant=await select(a,2);
  assert.notEqual(variant,first,'Changed parameters must not reuse a mismatched run');
  await page.locator('#windowScale').fill('1');
  await select(b);
  assert.equal(await select(a),first,'Restoring parameters must reuse their original run');
  let replacedStatus=false;
  await page.route('**/api/status',async route=>{
    const response=await route.fetch(), state=await response.json();
    if(state.status==='complete' && !replacedStatus){
      assert.ok(state.requestId,'Live server must expose the accepted request identity');
      replacedStatus=true;state.requestId='another-browser';
      state.latest={...state.latest,requestId:'another-browser',ifcGlobalId:b,resultUrl:'/runs/wrong-result/result.json'};
    }
    await route.fulfill({response,json:state});
  });
  await page.locator('#run').click();
  await page.waitForFunction(old=>window.scanBimWorkbench.result.runId!==old&&!document.querySelector('#run').disabled,first,{timeout:60000});
  const forced=await ready(a);
  assert.ok(replacedStatus,'Concurrent-browser completion scenario must be exercised');
  await page.unroute('**/api/status');
  assert.notEqual(forced,first,'Explicit rerun must create a new run');
  await select(b);assert.equal(await select(a),forced,'Switching must use latest forced run');
  // Visiting other bars must retain A's latest pinned default result in memory.
  for(const bar of bars.slice(1))await select(bar.ifcGlobalId);
  const beforeEviction=posts;
  assert.equal(await select(a),forced);
  assert.equal(posts,beforeEviction,'Visiting other bars must not recompute pinned results');
  const beforeReload=posts;
  await page.reload();assert.equal(await ready(a),forced);
  assert.equal(posts,beforeReload,'Reload must reuse a run from the same server session');
  // A fully preloaded page browses its pinned snapshot offline. Explicit reload
  // refreshes the snapshot, including after input/code changes on the server.
  await page.route('**/api/catalog',async route=>{
    const response=await route.fetch(), changed=await response.json();
    changed.sessionId='different-server-session';
    await route.fulfill({response,json:changed});
  });
  await select(b);
  const beforeRestart=posts;
  // Missing results in the simulated version must be prepared at the loading
  // screen. Reject the first computation to keep this failure case bounded.
  await page.route('**/api/run',route=>route.fulfill({status:503,json:{error:'simulated preparation failure'}}));
  await page.locator('#reloadAll').click();
  await page.waitForFunction(()=>!document.querySelector('#preloadRetry').hidden);
  assert.equal(posts,beforeRestart+1,'Reloading changed inputs must prepare new results');
  assert.equal(await page.locator('#preloadOverlay').isVisible(),true);
  await page.unroute('**/api/run');await page.unroute('**/api/catalog');
  await page.locator('#preloadRetry').click();await ready(b);
  assert.deepEqual(errors,[]);
  await mkdir('.cloudbim/switch-diagnosis',{recursive:true});
  const evidence={passed:true,posts,downloads,measurements,errors};
  await writeFile('.cloudbim/switch-diagnosis/cache-checks.json',JSON.stringify(evidence,null,2));
  console.log(JSON.stringify(evidence,null,2));
}finally{await browser.close();}
