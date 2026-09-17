import * as THREE from 'three'
import type { RebarComparisonBar, RebarProfileSample } from '@cloudbim/viewer-core'

export interface RebarDebugInventory {
  inventory: { bars: { ifcGlobalId: string; designBarId: string; name: string }[] }
  instances: { id: number; designBarId?: string; designUnitId?: string; reviewStatus: string; pointCount?: number }[]
}

/** Allow ownership inspection before a comparison has been calculated. */
export function debugInventoryBars(map: RebarDebugInventory | null): RebarComparisonBar[] {
  return (map?.inventory.bars ?? []).map(bar => {
    const instances = (map?.instances ?? []).filter(row => row.designBarId === bar.designBarId)
    const matched = instances.filter(row => row.reviewStatus === 'matched')
    const review = instances.filter(row => row.reviewStatus !== 'matched')
    return { ...bar, instanceIds: matched.map(row => row.id), reviewInstanceIds: review.map(row => row.id),
      pointCount: matched.reduce((sum, row) => sum + (row.pointCount ?? 0), 0),
      reviewPointCount: review.reduce((sum, row) => sum + (row.pointCount ?? 0), 0),
      pointsAfter: 0, vertexStart: 0, vertexCount: 0, knownCount: 0, unknownCount: 0, stats: null,
      status: review.length ? 'review' : matched.length ? 'matched' : 'missing' }
  })
}

/** Both index and drawRange matter: the point preview retains an oversized index buffer. */
export function visibleVertexIds(geometry: THREE.BufferGeometry): number[] {
  const count = geometry.index?.count ?? geometry.getAttribute('position')?.count ?? 0
  const start = geometry.drawRange.start
  const end = Math.min(count, start + geometry.drawRange.count)
  const ids = new Set<number>()
  for (let i = start; i < end; i++) ids.add(geometry.index ? geometry.index.getX(i) : i)
  return [...ids]
}

export function debugGeometryBounds(object: THREE.Mesh | THREE.Points) {
  object.updateWorldMatrix(true, false)
  const box = new THREE.Box3()
  const positions = object.geometry.getAttribute('position')
  for (const id of visibleVertexIds(object.geometry)) {
    const point = new THREE.Vector3().fromBufferAttribute(positions, id).applyMatrix4(object.matrixWorld)
    if (point.toArray().every(Number.isFinite)) box.expandByPoint(point)
  }
  return box
}

type ProfileRow = RebarProfileSample & { windowM?: number }

/** Reproduce saved section support on preview samples, not solver correspondences.
 * Rows and points must be in the model frame. Missing sections are never bridged.
 */
export function observedRadialNormal(point: THREE.Vector3, rows: ProfileRow[]): THREE.Vector3 | null {
  const first = rows[0], last = rows.at(-1)
  if (!first || !last || rows.length < 2) return null
  const tangent = new THREE.Vector3(...last.designCenterM).sub(new THREE.Vector3(...first.designCenterM))
  if (tangent.lengthSq() < 1e-18) return null
  tangent.normalize()
  const station = point.clone().sub(new THREE.Vector3(...first.designCenterM)).dot(tangent) + first.stationM
  let center: THREE.Vector3 | null = null, radius: number | null = null
  for (const row of rows) {
    if (!row.observedCenterM || row.windowM == null || row.radiusM == null) continue
    const delta = station - row.stationM
    if (Math.abs(delta) <= row.windowM + 1e-9) {
      center = new THREE.Vector3(...row.observedCenterM).addScaledVector(tangent, delta)
      radius = row.radiusM
    }
  }
  for (let i = 1; i < rows.length; i++) {
    const left = rows[i - 1]!, right = rows[i]!
    if (!left.observedCenterM || !right.observedCenterM || left.radiusM == null || right.radiusM == null) continue
    const span = right.stationM - left.stationM
    if (span <= 0 || station < left.stationM || station > right.stationM) continue
    const f = (station - left.stationM) / span
    center = new THREE.Vector3(...left.observedCenterM).lerp(new THREE.Vector3(...right.observedCenterM), f)
    radius = left.radiusM * (1 - f) + right.radiusM * f
  }
  if (!center || radius == null) return null
  const radial = point.clone().sub(center)
  radial.addScaledVector(tangent, -radial.dot(tangent))
  const length = radial.length()
  if (length <= 1e-9 || Math.abs(length - radius) > Math.max(.001, .25 * radius)) return null
  return radial.divideScalar(length)
}

