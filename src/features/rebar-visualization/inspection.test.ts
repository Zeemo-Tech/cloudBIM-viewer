import assert from 'node:assert/strict'
import test from 'node:test'
import * as THREE from 'three'
// @ts-ignore Node's strip-types runner intentionally uses the explicit source extension.
import { buildRebarOverlay, disposeRebarOverlay } from './inspection.ts'
import type { RebarInspection } from '../../api/backend-rebar'

const intersection = {
  id: 17,
  position: [1, 2, 3] as [number, number, number],
  instanceIds: [4, 8],
  segmentRefs: [{ instanceId: 4, segmentIndex: 0, edgeIndex: 0 }],
  angleDegrees: 90,
  residual: 0,
  evidence: 'observed-finite-centerlines' as const,
}

function inspection(overrides: Partial<RebarInspection> = {}): RebarInspection {
  return {
    instances: [{ id: 4, radius: .01, role: 'planar', centerline: [[0, 0, 0], [1, 0, 0]], observedSegments: [{ points: [[0, 0, 0], [1, 0, 0]] }] },
      { id: 8, radius: .01, role: 'web', centerline: [[0, 0, 0], [0, 1, 0]], observedSegments: [{ points: [[0, 0, 0], [0, 1, 0]] }] }],
    intersections: [intersection],
    selectedId: null,
    selectedIntersectionId: null,
    showCenterlines: false,
    showIntersections: true,
    hideFixtures: false,
    ...overrides,
  }
}

test('intersection markers are independent red sphere meshes and preserve picking metadata', () => {
  const overlay = buildRebarOverlay(inspection())
  const meshes = overlay.children.filter((child): child is THREE.Mesh => child instanceof THREE.Mesh)
  assert.equal(overlay.children.filter((child) => child instanceof THREE.Line).length, 0)
  assert.equal(meshes.length, 1)
  const marker = meshes[0]
  assert.ok(marker.geometry instanceof THREE.SphereGeometry)
  assert.equal((marker.material as THREE.MeshBasicMaterial).color.getHexString(), 'ef4444')
  assert.equal(marker.name, 'rebar-intersection-17')
  assert.equal(marker.userData.rebarIntersectionId, 17)
  assert.equal(marker.userData.rebarIntersection, intersection)
  assert.deepEqual(marker.position.toArray(), [1, 2, 3])
  assert.ok(marker.raycast)
  disposeRebarOverlay(overlay)
})

test('roles are encoded for highlighting and selected intersections retain only associated lines', () => {
  const all = buildRebarOverlay(inspection({ showCenterlines: true }))
  const lines = all.children.filter((child): child is THREE.Line => child instanceof THREE.Line)
  assert.equal(lines.length, 2)
  assert.deepEqual(lines.map((line) => line.userData.rebarRole), ['planar', 'web'])
  assert.deepEqual(lines.map((line) => (line.material as THREE.LineBasicMaterial).color.getStyle()), ['rgb(34,211,238)', 'rgb(251,146,60)'])
  disposeRebarOverlay(all)

  const selected = buildRebarOverlay(inspection({ selectedIntersectionId: 17 }))
  assert.equal(selected.children.filter((child) => child instanceof THREE.Line).length, 2)
  assert.equal(((selected.children.find((child) => child instanceof THREE.Mesh) as THREE.Mesh).geometry as THREE.SphereGeometry).parameters.radius, .025)
  disposeRebarOverlay(selected)
})

test('disposing every overlay releases line and sphere geometry and material', () => {
  const overlay = buildRebarOverlay(inspection({ showCenterlines: true }))
  const parent = new THREE.Group()
  parent.add(overlay)
  const marker = overlay.children.find((child): child is THREE.Mesh => child instanceof THREE.Mesh)!
  let geometryDisposed = false
  let materialDisposed = false
  marker.geometry.dispose = () => { geometryDisposed = true }
  ;(marker.material as THREE.Material).dispose = () => { materialDisposed = true }
  disposeRebarOverlay(overlay)
  assert.equal(parent.children.length, 0)
  assert.equal(geometryDisposed, true)
  assert.equal(materialDisposed, true)

  const empty = buildRebarOverlay(inspection({ instances: [], intersections: [] }))
  assert.equal(empty.children.length, 0)
  disposeRebarOverlay(empty)
})
