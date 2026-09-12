import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as THREE from 'three';

const source = fs.readFileSync(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');

function extractFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `missing ${name}`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    else if (source[index] === '}' && --depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`unterminated ${name}`);
}

const context = vm.createContext({
  ArrayBuffer,
  DataView,
  Uint8Array,
  Uint32Array,
  URL,
  controls: {
    completeCompare: {value: 'result'},
    completeClassFilter: {value: 'all'},
  },
  window: {location: {href: 'http://127.0.0.1:8766/?run=20260911T120000-1234abcd'}},
});
vm.runInContext([
  "const $ = id => controls[id]; let completeTiles = {}; let completeTileFailed = false; let preferredSemanticTag = 'all'; let semanticVisibilityCustomized = false; const visibleSemanticCodes = new Set([0,1,2,3,4,5,6,7,8,9,11]);",
  extractFunction('completeTilesContract'),
  extractFunction('tileAttributeUrl'),
  extractFunction('parseCompleteTileAttributes'),
  extractFunction('completeTilesSupported'),
  extractFunction('completeTileSemanticLabel'),
  extractFunction('completeTileStyleTargets'),
  extractFunction('completeTileErrorTarget'),
].join('\n'), context);

const runId = '20260911T120000-1234abcd';
const manifest = {
  tiles: {
    schema: 'pointcloud-tiles-v1',
    mode: 'source-3d-tiles-sidecar',
    coordinateFrame: 'native LAS XYZ; source-coordinate PNTS; no RTC_CENTER',
    tilesetUrl: `/runs/${runId}/tiles/tileset.json`,
    completeRebar: {
      schema: 'pointcloud-tile-attributes-v1',
      attributeUrlTemplate: `/runs/${runId}/tile-attributes/complete-rebar/{tilePath}.bin`,
      format: {
        magic: 'PCTA', version: 1, headerBytes: 32, recordBytes: 9,
        properties: [
          {name: 'complete_class', type: 'uint8', offset: 0},
          {name: 'complete_instance', type: 'uint32', offset: 1},
          {name: 'complete_cluster', type: 'uint32', offset: 5},
        ],
      },
    },
  },
};
assert.deepEqual(
  JSON.parse(JSON.stringify(context.completeTilesContract(manifest))),
  {
    tilesetUrl: `/runs/${runId}/tiles/tileset.json`,
    attributeUrlTemplate: `/runs/${runId}/tile-attributes/complete-rebar/{tilePath}.bin`,
  },
);
assert.equal(context.completeTilesContract({...manifest, tiles: {...manifest.tiles, coordinateFrame: 'RTC'}}), null);

assert.equal(
  context.tileAttributeUrl(
    manifest.tiles.tilesetUrl,
    manifest.tiles.completeRebar.attributeUrlTemplate,
    `http://127.0.0.1:8766/runs/${runId}/tiles/level 1/tile-2.pnts`,
  ),
  `/runs/${runId}/tile-attributes/complete-rebar/level%201/tile-2.pnts.bin`,
);
assert.throws(() => context.tileAttributeUrl(
  manifest.tiles.tilesetUrl,
  manifest.tiles.completeRebar.attributeUrlTemplate,
  'http://example.com/tile.pnts',
), /不属于/);

const pointCount = 3;
const buffer = new ArrayBuffer(32 + pointCount * 9);
const bytes = new Uint8Array(buffer);
bytes.set([...'PCTA'].map(character => character.charCodeAt(0)));
const view = new DataView(buffer);
view.setUint16(4, 1, true);
view.setUint16(6, 32, true);
view.setUint32(8, pointCount, true);
view.setUint16(12, 9, true);
view.setUint16(14, 3, true);
for (const [index, [cls, instance, cluster]] of [[1, 0, 0], [3, 42, 7], [4, 0, 8]].entries()) {
  const offset = 32 + index * 9;
  view.setUint8(offset, cls);
  view.setUint32(offset + 1, instance, true);
  view.setUint32(offset + 5, cluster, true);
}
const parsed = context.parseCompleteTileAttributes(buffer);
assert.equal(parsed.pointCount, 3);
assert.deepEqual([...parsed.classes], [1, 3, 4]);
assert.deepEqual([...parsed.instances], [0, 42, 0]);
assert.deepEqual([...parsed.clusters], [0, 7, 8]);

