import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import * as THREE from 'three';
import { buildInstancePalette, instanceColorDistance } from '../packages/viewer-core/src/features/rebar-visualization/instancePalette.js';

const distance = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));
const bar = (id, y, start = 0, end = 3) => ({ id, paths: [[[start, y, 0], [end, y, 0]]] });

test('nearby parallel bars stay distinct with sparse IDs that previously had similar hues', () => {
  const bars = Array.from({ length: 24 }, (_, i) => bar(1 + i * 34, i * .025));
  const colors = buildInstancePalette(bars);
  for (let i = 1; i < bars.length; i++) {
    // RGB distance alone rejects some visually distinct light/dark pairs and
    // accepts similar hues. Check the perceptual separation used for coloring.
    assert.ok(instanceColorDistance(colors.get(bars[i-1].id), colors.get(bars[i].id)) >= .18);
  }
  assert.deepEqual(buildInstancePalette([...bars].reverse()), colors, 'input order does not change colors');
  assert.deepEqual(buildInstancePalette(bars), colors, 'reopening the same result is deterministic');
});

test('overlapping long chords cannot lose adjacency when shorter rods occupy all four nearest slots', () => {
  const bars = [2, 45, 88].flatMap((id, layer) => [
    { id, paths: [[[0, layer * .3, 0], [3.6, layer * .3, 0]]] },
    ...[-.015, -.005, .005, .015].map((gap, index) => ({
      id: 100 + layer * 4 + index,
      paths: [[[1, layer * .3 + gap, 0], [1.15, layer * .3 + gap, 0]]],
    })),
  ]);
  const colors = buildInstancePalette(bars);
  for (const [a, b] of [[2, 45], [2, 88], [45, 88]]) {
    assert.ok(instanceColorDistance(colors.get(a), colors.get(b)) >= .18);
  }
});

test('parallel neighbors remain distinct despite many closer crossing bars', () => {
  const bars = [bar(1, 0), bar(35, .02), ...Array.from({length: 12}, (_, i) => ({
    id: 100 + i, paths: [[[.1 + i * .2, -1, 0], [.1 + i * .2, 1, 0]]],
  }))];
  const colors = buildInstancePalette(bars);
  assert.ok(distance(colors.get(1), colors.get(35)) > .5);
});

test('overlapping ends of staggered long bars and bent paths get contrasting colors', () => {
  const bars = [bar(1, 0, 0, 10), bar(35, .02, 9, 20),
    {id: 69, paths: [[[5, 1, 0], [5, .03, 0], [6, .03, 0]]]}];
  const colors = buildInstancePalette(bars);
  assert.ok(distance(colors.get(1), colors.get(35)) > .5);
  assert.ok(distance(colors.get(1), colors.get(69)) > .5);
});

test('empty/invalid geometry and reserved IDs are safe; repeated segments share one instance', () => {
  assert.equal(buildInstancePalette([]).size, 0);
  const colors = buildInstancePalette([bar(0, 0), bar(0xffffffff, 0), bar(-1, 0),
    {id: 2, paths: [[[NaN, 0, 0], [1, 0, 0]]]}, {id: 3, paths: [[]]},
    bar(1, 0), bar(1, .01), bar(35, .02), bar(69, 0, 1, 1)]);
  assert.deepEqual([...colors.keys()], [1, 35, 69]);
  assert.ok([...colors.values()].flat().every(v => Number.isFinite(v) && v >= 0 && v <= 1));
});

const source = fs.readFileSync(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  const next = source.indexOf('\nfunction ', start + 1);
  return source.slice(start, next);
}

test('workbench sample, centerline and tile lookup share cached instance colors, separate from cluster IDs', () => {
  const context = vm.createContext({ THREE, buildInstancePalette });
  vm.runInContext(`
    let current;
    let spatialInstancePalettes = { internalRebar: new Map(), completeRebar: new Map() };
    const completeTileColorCache = new Map();
    const internalTypeColors = {4:'#94a3b8'};
    ${['hexColor', 'instanceColor', 'rebarInstanceColor', 'rebuildSpatialInstancePalettes', 'completeTileColorBytes'].map(extract).join('\n')}
  `, context);
  const segments = [1, 35, 69].map((id, i) => ({instanceId: id, startM: [0, i*.02, 0], endM: [3, i*.02, 0]}));
  context.manifest = {internalRebar: {segments}, completeRebar: {segments}};
  vm.runInContext('current = manifest; rebuildSpatialInstancePalettes()', context);
  for (const id of [1, 35, 69]) {
    const sample = context.rebarInstanceColor(id, 'completeRebar');
    assert.deepEqual([...context.completeTileColorBytes(id)], [...sample].map(v => Math.round(v * 255)));
    assert.deepEqual([...context.completeTileColorBytes(id, true)], [...context.instanceColor(id)].map(v => Math.round(v * 255)));
    assert.equal(context.rebarInstanceColor(id, 'completeRebar'), sample, 'lookup does not allocate or recolor');
  }
  vm.runInContext('current = {}; rebuildSpatialInstancePalettes()', context);
  assert.deepEqual([...context.completeTileColorBytes(35)], [...context.instanceColor(35)].map(v => Math.round(v * 255)), 'new results clear cached colors');
});
