import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';

const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const html = await readFile(new URL('./pointcloud-debug/index.html', import.meta.url), 'utf8');
assert.match(html, /id="floatingZonesStep" data-step="floatingZones"/);
assert.match(html, /id="floatingLayerFilter"/);
assert.match(html, /id="floatingForbiddenOnly"/);
for (const id of ['floatingPlanes', 'floatingVolumes', 'steelCenterlines']) assert.match(html, new RegExp(`id="${id}"`));
assert.match(html, /05<\/span>钢筋识别/);
assert.match(source, /共享钢筋分层预览包含 Manifest 未声明的层编号/);
assert.match(source, /sharedFloatingNoise\?\.some\(\(value\) => value > 1\)/);
assert.match(source, /Class 4 is the stable cross-stage representation of removed noise/);
assert.match(source, /function normalizedOuterEnvelope/);
assert.doesNotMatch(source, /conservativeForbiddenCells/);

const elements = new Map();
const element = () => ({value:'all', checked:false, textContent:'', style:{}, replaceChildren(...children) { this.children = children; }, append(...children) { this.children = children; }});
const $ = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
const context = vm.createContext({THREE, $, Uint8Array, Uint32Array, Float32Array, Number, document:{createElement: element}, current:null, requestRender(){}, fmt:String});
context.filterIndexedGeometry = (geometry, count, predicate) => {
  const indices = new Uint32Array(count); let selected = 0;
  for (let index = 0; index < count; index += 1) if (predicate(index)) indices[selected++] = index;
  geometry.setIndex(new THREE.BufferAttribute(indices.subarray(0, selected), 1)); geometry.setDrawRange(0, selected);
  return selected;
};
context.hexColor = value => new THREE.Color(value || '#94a3b8').toArray();
const code = source.slice(source.indexOf('const floatingLayerNames'), source.indexOf('const internalTypeNames'));
vm.runInContext(code, context);
vm.runInContext(source.slice(source.indexOf('// One display vocabulary;'), source.indexOf('function internalFamilyNames(')), context);
context.current = {
  preview: {pointCount: 5},
  _sharedTableMask: new Uint8Array([1, 0, 0, 0, 0]),
  _partitionZones: new Uint8Array([0, 1, 1, 3, 2]),
  _sharedLayers: new Uint8Array([1, 1, 2, 3, 0]),
  _sharedFloatingNoise: new Uint8Array([0, 0, 1, 0, 1]),
  preprocessing:{floatingZones:{enabled:true}},
};
context.floatingZonesGeometry = new THREE.BufferGeometry();
context.floatingZonesGeometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(15), 3));
context.applyFloatingZonesAppearance();
assert.deepEqual(Array.from(context.floatingZonesGeometry.index.array), [0,1,2,3,4], 'the unified result view keeps every non-noise category visible');
context.current.preprocessing.floatingZones.forbiddenRule = {action:'review-only'};
context.applyFloatingZonesAppearance();
assert.deepEqual(Array.from(context.floatingZonesGeometry.index.array), [0,1,2,3,4], 'review candidates stay visible until noise is confirmed');
assert.equal(context.hardMaskVisible(2, 'classification'), true, 'a cloth candidate cannot hide an independent A result');
assert.equal(context.hardMaskVisible(2, 'projection'), true, 'a cloth candidate cannot hide an independent B result');
context.current.preprocessing.floatingZones.forbiddenRule = {action:'step05-residual-veto'};
context.applyFloatingZonesAppearance();
assert.deepEqual(Array.from(context.floatingZonesGeometry.index.array), [0,1,2,3,4], 'step05 veto cannot hide source rows in 01D');
assert.equal(context.hardMaskVisible(2, 'classification'), true);
assert.equal(context.hardMaskVisible(2, 'internalRebar'), true, '05 uses actual noise labels, not the raw cloth mask');
context.current.preprocessing.floatingZones.forbiddenRule = {action:'step05-steel-boundary'};
context.applyFloatingZonesAppearance();
assert.equal(context.hardMaskVisible(2, 'classification'), true, 'strict 05 boundary cannot affect 02A display');
assert.deepEqual(Array.from(context.floatingZonesGeometry.index.array), [0,1,2,3,4]);
delete context.current.preprocessing.floatingZones.forbiddenRule;
for (const [tag, expected] of [['table',[0]], ['lower',[1]], ['upper',[2]], ['external',[3]], ['fixture',[4]]]) {
  vm.runInContext(`preferredSemanticTag = '${tag}'`, context);
  context.applyFloatingZonesAppearance();
  assert.deepEqual(Array.from(context.floatingZonesGeometry.index.array), expected);
}
vm.runInContext(source.slice(source.indexOf('function hardMaskVisible'), source.indexOf('const internalTypeNames')), context);
context.current._sharedFloatingNoise = new Uint8Array([0, 1]);
vm.runInContext("preferredSemanticTag = 'all'", context);
assert.equal(context.hardMaskVisible(1, 'fusion'), false, 'downstream default excludes hard-mask points');
vm.runInContext("preferredSemanticTag = 'noise'", context);
assert.equal(context.hardMaskVisible(1, 'fusion'), true, 'explicit noise view retains hard-mask diagnostics');
context.current._sharedFloatingNoise = null;
vm.runInContext("preferredSemanticTag = 'all'", context);
assert.equal(context.hardMaskVisible(1, 'fusion'), true, 'legacy reports without a hard mask retain prior display behavior');
context.current._sharedFloatingNoise = new Uint8Array([0, 0, 1, 0, 1]);
const colors = context.floatingZoneColors(context.current._sharedLayers, context.current._sharedFloatingNoise);
assert.ok(Array.from(colors.slice(6, 9)).every((value, index) => Math.abs(value - new THREE.Color('#ef476f').toArray()[index]) < 1e-6));