const invalid = buffer.slice(0);
new DataView(invalid).setUint32(33, 9, true);
assert.throws(() => context.parseCompleteTileAttributes(invalid), /无效类别或实例/);

assert.equal(context.completeTilesSupported(), true);
vm.runInContext('semanticVisibilityCustomized = true', context);
assert.equal(context.completeTilesSupported(), false, 'multi-category visibility uses the complete preview for exact filtering');
vm.runInContext('semanticVisibilityCustomized = false', context);
vm.runInContext('completeTileFailed = true', context);
assert.equal(context.completeTilesSupported(), false, 'any tile failure must restore the sample fallback');
vm.runInContext("completeTileFailed = false; preferredSemanticTag = 'internal'", context);
assert.equal(context.completeTilesSupported(), false, 'unsupported detailed semantics must use the sample fallback');
vm.runInContext("controls.completeClassFilter.value = '2'", context);
assert.equal(context.completeTilesSupported(), true, 'specific result filters do not intersect the unified semantic filter');
context.THREE=THREE;
context.current={completeRebar:{clusters:[{id:7,category:'curved-exterior'}],
  designReview:{operations:[],finalClusterFilter:{decisions:[{clusterId:8}]}}}};
context.controls.completeInstanceFilter={value:'all'};
context.controls.completeColorMode={value:'classes'};
context.controls.size={value:'2'};
context.completeTileRecords=new Map();
context.refreshCompleteTilePresentation=()=>true;
context.semanticTagMatches=()=>true;
vm.runInContext([extractFunction('completeOperationSets'),extractFunction('completeTilesHideHardNoise'),extractFunction('applyCompleteTileAppearance')].join('\n'),context);
const part={object:new THREE.Points(new THREE.BufferGeometry(),new THREE.PointsMaterial()),
  classes:parsed.classes,instances:parsed.instances,clusters:parsed.clusters};
context.completeTileRecords.set('test',{ready:true,parts:[part]});
for (const [filter,expected] of [['hooks',[1]],['final-rejected',[2]]]) {
  context.controls.completeClassFilter.value=filter;
  assert.equal(context.completeTilesSupported(),true);
  context.applyCompleteTileAppearance();
  assert.deepEqual([...part.object.geometry.index.array],expected);
}
assert.deepEqual([1, 2, 3, 3, 4].map((cls, index) => context.completeTileSemanticLabel(cls, index === 3 ? 5 : 0)), [1, 2, 9, 3, 10]);
context.current.preprocessing={floatingZones:{forbiddenRule:{scope:'all-source-points'}}};
context.controls.completeClassFilter.value='all';
vm.runInContext("preferredSemanticTag = 'all'", context);
context.applyCompleteTileAppearance();
assert.deepEqual([...part.object.geometry.index.array], [0,1], 'new all-source hard mask never lets tile noise leak into all-result views');
context.controls.completeClassFilter.value='4';
context.applyCompleteTileAppearance();
assert.deepEqual([...part.object.geometry.index.array], [2], 'explicit noise filtering remains available for tile diagnostics');

const readyA = {id: 'a'}, readyB = {id: 'b'};
const records = new Map([['a', readyA], ['b', readyB]]);
assert.deepEqual([...context.completeTileStyleTargets(records, readyB)].map(record => record.id), ['b'],
  'a newly loaded tile must not restyle every cached tile');
assert.deepEqual([...context.completeTileStyleTargets(records, null)].map(record => record.id), ['a', 'b'],
  'an explicit filter change must still restyle every cached tile');
assert.ok(context.completeTileErrorTarget(true) > context.completeTileErrorTarget(false),
  'interaction must use a coarser LOD than the settled view');
assert.match(source, /record\.ready = true;[\s\S]{0,120}applyCompleteTileAppearance\(record\)/,
  'the tile load path must request incremental styling');

console.log('Tiles viewer protocol: manifest contract, safe tile paths and binary sidecar validation passed.');
