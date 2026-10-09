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

test('patrol includes adjacent normal bars, orders by space and excludes local ties and compound members', async () => {
  const { operatorPatrolGroups } = await import(modulePath) as typeof import('./operator-presentation')
  function entry(id: string, length: number, axis: Vector3, center: Vector3, designUnitCount = 1) {
    return { id, designUnitCount, frame: { axis, center, up: new Vector3(0, 1, 0), side: new Vector3().crossVectors(axis, new Vector3(0, 1, 0)), halfSize: new Vector3(length / 2 + .02, .024, .024), corners: [] } }
  }
  // Rotated, translated and deliberately shuffled; IDs do not encode position.
  const axis = new Vector3(1, 0, .4).normalize(), cross = new Vector3(-axis.z, 0, axis.x), origin = new Vector3(11, 2, -7)
  const entries = [3, 0, 4, 1, 2].map(i => entry('long-' + i, 4.2, i % 2 ? axis.clone().negate() : axis, origin.clone().addScaledVector(cross, i * .1)))
  entries.push(...[3, 0, 4, 1, 2].map(i => entry('short-' + i, 1.15, cross, origin.clone().addScaledVector(axis, i * .1))))
  entries.push(...[0, 1, 2].map(i => entry('tie-' + i, .28, cross, origin.clone().addScaledVector(axis, i * .1))))
  entries.push(...Array.from({ length: 20 }, (_, i) => entry('compound-' + i, 3.6, axis, origin.clone().addScaledVector(cross, i * .01), 36)))
  const original = entries.map(e => e.frame.center.toArray())
  const result = operatorPatrolGroups(entries)
  assert.deepEqual(result.longitudinal, ['long-0', 'long-1', 'long-2', 'long-3', 'long-4'])
  assert.deepEqual(result.transverse, ['short-0', 'short-1', 'short-2', 'short-3', 'short-4'])
  assert.deepEqual(entries.map(e => e.frame.center.toArray()), original)
  assert.deepEqual(operatorPatrolGroups(entries.slice(0, 5)), { longitudinal: [], transverse: [] })
})
