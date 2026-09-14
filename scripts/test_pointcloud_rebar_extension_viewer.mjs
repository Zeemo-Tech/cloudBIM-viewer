import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';
import { buildInstancePalette } from '../src/features/rebar-visualization/instancePalette.js';

// Exercise the actual preview loader and Three.js filtering code without a GPU.
const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('async function loadCompletePreview('), source.indexOf('function updateInternalLegend('));
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value:'all', checked:false, replaceChildren(...children) {this.children=children;}});
  return elements.get(id);
};
const cls = new Uint8Array([1,2,3,3,3,4]);
const ids = new Uint32Array([0,0,1,1,0,0]);
const data = {complete_class:cls, complete_instance:ids, complete_segment:ids, complete_confidence:new Float32Array([0,0,1,.8,0,0])};
const manifest = {preview:{pointCount:6, origin:[0,0,0], ...Object.fromEntries(Object.keys(data).map(k=>[k+'Url',k]))},
  completeRebar:{enabled:true,instances:[{id:1,lengthM:1,extensionLengthM:.1}],segments:[{id:1,instanceId:1,startM:[0,0,0],endM:[1,0,0]}],counts:{table:1,fixture:1,rebar:3,noise:1}},files:{}};
const sandbox = vm.createContext({THREE, $, Uint8Array, Uint32Array, Float32Array, Number, Set, Map, Object, Promise,
  buildInstancePalette, spatialInstancePalettes: { internalRebar: new Map(), completeRebar: new Map() }, completeTileColorCache: new Map(),
  fetchBytes:async name=>data[name].buffer, Option:function(text,value){this.text=text;this.value=value;},
  document:{createElement:()=>({})}, fmt:String, requestRender:()=>{},
  hexColor:hex=>new THREE.Color(hex).toArray(), internalTypeColors:{4:'#94a3b8'},
  hardMaskVisible:()=>true,
  semanticVisibilityCustomized:false, visibleSemanticCodes:new Set([0,1,2,3,4,5,6,7,8,9,11]),
  current:null, completeGeometry:null, completeAxisLines:null, completeRebarScene:new THREE.Scene(),
  completePoints:new THREE.Points(),
});
vm.runInContext(code,sandbox);
assert.equal(await sandbox.loadCompletePreview({}),null);
sandbox.current={...manifest,_complete:await sandbox.loadCompletePreview(manifest),_positions:new Float32Array(18),_internalInstances:new Uint32Array([0,0,1,0,0,0])};
sandbox.rebuildSpatialInstancePalettes();
sandbox.installCompletePreview();
$('completeColorMode').value='instances';
sandbox.applyCompleteAppearance();
assert.equal(sandbox.completeGeometry.index.count,6);
for (const [filter,expected] of [['resolved',[2,3]],['3',[2,3,4]],['4',[5]],['extended',[3]],['pending',[4]]]) {
  $('completeClassFilter').value=filter;sandbox.applyCompleteAppearance();
  assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),expected);
}
$('completeClassFilter').value='3';$('completeInstanceFilter').value='1';$('completeAxes').checked=true;
sandbox.applyCompleteAppearance();
assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),[2,3]);
assert.equal(sandbox.completeAxisLines.geometry.attributes.position.count,2);
const assignedColor = sandbox.rebarInstanceColor(1, 'completeRebar');
for (let channel = 0; channel < 3; channel++) {
  assert.ok(Math.abs(sandbox.completeGeometry.attributes.color.array[2 * 3 + channel] - assignedColor[channel]) < 1e-6);
  assert.ok(Math.abs(sandbox.completeAxisLines.geometry.attributes.color.array[channel] - assignedColor[channel]) < 1e-6);
}
sandbox.clearCompleteAxes();assert.equal(sandbox.completeAxisLines,null);
const malformed=structuredClone(manifest);delete malformed.preview.complete_classUrl;
await assert.rejects(sandbox.loadCompletePreview(malformed), /缺少/);
data.complete_instance=new Uint32Array([0,0,9,1,0,0]);
await assert.rejects(sandbox.loadCompletePreview(manifest), /无效/);
data.complete_instance=ids;
const clusterManifest=structuredClone(manifest);
clusterManifest.completeRebar.clusters=[{id:7}];
clusterManifest.preview.complete_clusterUrl='complete_cluster';
data.complete_cluster=new Uint32Array([0,0,0,7,0,0]);
sandbox.current={...sandbox.current, ...clusterManifest, _complete:await sandbox.loadCompletePreview(clusterManifest)};
sandbox.installCompletePreview();
$('completeColorMode').value='clusters';$('completeClassFilter').value='all';$('completeInstanceFilter').value='all';
sandbox.applyCompleteAppearance();
assert.equal($('completeClusterColorOption').disabled,false);
assert.equal(sandbox.completeGeometry.index.count,6);
data.complete_cluster[3]=8;
await assert.rejects(sandbox.loadCompletePreview(clusterManifest), /簇编号无效/);
// Guided Step 06: baseline ownership, operation filters and separate downloads.
data.complete_cluster[3]=7;
sandbox.current = {...sandbox.current, _refinedClasses:new Uint8Array([1,2,3,3,3,3]),
  _internalTypes:new Uint8Array([0,0,1,1,4,1]), _internalInstances:new Uint32Array([0,0,1,2,0,3]),
  internalRebar:{instances:[{id:1},{id:2},{id:3}],segments:manifest.completeRebar.segments},
  completeRebar:{...manifest.completeRebar,designReview:{operations:[{action:'merge',sourceInstanceIds:[1,2],acrossFixture:true}]}},
  files:{resolvedSteelLasUrl:'/resolved.las',pendingSteelLasUrl:'/pending.las'}};
