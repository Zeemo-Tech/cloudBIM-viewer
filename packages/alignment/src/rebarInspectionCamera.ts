import { Box3, Vector3, type Mesh } from 'three'

const ELEVATION_FROM_TOP = Math.PI / 10

/** Design vertices in viewer world metres. Preserve the complete parent transform;
 * never infer this direction from scan support, an AABB, or IFC axis labels. */
export function appendInspectionMeshPoints(mesh: Mesh, points: Vector3[], start = 0, count = Infinity) {
  const positions = mesh.geometry.getAttribute('position')
  if (!positions) return
  mesh.updateWorldMatrix(true, false)
  const end = Math.min(positions.count, start + count)
  for (let index = Math.max(0, start); index < end; index++) {
    const point = new Vector3().fromBufferAttribute(positions, index).applyMatrix4(mesh.matrixWorld)
    if (point.toArray().every(Number.isFinite)) points.push(point)
  }
}

function longitudinalAxis(points: readonly Vector3[]) {
  const center = points.reduce((sum, point) => sum.add(point), new Vector3()).divideScalar(points.length)
  let xx = 0, xy = 0, xz = 0, yy = 0, yz = 0, zz = 0
  for (const point of points) {
    const x = point.x - center.x, y = point.y - center.y, z = point.z - center.z
    xx += x * x; xy += x * y; xz += x * z; yy += y * y; yz += y * z; zz += z * z
  }
  const multiply = (v: Vector3) => new Vector3(xx * v.x + xy * v.y + xz * v.z,
    xy * v.x + yy * v.y + yz * v.z, xz * v.x + yz * v.y + zz * v.z)
  let best = new Vector3(1, 0, 0), score = -1
  for (const seed of [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)]) {
    let axis = seed
    for (let i = 0; i < 32; i++) {
      const next = multiply(axis)
      if (next.lengthSq() < 1e-24) break
      axis = next.normalize()
    }
    const variance = axis.dot(multiply(axis))
    if (variance > score) { best = axis; score = variance }
  }
  const dominant = best.toArray().reduce((a, b) => Math.abs(a) >= Math.abs(b) ? a : b)
  return dominant < 0 ? best.negate() : best
}

/** Keep engineering vertical upright. Horizontal members use an elevated side
 * view; stirrups use a front elevation and are never rolled to fill the viewport. */
export function inspectionCameraFrame(vertices: readonly Vector3[], bounds: Box3, aspect: number, fovDegrees: number, margin: number, options: { upright?: boolean } = {}) {
  const points = vertices.length ? vertices : Array.from({ length: 8 }, (_, i) => new Vector3(
    i & 1 ? bounds.max.x : bounds.min.x, i & 2 ? bounds.max.y : bounds.min.y, i & 4 ? bounds.max.z : bounds.min.z,
  ))
  const axis = longitudinalAxis(points)
  // The report's design-unit classification also identifies wide stirrups whose
  // longest PCA axis can be horizontal. Geometry handles unloaded inventories.
  const upright = options.upright || Math.abs(axis.y) > .3
  const right = upright ? longitudinalAxis(points.map(point => new Vector3(point.x, 0, point.z)))
    : new Vector3(axis.x, 0, axis.z).normalize()
  // Remove small hook/triangulation bias near the engineering cardinal axes.
  // This changes the viewing azimuth only, never the geometry or world-up axis.
  const snapCosine = Math.cos(Math.PI / 90)
  if (Math.abs(right.x) > snapCosine) right.set(Math.sign(right.x), 0, 0)
  else if (Math.abs(right.z) > snapCosine) right.set(0, 0, Math.sign(right.z))
  const up = new Vector3(0, 1, 0)
  const normal = right.clone().cross(up).normalize()
  if (!upright) normal.multiplyScalar(Math.sin(ELEVATION_FROM_TOP)).addScaledVector(up, Math.cos(ELEVATION_FROM_TOP))
  const screenUp = normal.clone().cross(right).normalize()
  const projected = points.map(point => new Vector3(point.dot(right), point.dot(screenUp), point.dot(normal)))
  const extent = new Box3().setFromPoints(projected)
  const center = extent.getCenter(new Vector3()), size = extent.getSize(new Vector3())
  const target = right.clone().multiplyScalar(center.x).addScaledVector(screenUp, center.y).addScaledVector(normal, center.z)
  const tanVertical = Math.tan(fovDegrees * Math.PI / 360), tanHorizontal = tanVertical * aspect
  let distance = Math.max(size.length() * .42, .08)
  // Include each vertex's depth so perspective fitting cannot clip a bent end.
  for (const point of projected) distance = Math.max(distance, point.z - center.z + margin * Math.max(
    Math.abs(point.x - center.x) / tanHorizontal, Math.abs(point.y - center.y) / tanVertical,
  ))
  distance = Math.max(distance, size.z / 2 + .02)
  return {
    axis, target, up, position: target.clone().addScaledVector(normal, distance),
    // Alignment's orthoViewSize is the half-height of the frustum.
    viewSize: Math.max(size.y * margin / 2, size.x / aspect * margin / 2, .04),
  }
}
