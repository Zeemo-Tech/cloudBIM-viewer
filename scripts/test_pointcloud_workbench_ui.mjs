import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const html = await readFile(new URL('./pointcloud-debug/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');

assert.match(html, /data-step="raw" hidden/, 'the raw point cloud is not a visible step');
assert.match(html, /class="rerunButton" id="run"[^>]*>↻ 重新运行<\/button>/, 'a simple rerun action remains visible');
assert.match(html, /id="compare" type="checkbox">/, 'source comparison is off by default');
assert.doesNotMatch(html, /id="compare"[^>]*checked/, 'source comparison must stay off');
assert.match(source, /let compareSource = false/, 'the renderer uses one result viewport');
assert.match(html, /id="resultColorMode"[\s\S]*?value="layers" selected>分层着色<[\s\S]*?value="categories">类别着色<[\s\S]*?value="instances">逐个实例着色</,
  'the simplified sidebar keeps all three result coloring modes');
assert.match(source, /step === 'internalRebar'[\s\S]*step === 'completeRebar'/,
  'instance coloring is limited to the two steps that have instance IDs');

const categoryNames = [...source.matchAll(/\{id:'(?:allSteel|cleanSteel|table|fixture|internal|external|upper|web|lower|noise)', name:'([^']+)'/g)]
  .map(([, name]) => name);
assert.deepEqual(categoryNames, ['全部钢筋', '全部钢筋（去噪后）', '台面', '夹具', '内部钢筋', '外部钢筋', '上层钢筋', '腹杆', '下层钢筋', '噪音']);
assert.match(source, /className = 'visibilityToggle'/, 'every rendered category includes an independent visibility switch');

const start = source.indexOf('function stepSemanticData(');
const end = source.indexOf('\nfunction semanticTagAvailable(', start);
assert.ok(start >= 0 && end > start, 'semantic mapping function exists');
const context = vm.createContext({Map, Uint8Array, Uint32Array});
vm.runInContext(`${source.slice(start, end)}\nthis.stepSemanticData = stepSemanticData;`, context);
const manifest = {
  preview: {pointCount: 8},
  _classes: new Uint8Array([1, 2, 3, 3, 3, 3, 3, 4]),
  _sharedTableMask: new Uint8Array([1, 0, 0, 0, 0, 0, 0, 0]),
  _partitionZones: new Uint8Array([0, 2, 1, 3, 1, 1, 1, 1]),
  _sharedLayers: new Uint8Array([0, 0, 0, 0, 2, 3, 1, 0]),
  classification: {},
};
const result = context.stepSemanticData(manifest, 'classification');
assert.deepEqual([...result.labels], [1, 2, 4, 5, 7, 8, 6, 10], 'one stable label is assigned to every requested category');
delete manifest._semanticCache;
const partition = context.stepSemanticData(manifest, 'partition');
assert.deepEqual([...partition.labels], [1, 2, 4, 5, 7, 8, 6, 4], 'preprocessing results use the same category vocabulary');

console.log('Workbench UI: single result view, unified categories and three coloring modes passed.');
