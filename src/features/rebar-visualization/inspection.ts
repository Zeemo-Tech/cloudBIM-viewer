import * as THREE from 'three'
import type { RebarInspection } from '@/api/backend-rebar'
import { instanceColor } from './index'

export function buildRebarOverlay(inspection: RebarInspection): THREE.Group {
  const group = new THREE.Group()
  group.name = 'rebar-centerlines'
  for (const instance of inspection.instances) {
    if (!inspection.showCenterlines && instance.id !== inspection.selectedId) continue
    const color = new THREE.Color(...instanceColor(instance.id))
    const paths = instance.observedSegments?.map((segment) => segment.points) ?? [instance.centerline]
    for (const points of paths) {
      if (points.length < 2 || points.some((p) => p.length !== 3 || !p.every(Number.isFinite))) continue
      const geometry = new THREE.BufferGeometry().setFromPoints(points.map((p) => new THREE.Vector3(p[0], p[1], p[2])))
      const material = new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true,
        opacity: inspection.selectedId && instance.id !== inspection.selectedId ? 0.25 : 1 })
      const line = new THREE.Line(geometry, material)
      line.renderOrder = 9000
      group.add(line)
    }
    for (const inferred of instance.inferredSegments ?? []) {
      const segment = typeof inferred === 'object' && inferred !== null
        ? (inferred as { points?: number[][]; centerline?: number[][] }) : null
      const path = segment?.points ?? segment?.centerline
      if (!path || path.length < 2 || path.some((p) => p.length !== 3 || !p.every(Number.isFinite))) continue
      const dashed = new THREE.Line(new THREE.BufferGeometry().setFromPoints(path.map((p) => new THREE.Vector3(p[0], p[1], p[2]))),
        new THREE.LineDashedMaterial({ color, dashSize: 0.012, gapSize: 0.008, depthTest: false }))
      dashed.computeLineDistances()
      dashed.renderOrder = 9001
      group.add(dashed)
    }
  }
  return group
}

export function disposeRebarOverlay(group: THREE.Group | null) {
  group?.removeFromParent()
  group?.traverse((object) => {
    if (object instanceof THREE.Line) {
      object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach((material) => material.dispose())
    }
  })
}
