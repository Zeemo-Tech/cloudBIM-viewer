import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';

const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const elements = new Map();
const element = () => ({value:'all', checked:false, replaceChildren(...children) {this.children=children;}, append(...children) {this.children=children;}, style:{}});
const $ = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
const current = {
  preview:{pointCount:9},
  _classes:new Uint8Array([1,2,3,3,3,3,3,3,3]),
  _projectionClasses:new Uint8Array([1,2,3,3,3,3,3,3,3]),
  _projectionLayers:new Uint8Array([0,0,1,1,1,2,2,0,0]),
  _fusedClasses:new Uint8Array([1,2,3,3,3,3,3,3,3]),
  _fusedRegions:new Uint8Array([0,3,2,1,1,1,1,1,4]),
  _fusedSteelScores:new Float32Array([0,.2,.4,.95,.5,1,.3,.8,.45]),
  _fusedSteelEvidence:new Uint8Array([0,0,1,3,2,3,8,1,2]),
  _refinedClasses:new Uint8Array([1,2,3,3,3,3,3,3,3]),
  _refinedRegions:new Uint8Array([0,3,2,1,1,1,1,1,4]),
  _refinedZones:new Uint8Array([0,2,3,1,1,1,1,1,0]),
  _refinedChanged:new Uint8Array([0,0,0,0,0,1,0,0,0]),
  _internalTypes:new Uint8Array([0,0,0,1,2,3,4,5,0]),
  _internalInstances:new Uint32Array([0,0,0,1,2,3,0,0,0]),
  _internalConfidence:new Float32Array(9),
  fusion:{score:{protectionThreshold:.9,lowScoreThreshold:.5}},
};
const context = vm.createContext({THREE, $, current, rightScene:'internalRebar',
  Uint8Array, Uint32Array, Float32Array,
  Option:class {constructor(text, value) {this.text=text; this.value=value;}},
  document:{createElement:element},
  hexColor:color => new THREE.Color(color).toArray(), fmt:String,
  hardMaskVisible:()=>true,
  updateInternalLegend(){}, rebuildInternalAxes(){}, updateRefinementLegend(){}, updateFusionLegend(){}, requestRender(){},
  classGeometry:new THREE.BufferGeometry(), projectionGeometry:new THREE.BufferGeometry(),
  fusionGeometry:new THREE.BufferGeometry(), refinementGeometry:new THREE.BufferGeometry(), internalRebarGeometry:new THREE.BufferGeometry(),
});
const extract = (start, end) => vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))), context);
extract('// One display vocabulary;', 'function internalFamilyNames(');
extract('const fusionEvidenceBits', 'function updateFusionLegend');
extract('function applyFusionAppearance()', 'const projectionImageNotes');
extract('function applyRefinementAppearance()', 'const fusionEvidenceBits');
extract('function applyInternalRebarAppearance()', 'function validCorners(');
$('internalColorMode').value='types';

const expectedNames = ['全部（不含噪音）','全部（含噪音）','全部钢筋','内部钢筋','外部钢筋','上层钢筋','腹杆','下层钢筋','夹具','台面','噪音','待定候选','未定位钢筋','未分类'];
for (const step of ['raw','normal','classification','projection','fusion','refinement','internalRebar']) {
  context.rightScene=step;
  context.updateSemanticControls();
  assert.deepEqual($('semanticFilter').children.map(option => option.text), expectedNames);
  const disabled = id => $('semanticFilter').children.find(option => option.value===id).disabled;
  assert.equal(disabled('upper'), step!=='internalRebar');
  assert.equal(disabled('noise'), step!=='internalRebar');
  assert.equal(disabled('external'), !['fusion','refinement','internalRebar'].includes(step));
}

function select(step, tag) {
  context.rightScene=step;
  vm.runInContext(`preferredSemanticTag = '${tag}'`, context);
  context.updateSemanticControls();
  context.applyCurrentSemanticFilter();
  const geometry = context[({classification:'class', projection:'projection', fusion:'fusion', refinement:'refinement', internalRebar:'internalRebar'})[step]+'Geometry'];
  return Array.from(geometry.index.array);
}
for (const step of ['classification','projection','fusion','refinement','internalRebar']) {
  assert.deepEqual(select(step,'fixture'),[1]);
  assert.deepEqual(select(step,'table'),[0]);
}
assert.deepEqual(select('internalRebar','steel'),[2,3,4,5,6,8]);
assert.deepEqual(select('internalRebar','internal'),[3,4,5,6]);
assert.deepEqual(select('internalRebar','external'),[2]);
assert.deepEqual(select('internalRebar','upper'),[4]);
assert.deepEqual(select('internalRebar','web'),[5]);
assert.deepEqual(select('internalRebar','lower'),[3]);
assert.deepEqual(select('internalRebar','pending'),[6]);
assert.deepEqual(select('internalRebar','noise'),[7]);
assert.deepEqual(select('internalRebar','all'),[0,1,2,3,4,5,6,8]);
assert.deepEqual(select('internalRebar','before'),[0,1,2,3,4,5,6,7,8]);
// Earlier steps must keep their own steel labels, even after noise is known.
assert.deepEqual(select('refinement','steel'),[2,3,4,5,6,7,8]);
select('internalRebar','upper');
context.rightScene='classification'; context.updateSemanticControls();
assert.equal($('semanticFilter').value,'all');
assert.match($('semanticHint').textContent,/上层钢筋/);
context.rightScene='internalRebar'; context.updateSemanticControls();
assert.equal($('semanticFilter').value,'upper');

