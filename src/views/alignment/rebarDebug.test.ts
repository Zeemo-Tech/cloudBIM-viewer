import assert from 'node:assert/strict'
import test from 'node:test'
import * as THREE from 'three'
// @ts-ignore Node source runner requires the extension.
import { debugInventoryBars, debugGeometryBounds, debugNormalArrows, debugFaceNormalArrows, debugMeshCounts, observedRadialNormal, visibleVertexIds } from './rebarDebug.ts'

test('pre-computation directory retains missing bars and separates review candidates', () => {
  const bars = debugInventoryBars({ inventory: { bars: [
    { ifcGlobalId: 'A', designBarId: 'a', name: 'A' }, { ifcGlobalId: 'B', designBarId: 'b', name: 'B' },
  ] }, instances: [
    { id: 1, designBarId: 'a', reviewStatus: 'matched', pointCount: 20 },
    { id: 2, designBarId: 'a', reviewStatus: 'review', pointCount: 10 },
    { id: 3, designBarId: 'unrelated', reviewStatus: 'matched', pointCount: 100 },
  ] })
  assert.deepEqual(bars[0].instanceIds, [1])
  assert.deepEqual(bars[0].reviewInstanceIds, [2])
  assert.equal(bars[0].pointCount, 20)
  assert.equal(bars[1].status, 'missing')
  assert.deepEqual(bars[1].instanceIds, [])
})

test('focus and arrows honor filtered indices, drawRange and world transforms', () => {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 500, 500, 500, 1, 0, 0], 3))
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([1, 0, 0, 1, 0, 0, 1, 0, 0], 3))
  geometry.setIndex([0, 2, 1]); geometry.setDrawRange(0, 2)
  const points = new THREE.Points(geometry, new THREE.PointsMaterial())
  points.position.set(10, 20, 30); points.rotation.z = Math.PI / 2
  assert.deepEqual(visibleVertexIds(geometry), [0, 2])
  const box = debugGeometryBounds(points)
  assert.ok(box.min.distanceTo(new THREE.Vector3(10, 20, 30)) < 1e-9)
  assert.ok(box.max.distanceTo(new THREE.Vector3(10, 21, 30)) < 1e-9)
  const arrows = debugNormalArrows(points, .01, 1, 0xffffff)
  assert.equal(arrows.userData.arrowCount, 1)
  const attribute = arrows.geometry.getAttribute('position')
  const delta = new THREE.Vector3().fromBufferAttribute(attribute, 1).sub(new THREE.Vector3().fromBufferAttribute(attribute, 0))
  assert.ok(delta.distanceTo(new THREE.Vector3(0, .01, 0)) < 1e-5)
  geometry.setDrawRange(0, 0)
  assert.ok(debugGeometryBounds(points).isEmpty(), 'empty selection never focuses an unrelated cluster')
  const empty = debugNormalArrows(points, .01, 200, 0xffffff)
  assert.equal(empty.userData.arrowCount, 0)
  for (const object of [arrows, empty, points]) { object.geometry.dispose(); object.material.dispose() }
})

test('scan radial reconstruction respects shifted axis, gaps, radius support and direction', () => {
  const row = (station: number, known = true) => ({
    designUnitId: 'unit', stationM: station, windowM: .01,
    designCenterM: [station, 0, 0] as [number, number, number],
    observedCenterM: known ? [station, .02, 0] as [number, number, number] : null,
    radiusM: .004, transverseOffsetM: known ? .02 : null,
  })
  const rows = [row(0), row(.5), row(1)]
  assert.ok(observedRadialNormal(new THREE.Vector3(.25, .024, 0), rows)!.distanceTo(new THREE.Vector3(0, 1, 0)) < 1e-9)
  assert.equal(observedRadialNormal(new THREE.Vector3(.25, .024, 0), [row(0), row(.5, false), row(1)]), null)
  assert.equal(observedRadialNormal(new THREE.Vector3(-.1, .024, 0), rows), null)
  assert.equal(observedRadialNormal(new THREE.Vector3(.25, .04, 0), rows), null)
  assert.equal(observedRadialNormal(new THREE.Vector3(.25, .02, 0), rows), null)
})

