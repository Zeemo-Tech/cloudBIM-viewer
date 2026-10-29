import assert from 'node:assert/strict'
import test from 'node:test'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'
// @ts-ignore Node source runner.
import { summarizeReportMembers } from './rebarReportSummary.ts'

const bar = (id: string, status: RebarComparisonBar['status'], p95 = .01): RebarComparisonBar => ({
  ifcGlobalId: id, designBarId: id, name: id, status, instanceIds: [], pointCount: 100, pointsAfter: 100,
  vertexStart: 0, vertexCount: 100, knownCount: 80, unknownCount: 20,
  stats: { min: 0, max: .03, mean: .001, std: .002, p50: 0, p90: 0, p95: 0, p99: 0, p95Abs: p95 },
})
const diameter = (max: number, count = 3) => ({ designMm: 8, measuredMeanMm: 8, meanDeltaMm: 0, maxAbsDeltaMm: max, count, total: 12 })

test('member counts partition evidence and use inclusive independent thresholds, not a signed mean', () => {
  const bars = [bar('pass', 'matched'), bar('diameter-fail', 'matched'), bar('surface-fail', 'matched', .01001), bar('no-diameter', 'matched'), bar('missing', 'missing', 0), bar('review', 'review', 0), bar('invalid', 'matched', NaN)]
  const diameters = new Map([['pass', diameter(2)], ['diameter-fail', diameter(2.001)], ['surface-fail', diameter(1)], ['missing', diameter(0)], ['review', diameter(0)]])
  const before = structuredClone(bars)
  const result = summarizeReportMembers(bars, diameters, 10, 2)
  assert.deepEqual([result.total, result.matched, result.missing, result.review], [7, 5, 1, 1])
  assert.deepEqual([result.surfaceEligible, result.surfacePass, result.surfaceFail, result.surfaceUnknown], [4, 3, 1, 3])
  assert.deepEqual([result.diameterMeasured, result.diameterPass, result.diameterFail, result.diameterUnknown, result.validSections], [3, 2, 1, 4, 9])
  assert.deepEqual([result.bothEligible, result.bothPass], [3, 1])
  assert.equal(result.surfacePassRatio, 3 / 4)
  assert.equal(result.diameterPassRatio, 2 / 3)
  assert.deepEqual(bars, before)
})

test('missing thresholds and empty evidence do not invent pass rates or zero deviations', () => {
  const bars = [bar('a', 'matched')], diameters = new Map([['a', diameter(0)]])
  for (const limit of [undefined, null, NaN, -1]) {
    const result = summarizeReportMembers(bars, diameters, 10, limit)
    assert.equal(result.diameterMeasured, 1)
    assert.equal(result.diameterEligible, 0)
    assert.equal(result.diameterPassRatio, null)
    assert.equal(result.diameterUnknown, 1)
  }
  assert.equal(summarizeReportMembers(bars, diameters, 10, 0).diameterPass, 1)
  assert.equal(summarizeReportMembers([], new Map(), 10, 2).surfacePassRatio, null)
  bars[0]!.knownCount = 0
  assert.equal(summarizeReportMembers(bars, new Map([['a', diameter(NaN)]]), 10, 2).surfaceEligible, 0)
  assert.equal(summarizeReportMembers(bars, new Map([['a', diameter(0, 0)]]), 10, 2).diameterMeasured, 0)
})

test('chapter subtotals conserve global counts while rates use eligible members', () => {
  const chapters = [[bar('a', 'matched')], [bar('b', 'matched', .02), bar('c', 'missing')], [bar('d', 'review')]]
  const map = new Map([['a', diameter(1)], ['b', diameter(3)]])
  const rows = chapters.map(bars => summarizeReportMembers(bars, map, 10, 2))
  const total = summarizeReportMembers(chapters.flat(), map, 10, 2)
  for (const field of ['total', 'matched', 'missing', 'review', 'surfacePass', 'surfaceFail', 'surfaceUnknown', 'diameterMeasured', 'diameterPass', 'diameterFail', 'diameterUnknown', 'validSections', 'bothPass'] as const) {
    assert.equal(rows.reduce((sum, row) => sum + row[field], 0), total[field], field)
  }
})
