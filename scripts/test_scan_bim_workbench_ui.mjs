import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const modulePath=process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
const {chromium}=await import(pathToFileURL(modulePath));
const output=process.env.CLOUDBIM_WORKBENCH_EVIDENCE || '.cloudbim/scan-bim-workbench-task/ui';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1920,height:1080}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto('http://127.0.0.1:8767/',{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.scanBimWorkbench?.result?.runId,{timeout:45000});
  await page.waitForFunction(()=>!document.querySelector('#run').disabled);
  assert.equal(await page.locator('#error').isVisible(),false,await page.locator('#error').textContent());
  const data=await page.evaluate(()=>{
    const r=window.scanBimWorkbench.result;
    return {id:r.runId,points:r.stats.pointCount,known:r.stats.knownCount,vertices:r.stats.vertexCount,profile:r.profile.length,axisMethod:r.measurement.bending.method,continuous:r.profile.every(p=>p.observedCenterM),
      fitReasons:r.profile.map(p=>p.fitEvidence?.reason),indicesOK:r.matchedScanIndices.every(i=>i<r.scan.instanceIds.length),
      normalCount:r.scan.supported.filter(Boolean).length};
  });
  assert.ok(data.points>0&&data.vertices>0&&data.profile>0);assert.ok(data.indicesOK);
  assert.equal(data.axisMethod,'scan-cluster-independent-axis-v1');assert.ok(data.continuous);
  assert.equal(await page.locator('#sectionOnly').isChecked(),false);
  const initialAxis=await page.evaluate(()=>window.scanBimWorkbench.result.profile.map(p=>p.observedCenterM));
  await page.screenshot({path:`${output}/sections-1920.png`});
  await page.locator('[data-step=normals]').click();
  await page.waitForFunction(()=>window.scanBimWorkbench.stage==='normals');
  assert.equal(await page.locator('#showScanNormals').isChecked(),true);
  await page.screenshot({path:`${output}/normals-1920.png`});
  await page.locator('[data-step=matches]').click();
  const knownIndex=await page.evaluate(()=>window.scanBimWorkbench.result.matchedScanIndices.findIndex(i=>i>=0));
  if(knownIndex>=0){await page.locator('#vertexIndex').fill(String(knownIndex));await page.locator('#inspectVertex').click();assert.match(await page.locator('#inspection').textContent(),/三维连线长度/);}
  await page.screenshot({path:`${output}/matches-1920.png`});
  await page.locator('#pinBaseline').click();
  await page.locator('#windowScale').fill('2');
  assert.equal(await page.locator('#dirty').isVisible(),true);
  await page.locator('#run').click();
  await page.waitForFunction(old=>window.scanBimWorkbench.result.runId!==old,data.id,{timeout:45000});
  await page.waitForFunction(()=>!document.querySelector('#run').disabled);
  assert.equal(await page.evaluate(()=>window.scanBimWorkbench.result.parameters.windowScale),2);
  assert.deepEqual(await page.evaluate(()=>window.scanBimWorkbench.result.profile.map(p=>p.observedCenterM)),initialAxis);
  assert.match(await page.locator('#summary').textContent(),/对照有效顶点/);
  const changed=await page.evaluate(()=>({known:window.scanBimWorkbench.result.stats.knownCount,window:window.scanBimWorkbench.result.profile[0].windowM}));
  await page.locator('#defaults').click();await page.locator('#run').click();
  await page.waitForFunction(()=>window.scanBimWorkbench.result.parameters.windowScale===1,{timeout:45000});
  await page.waitForFunction(()=>!document.querySelector('#run').disabled);
  await page.setViewportSize({width:2560,height:1440});await page.locator('[data-step=sections]').click();
  await page.screenshot({path:`${output}/sections-2560.png`});
  await page.setViewportSize({width:1280,height:720});
  await page.screenshot({path:`${output}/sections-1280.png`});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:`${output}/mobile-390.png`,fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);
  await writeFile(`${output}/checks.json`,JSON.stringify({data,changed,errors},null,2));
  console.log(JSON.stringify({passed:true,data,changed,errors}));
}finally{await browser.close();}