test('face normals distinguish coarse bar sides from axial end caps', () => {
  // Imported tubes can have only two vertex rings and smoothed rendering normals.
  const geometry = new THREE.CylinderGeometry(.004, .004, 1, 16, 1)
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial())
  const original = debugNormalArrows(mesh, .01, 200, 0xffffff)
  const originalPositions = original.geometry.getAttribute('position')
  for (let i = 0; i < originalPositions.count; i += 6) {
    assert.equal(Math.abs(originalPositions.getY(i)), .5, 'coarse vertex mode faithfully retains two rings')
  }
  original.geometry.dispose(); original.material.dispose()
  const arrows = debugFaceNormalArrows(mesh, .01, 200, 0xffffff)
  const positions = arrows.geometry.getAttribute('position')
  let side = 0, cap = 0
  for (let i = 0; i < positions.count; i += 6) {
    const origin = new THREE.Vector3().fromBufferAttribute(positions, i)
    const direction = new THREE.Vector3().fromBufferAttribute(positions, i + 1).sub(origin).normalize()
    if (Math.abs(origin.y) < .45) {
      side++
      assert.ok(Math.abs(direction.y) < 1e-5)
      assert.ok(direction.dot(new THREE.Vector3(origin.x, 0, origin.z)) > 0)
    } else if (Math.hypot(origin.x, origin.z) < .0035) {
      cap++
      assert.ok(direction.y * Math.sign(origin.y) > .999)
    }
  }
  assert.ok(side > 20, `expected normals along the side, got ${side}`)
  assert.ok(cap > 0, 'end cap must have its own face normal')
  arrows.geometry.dispose(); arrows.material.dispose(); geometry.dispose(); mesh.material.dispose()
})

test('face diagnostics honor selection and transforms and expose reversed winding', () => {
  const geometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([
    100, 0, 0, 101, 0, 0, 100, 1, 0,
    0, 0, 0, 1, 0, 0, 0, 1, 0,
  ], 3))
  geometry.setIndex([0, 1, 2, 3, 5, 4]) // The visible triangle deliberately points -Z.
  geometry.setDrawRange(3, 3)
  // Rendering normals must not mask the reversed face.
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(Array.from({ length: 6 }, () => [0, 0, 1]).flat(), 3))
  const mesh = new THREE.Mesh(geometry)
  mesh.position.set(10, 20, 30); mesh.rotation.y = Math.PI / 2; mesh.scale.set(2, 3, 4)
  assert.deepEqual(debugMeshCounts(geometry), { vertices: 3, faces: 1 })
  const arrows = debugFaceNormalArrows(mesh, .01, 20, 0xffffff)
  assert.equal(arrows.userData.arrowCount, 1)
  const positions = arrows.geometry.getAttribute('position')
  const origin = new THREE.Vector3().fromBufferAttribute(positions, 0)
  const delta = new THREE.Vector3().fromBufferAttribute(positions, 1).sub(origin)
  assert.ok(origin.distanceTo(new THREE.Vector3(10, 21, 30 - 2 / 3)) < 1e-5)
  assert.ok(delta.distanceTo(new THREE.Vector3(-.01, 0, 0)) < 1e-5)
  geometry.setDrawRange(0, 0)
  assert.deepEqual(debugMeshCounts(geometry), { vertices: 0, faces: 0 })
  const empty = debugFaceNormalArrows(mesh, .01, 20, 0xffffff)
  assert.equal(empty.userData.arrowCount, 0)
  for (const item of [arrows, empty, mesh]) {
    item.geometry.dispose()
    for (const material of Array.isArray(item.material) ? item.material : [item.material]) material.dispose()
  }
})
