import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';

const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const html = await readFile(new URL('./pointcloud-debug/index.html', import.meta.url), 'utf8');
assert.match(html, /data-step="designPrior"/);
assert.match(html, /id="priorMode"/);
assert.match(source, /priorMode: \$\('priorMode'\)\.value/);
assert.match(source, /manifest\.designPrior\?\.enabled/); // old manifests omit this report and remain loadable
assert.doesNotMatch(source, /inventory\?\.units\?\.some/);
const code = source.slice(source.indexOf('function priorColor('), source.indexOf('async function loadCompletePreview('));
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, { value:'all', checked:false, hidden:false, replaceChildren(...children) { this.children=children; } });
  return elements.get(id);
};
const arrays = {
  prior_class:new Uint8Array([1, 3, 3, 4]), prior_instance:new Uint32Array([0, 9, 9, 0]),
  prior_component:new Uint32Array([0, 1, 2, 3]), prior_status:new Uint8Array([0, 1, 2, 3]), prior_action:new Uint8Array([0, 2, 5, 1]),
};
const manifest = {preview:{pointCount:4,origin:[10,20,30],...Object.fromEntries(Object.keys(arrays).map(k=>[`${k}Url`,k]))}, files:{}, designPrior:{enabled:true,mode:'topology',counts:{matched:1,pending:1,noise:1},timings:{priorS:.1},instances:[{id:9}],components:[{id:1,instanceId:9,status:1,action:2,designBarId:'IfcGlobalId-A:representation/17',designUnitId:'U1'},{id:2,instanceId:9,status:2,action:5,designBarId:'IfcGlobalId-A:representation/17',designUnitId:'U2'},{id:3,status:3,action:1,designBarId:null,designUnitId:null,family:3}],inventory:{bars:[{designBarId:'IfcGlobalId-A:representation/17'}],units:[{designBarId:'IfcGlobalId-A:representation/17',designUnitId:'U1',kind:'straight',startM:[10,20,30],endM:[11,20,30]},{designBarId:'IfcGlobalId-A:representation/17',designUnitId:'U2',kind:'short',startM:[10,20,30],endM:[10,21,30]}],coverage:{designBars:1,complete:1,partial:0,unresolved:0,designDiagonalUnits:2,observedDiagonalInstances:2}}}};
const sandbox = vm.createContext({THREE,$,Uint8Array,Uint32Array,Float32Array,Number,Set,Map,Object,Array,Promise,
  fetchBytes:async key=>arrays[key].buffer, fmt:v=>String(v), hexColor:v=>new THREE.Color(v).toArray(), instanceColor:id=>new THREE.Color().setHSL(id ? (id*.2)%1 : 0, .7, .5).toArray(), requestRender:()=>{}, document:{createElement:()=>({})}, designPriorScene:new THREE.Scene(), designPriorPoints:new THREE.Points(), designPriorGeometry:null, designPriorLines:null, current:null,
});
vm.runInContext(code, sandbox);
const loaded = await sandbox.loadDesignPriorPreview(manifest);
assert.equal(loaded.prior_action[2], 5);
sandbox.current={...manifest,_designPrior:loaded,_positions:new Float32Array(12),_complete:{complete_class:new Uint8Array([1,3,3,4]),complete_instance:new Uint32Array([0,7,8,0])},_internalFamilies:new Uint8Array(4)};
sandbox.installDesignPriorPreview();
$('priorCompare').value='result'; $('priorColorMode').value='status'; $('priorFilter').value='pending'; $('priorLines').checked=true;
sandbox.applyDesignPriorAppearance();
assert.deepEqual(Array.from(sandbox.designPriorGeometry.index.array), [2]);
assert.deepEqual(Array.from(sandbox.designPriorLines.geometry.attributes.position.array.slice(0,3)), [0,0,0]);
$('priorFilter').value='recovered'; sandbox.applyDesignPriorAppearance(); assert.deepEqual(Array.from(sandbox.designPriorGeometry.index.array), [1]);
$('priorFilter').value='merged'; sandbox.applyDesignPriorAppearance(); assert.deepEqual(Array.from(sandbox.designPriorGeometry.index.array), [1,2]);
$('priorCompare').value='baseline'; sandbox.applyDesignPriorAppearance(); assert.deepEqual(Array.from(sandbox.designPriorGeometry.index.array), [1,2]); $('priorCompare').value='result';
$('priorFilter').value='short'; sandbox.applyDesignPriorAppearance(); assert.deepEqual(Array.from(sandbox.designPriorGeometry.index.array), [2,3]);
// A component with no design unit can still use its observed short-bar family.
arrays.prior_component[3]=3; $('priorFilter').value='short'; sandbox.applyDesignPriorAppearance(); assert.deepEqual(Array.from(sandbox.designPriorGeometry.index.array), [2,3]); arrays.prior_component[3]=3;
$('priorFilter').value='all'; $('priorColorMode').value='design'; sandbox.applyDesignPriorAppearance();
assert.deepEqual(Array.from(sandbox.designPriorLines.geometry.attributes.color.array.slice(0,3)), Array.from(sandbox.designPriorLines.geometry.attributes.color.array.slice(6,9)));
const bad = structuredClone(manifest); arrays.prior_status = new Uint8Array([0,4,2,3]); await assert.rejects(sandbox.loadDesignPriorPreview(bad), /无效/); arrays.prior_status = new Uint8Array([0,1,2,3]);
const duplicateUnit = structuredClone(manifest); duplicateUnit.designPrior.inventory.units.push({...duplicateUnit.designPrior.inventory.units[0]}); await assert.rejects(sandbox.loadDesignPriorPreview(duplicateUnit), /无效/);
const unknownInstance = structuredClone(manifest); arrays.prior_instance = new Uint32Array([0,99,9,0]); await assert.rejects(sandbox.loadDesignPriorPreview(unknownInstance), /无效/); arrays.prior_instance = new Uint32Array([0,9,9,0]);
const legacy = structuredClone(manifest); legacy.designPrior.enabled=false; assert.equal(await sandbox.loadDesignPriorPreview(legacy), null);
console.log('Design prior preview: loader validation, baseline/result filtering, short bars and origin-adjusted design lines passed.');

if (process.argv[2]) {
  const root = process.argv[2];
  const real = JSON.parse(await readFile(`${root}/manifest.json`, 'utf8'));
  sandbox.fetchBytes = async url => {
    const buffer = await readFile(`${root}/preview/${url.split('/').pop()}`);
    return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
  };
  const loadedReal = await sandbox.loadDesignPriorPreview(real);
  assert.equal(loadedReal.prior_class.length, real.preview.pointCount);
  console.log(`Design prior real preview validated: ${real.runId}, ${real.preview.pointCount} points.`);
}