const geometryCode = source.slice(source.indexOf('function finitePoint'), source.indexOf('function rebuildFloatingGeometryOverlays'));
vm.runInContext(geometryCode, context);
const envelope = {kind:'triangulated-cloth', closed:true, upperVertexCount:3,
  verticesM:[[9,9,11],[11,9,11],[10,11,12],[9,9,9],[11,9,9],[10,11,10]],
  triangles:[[0,1,2],[3,5,4],[0,3,4],[0,4,1],[1,4,5],[1,5,2],[2,5,3],[2,3,0]]};
assert.equal(context.normalizedOuterEnvelope(envelope), envelope);
assert.equal(context.normalizedOuterEnvelope({...envelope, closed:false}), null);
const shell = context.outerEnvelopeMesh(envelope, [5,5,5]);
assert.equal(shell.geometry.index.count, 24);
assert.ok(shell.geometry.attributes.normal, 'continuous shell computes smooth vertex normals');
assert.deepEqual(Array.from(shell.geometry.attributes.position.array.slice(0, 3)), [4,4,6], 'scan coordinate origin is subtracted once');
assert.equal(shell.material.color.getHex(), 0x67e8f9);
assert.equal(shell.material.flatShading, true, 'flat layer faces must not look rippled by averaged normals');
const curve = {kind:'swept-curve', closed:true, centerlineM:[[20,20,10],[21,20,12],[22,21,10]],
  verticesM:[[20,20,10],[21,20,12],[22,21,10],[21,20.2,10.8]], triangles:[[0,1,3],[1,2,3],[2,0,3],[0,2,1]]};
const composite = {kind:'composite-steel-envelope', closed:true, body:envelope, curveShells:[curve], xyAxes:[[1,0],[0,1]],
  // The aggregate mesh deliberately is not 2×upperVertexCount: it contains the swept elbow too.
  verticesM:[...envelope.verticesM, ...curve.verticesM], triangles:[...envelope.triangles, ...curve.triangles.map(triangle => triangle.map(index => index + envelope.verticesM.length))], parameters:{hookProtection:'swept-3d-curve'}};
