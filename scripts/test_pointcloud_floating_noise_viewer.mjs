import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';

const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value: 'all', textContent: ''});
  return elements.get(id);
};
const context = vm.createContext({THREE, $, Uint32Array, Float32Array,
  current: {preview:{pointCount:6}, _refinedClasses:new Uint8Array([1,3,3,3,3,3]), _refinedRegions:new Uint8Array([0,1,1,1,1,1]), _internalTypes: new Uint8Array([0,1,2,3,4,5]),
    _internalInstances: new Uint32Array([0,1,2,3,0,0]), _internalConfidence: new Float32Array(6)},
  internalRebarGeometry: new THREE.BufferGeometry(),
  internalTypeColors: {4:'#94a3b8',5:'#ef476f'},
  hardMaskVisible: () => true,
  hexColor: value => new THREE.Color(value ?? '#fff').toArray(),
  instanceColor: () => [1,1,1], fmt: String,
  updateInternalLegend() {}, rebuildInternalAxes() {}, requestRender() {},
});
vm.runInContext(source.slice(source.indexOf('// One display vocabulary;'), source.indexOf('function internalFamilyNames(')), context);
vm.runInContext(source.slice(source.indexOf('function internalColors('), source.indexOf('function updateInternalLegend(')), context);
vm.runInContext(source.slice(source.indexOf('function applyInternalRebarAppearance('), source.indexOf('function disposeFrameOverlays(')), context);
for (const [filter, expected] of [['all',[0,1,2,3,4]], ['before',[0,1,2,3,4,5]], ['noise',[5]], ['pending',[4]], ['table',[0]]]) {
  vm.runInContext(`preferredSemanticTag = '${filter}'`, context);
  $('internalColorMode').value = 'types';
  context.applyInternalRebarAppearance();
  assert.deepEqual(Array.from(context.internalRebarGeometry.index.array), expected);
}
const colors = context.internalRebarGeometry.attributes.color.array;
assert.notDeepEqual(Array.from(colors.slice(12,15)), Array.from(colors.slice(15,18)));
vm.runInContext(source.slice(source.indexOf('const fusionEvidenceBits'), source.indexOf('function updateFusionLegend(')), context);
context.current.fusion = {score:{protectionThreshold:.9, lowScoreThreshold:.5}};
context.current._fusedSteelScores = new Float32Array([0,1,.65,.25,.65,.25]);
vm.runInContext("preferredSemanticTag = 'before'", context);
$('internalColorMode').value = 'score';
context.applyInternalRebarAppearance();
const scoreColors = context.internalRebarGeometry.attributes.color.array;
assert.deepEqual(Array.from(context.internalRebarGeometry.index.array), [0,1,2,3,4,5]);
assert.deepEqual(Array.from(scoreColors.slice(9,12)), Array.from(scoreColors.slice(15,18)));
assert.notDeepEqual(Array.from(scoreColors.slice(3,6)), Array.from(scoreColors.slice(6,9)));
assert.equal(context.current._fusedSteelScores[5], .25);
context.current._fusedSteelScores[5] = 1;
context.current._fusedSteelEvidence = new Uint8Array(6).fill(3);
context.current._fusedClasses = new Uint8Array([1,3,3,3,3,3]);
assert.equal(context.pointScoreTrace(context.current, 5).protectionViolation, true, 'legacy high-score contract');
context.current.internalRebar = {denoising:{highScoreOverrideAllowed:true}};
assert.equal(context.pointScoreTrace(context.current, 5).spatialOverride, true);
assert.equal(context.pointScoreTrace(context.current, 5).protectionViolation, false);
context.current._complete = {complete_class:new Uint8Array([1,4,3,3,3,4])};
assert.equal(context.pointScoreTrace(context.current, 1).protectionViolation, true, 'Step 06 cannot silently gain the Step 05 override');
context.current._complete.complete_cluster = new Uint32Array([0,77,0,0,0,0]);
context.current.completeRebar = {designReview:{finalClusterFilter:{highScoreOverrideAllowed:true,
  decisions:[{clusterId:77,lengthRatio:.1,pointCountRatio:.05}]}}};
assert.equal(context.pointScoreTrace(context.current, 1).finalOverride, true);
assert.equal(context.pointScoreTrace(context.current, 1).protectionViolation, false);
context.current.completeRebar.designReview.finalClusterFilter.decisions[0].clusterId=78;
assert.equal(context.pointScoreTrace(context.current, 1).protectionViolation, true, 'override requires a decision for this exact cluster');
// External noise must disappear from steel even though it has no internal
// instance. The same row remains inspectable in the noise and before filters.
context.current._refinedRegions[5] = 2;
context.current._semanticCache.clear();
context.current.internalRebar.denoising.exteriorReviewEnabled = true;
for (const [filter, expected] of [['all',[0,1,2,3,4]], ['noise',[5]], ['before',[0,1,2,3,4,5]]]) {
  vm.runInContext(`preferredSemanticTag = '${filter}'`, context);
  context.applyInternalRebarAppearance();
  assert.deepEqual(Array.from(context.internalRebarGeometry.index.array), expected);
}
assert.match($('internalRebarHint').textContent, /空间复核结果|内外钢筋均已做空间去噪/);
const page = await readFile(new URL('./pointcloud-debug/index.html', import.meta.url), 'utf8');
assert.doesNotMatch(page, /hidden id="completeRebarStep"/);
assert.match(page, /hidden id="designPriorStep"/);
assert.match(page, /value="7" selected>06/);
assert.match(page.match(/<select id="throughStep">(.*?)<\/select>/s)[1], /value="7"/);
console.log('Step 05: retained/before/noise filters, noise color and Step 06 entry and withdrawn Step 07 controls passed.');
