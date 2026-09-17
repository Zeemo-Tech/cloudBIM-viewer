import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const output=process.env.CLOUDBIM_WORKBENCH_EVIDENCE || '.cloudbim/scan-bim-independent-clusters/ui';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1920,height:1080}});
const errors=[], checks=[];page.on('pageerror',e=>errors.push(e.message));
try{
  const catalog=await (await page.request.get('http://127.0.0.1:8767/api/catalog')).json();
  for(const name of (process.argv.length>2?process.argv.slice(2):['555867','555928','555987','555999','558730','558894','559236','559237'])){
    const bar=catalog.bars.find(b=>b.name.includes(name));assert.ok(bar);
    await page.goto(`http://127.0.0.1:8767/?bar=${bar.ifcGlobalId}`,{waitUntil:'networkidle'});
    await page.waitForFunction(id=>window.scanBimWorkbench?.result?.ifcGlobalId===id&&!document.querySelector('#run').disabled,bar.ifcGlobalId,{timeout:90000});
    const data=await page.evaluate(()=>{
      const r=window.scanBimWorkbench.result;
      const original=new Map();for(let i=0;i<r.mesh.indices.length;i+=3){const key=r.mesh.indices.slice(i,i+3).join(',');original.set(key,(original.get(key)||0)+1);}
      for(const part of r.mesh.parts)for(let i=0;i<part.indices.length;i+=3){const key=part.indices.slice(i,i+3).join(',');original.set(key,(original.get(key)||0)-1);}
      return {name:r.name,runId:r.runId,units:r.units.length,parts:r.mesh.parts.length,
        completePartition:[...original.values()].every(n=>n===0),
        independent:r.measurement.clusterFits.every(f=>!f.positionConstrained&&!f.lengthConstrained&&!f.directionConstrained),
        fits:r.measurement.clusterFits,axisSamples:r.profile.length,known:r.stats.knownCount,
        supportedSamples:r.profile.filter(p=>p.observedCenterM).length,maxOffsetM:r.measurement.bending.maxCentrelineDepartureM,
        ownership:r.matchedScanIndices.every((j,i)=>j<0||r.scan.unitIds[j]===r.vertexEvidence.unitIds[i]),
        normalsSupported:r.scan.supported.filter(Boolean).length};
    });
    assert.ok(data.completePartition&&data.independent&&data.ownership);
    assert.equal(data.units,data.parts);
    if(data.units===1){
      assert.equal(data.supportedSamples,data.axisSamples);
      assert.ok(data.maxOffsetM>.15);assert.ok(data.normalsSupported>0);
      // With the design hidden, a fit view must frame the actual displaced scan.
      await page.locator('#showDesign').uncheck();await page.locator('[data-view=fit]').click();
      await page.screenshot({path:`${output}/${name}-independent.png`});
      await page.locator('#showDesign').check();await page.locator('[data-view=fit]').click();
    }else{
      assert.equal(data.units,36);
      await page.locator('[data-step=input]').click();
      const selected=await page.evaluate(()=>window.scanBimWorkbench.result.units.find(u=>u.pointCount>0).id);
      await page.locator('#unitSelect').selectOption(selected);
      const visible=await page.evaluate(()=>{
        const {result:r,scene}=window.scanBimWorkbench,selected=document.querySelector('#unitSelect').value;
        const mesh=scene.children[0].children.find(o=>o.isMesh),scan=scene.children[0].children.find(o=>o.isPoints&&o.material.vertexColors);
        const part=r.mesh.parts.find(p=>p.unitId===selected);
        return {scanOK:[...scan.geometry.index.array].every(i=>r.scan.unitIds[i]===selected),scanCount:scan.geometry.index.count,
          indices:[...mesh.geometry.index.array],expected:part.indices,sectionUnits:[...document.querySelector('#sectionSelect').options].map(o=>r.profile[Number(o.value)].designUnitId),selected};
      });
      assert.ok(visible.scanOK&&visible.scanCount>0);assert.deepEqual(visible.indices,visible.expected);
      assert.ok(visible.sectionUnits.every(id=>id===selected));
      await page.screenshot({path:`${output}/${name}-single-segment.png`});
      await page.locator('#unitSelect').selectOption('all');
    }
    await page.screenshot({path:`${output}/${name}-overview.png`});
    checks.push(data);console.log(JSON.stringify({name,units:data.units,supported:data.supportedSamples,total:data.axisSamples,offsetMm:data.maxOffsetM*1000,known:data.known}));
  }
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:`${output}/cluster-mobile.png`,fullPage:true});
  assert.deepEqual(errors,[]);
  await writeFile(`${output}/cluster-checks.json`,JSON.stringify({checks,errors},null,2));
}finally{await browser.close();}
