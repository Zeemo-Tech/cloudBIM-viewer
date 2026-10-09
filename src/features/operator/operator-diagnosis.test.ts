import assert from 'node:assert/strict'
import { test } from 'node:test'
import { PerspectiveCamera, Vector3 } from 'three'
import type { RebarComparisonBar, RebarProfileSample } from '@cloudbim/viewer-core'
const modulePath = './operator-diagnosis.ts'
const { operatorDiagnosis, operatorOffset, operatorOffsetText } = await import(modulePath) as typeof import('./operator-diagnosis')
const sample = (delta: [number, number, number], quality = 'supported'): RebarProfileSample => ({ designUnitId: 'unit', stationM: .3, designCenterM: [11, 2, -7], observedCenterM: [11 + delta[0], 2 + delta[1], -7 + delta[2]], offsetVectorM: delta, transverseOffsetM: Math.hypot(...delta), radiusM: .0043, radiusDeltaM: .0003, quality })
function bar(profile: RebarProfileSample[], bow: number | null = null, quality = 'insufficient-coverage'): RebarComparisonBar {
  return { ifcGlobalId: 'a', designBarId: 'a', name: 'a', status: 'matched', instanceIds: [], pointCount: 10, pointsAfter: 10, vertexStart: 0, vertexCount: 3, knownCount: 3, unknownCount: 0, stats: null,
    measurement: { surface: { maxAbs: .1, maxLocationM: null }, longitudinalProfile: profile, bending: { maxCentrelineDepartureM: .1, residualBowM: bow, curvatureMInv: null, quality, method: 'cross-section-circle-fit-v1' } } }
}
test('direction agrees with asymmetric operator top-view points, without applying engineeringRoot rotation', () => {
  const origin = new Vector3(11, 2, -7), camera = new PerspectiveCamera(42, 2.4, .001, 100)
  camera.up.set(0, 1, 0); camera.position.copy(origin).add(new Vector3(0, 10, .01)); camera.lookAt(origin); camera.updateMatrixWorld()
  const projected = origin.clone().project(camera)
  assert.ok(origin.clone().add(new Vector3(1, 0, 0)).project(camera).x > projected.x)
  assert.ok(origin.clone().add(new Vector3(0, 0, 1)).project(camera).y < projected.y)
  assert.equal(operatorOffsetText([.012, -.003, -.004]), '俯视向右 12.0 mm、向上 4.0 mm；降低 3.0 mm')
  assert.equal(operatorOffsetText([-.012, .003, .004]), '俯视向左 12.0 mm、向下 4.0 mm；抬高 3.0 mm')
  const fallback = sample([.012, -.003, -.004]); delete fallback.offsetVectorM
  const actual = operatorOffset(fallback)!; for (let i = 0; i < 3; i++) assert.ok(Math.abs(actual[i]! - [.012, -.003, -.004][i]!) < 1e-10)
})
test('uses the worst supported local offset, doubles signed radius difference, and never calls total departure bending', () => {
  const result = operatorDiagnosis(bar([sample([.01, .002, 0]), sample([.2, .3, 0], 'unstable-section')]), 'outlier')
  assert.match(result.lines[0]!.text, /向右 10.0 mm；抬高 2.0 mm/)
  assert.match(result.lines[1]!.text, /8.6 mm（设计 8.0 mm），较设计粗 0.6 mm/)
  assert.match(result.lines[2]!.text, /暂不能判断是否过弯/)
  assert.match(result.coverage!, /1 \/ 2/)
  const bend = operatorDiagnosis(bar([sample([.01, 0, 0])], .0046, 'supported'), 'outlier')
  assert.match(bend.lines[2]!.text, /4.6 mm/); assert.match(bend.lines[2]!.detail!, /未设置弯曲限值/)
})
test('missing, review, malformed or legacy values do not manufacture a diagnosis or zero measurement', () => {
  const known = bar([sample([.02, 0, 0])], .01, 'supported')
  assert.match(operatorDiagnosis(known, 'missing').title, /尚不能确认少筋/)
  assert.match(operatorDiagnosis(known, 'review').lines[1]!.text, /暂不判断/)
  const invalid = sample([.02, 0, 0]); invalid.observedCenterM = null
  const legacy = sample([.02, 0, 0]); delete legacy.quality
  assert.equal(operatorOffset(invalid), null); assert.equal(operatorOffset(legacy), null)
  const unknown = operatorDiagnosis(bar([invalid, legacy]), 'outlier')
  assert.match(unknown.lines[0]!.text, /不能确定偏移方向/); assert.match(unknown.lines[1]!.text, /暂不能判断/)
  const bad = sample([.02, 0, 0]); bad.radiusDeltaM = NaN
  assert.match(operatorDiagnosis(bar([bad], .003, 'insufficient-coverage'), 'outlier').lines[2]!.text, /暂不能判断/)
})
