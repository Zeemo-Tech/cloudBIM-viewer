import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BufferGeometry, Float32BufferAttribute } from 'three'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'

// Node's type-stripping runner loads TS directly; the application uses bundler resolution.
const modulePath = './operator-geometry.ts'
const { mapOperatorGeometry, operatorBarState, validateOperatorPlySurface } = await import(modulePath) as typeof import('./operator-geometry')

function bar(id: string, start: number, count = 3): RebarComparisonBar {
  return { ifcGlobalId: id, designBarId: `design-${id}`, name: id, instanceIds: [1], pointCount: 10, pointsAfter: 10,
    vertexStart: start, vertexCount: count, knownCount: count, unknownCount: 0, status: 'matched',
    stats: { min: 0, max: 0.002, mean: 0.001, std: 0.001, p50: 0.001, p90: 0.002, p95: 0.002, p99: 0.002, p95Abs: 0.002 } }
}
function fixture(indexed = true) {
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute([
    11, 2, -7, 12, 2, -7, 11, 3, -7,
    -4, 8, 15, -3, 8, 15, -4, 10, 15,
  ], 3))
  if (indexed) geometry.setIndex([3, 4, 5, 0, 1, 2, 0, 3, 4])
  return geometry
}

test('asymmetric indexed geometry preserves metres and XYZ; only whole-range faces select bars', () => {
  const geometry = fixture()
  const input = Array.from(geometry.getAttribute('position').array)
  const mapped = mapOperatorGeometry(geometry, [bar('a', 0), bar('b', 3)], 6)
  assert.deepEqual(Array.from(geometry.getAttribute('position').array), input)
  assert.deepEqual(mapped.bounds[0]!.min.toArray(), [11, 2, -7])
  assert.deepEqual(mapped.bounds[1]!.max.toArray(), [-3, 10, 15])
  assert.deepEqual(mapped.pickIds, ['a', 'b', ''])
  assert.deepEqual(Array.from(geometry.index!.array), [0, 1, 2, 3, 4, 5, 0, 3, 4])
  assert.deepEqual(geometry.groups, [
    { start: 0, count: 3, materialIndex: 0 }, { start: 3, count: 3, materialIndex: 1 }, { start: 6, count: 3, materialIndex: 2 },
  ])
  geometry.dispose()
})

test('nonindexed triangle stream maps using its original position ranges', () => {
  const geometry = fixture(false)
  const mapped = mapOperatorGeometry(geometry, [bar('a', 0), bar('b', 3)], 6)
  assert.deepEqual(mapped.pickIds, ['a', 'b', ''])
  assert.deepEqual(Array.from(geometry.index!.array), [0, 1, 2, 3, 4, 5])
  assert.equal(geometry.groups.length, 2)
  geometry.dispose()
})

test('actual PLY parsing preserves asymmetric vertex order; vertex-only PLY cannot invent triangles', () => {
  const ply = `ply\nformat ascii 1.0\nelement vertex 6\nproperty float x\nproperty float y\nproperty float z\nelement face 2\nproperty list uchar int vertex_indices\nend_header\n11 2 -7\n12 2 -7\n11 3 -7\n-4 8 15\n-3 8 15\n-4 10 15\n3 3 4 5\n3 0 1 2\n`
  const buffer = new TextEncoder().encode(ply).buffer
  validateOperatorPlySurface(buffer)
  const geometry = new PLYLoader().parse(buffer)
  const mapped = mapOperatorGeometry(geometry, [bar('a', 0), bar('b', 3)], 6)
  assert.deepEqual(mapped.bounds[0]!.min.toArray(), [11, 2, -7])
  assert.deepEqual(mapped.bounds[1]!.max.toArray(), [-3, 10, 15])
  const pointCloud = ply.replace('element face 2\nproperty list uchar int vertex_indices\n', '').replace('3 3 4 5\n3 0 1 2\n', '')
  assert.throws(() => validateOperatorPlySurface(new TextEncoder().encode(pointCloud).buffer), /没有钢筋三角面/)
  geometry.dispose()
})

test('invalid, overlapping, mismatched and cross-range-only mappings reject localization', () => {
  assert.throws(() => mapOperatorGeometry(fixture(), [bar('a', 0)], 7), /顶点与检测记录不一致/)
  assert.throws(() => mapOperatorGeometry(fixture(), [bar('a', 5)], 6), /范围越界/)
  assert.throws(() => mapOperatorGeometry(fixture(), [bar('a', 0), bar('b', 2)], 6), /范围重叠/)
  assert.throws(() => mapOperatorGeometry(fixture(), [bar('a', 0), bar('a', 3)], 6), /编号重复/)
  const invalidIndex = fixture(); invalidIndex.setIndex([0, 1, 6])
  assert.throws(() => mapOperatorGeometry(invalidIndex, [bar('a', 0)], 6), /引用无效顶点/)
  const crossed = fixture(); crossed.setIndex([0, 3, 4])
  assert.throws(() => mapOperatorGeometry(crossed, [bar('a', 0), bar('b', 3)], 6), /无法对应钢筋记录/)
})

test('missing/review/unknown-tolerance bars never imply passing; P95 uses absolute deviation', () => {
  const a = bar('a', 0)
  assert.equal(operatorBarState(a, 0.003), 'measured')
  assert.equal(operatorBarState(a, 0.001), 'outlier')
  assert.equal(operatorBarState({ ...a, knownCount: 0 }, 0.003), 'missing')
  assert.equal(operatorBarState({ ...a, status: 'review' }, 0.003), 'review')
  assert.equal(operatorBarState({ ...a, stats: null }, 0.003), 'review')
  assert.equal(operatorBarState(a, null), 'review')
  assert.equal(operatorBarState({ ...a, stats: { ...a.stats!, p95Abs: undefined } }, 0.003), 'review')
})
