import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BufferGeometry, Float32BufferAttribute, Vector3 } from 'three'
const modulePath = './operator-presentation.ts'
const { operatorBarFrame, operatorBoxEdges, operatorFitDistance, operatorAxisHeading, operatorResultColor, resultPalette } = await import(modulePath) as typeof import('./operator-presentation')

test('oriented bounds preserve asymmetric XYZ metres and contain bent bar geometry in a right-handed frame', () => {
  const center = new Vector3(11, 2, -7), axis = new Vector3(3, 0, 4).normalize(), up = new Vector3(0, 1, 0), side = axis.clone().cross(up)
  const values: number[] = []
  for (const x of [-2, -.7, 1, 2]) for (const y of [-.01, .01]) for (const z of [-.004, .004]) values.push(...center.clone().addScaledVector(axis, x).addScaledVector(up, y + (x === 2 ? .02 : 0)).addScaledVector(side, z).toArray())
  const geometry = new BufferGeometry().setAttribute('position', new Float32BufferAttribute(values, 3))
  const original = Array.from(geometry.getAttribute('position').array)
  const frame = operatorBarFrame(geometry, 0, values.length / 3)!
  assert.ok(Math.abs(frame.axis.dot(axis)) > .9999)
  assert.ok(frame.axis.clone().cross(frame.up).dot(frame.side) > .999999)
  assert.ok(frame.center.distanceTo(center) < .02)
  for (let i = 0; i < original.length; i += 3) {
    const p = new Vector3(...original.slice(i, i + 3) as [number, number, number]).sub(frame.center)
    assert.ok(Math.abs(p.dot(frame.axis)) <= frame.halfSize.x)
    assert.ok(Math.abs(p.dot(frame.up)) <= frame.halfSize.y)
    assert.ok(Math.abs(p.dot(frame.side)) <= frame.halfSize.z)
  }
  assert.deepEqual(Array.from(geometry.getAttribute('position').array), original)
  assert.equal(operatorBoxEdges(frame).length, 12 * 2 * 3)
  for (const aspect of [.6, 2.4]) {
    const direction = axis.clone().add(up).normalize(), distance = operatorFitDistance(frame, direction, 42, aspect)
    const right = up.clone().cross(direction).normalize(), vertical = direction.clone().cross(right), tan = Math.tan(42 * Math.PI / 360)
    for (const corner of frame.corners) {
      const p = corner.clone().sub(frame.center), depth = distance - p.dot(direction)
      assert.ok(depth > 0)
      assert.ok(Math.abs(p.dot(right)) / (depth * tan * aspect) < 1)
      assert.ok(Math.abs(p.dot(vertical)) / (depth * tan) < 1)
    }
  }
  assert.ok(Math.abs(operatorAxisHeading(frame, .8) - Math.atan2(frame.axis.x, frame.axis.z)) < 1e-8)
  geometry.dispose()
})

test('deviation boundaries are display-only; missing and review take precedence', () => {
  const t = .01
  assert.equal(operatorResultColor('measured', t, t), resultPalette.measured)
  assert.equal(operatorResultColor('outlier', t + 1e-8, t), resultPalette.low)
  assert.equal(operatorResultColor('outlier', t * 1.5, t), resultPalette.low)
  assert.equal(operatorResultColor('outlier', t * 1.5 + 1e-8, t), resultPalette.medium)
  assert.equal(operatorResultColor('outlier', t * 2, t), resultPalette.medium)
  assert.equal(operatorResultColor('outlier', t * 2 + 1e-8, t), resultPalette.high)
  assert.equal(operatorResultColor('missing', .05, t), resultPalette.missing)
  assert.equal(operatorResultColor('review', .001, t), resultPalette.review)
  assert.equal(operatorResultColor('measured', NaN, t), resultPalette.review)
  assert.equal(operatorResultColor('measured', .001, null), resultPalette.review)
})