/** One draw call for bounded, deterministic normal arrows. All output is world-space. */
export function debugNormalArrows(object: THREE.Mesh | THREE.Points, length: number, limit: number, color: number,
  normalAt?: (id: number, localPoint: THREE.Vector3) => THREE.Vector3 | null) {
  object.updateWorldMatrix(true, false)
  const geometry = object.geometry, positions = geometry.getAttribute('position'), normals = geometry.getAttribute('normal')
  const ids = visibleVertexIds(geometry)
  const values: number[] = []
  const normalMatrix = new THREE.Matrix3().getNormalMatrix(object.matrixWorld)
  const stride = Math.max(1, Math.ceil(ids.length / Math.max(1, limit)))
  for (let i = 0; i < ids.length; i += stride) {
    const id = ids[i]!, localPoint = new THREE.Vector3().fromBufferAttribute(positions, id)
    const direction = normalAt ? normalAt(id, localPoint) : normals ? new THREE.Vector3().fromBufferAttribute(normals, id).applyMatrix3(normalMatrix).normalize() : null
    if (!direction || !direction.toArray().every(Number.isFinite) || direction.lengthSq() < 1e-18) continue
    const point = localPoint.applyMatrix4(object.matrixWorld)
    if (!point.toArray().every(Number.isFinite)) continue
    appendNormalArrow(values, point, direction, length)
  }
  return normalArrowObject(values, color)
}

function appendNormalArrow(values: number[], point: THREE.Vector3, direction: THREE.Vector3, length: number) {
  const tip = point.clone().addScaledVector(direction, length)
  const side = new THREE.Vector3(Math.abs(direction.y) < .9 ? 0 : 1, Math.abs(direction.y) < .9 ? 1 : 0, 0).cross(direction).normalize()
  const base = tip.clone().addScaledVector(direction, -length * .22)
  values.push(...point.toArray(), ...tip.toArray(), ...tip.toArray(), ...base.clone().addScaledVector(side, length * .1).toArray(), ...tip.toArray(), ...base.addScaledVector(side, -length * .1).toArray())
}

function normalArrowObject(values: number[], color: number) {
  const arrows = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(values, 3)),
    new THREE.LineBasicMaterial({ color, toneMapped: false, depthTest: true }))
  arrows.userData.arrowCount = values.length / 18
  return arrows
}

export function debugMeshCounts(geometry: THREE.BufferGeometry) {
  const count = geometry.index?.count ?? geometry.getAttribute('position')?.count ?? 0
  const entries = Math.max(0, Math.min(count, geometry.drawRange.start + geometry.drawRange.count) - geometry.drawRange.start)
  return { vertices: visibleVertexIds(geometry).length, faces: Math.floor(entries / 3) }
}

/** Face centers and winding normals; no smoothing, remeshing or orientation repair.
 * Keep this separate from saved vertex normals used by the comparison solver.
 */
export function debugFaceNormalArrows(object: THREE.Mesh, length: number, limit: number, color: number) {
  object.updateWorldMatrix(true, false)
  const geometry = object.geometry, positions = geometry.getAttribute('position')
  const count = geometry.index?.count ?? positions?.count ?? 0
  const start = geometry.drawRange.start
  const faces = Math.floor(Math.max(0, Math.min(count, start + geometry.drawRange.count) - start) / 3)
  const stride = Math.max(1, Math.ceil(faces / Math.max(1, limit)))
  const normalMatrix = new THREE.Matrix3().getNormalMatrix(object.matrixWorld)
  const values: number[] = []
  const vertex = (i: number) => new THREE.Vector3().fromBufferAttribute(positions, geometry.index ? geometry.index.getX(i) : i)
  for (let face = 0; face < faces; face += stride) {
    const i = start + face * 3, a = vertex(i), b = vertex(i + 1), c = vertex(i + 2)
    const direction = b.clone().sub(a).cross(c.clone().sub(a)).applyMatrix3(normalMatrix).normalize()
    const point = a.add(b).add(c).multiplyScalar(1 / 3).applyMatrix4(object.matrixWorld)
    if (!point.toArray().every(Number.isFinite) || !direction.toArray().every(Number.isFinite) || direction.lengthSq() < 1e-18) continue
    appendNormalArrow(values, point, direction, length)
  }
  return normalArrowObject(values, color)
}
