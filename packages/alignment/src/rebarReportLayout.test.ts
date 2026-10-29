import assert from 'node:assert/strict'
import test from 'node:test'
import { Matrix4, Vector3 } from 'three'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'
// @ts-ignore Node source runner.
import { buildRebarReportLayout, buildReportLocator, reportPlanPoint } from './rebarReportLayout.ts'
import type { ReportDesignInventory } from './rebarReportLayout'

function fixture() {
  const inventory: ReportDesignInventory = { bars: [], units: [] }
  const bars: RebarComparisonBar[] = []
  const add = (key: string, a: [number, number, number], b: [number, number, number], kind = 'straight') => {
    inventory.bars.push({ ifcGlobalId: key, designBarId: key, name: key, points: [a, b], radiusM: .004 })
    inventory.units!.push({ designBarId: key, designUnitId: key + '/unit', startM: a, endM: b, kind })
    bars.push({ ifcGlobalId: key, designBarId: key, name: key, instanceIds: [], reviewInstanceIds: [], pointCount: 0, reviewPointCount: 0, pointsAfter: 0, vertexStart: 0, vertexCount: 0, knownCount: 0, unknownCount: 0, stats: null, status: 'missing' })
  }
  add('x-lower', [0, .025, -1.1], [1.5, .025, -1.1])
  add('x-upper', [1.5, .15, -.8], [0, .15, -.8]) // reversed endpoints
  add('y-lower', [.4, .04, 0], [.4, .04, -2.7]) // crossing bars in the same mat
  add('y-upper', [.6, .162, 0], [.6, .162, -2.7])
  add('link', [.4, .035, -.3], [.4, .155, -.3], 'web')
  return { bars, inventory }
}

test('design geometry defines three chapters and two physical mats, independent of source order', () => {
  const { bars, inventory } = fixture()
  const a = buildRebarReportLayout(bars, inventory, new Matrix4())
  const b = buildRebarReportLayout([...bars].reverse(), inventory, new Matrix4())
  assert.deepEqual(a.orderedKeys, b.orderedKeys)
  assert.deepEqual(a.chapters.map(c => [c.id, c.start, c.keys.length]), [['transverse', 0, 2], ['longitudinal', 2, 2], ['connector', 4, 1]])
  assert.equal(a.layers.length, 2)
  assert.match(a.members.get('x-lower')!.layerLabel, /第 1 层（下层）/)
  assert.match(a.members.get('y-upper')!.layerLabel, /第 2 层（上层）/)
  assert.deepEqual(a.members.get('link')!.layers, [1, 2])
  assert.match(a.members.get('link')!.layerLabel, /连接第 1、2 层/)
})

test('navigation transforms inventory exactly once and preserves an asymmetric plan under rotation', () => {
  const { bars, inventory } = fixture()
  const alignment = new Matrix4().makeRotationY(.37).setPosition(3, .07, -5)
  const layout = buildRebarReportLayout(bars, inventory, alignment)
  const expected = new Vector3(1.5, .025, -1.1).applyMatrix4(alignment)
  assert.ok(layout.members.get('x-lower')!.path[1]!.distanceTo(expected) < 1e-12)
  assert.deepEqual(reportPlanPoint(new Vector3(2, 3, -5)), { x: 2000, y: 5000 })
  assert.equal(layout.members.get('y-lower')!.chapterId, 'longitudinal')
  assert.equal(layout.layers.length, 2)
  const locator = buildReportLocator(layout, 'link')
  assert.equal((locator.markup.match(/data-layer="locator-current"/g) ?? []).length, 1)
  assert.match(locator.markup, /第 1 层/)
  assert.match(locator.markup, /第 2 层/)
  assert.doesNotMatch(locator.markup, /NaN|Infinity/)
  assert.deepEqual(inventory.bars[0]!.points![1], [1.5, .025, -1.1])
})

test('unavailable design evidence is not guessed from a bar name or algorithm layer number', () => {
  const { bars } = fixture()
  const layout = buildRebarReportLayout([bars[0]!])
  assert.equal(layout.chapters[0]!.id, 'unresolved')
  assert.equal(layout.members.get('x-lower')!.layerLabel, '层位未判定')
  assert.match(buildReportLocator(layout, 'x-lower').markup, /未就绪/)
})
