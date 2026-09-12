import assert from 'node:assert/strict'
import test from 'node:test'
import * as THREE from 'three'
// @ts-ignore Node's strip-types runner uses explicit source extensions.
import { PointcloudTableVisibility, validPointcloudTablePlane } from './tableVisibility.ts'

const plane = { origin: [10, 20, 30], slopes: [.1, -.05], clearanceM: .005 } as const
const tablePlane = () => ({ origin: [...plane.origin] as [number, number, number], slopes: [...plane.slopes] as [number, number], clearanceM: plane.clearanceM })
function model(indexed = false) {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([
    0, 0, 0, // table
    1, 0, .1, // sloping table
    0, 1, -.04, // 1 cm above the plane: retained
    1, 1, .2, // retained
  ], 3))
  geometry.setAttribute('color', new THREE.Float32BufferAttribute([.1, .2, .3, .4, .5, .6, .7, .8, .9, 1, .9, .8], 3))
  geometry.setAttribute('intensity', new THREE.Float32BufferAttribute([10, 20, 30, 40], 1))
  if (indexed) geometry.setIndex([3, 1, 2, 0])
  geometry.computeBoundingBox()
  geometry.computeBoundingSphere()
  const points = new THREE.Points(geometry, new THREE.PointsMaterial())
  const root = new THREE.Group()
  root.position.set(...plane.origin)
  root.add(points)
  return { root, points, geometry }
}

test('table toggles keep colors, positions, material and bounds; reuse the same retained index', () => {
  const filter = new PointcloudTableVisibility()
  const { root, points, geometry } = model()
  const before = { color: geometry.getAttribute('color'), position: geometry.getAttribute('position'), intensity: geometry.getAttribute('intensity'), material: points.material, box: geometry.boundingBox, sphere: geometry.boundingSphere }
  filter.apply(root, tablePlane(), false)
  const hidden = geometry.index
  assert.deepEqual(Array.from(hidden!.array), [2, 3])
  for (let i = 0; i < 20; i++) {
    filter.apply(root, tablePlane(), true)
    assert.equal(geometry.index, null)
    filter.apply(root, tablePlane(), false)
    assert.equal(geometry.index, hidden)
  }
  assert.deepEqual({ color: geometry.getAttribute('color'), position: geometry.getAttribute('position'), intensity: geometry.getAttribute('intensity'), material: points.material, box: geometry.boundingBox, sphere: geometry.boundingSphere }, before)
  for (const [name, attribute] of Object.entries(before).slice(0, 3)) assert.equal(geometry.getAttribute(name), attribute)
})

test('source-frame filtering is identical for attached and detached LODs, including nested transforms', () => {
  const filter = new PointcloudTableVisibility()
  const a = model(), b = model()
  const wrapper = new THREE.Group()
  wrapper.rotation.x = -Math.PI / 2
  wrapper.position.set(100, -100, 15)
  wrapper.add(a.root)
  wrapper.updateMatrixWorld(true)
  // Move translation down one level to exercise nested model transforms.
  b.points.position.copy(b.root.position)
  b.root.position.set(0, 0, 0)
  filter.apply(a.root, tablePlane(), false)
  filter.apply(b.root, tablePlane(), false)
  assert.deepEqual(Array.from(a.geometry.index!.array), [2, 3])
  assert.deepEqual(Array.from(b.geometry.index!.array), [2, 3])
})

test('indexed clouds restore their original ordering and pick only visible points', () => {
  const filter = new PointcloudTableVisibility()
  const { root, points, geometry } = model(true)
  const original = geometry.index
  filter.apply(root, tablePlane(), false)
  assert.deepEqual(Array.from(geometry.index!.array), [3, 2])
  root.updateMatrixWorld(true)
  const ray = new THREE.Raycaster(new THREE.Vector3(10, 20, 40), new THREE.Vector3(0, 0, -1))
  ray.params.Points!.threshold = .01
  assert.equal(ray.intersectObject(points).length, 0, 'hidden tabletop cannot be measured')
  filter.apply(root, tablePlane(), true)
  assert.equal(geometry.index, original)
  assert.equal(ray.intersectObject(points).length, 1)
})

test('plane changes invalidate the cached mask and absent or malformed planes restore all points', () => {
  const filter = new PointcloudTableVisibility()
  const { root, geometry } = model()
  filter.apply(root, tablePlane(), false)
  const first = geometry.index
  filter.apply(root, { ...tablePlane(), clearanceM: .3 }, false)
  assert.equal(geometry.index!.count, 0)
  assert.notEqual(geometry.index, first)
  filter.apply(root, null, false)
  assert.equal(geometry.index, null)
  assert.equal(validPointcloudTablePlane({ ...tablePlane(), clearanceM: NaN }), false)
  assert.equal(validPointcloudTablePlane({ ...tablePlane(), clearanceM: -.1 }), false)
  filter.apply(root, { ...tablePlane(), clearanceM: NaN }, false)
  assert.equal(geometry.index, null)
})

test('new LODs inherit hidden state and changed position data invalidates their mask', () => {
  const filter = new PointcloudTableVisibility()
  const { root, geometry } = model()
  filter.apply(root, tablePlane(), false)
  const position = geometry.getAttribute('position') as THREE.BufferAttribute
  position.setXYZ(0, 0, 0, .1)
  position.needsUpdate = true
  filter.apply(root, tablePlane(), false)
  assert.deepEqual(Array.from(geometry.index!.array), [0, 2, 3])
  const next = model()
  filter.apply(next.root, tablePlane(), false)
  assert.deepEqual(Array.from(next.geometry.index!.array), [2, 3])
})
