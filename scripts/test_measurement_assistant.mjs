import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, writeFileSync, rmSync, mkdirSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import ts from 'typescript'
import * as THREE from 'three'

mkdirSync('.cloudbim', { recursive: true })
const temp = mkdtempSync('.cloudbim/measurement-session-test-')
process.on('exit', () => rmSync(temp, { recursive: true, force: true }))
for (const name of ['types', 'geometry', 'picking', 'evidence', 'MeasurementAssistant']) {
  const source = readFileSync(`packages/viewer-core/src/viewer/measurement/${name}.ts`, 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
  writeFileSync(`${temp}/${name}.mjs`, js.replace(/from (['"])(\.\/[^'"]+)\1/g, 'from $1$2.mjs$1'))
}
const load = name => import(pathToFileURL(`${process.cwd()}/${temp}/${name}.mjs`))
for (const name of ['tableVisibility','displayFrame']) {
  const source=readFileSync(`packages/viewer-core/src/features/pointcloud/${name}.ts`,'utf8')
  const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
  writeFileSync(`${temp}/${name}.mjs`,js.replace('../../viewer/measurement/evidence.ts','./evidence.mjs').replace('./tableVisibility.ts','./tableVisibility.mjs'))
}
const { MeasurementAssistant } = await load('MeasurementAssistant')
const { defaultMeasurementState } = await load('types')
const { resolveMeasurementHit, pickMeasurementPoint, sampleMeasurementNeighbourhood } = await load('picking')
const { transformMeasurementEvidence } = await load('evidence')
const { transformMeasurement } = await load('displayFrame')
const near = (a, b, eps = 1e-8) => assert.ok(Math.abs(a-b) < eps, `${a} != ${b}`)
const nearPoint = (a,b) => a.forEach((v,i) => near(v,b[i]))
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(50, 4/3, .01, 100)
camera.position.set(0,0,5); camera.lookAt(0,0,0); camera.updateMatrixWorld(true)
const cloud = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([.012,.021,0, -.1,-.2,.3, .1,.2,-.3],3)), new THREE.PointsMaterial())
const root = new THREE.Group(); root.add(cloud); scene.add(root)
root.position.set(.3,-.2,.1); root.rotation.set(.12,.23,.31); root.scale.set(1.2,.8,1.5); scene.updateMatrixWorld(true)
const expected = new THREE.Vector3().fromBufferAttribute(cloud.geometry.attributes.position,0).applyMatrix4(cloud.matrixWorld)
nearPoint(resolveMeasurementHit({object:cloud,index:0,point:new THREE.Vector3(99,99,99)}).toArray(),expected.toArray())
const settings = defaultMeasurementState().settings
const context = {camera,width:800,height:600,targets:[{object:root,kind:'pointcloud',assetId:14}],contains:()=>true,settings}
const projected=expected.clone().project(camera), x=(projected.x+1)*400,y=(1-projected.y)*300
const hit = pickMeasurementPoint(context,x+1,y+1)
assert.ok(hit);nearPoint(hit.point.toArray(),expected.toArray());assert.equal(hit.source.vertexIndex,0)
assert.equal(hit.source.assetId,14);nearPoint(hit.source.localPoint,[.012000000104308128,.020999999716877937,0])
assert.equal(pickMeasurementPoint({...context,contains:()=>false},x,y),null)
root.visible=false;assert.equal(pickMeasurementPoint(context,x,y),null);root.visible=true
assert.equal(pickMeasurementPoint({...context,settings:{...settings,sourceFilter:'bim'}},x,y),null)
cloud.material.clippingPlanes=[new THREE.Plane(new THREE.Vector3(1,0,0),-2)]
assert.equal(pickMeasurementPoint(context,x,y),null)
assert.equal(sampleMeasurementNeighbourhood(context,hit,2).points.length,0)
cloud.material.clippingPlanes=[]
// Parent scale must not change the pixel tolerance (including the mm-to-m case).
for (const scale of [.001, 1000]) {
  const parent=new THREE.Group(), point=new THREE.Points(new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute([.013,.021,0],3)),new THREE.PointsMaterial())
  parent.scale.set(scale,scale*.8,scale*1.2);parent.add(point);scene.add(parent);parent.updateMatrixWorld(true)
  const world=new THREE.Vector3().fromBufferAttribute(point.geometry.attributes.position,0).applyMatrix4(point.matrixWorld)
  const p=world.clone().project(camera), candidate=pickMeasurementPoint({...context,targets:[{object:parent,kind:'pointcloud',assetId:14}]},(p.x+1)*400+4,(1-p.y)*300)
  assert.ok(candidate,`parent scale ${scale}: four-pixel candidate`);nearPoint(candidate.point.toArray(),world.toArray());parent.removeFromParent()
}
const design = cloud.clone();design.geometry=cloud.geometry.clone();root.add(design)
const mixed={...context,targets:[{object:cloud,kind:'pointcloud',assetId:14},{object:design,kind:'bim',assetId:13}]}
assert.equal(sampleMeasurementNeighbourhood(mixed,hit,2).points.length,3,'design vertices must not enter scan fit')
console.log('PASS picking: actual vertex, asymmetric full transform, source filter, visibility, clipping and source isolation')

let anchor=null, state, committed=[]
const orbit={target:new THREE.Vector3(),update(){camera.lookAt(this.target);camera.updateMatrixWorld(true)}}
const assistant = new MeasurementAssistant({element:()=>({clientHeight:600,getBoundingClientRect:()=>({left:0,top:0,width:800,height:600})}),
  camera:()=>camera,scene:()=>scene,orbit:()=>orbit,targets:()=>context.targets,contains:()=>true,references:()=>[],anchor:()=>anchor,
  onState:s=>state=s,onRecord:(kind,record)=>committed.push({kind,record}),onCoordinates:p=>anchor=p,onUndo:()=>anchor=null,requestRender(){}})
assistant.setMode('distance');assistant.coordinates({point:[1,2,3],relative:false})
assert.equal(state.canUndo,true)
assistant.coordinates({point:[.5,-.25,1],relative:true});nearPoint(anchor.toArray(),[1.5,1.75,4])
assistant.undo();assert.equal(anchor,null)
assistant.setMode('geometry');assistant.setTool('angle')
for(const p of [[2,1,0],[1,1,0],[1,4,0]])assistant.coordinates({point:p,relative:false})
near(committed.at(-1).record.measurement.value,90);assert.equal(committed.at(-1).record.measurement.sources.length,3)
assistant.coordinates({point:[1,1,0],relative:false});assistant.coordinates({point:[1,1,0],relative:false});assistant.coordinates({point:[1,2,0],relative:false})
assert.ok(state.error.includes('零长度'));assert.equal(state.pickedCount,2);assistant.undo();assert.equal(state.pickedCount,1)
assistant.reset()
assistant.setTool('line-angle')
for(const p of [[0,0,0],[1,0,0],[2,2,1],[3,3,1]])assistant.coordinates({point:p,relative:false})
near(committed.at(-1).record.measurement.value,45)
// Use real raycasts on two independently transformed surfaces for signed normal distance.
root.visible=false
const plane=new THREE.Mesh(new THREE.PlaneGeometry(4,4),new THREE.MeshBasicMaterial({side:THREE.DoubleSide}))
const second=plane.clone();second.position.z=.7;scene.add(plane,second);scene.updateMatrixWorld(true)
context.targets=[{object:plane,kind:'bim',assetId:13}]
assistant.setTool('point-plane');assistant.commit({clientX:400,clientY:300,altKey:false})
assert.equal(state.hasPlane,true)
context.targets=[{object:second,kind:'bim',assetId:13}]
assistant.commit({clientX:400,clientY:300,altKey:false})
near(committed.at(-1).record.distance,.7);near(committed.at(-1).record.measurement.value,.7)
assistant.flip();assistant.commit({clientX:400,clientY:300,altKey:false});near(committed.at(-1).record.measurement.value,-.7)
const beforeCamera=camera.position.toArray(),beforeTarget=orbit.target.toArray()
assistant.faceOn();assert.equal(state.canRestoreView,true);assistant.restoreView()
nearPoint(camera.position.toArray(),beforeCamera);nearPoint(orbit.target.toArray(),beforeTarget)
assistant.setTool('plane-plane');context.targets=[{object:plane,kind:'bim',assetId:13}]
assistant.commit({clientX:400,clientY:300,altKey:false});context.targets=[{object:second,kind:'bim',assetId:13}]
assistant.commit({clientX:400,clientY:300,altKey:false});near(committed.at(-1).record.distance,.7)
assistant.setTool('plane-angle');context.targets=[{object:plane,kind:'bim',assetId:13}]
assistant.commit({clientX:400,clientY:300,altKey:false});second.rotation.y=.25;second.updateMatrixWorld(true)
context.targets=[{object:second,kind:'bim',assetId:13}]
assistant.commit({clientX:400,clientY:300,altKey:false});near(committed.at(-1).record.measurement.value,.25*180/Math.PI)
second.rotation.y=0;second.updateMatrixWorld(true)
assistant.setTool('frame');assistant.commit({clientX:400,clientY:300,altKey:false});assert.equal(state.hasFrame,true)
assistant.setTool('pick');assistant.coordinates({point:[.1,.2,.3],relative:false})
const e=committed.at(-1).record.measurement, frame=e.frame
const expectedLocal=new THREE.Vector3(...frame.origin).addScaledVector(new THREE.Vector3(...frame.x),.1).addScaledVector(new THREE.Vector3(...frame.y),.2).addScaledVector(new THREE.Vector3(...frame.z),.3)
nearPoint(e.anchors[0],expectedLocal.toArray())
const matrix=new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(.3,-.5,.7)).setPosition(12,-3,7)
const original=structuredClone(e), transformed=transformMeasurementEvidence(e,matrix)
nearPoint(transformed.anchors[0],new THREE.Vector3(...e.anchors[0]).applyMatrix4(matrix).toArray())
assert.deepEqual(transformed.sources[0].localPoint,e.sources[0].localPoint)
const roundtrip=transformMeasurementEvidence(transformed,matrix.clone().invert())
nearPoint(roundtrip.anchors[0],e.anchors[0]);nearPoint(roundtrip.frame.z,e.frame.z);assert.deepEqual(e,original)
assistant.clearFrame();assistant.setTool('cylinder-distance');assistant.setSettings({radius:.45,tolerance:.001})
for(const cx of [-.6,.6]) {
  const positions=[]
  for(let k=0;k<60;k++)for(let j=0;j<17;j++)positions.push(cx+.12*Math.cos(k*Math.PI/30),-.3+.6*j/16,.12*Math.sin(k*Math.PI/30))
  const cylinder=new THREE.Points(new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute(positions,3)),new THREE.PointsMaterial())
  scene.add(cylinder);scene.updateMatrixWorld(true);context.targets=[{object:cylinder,kind:'pointcloud',assetId:14}]
  const p=new THREE.Vector3(cx,0,.12).project(camera)
  assistant.commit({clientX:(p.x+1)*400,clientY:(1-p.y)*300,altKey:false})
}
assert.equal(committed.at(-1).record.measurement.type,'cylinder-distance');near(committed.at(-1).record.distance,1.2,1e-5)
assert.ok(committed.at(-1).record.measurement.referenceCylinder)
for (const type of ['frame','point-plane','cylinder-distance']) {
  const record=committed.find(r=>r.record.measurement.type===type).record, snapshot=structuredClone(record)
  const transformedRecord=transformMeasurement(record,matrix), restored=transformMeasurement(transformedRecord,matrix.clone().invert())
  nearPoint(restored.measurement.anchors[0],record.measurement.anchors[0])
  assert.deepEqual(restored.measurement.sources[0].localPoint,record.measurement.sources[0].localPoint)
  if(record.measurement.geometry?.kind==='plane')nearPoint(restored.measurement.geometry.normal,record.measurement.geometry.normal)
  if(record.measurement.referenceCylinder)nearPoint(restored.measurement.referenceCylinder.axis,record.measurement.referenceCylinder.axis)
  assert.deepEqual(record,snapshot,'persistence conversion leaves original display record untouched')
}
assistant.syncSaved(committed.map(r=>r.record));assert.ok(scene.getObjectByName('Saved measurement geometry').children.length)
assistant.syncSaved([]);assert.equal(scene.getObjectByName('Saved measurement geometry').children.length,0)
assistant.dispose()
assert.equal(scene.getObjectByName('Measurement assistance'),undefined)
console.log('PASS session: exact inputs/deltas, angle/undo, signed point-plane/flip, view restoration, local frame, immutable saved evidence transform and cleanup')
