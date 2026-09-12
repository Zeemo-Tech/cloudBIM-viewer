import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';

const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const html = await readFile(new URL('./pointcloud-debug/index.html', import.meta.url), 'utf8');

const nav = [...html.matchAll(/data-step="([^"]+)"/g)].map(match => match[1]);
assert.deepEqual(nav.slice(0, 8), ['raw', 'normal', 'tableRemoval', 'partition', 'floatingZones', 'classification', 'projection', 'fusion']);
assert.match(html, /data-step="tableRemoval"[^>]*disabled><span class="num">01B<\/span>台面移除/);
assert.match(html, /data-step="partition"[^>]*disabled><span class="num">01C<\/span>钢筋分区/);
assert.match(html, /data-step="floatingZones"[^>]*disabled><span class="num">01D<\/span>钢筋分层与禁飞区/);
assert.match(html, /<span class="num">03<\/span>评分融合/);
assert.match(html, /id="refinementStep" data-step="refinement" hidden disabled><span class="num">04<\/span>边带与类别整理/);
assert.deepEqual([...html.matchAll(/<option value="([1-7])"(?: selected)?>/g)].slice(0, 6).map(match => match[1]), ['1','2','3','4','6','7']);
assert.match(html, /id="fusionScoreFilter"/);
assert.match(html, /<option value="low">低分可疑钢筋<\/option>/);
assert.match(html, /id="pointInspector"/);

assert.match(source, /preview\.sharedTableMaskUrl/);
assert.match(source, /preview\.partitionZonesUrl/);
assert.match(source, /preview\.sharedLayersUrl/);
assert.match(source, /preview\.sharedFloatingNoiseUrl/);
assert.match(source, /preview\.fusedSteelScoreUrl/);
assert.match(source, /preview\.fusedSteelEvidenceUrl/);
assert.match(source, /const fusedSteelScores = fusedScoreBytes \? new Float32Array\(fusedScoreBytes\) : null/);
assert.match(source, /sharedFrame = normalizedFrame\(current\.preprocessing\?\.partition\?\.frame\)/);
assert.match(source, /\(current\._fusedSteelEvidence\[index\] & 3\) === 3/);
assert.match(source, /current\._fusedSteelScores\[index\] >= threshold/);
assert.match(source, /current\._fusedClasses\[index\] === 3 && current\._fusedSteelScores\[index\] <= lowThreshold/);

const elements = new Map();
const element = () => ({value:'all', checked:false, textContent:'', style:{}, replaceChildren(...children) {this.children=children;}, append(...children) {this.children=children;}});
const $ = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
const context = vm.createContext({
  THREE, $, Uint8Array, Uint32Array, Float32Array, Number,
  current:null, requestRender(){}, fmt:String,
  document:{createElement:element},
  confidenceColor(value) { return [value, 1-value, 0]; },
});
const zoneCode = source.slice(source.indexOf('const defaultZoneNames'), source.indexOf('const internalTypeNames'));
vm.runInContext(zoneCode, context);
context.current = {
  _sharedTableMask:new Uint8Array([1,0,0,1]),
  _partitionZones:new Uint8Array([0,1,2,3]),
  preprocessing:{partition:{zoneNames:{0:'未定位',1:'内框内部',2:'夹具边带',3:'外框外部'}}},
};
context.tableRemovalGeometry = new THREE.BufferGeometry();
context.partitionGeometry = new THREE.BufferGeometry();
context.tableRemovalGeometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(12), 3));
context.partitionGeometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(12), 3));
context.applyTableRemovalAppearance();
assert.deepEqual(Array.from(context.tableRemovalGeometry.index.array), [1,2]);
$('showRemovedTable').checked = true;
context.applyTableRemovalAppearance();
assert.deepEqual(Array.from(context.tableRemovalGeometry.index.array), [0,1,2,3]);
$('partitionZoneFilter').value = '2';
context.applyPartitionAppearance();
assert.deepEqual(Array.from(context.partitionGeometry.index.array), [2]);

const fusionCode = source.slice(source.indexOf('const fusionEvidenceBits'), source.indexOf('function updateFusionLegend'));
vm.runInContext(fusionCode, context);
assert.equal(context.fusionEvidenceLabel(3), 'A 几何 + B 投影');
assert.equal(context.fusionEvidenceLabel(12), '轴线恢复 + 区域或高度候选');
assert.equal(context.fusionEvidenceLabel(0), '无明确支持证据');
context.current = {fusion:{score:{protectionThreshold:.9}}};
assert.equal(context.fusionProtectionThreshold(), .9);
context.current = {fusion:{}};
assert.equal(context.fusionProtectionThreshold(), null);
assert.equal(context.fusionLowScoreThreshold(), .5);
context.current = {fusion:{score:{lowScoreThreshold:.35}}};
assert.equal(context.fusionLowScoreThreshold(), .35);
assert.equal(context.projectionClassValueInvalid(0, {projection:{pendingClass:0}}), false);
assert.equal(context.projectionClassValueInvalid(0, {projection:{}}), true);
assert.equal(context.projectionClassValueInvalid(3, {projection:{}}), false);
assert.equal(context.isFusionPassThrough({refinement:{mode:'fusion-pass-through'}}), true);
assert.equal(context.isFusionPassThrough({refinement:{mode:'legacy-cleanup'}}), false);
const available = {refinedClasses:true, fusedClasses:true};
assert.equal(context.defaultPreviewStep({refinement:{mode:'fusion-pass-through'}}, available), 'fusion');
assert.equal(context.defaultPreviewStep({refinement:{mode:'legacy-cleanup'}}, available), 'refinement');
assert.equal(context.defaultPreviewStep({refinement:{}}, available), 'refinement');

// A hook's fitting confidence must not replace its fusion score or hide a
// downstream protection violation. Track the same array row across stages.
const traced = {
  fusion:{score:{protectionThreshold:.9}},
  _fusedClasses:new Uint8Array([3,3,3]), _refinedClasses:new Uint8Array([3,3,3]),
  _fusedSteelScores:new Float32Array([1,.25,1]), _fusedSteelEvidence:new Uint8Array([3,8,3]),
  _internalTypes:new Uint8Array([4,5,5]), _internalConfidence:new Float32Array([.1,.99,.99]),
  _complete:{complete_class:new Uint8Array([3,4,4])},
};
assert.equal(context.pointScoreTrace(traced, 0).protected, true);
assert.equal(context.pointScoreTrace(traced, 0).protectionViolation, false);
assert.equal(context.pointScoreTrace(traced, 1).score, .25);
assert.equal(context.pointScoreTrace(traced, 1).internal, 4);
assert.equal(context.pointScoreTrace(traced, 1).protected, false);
assert.equal(context.pointScoreTrace(traced, 2).protectionViolation, true);
assert.equal(context.pointScoreTrace({}, 0), null);
const scoreColors = context.fusionScoreColors(new Float32Array([.25,.65,1]), traced);
assert.notDeepEqual(Array.from(scoreColors.slice(3,6)), Array.from(scoreColors.slice(6,9)));
assert.deepEqual(Array.from(scoreColors.slice(6,9)), Array.from(new Float32Array(new THREE.Color('#22c55e').toArray())));
assert.match(html, /id="internalScoreColorOption">03 融合支持分数/);
assert.match(html, /id="completeScoreColorOption">03 融合支持分数/);

console.log('Shared preprocessing stages and fusion scores: navigation, legacy-safe loading, masks, zones, filters and evidence labels passed.');
