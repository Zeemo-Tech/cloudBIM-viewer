import * as THREE from 'three'
import type { RebarInspection, RebarIntersection } from '../../api/backend-rebar'
// @ts-expect-error Node's strip-types test runner resolves the source extension.
import { instanceColor } from './index.ts'

const ROLE_COLORS: Record<NonNullable<RebarInspection['instances'][number]['role']>, string> = {
  planar: '#22d3ee',
  web: '#fb923c',
  unresolved: '#a855f7',
}

function roleColor(instance: RebarInspection['instances'][number]) {
  return ROLE_COLORS[instance.role ?? 'unresolved'] ?? new THREE.Color(...instanceColor(instance.id)).getStyle()
}

export function buildRebarOverlay(inspection: RebarInspection): THREE.Group {
  const group = new THREE.Group()
  group.name = 'rebar-centerlines'
  for (const instance of inspection.instances) {
    const associatedWithSelectedIntersection = inspection.selectedIntersectionId !== undefined && inspection.selectedIntersectionId !== null &&
      inspection.intersections.find((intersection) => intersection.id === inspection.selectedIntersectionId)?.instanceIds.includes(instance.id)
    if (!inspection.showCenterlines && instance.id !== inspection.selectedId && !associatedWithSelectedIntersection) continue
    const color = new THREE.Color(roleColor(instance))
    const paths = instance.observedSegments?.map((segment) => segment.points) ?? [instance.centerline]
    for (const points of paths) {
      if (points.length < 2 || points.some((p) => p.length !== 3 || !p.every(Number.isFinite))) continue
      const geometry = new THREE.BufferGeometry().setFromPoints(points.map((p) => new THREE.Vector3(p[0], p[1], p[2])))
      const material = new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true,
        opacity: (inspection.selectedId && instance.id !== inspection.selectedId) ||
          (inspection.selectedIntersectionId && !associatedWithSelectedIntersection) ? 0.25 : 1 })
      const line = new THREE.Line(geometry, material)
      line.name = `rebar-${instance.role ?? 'unresolved'}-${instance.id}`
      line.userData.rebarInstanceId = instance.id
      line.userData.rebarRole = instance.role ?? 'unresolved'
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
      dashed.name = `rebar-inferred-${instance.role ?? 'unresolved'}-${instance.id}`
      dashed.userData.rebarInstanceId = instance.id
      dashed.userData.rebarRole = instance.role ?? 'unresolved'
      dashed.renderOrder = 9001
      group.add(dashed)
    }
  }
  if (inspection.showIntersections) {
    for (const intersection of inspection.intersections) {
      if (!isFiniteIntersection(intersection)) continue
      const selected = inspection.selectedIntersectionId === intersection.id
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        color: selected ? '#fef08a' : '#facc15', depthTest: false, depthWrite: false,
      }))
      sprite.name = `rebar-intersection-${intersection.id}`
      sprite.userData.rebarIntersectionId = intersection.id
      sprite.userData.rebarIntersection = intersection
      sprite.position.set(...intersection.position)
      sprite.scale.setScalar(selected ? 0.05 : 0.035)
      sprite.renderOrder = 9002
      group.add(sprite)
    }
  }
  return group
}

function isFiniteIntersection(intersection: RebarIntersection) {
  return intersection.position.length === 3 && intersection.position.every(Number.isFinite)
}

export function disposeRebarOverlay(group: THREE.Group | null) {
  group?.removeFromParent()
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  const textures = new Set<THREE.Texture>()
  group?.traverse((object) => {
    const geometry = (object as THREE.Object3D & { geometry?: THREE.BufferGeometry }).geometry
    if (geometry) geometries.add(geometry)
    const material = (object as THREE.Object3D & { material?: THREE.Material | THREE.Material[] }).material
    for (const entry of material ? (Array.isArray(material) ? material : [material]) : []) {
      materials.add(entry)
      for (const value of Object.values(entry)) if (value instanceof THREE.Texture) textures.add(value)
    }
  })
  geometries.forEach((geometry) => geometry.dispose())
  textures.forEach((texture) => texture.dispose())
  materials.forEach((material) => material.dispose())
}
