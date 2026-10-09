import * as THREE from 'three'
// @ts-ignore Node's strip-types test runner uses explicit source extensions.
import { validPointcloudTablePlane, type PointcloudTablePlane } from './tableVisibility.ts'

/** Source LAS stays immutable. Only the single viewer parent is transformed. */
export function pointcloudDisplayMatrix(plane?: PointcloudTablePlane | null) {
  const sourceToViewer = new THREE.Matrix4().makeRotationX(-Math.PI / 2)
  if (!validPointcloudTablePlane(plane)) return sourceToViewer
  const normal = plane.normal
    ? new THREE.Vector3(...plane.normal).normalize()
    : new THREE.Vector3(-plane.slopes![0], -plane.slopes![1], 1).normalize()
  const rotation = new THREE.Quaternion().setFromUnitVectors(normal, new THREE.Vector3(0, 0, 1))
  return sourceToViewer.multiply(new THREE.Matrix4().makeRotationFromQuaternion(rotation))
    .multiply(new THREE.Matrix4().makeTranslation(-plane.origin[0], -plane.origin[1], -plane.origin[2]))
}

/** Saved measurements keep the historical source-viewer frame across leveling. */
export function transformMeasurement(value: unknown, matrix: THREE.Matrix4): any {
  if (Array.isArray(value)) return value.map(item => transformMeasurement(item, matrix))
  if (!value || typeof value !== 'object') return value
  const record = value as Record<string, unknown>
  if (['x', 'y', 'z'].every(key => typeof record[key] === 'number' && Number.isFinite(record[key]))) {
    const point = new THREE.Vector3(record.x as number, record.y as number, record.z as number).applyMatrix4(matrix)
    return { ...record, x: point.x, y: point.y, z: point.z }
  }
  const transformed = Object.fromEntries(Object.entries(record).map(([key, child]) => [key, transformMeasurement(child, matrix)]))
  if (transformed.start && transformed.end && typeof transformed.distance === 'number') {
    const dx = transformed.end.x - transformed.start.x
    const dy = transformed.end.y - transformed.start.y
    const dz = transformed.end.z - transformed.start.z
    const horizontal = Math.hypot(dx, dz), vertical = Math.abs(dy)
    Object.assign(transformed, {
      distance: Math.hypot(dx, dy, dz), heightDifference: vertical,
      horizontalDistance: horizontal, verticalDistance: vertical,
      slopeDegrees: horizontal <= 1e-8 ? (vertical <= 1e-8 ? 0 : 90) : Math.atan2(vertical, horizontal) * 180 / Math.PI,
    })
  }
  return transformed
}
