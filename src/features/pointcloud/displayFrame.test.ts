import assert from 'node:assert/strict'
import test from 'node:test'
import * as THREE from 'three'
// @ts-ignore Node strip types uses explicit extensions.
import { pointcloudDisplayMatrix, transformMeasurement } from './displayFrame.ts'
// @ts-ignore Node strip types uses explicit extensions.
import { PointcloudTableVisibility, validPointcloudTablePlane } from './tableVisibility.ts'

const plane = { origin: [10, 20, 30] as [number, number, number], normal: [.0624918086, -.8787392474, -.4731935216] as [number, number, number], clearanceM: .005 }
test('tilted and upside-down source table faces viewer +Y; one rigid transform preserves geometry', () => {
  assert.ok(validPointcloudTablePlane(plane))
  const matrix = pointcloudDisplayMatrix(plane)
  const normal = new THREE.Vector3(...plane.normal).normalize()
  assert.ok(normal.clone().transformDirection(matrix).distanceTo(new THREE.Vector3(0, 1, 0)) < 1e-9)
  assert.ok(new THREE.Vector3(...plane.origin).applyMatrix4(matrix).length() < 1e-9)
  const source = { points: [{ x: 10, y: 20, z: 30 }, { x: 10.1, y: 20, z: 30 }], area: .03 }
  const transformed = transformMeasurement(source, matrix)
  assert.equal(transformed.area, .03)
  assert.ok(Math.abs(new THREE.Vector3(...Object.values(transformed.points[0]) as [number, number, number]).distanceTo(new THREE.Vector3(...Object.values(transformed.points[1]) as [number, number, number])) - .1) < 1e-9)
  const restored = transformMeasurement(transformed, matrix.clone().invert())
  source.points.forEach((p, i) => ['x', 'y', 'z'].forEach(k => assert.ok(Math.abs(restored.points[i][k] - p[k as keyof typeof p]) < 1e-9)))
})
test('signed normal keeps the object side even when native normal Z is negative', () => {
  const origin = new THREE.Vector3(...plane.origin)
  const normal = new THREE.Vector3(...plane.normal).normalize()
  const geometry = new THREE.BufferGeometry().setFromPoints([origin, origin.clone().addScaledVector(normal, .02)])
  const root = new THREE.Points(geometry, new THREE.PointsMaterial())
  new PointcloudTableVisibility().apply(root, plane, false)
  assert.deepEqual(Array.from(geometry.index!.array), [1])
  assert.equal(validPointcloudTablePlane({ ...plane, normal: [0, 0, 0] }), false)
})

test('saved distance metrics are recomputed in the displayed frame', () => {
  const distance = { start: { x: 0, y: 0, z: 0 }, end: { x: 0, y: 1, z: 0 }, distance: 1, heightDifference: 1, horizontalDistance: 0, verticalDistance: 1, slopeDegrees: 90 }
  const turned = transformMeasurement(distance, new THREE.Matrix4().makeRotationZ(Math.PI / 2))
  assert.ok(Math.abs(turned.distance - 1) < 1e-9)
  assert.ok(turned.verticalDistance < 1e-9)
  assert.ok(Math.abs(turned.horizontalDistance - 1) < 1e-9)
  assert.ok(turned.slopeDegrees < 1e-9)
})
