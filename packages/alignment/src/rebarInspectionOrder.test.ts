import assert from 'node:assert/strict'
import test from 'node:test'
import { Box3, PerspectiveCamera, Vector3 } from 'three'
// @ts-ignore Node's source runner needs explicit extensions.
import { orderRebarInspection, REBAR_INSPECTION_TILT } from './rebarInspectionOrder.ts'

function bar(ifcGlobalId: string, x: number, z: number, length = 1, y = .2) {
  return { ifcGlobalId, bounds: new Box3().setFromCenterAndSize(new Vector3(x, y, z), new Vector3(.016, .016, length)) }
}
const ids = (bars: { ifcGlobalId: string }[]) => bars.map(item => item.ifcGlobalId)
const order = (bars: ReturnType<typeof bar>[]) => orderRebarInspection(bars, item => item.bounds)

test('shuffled input follows adjacent short bars left-to-right, then lower rows, then long bars', () => {
  const expected = [
    bar('top-left', -.7, -.8, 1.004), bar('top-middle', .1, -.804, 1.012), bar('top-right', .9, -.802, 1),
    bar('bottom-left', -.7, .6, 1.01), bar('bottom-right', .9, .6, 1),
    bar('long-top', 0, -1, 2.8), bar('long-bottom', 0, 1, 2.8),
  ]
  const originalBounds = expected.map(item => item.bounds.clone())
  // The same layout must give the same route regardless of result/IFC ordering.
  for (let offset = 0; offset < expected.length; offset++) {
    const shuffled = [...expected.slice(offset), ...expected.slice(0, offset)].reverse()
    const original = [...shuffled]
    assert.deepEqual(ids(order(shuffled)), ids(expected))
    assert.deepEqual(shuffled, original, 'the report/result array is not reordered')
  }
  expected.forEach((item, index) => assert.ok(item.bounds.equals(originalBounds[index]!)))
})

test('left/up sorting agrees with asymmetric points in the actual fixed inspection camera', () => {
  const camera = new PerspectiveCamera(50, 1.6, .01, 500)
  camera.position.set(0, 10 * Math.cos(REBAR_INSPECTION_TILT), 10 * Math.sin(REBAR_INSPECTION_TILT))
  camera.up.set(0, 1, 0)
  camera.lookAt(0, 0, 0)
  camera.updateMatrixWorld(true)
  const left = bar('left', -.37, -.63)
  const right = bar('right', .83, -.63)
  const bottom = bar('bottom', -.37, .41)
  const project = (item: ReturnType<typeof bar>) => item.bounds.getCenter(new Vector3()).project(camera)
  assert.ok(project(left).x < project(right).x)
  assert.ok(project(left).y > project(bottom).y)
  assert.deepEqual(ids(order([bottom, right, left])), ['left', 'right', 'bottom'])
  const raised = bar('raised', -.37, -.63, 1, .8)
  assert.ok(project(raised).y > project(left).y)
  assert.deepEqual(ids(order([left, raised])), ['raised', 'left'])
})

test('length and row bands do not chain together through pairwise tolerances', () => {
  assert.deepEqual(ids(order([
    bar('short', 1, 0, 1), bar('same-size-left', 0, 0, 1.03), bar('longer-left', -1, 0, 1.06),
  ])), ['same-size-left', 'short', 'longer-left'])
  assert.deepEqual(ids(order([
    bar('top', 1, 0), bar('same-row-left', 0, .015), bar('next-row-left', -1, .03),
  ])), ['same-row-left', 'top', 'next-row-left'])
})

test('submillimetre horizontal drift does not reverse vertically stacked neighbours', () => {
  assert.deepEqual(ids(order([
    bar('lower-left', .0637, -.882, 1.76, -.118),
    bar('upper-left', .0641, -.881, 1.76, -.055),
    bar('upper-right', .4641, -.881, 1.76, -.055),
    bar('lower-right', .4637, -.882, 1.76, -.118),
  ])), ['upper-left', 'lower-left', 'upper-right', 'lower-right'])
})

test('unavailable/nonfinite bounds remain at the end with stable ties and no dropped bars', () => {
  const valid = bar('valid', 0, 0)
  const items = [
    { ifcGlobalId: 'missing-z', bounds: null },
    { ifcGlobalId: 'invalid', bounds: new Box3(new Vector3(NaN, 0, 0), new Vector3(1, 1, 1)) },
    valid, { ifcGlobalId: 'missing-a', bounds: new Box3() },
  ]
  assert.deepEqual(ids(orderRebarInspection(items, item => item.bounds)), ['valid', 'invalid', 'missing-a', 'missing-z'])
  assert.deepEqual(ids(order([bar('z', 0, 0), bar('a', 0, 0)])), ['a', 'z'])
  assert.deepEqual(order([]), [])
})
