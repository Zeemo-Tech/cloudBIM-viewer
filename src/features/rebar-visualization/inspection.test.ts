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

test('intersection markers default independently from hidden centerlines and preserve picking metadata', () => {
  const overlay = buildRebarOverlay(inspection())
  const sprites = overlay.children.filter((child): child is THREE.Sprite => child instanceof THREE.Sprite)
  assert.equal(overlay.children.filter((child) => child instanceof THREE.Line).length, 0)
  assert.equal(sprites.length, 1)
  const marker = sprites[0]
  assert.equal(marker.name, 'rebar-intersection-17')
  assert.equal(marker.userData.rebarIntersectionId, 17)
  assert.equal(marker.userData.rebarIntersection, intersection)
  assert.deepEqual(marker.position.toArray(), [1, 2, 3])
  assert.deepEqual(marker.scale.toArray(), [.035, .035, .035])
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
  assert.equal((selected.children.find((child) => child instanceof THREE.Sprite) as THREE.Sprite).scale.x, .05)
  disposeRebarOverlay(selected)
})

test('disposing every overlay releases line and sprite geometry, material, and marker texture', () => {
  const overlay = buildRebarOverlay(inspection({ showCenterlines: true }))
  const parent = new THREE.Group()
  parent.add(overlay)
  const sprite = overlay.children.find((child): child is THREE.Sprite => child instanceof THREE.Sprite)!
  const texture = new THREE.Texture()
  let textureDisposed = false
  let geometryDisposed = false
  let materialDisposed = false
  texture.dispose = () => { textureDisposed = true }
  sprite.geometry.dispose = () => { geometryDisposed = true }
  sprite.material.map = texture
  sprite.material.dispose = () => { materialDisposed = true }
  disposeRebarOverlay(overlay)
  assert.equal(parent.children.length, 0)
  assert.equal(textureDisposed, true)
  assert.equal(geometryDisposed, true)
  assert.equal(materialDisposed, true)

  const empty = buildRebarOverlay(inspection({ instances: [], intersections: [] }))
  assert.equal(empty.children.length, 0)
  disposeRebarOverlay(empty)
})