assert.equal(context.normalizedOuterEnvelope(composite), composite, 'composite accepts merged cloth and swept shells');
const curveMesh = context.outerEnvelopeMesh(curve, [5,5,5], 0x22b8cf);
assert.equal(curveMesh.material.color.getHex(), 0x22b8cf);
assert.ok(Math.max(...Array.from(curveMesh.geometry.attributes.position.array).filter((_, index) => index % 3 === 2)) - Math.min(...Array.from(curveMesh.geometry.attributes.position.array).filter((_, index) => index % 3 === 2)) > 1, 'non-XY elbow vertices retain their original 3D rise and fall');
const sections = context.outerEnvelopeSections(envelope, [5,5,5]);
assert.ok(sections.geometry.attributes.position.count > 0);
const sectionPoints = sections.geometry.attributes.position.array;
for (let index = 0; index < sectionPoints.length; index += 6) {
  assert.ok(Math.abs(sectionPoints[index] - sectionPoints[index + 3]) < 1e-6, 'section edges lie in a true vertical cutting plane');
}
context.current = {preview:{origin:[5,5,5]}, preprocessing:{floatingZones:{outerEnvelope:composite, designSegments:[]}}};
context.floatingGeometryOverlays = [];
for (const name of ['floatingZonesScene', 'classScene', 'projectionScene', 'internalRebarScene']) context[name] = new THREE.Scene();
context.fmtHeight = value => `${value}`;
$('floatingVolumes').checked = true; $('floatingPlanes').checked = false; $('steelCenterlines').checked = true;
vm.runInContext(source.slice(source.indexOf('function disposeFloatingGeometryOverlays'), source.indexOf('function makeDashedFrame')), context);
context.rebuildFloatingGeometryOverlays();
assert.equal(context.floatingGeometryOverlays.length, 4);
for (const {group} of context.floatingGeometryOverlays) {
  const volume = group.getObjectByName('floatingVolumes');
  assert.ok(volume?.visible && volume.getObjectByName('sweptCurveShell')?.isMesh, 'one toggle owns body and swept elbow meshes');
  assert.ok(group.children.some(object => object.isHemisphereLight), 'shell lighting survives scene rebuild');
}
context.disposeFloatingGeometryOverlays();
assert.equal(context.floatingZonesScene.children.length, 0, 'rebuild disposal removes composite geometry');
console.log('01D floating zones: source-index filters, hard-mask color, closed cloth geometry and origin conversion passed.');

// Optional full-scale artifact check executes scene construction without WebGL.
if (process.argv[2]) {
  context.current = JSON.parse(await readFile(process.argv[2], 'utf8'));
  context.floatingGeometryOverlays = [];
  for (const name of ['floatingZonesScene', 'classScene', 'projectionScene', 'internalRebarScene']) context[name] = new THREE.Scene();
  $('floatingPlanes').checked = false; $('floatingVolumes').checked = true; $('steelCenterlines').checked = true;
  vm.runInContext(source.slice(source.indexOf('function disposeFloatingGeometryOverlays'), source.indexOf('function makeDashedFrame')), context);
  const started = performance.now();
  context.rebuildFloatingGeometryOverlays();
  assert.equal(context.floatingGeometryOverlays.length, 4);
  const shells = context.floatingGeometryOverlays.map(({group}) => group.getObjectByName('floatingVolumes'));
  assert.ok(shells.every(shell => shell?.isGroup && shell.visible && shell.children.every(child => child.isMesh && child.geometry.index)));
  assert.ok(context.floatingGeometryOverlays.every(({group}) => group.children.some(object => object.isHemisphereLight)), 'Phong shells have illumination in every point-cloud scene');
  assert.ok(context.floatingGeometryOverlays.every(({group}) => group.getObjectByName('floatingPlanes')?.visible === false));
  console.log(JSON.stringify({runId: context.current.runId, scenes: 4, shellVertices: shells[0].children.reduce((sum, child) => sum + child.geometry.attributes.position.count, 0), buildMs: Math.round(performance.now()-started)}));
  context.disposeFloatingGeometryOverlays();
  assert.equal(context.floatingZonesScene.children.length, 0);
}
