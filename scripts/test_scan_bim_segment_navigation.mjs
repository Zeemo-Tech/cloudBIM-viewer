import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1920,height:1080}}),base=process.env.CLOUDBIM_WORKBENCH_URL||'http://127.0.0.1:8767';
let posts=0,downloads=0;const errors=[];
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts++;if(r.url().endsWith('/result.json'))downloads++;});
async function ready(){await page.waitForFunction(()=>window.scanBimWorkbench?.result?.ifcGlobalId===document.querySelector('#barSelect').value&&!document.querySelector('#run').disabled,null,{timeout:60000});}
async function checkSegment(){
 const state=await page.evaluate(()=>{
  const {result:r,scene,section}=window.scanBimWorkbench,id=document.querySelector('#unitSelect').value,unit=r.units.find(u=>u.id===id);
  const mesh=scene.children[0].children.find(o=>o.isMesh),scan=scene.children[0].children.find(o=>o.isPoints&&o.material.vertexColors);
  const part=r.mesh.parts.find(p=>p.unitId===id),vertices=r.distances.map((_,i)=>i).filter(i=>(r.mesh.unitIds?.[i]||r.vertexEvidence.unitIds[i])===id);
  return {id,index:r.units.indexOf(unit),total:r.units.length,mesh:Array.from(mesh.geometry.index.array),expectedMesh:part?.indices,
   scan:Array.from(scan.geometry.index.array).map(i=>r.scan.unitIds[i]),scanCount:unit?.pointCount,
   sections:[...document.querySelector('#sectionSelect').options].map(o=>r.profile[Number(o.value)].designUnitId),section:section?.row.designUnitId,
   summary:document.querySelector('#summary').textContent,known:vertices.filter(i=>r.distances[i]!=null).length,vertices:vertices.length,
   run:r.runId,scope:document.querySelector('#summaryTitle')?.textContent};
 });
 assert.notEqual(state.id,'all','Abdominal bars must open in a single segment, not a wave-shaped overview');
 assert.deepEqual(state.mesh,state.expectedMesh);assert.ok(state.scan.every(id=>id===state.id));assert.equal(state.scan.length,state.scanCount);
 assert.ok(state.sections.every(id=>id===state.id));assert.equal(state.section,state.id);
 assert.match(state.scope,new RegExp(`第 ${state.index+1} / ${state.total} 段`));
 assert.ok(state.summary.includes(`${state.known.toLocaleString('zh-CN')} / ${state.vertices.toLocaleString('zh-CN')}`));
 return state;
}
try{
 await page.goto(base+'/?bar=2o97Tbskf2JPl1QtzNqS0X');await ready();
 assert.notEqual(await page.locator('#unitSelect').inputValue(),'all','Multi-segment bar must default to a single comparison segment');
 const first=await checkSegment();assert.equal(first.index,0);assert.equal(await page.locator('#prevUnit').isDisabled(),true);
 const picking=await page.evaluate(async()=>{
  const THREE=await import('three'),{result:r,scene}=window.scanBimWorkbench;
  const pick=scene.children[0].children.find(o=>o.isPoints&&!o.material.vertexColors);
  const i=r.mesh.parts.find(p=>p.unitId===document.querySelector('#unitSelect').value).indices[0];
  const point=new THREE.Vector3().fromArray(r.mesh.positions,i*3),direction=new THREE.Vector3(0,0,-1);
  const ray=new THREE.Raycaster(point.clone().add(new THREE.Vector3(0,0,.1)),direction);
  ray.params.Points.threshold=.0001;
  return {drawn:pick.material.visible,pickable:ray.intersectObject(pick).some(hit=>hit.index===i)};
 });assert.equal(picking.drawn,false,'Invisible pick sprites must not be rasterized in a close-up');assert.ok(picking.pickable,'Design vertices must remain pickable');
 const before=posts;await page.locator('#nextUnit').click();const second=await checkSegment();assert.equal(second.index,1);assert.equal(second.run,first.run);
 await page.locator('#prevUnit').click();assert.equal((await checkSegment()).id,first.id);
 await page.locator('#allUnits').click();assert.equal(await page.locator('#unitSelect').inputValue(),'all');
 assert.match(await page.locator('#summaryTitle').textContent(),/整根/);
 await page.locator('#nextUnit').click();assert.equal((await checkSegment()).id,first.id);
 for(const step of ['input','design','sections','normals','matches','deviation']){
  await page.locator(`[data-step=${step}]`).click();assert.equal((await checkSegment()).id,first.id);
 }
 assert.equal(posts,before,'Inspecting segments must never recompute the bar');
 await page.locator('[data-step=sections]').click();
 await mkdir('.cloudbim/segment-view',{recursive:true});await page.screenshot({path:'.cloudbim/segment-view/single-segment.png'});
 await page.locator('#nextBar').click();await ready();assert.equal((await checkSegment()).index,0);
 await page.locator('#nextUnit').click();const selected=await checkSegment();await page.selectOption('#history',`/runs/${selected.run}/result.json`);await ready();assert.equal((await checkSegment()).id,selected.id);
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,segments:first.total,firstScanPoints:first.scanCount,posts,downloads,errors}));
}finally{await browser.close();}
