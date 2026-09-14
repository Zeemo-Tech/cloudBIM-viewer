import assert from 'node:assert/strict'
import test from 'node:test'
import { BufferGeometry, Float32BufferAttribute } from 'three'
import type { C2MResult, RebarComparisonBar } from '../../api/backend-c2m'
// @ts-ignore Node's source runner needs explicit extensions.
import { comparisonBarsAtTolerance, filterComparisonGeometry, rebarReportCSV } from './rebarComparison.ts'
// @ts-ignore Node's source runner needs explicit extensions.
import { parseC2MDistances, histogramFromC2MDistances } from '../../utils/c2mColormap.ts'

const bar: RebarComparisonBar = {
  ifcGlobalId: 'bar-2', designBarId: 'design-2', name: '=unsafe', instanceIds: [8, 12], pointCount: 30,
  pointsAfter: 20, vertexStart: 3, vertexCount: 3, knownCount: 2, unknownCount: 1, status: 'matched',
  stats: { min: 0, max: .02, mean: .01, std: .01, p50: .01, p90: .02, p95: .02, p99: .02, meanAbs: .01 },
}

test('selecting one steel bar isolates triangles without altering vertex/distance correspondence', () => {
  const geometry = new BufferGeometry().setAttribute('position', new Float32BufferAttribute(new Float32Array(18), 3))
  geometry.setIndex([0, 1, 2, 3, 4, 5])
  const positions = geometry.getAttribute('position')
  filterComparisonGeometry(geometry, bar)
  assert.deepEqual(Array.from(geometry.index!.array), [3, 4, 5])
  filterComparisonGeometry(geometry, { ...bar, vertexCount: 0 })
  assert.equal(geometry.index!.count, 0, 'missing geometry never shows a neighbouring bar')
  filterComparisonGeometry(geometry)
  assert.deepEqual(Array.from(geometry.index!.array), [0, 1, 2, 3, 4, 5])
  assert.equal(geometry.getAttribute('position'), positions)
  geometry.dispose()
})

test('unknown distances remain unknown and do not count as failures or histogram overflow', () => {
  const distances = new Float32Array([0, 0, 0, .001, .02, NaN])
  assert.equal(parseC2MDistances(distances.buffer, 6), null)
  assert.ok(parseC2MDistances(distances.buffer, 6, true))
  assert.equal(parseC2MDistances(new Float32Array([Infinity]).buffer, 1, true), null)
  const [updated] = comparisonBarsAtTolerance([bar], distances, .01)
  assert.equal(updated.stats?.withinToleranceRatio, .5)
  assert.equal(histogramFromC2MDistances(distances, .03, 10).counts.reduce((a: number, b: number) => a + b, 0), 5)
  assert.equal(histogramFromC2MDistances(distances, .03, 10).overflowCount, 0)
})

test('report retains IFC/instance identities, blank missing metrics, and safe spreadsheet cells', () => {
  const result = { modelScanFileId: 5, modelBimFileId: 7, resultVersion: 'version-1', metricDirection: 'mesh-vertices-to-scan-points' } as C2MResult
  const csv = rebarReportCSV(result, [bar, { ...bar, ifcGlobalId: 'missing-bar', status: 'missing', stats: null, instanceIds: [] }], 10)
  assert.ok(csv.includes('"8;12"'))
  assert.ok(csv.includes('"\'=unsafe"'))
  assert.ok(csv.includes('"version-1"'))
  assert.ok(csv.includes('"缺测"'))
  assert.ok(!csv.includes('NaN'))
})
