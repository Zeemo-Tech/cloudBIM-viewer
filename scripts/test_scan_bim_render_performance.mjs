import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const base=process.env.CLOUDBIM_WORKBENCH_URL || 'http://127.0.0.1:8767';
const page=await browser.newPage({viewport:{width:1920,height:1080}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{
  window.programCreates=0;
  const original=WebGL2RenderingContext.prototype.createProgram;
  WebGL2RenderingContext.prototype.createProgram=function(...args){window.programCreates++;return original.apply(this,args);};
});
async function ready(){await page.waitForFunction(()=>window.scanBimWorkbench?.result?.ifcGlobalId===document.querySelector('#barSelect').value&&!document.querySelector('#run').disabled,null,{timeout:60000});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function axes(){return page.evaluate(()=>{
  const {result:r,scene}=window.scanBimWorkbench;
  const expected=[];
  const segment=(a,b,dashed=false)=>expected.push({positions:[...a,...b].map(Math.fround),dashed});
  const visible=id=>document.querySelector('#unitSelect').value==='all'||document.querySelector('#unitSelect').value===id;
  if(document.querySelector('#showDesign').checked)for(const unit of r.units)if(visible(unit.id))segment(unit.start,unit.end);
  let previous=null;
  for(const row of r.profile){
    if(!visible(row.designUnitId)){previous=null;continue;}
    if(row.observedCenterM){
      if(previous?.observedCenterM&&previous.designUnitId===row.designUnitId)segment(previous.observedCenterM,row.observedCenterM,previous.quality==='prior-axis-inferred'||row.quality==='prior-axis-inferred');
      if(row.quality!=='prior-axis-inferred'){
        const p=row.observedCenterM;segment([p[0]-.0006,p[1],p[2]],[p[0]+.0006,p[1],p[2]]);segment([p[0],p[1]-.0006,p[2]],[p[0],p[1]+.0006,p[2]]);
      }
    }previous=row;
  }
  const actual=[];
  for(const object of scene.children[1].children){
    if(!object.isLineSegments)continue;
    const positions=object.geometry.attributes.position.array;
    if(object.material.isLineDashedMaterial){
      const distances=object.geometry.attributes.lineDistance.array;
      for(let i=0;i<positions.length;i+=6){
        const expectedLength=Math.hypot(positions[i+3]-positions[i],positions[i+4]-positions[i+1],positions[i+5]-positions[i+2]);
        if(distances[i/3]!==0||Math.abs(distances[i/3+1]-expectedLength)>1e-7)throw new Error('Inferred dash phase changed');
      }
    }
    for(let i=0;i<positions.length;i+=6)actual.push({positions:Array.from(positions.slice(i,i+6)),dashed:!!object.material.isLineDashedMaterial});
  }
  const key=row=>JSON.stringify(row);
  return {expected:expected.map(key).sort(),actual:actual.map(key).sort(),objects:scene.children[1].children.length,programs:window.programCreates};
});}
try{
  await page.goto(base+'/?bar=2o97Tbskf2JPl1QtzNqS0X');await ready();
  await page.locator('#allUnits').click();
  const all=await axes();assert.deepEqual(all.actual,all.expected,'Batching must preserve every solid and inferred axis segment');
  assert.ok(all.objects<=3,`${all.objects} axis objects for one bar; expected at most three batches`);
  const unit=await page.evaluate(()=>window.scanBimWorkbench.result.units.find(u=>u.pointCount>0).id);
  await page.selectOption('#unitSelect',unit);const single=await axes();assert.deepEqual(single.actual,single.expected);
  // Exercise the real face filter with simultaneous unit, section, and reason filters.
  await page.locator('#sectionOnly').check();
  const reason=await page.evaluate(()=>window.scanBimWorkbench.result.reasons.find(r=>r==='matched'));
  assert.ok(reason,'Fixture needs matched vertices');await page.selectOption('#reasonFilter',reason);
  const filtered=await page.evaluate(()=>{
    const {result:r,scene}=window.scanBimWorkbench,selected=document.querySelector('#unitSelect').value;
    const row=r.profile[Number(document.querySelector('#sectionSelect').value)],center=row.observedCenterM||row.designCenterM,axis=row.axisTangent;
    const accepted=i=>r.reasons[i]===document.querySelector('#reasonFilter').value&&Math.abs(axis.reduce((sum,v,j)=>sum+v*(r.mesh.positions[3*i+j]-center[j]),0))<=row.windowM+1e-9;
    const expected=[];for(let i=0;i<r.mesh.indices.length;i+=3)if(r.mesh.faceUnitIds[i/3]===selected&&[0,1,2].every(j=>accepted(r.mesh.indices[i+j])))expected.push(...r.mesh.indices.slice(i,i+3));
    return {expected,actual:Array.from(scene.children[0].children.find(o=>o.isMesh).geometry.index.array)};
  });assert.deepEqual(filtered.actual,filtered.expected);
  await page.locator('#sectionOnly').uncheck();await page.selectOption('#reasonFilter','all');
  await page.selectOption('#unitSelect','all');
  // Once the required shader variants have rendered, navigation must reuse them.
  await page.locator('#nextBar').click();await ready();
  await page.locator('#prevBar').click();await ready();
  const programs=await page.evaluate(()=>window.programCreates);
  const times=[];
  for(const id of ['nextBar','prevBar','nextBar','prevBar']){
    const start=performance.now();await page.locator('#'+id).click();await ready();times.push(performance.now()-start);
    assert.equal(await page.evaluate(()=>window.programCreates),programs,'Switching must not discard and recompile the same shaders');
    const check=await axes();assert.deepEqual(check.actual,check.expected);assert.ok(check.objects<=3);
  }
  assert.ok(Math.max(...times)<1000,`Warm next/previous took ${Math.max(...times)} ms`);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,axisObjects:all.objects,axisSegments:all.expected.length,switchMs:times,programs,errors}));
}finally{await browser.close();}