$('completeCompare').value='result';sandbox.installCompletePreview();
// Count equality alone is not success when a design unit has no observed owner.
sandbox.current.completeRebar.designReview.clusterQuality={expectedClusterCount:2,observedClusterCount:2,
  countDelta:0,countMatches:false,shapeMismatchCount:1,tooShortCount:1,hookWidthMissingCount:0};
sandbox.current.completeRebar.designReview.finalDenoising={removedComponentCount:3,removedPointCount:7};
sandbox.current.completeRebar.designReview.hookClusters={expectedRegionCount:1,detectedClusterCount:1,
  mergedClusterCount:1,splitClusterCount:0,filteredPointCount:0,
  nonHookTerminalPolish:{removedPointCount:4,fixtureContactPointCount:12}};
sandbox.installCompletePreview();
const summary=$('completeSummary').children.map(node=>node.textContent);
assert(summary.includes('目标簇数（腹杆逐段） / 实际簇数：2 / 2'));
assert(summary.includes('数量与逐根对应：待核对（差 0）'));
assert(summary.includes('末尾细小悬浮噪音：3 簇 / 7 点'));
assert(summary.includes('弯钩实例直端圆柱打磨：4 点 / 复核 12 个夹具接触点'));
assert.equal($('resolvedSteelLas').href,'/resolved.las');
assert.equal($('pendingSteelLas').href,'/pending.las');
for (const [filter,expected] of [['filtered',[5]],['merged',[2,3]],['bridged',[2,3]],['resolved',[2,3]]]) {
  $('completeClassFilter').value=filter;sandbox.applyCompleteAppearance();
  assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),expected);
}
sandbox.current.completeRebar.designReview.operations=[{action:'attach',phase:'before_filter',instanceIds:[1]}];
$('completeClassFilter').value='bridged';sandbox.applyCompleteAppearance();
assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),[2,3]);
$('completeCompare').value='baseline';sandbox.rebuildCompleteInstanceOptions();
assert.equal($('completeInstanceFilter').children.length,4);
$('completeClassFilter').value='resolved';sandbox.applyCompleteAppearance();
assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),[2,3,5]);
$('completeInstanceFilter').value='3';sandbox.applyCompleteAppearance();
assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),[5]);
$('completeCompare').value='result';sandbox.rebuildCompleteInstanceOptions();
assert.equal($('completeInstanceFilter').value,'all');
// A separated source cluster includes both its retained rod and rejected edge.
sandbox.current._complete.complete_cluster[5]=7;
sandbox.current.completeRebar.designReview.operations=[{action:'separate',clusterId:7}];
$('completeClassFilter').value='separated';sandbox.applyCompleteAppearance();
assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),[3,5]);
$('completeCompare').value='result';
sandbox.current._complete.complete_cluster[5]=8;
sandbox.current.completeRebar.clusters=[{id:7,category:'curved-exterior'},{id:8,category:'rejected-final-cluster'}];
sandbox.current.completeRebar.designReview.finalClusterFilter={decisions:[{clusterId:8}]};
for (const [filter,expected] of [['hooks',[3]],['final-rejected',[5]]]) {
  $('completeClassFilter').value=filter;sandbox.applyCompleteAppearance();
  assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),expected);
}
$('completeClassFilter').value='separated';
sandbox.current._complete.complete_cluster[5]=7;
$('completeCompare').value='baseline';sandbox.applyCompleteAppearance();
assert.deepEqual(Array.from(sandbox.completeGeometry.index.array),[3,5]);
console.log('Extension preview: loading, legacy absence, filters, instance selection, axes and malformed data passed.');

if (process.argv[2]) {
  const root=process.argv[2];
  const real=JSON.parse(await readFile(`${root}/manifest.json`, 'utf8'));
  sandbox.fetchBytes=async url=>{
    const buffer=await readFile(`${root}/preview/${url.split('/').pop()}`);
    return buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.byteLength);
  };
  const loaded=await sandbox.loadCompletePreview(real);
  assert.equal(loaded.complete_class.length,real.preview.pointCount);
  console.log(`Full run preview validated: ${real.runId}, ${real.preview.pointCount} points.`);
}