$('projectionLayerFilter').value='2';
assert.deepEqual(select('projection','steel'),[5,6]);
$('refinementChangedOnly').checked=true;
assert.deepEqual(select('refinement','internal'),[5]);
$('refinementChangedOnly').checked=false;
for (const step of ['fusion','refinement','internalRebar']) {
  select(step,'external');
  const geometry = context[step==='fusion' ? 'fusionGeometry' : step==='refinement' ? 'refinementGeometry' : 'internalRebarGeometry'];
  assert.deepEqual(Array.from(geometry.attributes.color.array.slice(6,9)), Array.from(new Float32Array(new THREE.Color('#f472b6').toArray())));
}

vm.runInContext("preferredSemanticTag = 'all'", context);
$('fusionScoreFilter').value='low';
context.applyFusionAppearance();
assert.deepEqual(Array.from(context.fusionGeometry.index.array),[2,4,6,8]);
$('fusionScoreFilter').value='high';
context.applyFusionAppearance();
assert.deepEqual(Array.from(context.fusionGeometry.index.array),[3,5]);
$('fusionScoreFilter').value='both';
context.applyFusionAppearance();
assert.deepEqual(Array.from(context.fusionGeometry.index.array),[3,5]);
$('fusionScoreFilter').value='all';

const pendingClassification = context.stepSemanticData({
  preview:{pointCount:2}, classification:{pendingClass:0}, _classes:new Uint8Array([0,3]),
}, 'classification');
assert.deepEqual(Array.from(pendingClassification.labels),[9,3]);
const legacyClassification = context.stepSemanticData({
  preview:{pointCount:2}, classification:{}, _classes:new Uint8Array([0,3]),
}, 'classification');
assert.deepEqual(Array.from(legacyClassification.labels),[0,3]);
const pendingProjection = context.stepSemanticData({
  preview:{pointCount:2}, projection:{pendingClass:0}, _projectionClasses:new Uint8Array([0,3]),
}, 'projection');
assert.deepEqual(Array.from(pendingProjection.labels),[9,3]);

const completeData = context.stepSemanticData({
  _complete:{complete_class:new Uint8Array([1,2,3,3,3,3,4,3]),complete_instance:new Uint32Array([0,0,1,2,3,0,0,1])},
  _refinedZones:new Uint8Array([0,2,1,1,1,1,1,3]),
  completeRebar:{instances:[{id:1,type:1},{id:2,type:2},{id:3,type:3}]},
},'completeRebar');
assert.deepEqual(Array.from(completeData.labels),[1,2,6,7,8,9,10,5]);
assert.equal(completeData.counts[10],1);
// Use the actual million-point artifact when provided, without recomputing it.
if (process.argv[2]) {
  const root=process.argv[2];
  const manifest=JSON.parse(await readFile(`${root}/manifest.json`,'utf8'));
  const data={preview:manifest.preview};
  for (const [key,file] of Object.entries({_classes:'classes', _projectionClasses:'projection_classes', _fusedClasses:'fused_classes', _fusedRegions:'fused_regions', _refinedClasses:'refined_classes', _refinedRegions:'refined_regions', _internalTypes:'internal_types'})) {
    data[key]=new Uint8Array(await readFile(`${root}/preview/${file}.bin`));
  }
  const stage=context.stepSemanticData(data,'internalRebar');
  for (const type of [1,2,3,4,5]) {
    const code=[0,6,7,8,9,10][type];
    assert.equal(stage.counts[code],data._internalTypes.filter(value=>value===type).length);
  }
  assert.equal(stage.labels.length,manifest.preview.pointCount);
  assert.equal(stage.counts.reduce((a,b)=>a+b,0),manifest.preview.pointCount);
  console.log(`Real preview: ${stage.labels.length} rows, ${stage.counts[10]} noise, ${stage.counts[2]} fixture, ${stage.counts[5]} exterior steel.`);
}
console.log('Unified taxonomy: fixed names, unavailable labels, cross-step selection, own-stage evidence, all filters, diagnostic intersections and stable colors passed.');
