import * as THREE from 'three'
import type { MeasurementSettings, MeasurementSource, XYZ } from './types'

export interface MeasurementTarget { object: THREE.Object3D; kind: 'pointcloud' | 'bim' | 'surface'; assetId?: number | null }
export interface PickedMeasurementPoint {
  point: THREE.Vector3
  source: MeasurementSource
  object: THREE.Points | THREE.Mesh
  hit: THREE.Intersection
  label: string
}
export interface PickContext {
  camera: THREE.Camera
  width: number
  height: number
  targets: MeasurementTarget[]
  contains: (point: THREE.Vector3) => boolean
  settings: MeasurementSettings
}
export function worldUnitsPerPixel(camera: THREE.Camera, point: THREE.Vector3, height: number) {
  if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
    const c = camera as THREE.OrthographicCamera
    return Math.abs(c.top - c.bottom) / c.zoom / Math.max(height, 1)
  }
  const c = camera as THREE.PerspectiveCamera
  const depth = Math.abs(point.clone().applyMatrix4(camera.matrixWorldInverse).z)
  return 2 * depth * Math.tan(THREE.MathUtils.degToRad(c.fov ?? 50) / 2) / (c.zoom || 1) / Math.max(height, 1)
}
function selectable(object: THREE.Object3D, camera: THREE.Camera) {
  for (let node: THREE.Object3D | null = object; node; node = node.parent) {
    if (!node.visible || node.userData.__viewerPickIgnore || node.userData.measurementHelper) return false
  }
  if (!object.layers.test(camera.layers)) return false
  const drawable = object as THREE.Mesh
  const materials = Array.isArray(drawable.material) ? drawable.material : [drawable.material]
  return materials.some(m => m && m.visible && m.opacity > 0)
}
function materialAllowsPoint(material: THREE.Material, point: THREE.Vector3) {
  if (!material.visible || material.opacity <= 0) return false
  const planes = material.clippingPlanes
  return !planes?.length || !(material.clipIntersection
    ? planes.every(p => p.distanceToPoint(point) < 0)
    : planes.some(p => p.distanceToPoint(point) < 0))
}
export function measurementObjects(context: PickContext) {
  const result: Array<{ object: THREE.Points | THREE.Mesh; target: MeasurementTarget }> = []
  const seen = new Set<THREE.Object3D>()
  for (const target of context.targets) {
    if (context.settings.sourceFilter !== 'all' && context.settings.sourceFilter !== target.kind) continue
    target.object.updateWorldMatrix(true, true)
    target.object.traverse(object => {
      if (seen.has(object) || (!(object as THREE.Points).isPoints && !(object as THREE.Mesh).isMesh) || !selectable(object, context.camera)) return
      seen.add(object)
      result.push({ object: object as THREE.Points | THREE.Mesh, target })
    })
  }
  return result
}
export function sourceForPoint(object: THREE.Points | THREE.Mesh, target: MeasurementTarget, point: THREE.Vector3, hit?: THREE.Intersection): MeasurementSource {
  return { kind: target.kind, assetId: target.assetId, objectName: object.name || target.object.name || (target.kind === 'bim' ? 'BIM' : '点云'),
    objectId: object.uuid, vertexIndex: (object as THREE.Points).isPoints ? hit?.index : undefined,
    triangleIndex: hit?.faceIndex ?? undefined,
    localPoint: point.clone().applyMatrix4(object.matrixWorld.clone().invert()).toArray() as XYZ,
    worldMatrix: object.matrixWorld.toArray(),
    representation: target.kind === 'pointcloud' ? 'loaded-pointcloud-geometry' : 'model-surface' }
}

/** Points.raycast returns a point on the ray. Recover the actual indexed vertex. */
export function resolveMeasurementHit(hit: THREE.Intersection): THREE.Vector3 | null {
  const object = hit.object as THREE.Points
  if (!object.isPoints) return hit.point.clone()
  const positions = object.geometry.getAttribute('position')
  if (!positions || hit.index === undefined || hit.index < 0 || hit.index >= positions.count) return null
  return new THREE.Vector3().fromBufferAttribute(positions, hit.index).applyMatrix4(object.matrixWorld)
}

/** Screen-space point picking remains correct under scaled/rotated parents.
 * Three's Points.raycast scales its threshold by the object's own scale only.
 * Use the complete world transform for the coarse query and pixels for acceptance.
 */
function raycastPoints(object: THREE.Points, ray: THREE.Raycaster, context: PickContext): THREE.Intersection[] {
  const geometry = object.geometry, positions = geometry.getAttribute('position')
  if (!positions || !geometry.boundingSphere) return []
  const sphere = geometry.boundingSphere.clone().applyMatrix4(object.matrixWorld)
  const centerDepth = Math.abs(sphere.center.clone().applyMatrix4(context.camera.matrixWorldInverse).z)
  const tolerance = Math.max(1e-6, worldUnitsPerPixel(context.camera, sphere.center, context.height)
    * Math.max(1, (centerDepth + sphere.radius) / Math.max(centerDepth, 1e-6)) * 10)
  sphere.radius += tolerance
  if (!ray.ray.intersectsSphere(sphere)) return []
  const scale = new THREE.Vector3().setFromMatrixScale(object.matrixWorld)
  const minScale = Math.min(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z))
  if (minScale < 1e-12) return []
  const localRay = ray.ray.clone().applyMatrix4(object.matrixWorld.clone().invert())
  const localToleranceSquared = (tolerance / minScale) ** 2
  const hits: THREE.Intersection[] = [], local = new THREE.Vector3()
  const start = Math.max(0, geometry.drawRange.start), end = Math.min(geometry.index?.count ?? positions.count, start + geometry.drawRange.count)
  for (let i = start; i < end; i++) {
    const index = geometry.index ? geometry.index.getX(i) : i
    local.fromBufferAttribute(positions, index)
    if (localRay.distanceSqToPoint(local) > localToleranceSquared) continue
    const point = local.clone().applyMatrix4(object.matrixWorld)
    const distance = point.clone().sub(ray.ray.origin).dot(ray.ray.direction)
    if (distance < ray.near || distance > ray.far) continue
    hits.push({ object, index, point, distance, distanceToRay: Math.sqrt(ray.ray.distanceSqToPoint(point)) })
  }
  return hits
}

export function pickMeasurementPoint(context: PickContext, x: number, y: number): PickedMeasurementPoint | null {
  const ray = new THREE.Raycaster()
  context.camera.updateMatrixWorld(true)
  ray.setFromCamera(new THREE.Vector2(2 * x / context.width - 1, 1 - 2 * y / context.height), context.camera)
  const entries = measurementObjects(context)
  const candidates: Array<PickedMeasurementPoint & { depth: number; screen: number }> = []
  for (const { object, target } of entries) {
    const geometry = object.geometry
    if (!geometry.boundingSphere) geometry.computeBoundingSphere()
    for (const hit of (object as THREE.Points).isPoints ? raycastPoints(object as THREE.Points, ray, context) : ray.intersectObject(object, false)) {
      const point = resolveMeasurementHit(hit)
      if (!point || !Number.isFinite(point.lengthSq()) || !context.contains(point)) continue
      const projected = point.clone().project(context.camera)
      if (projected.z < -1 || projected.z > 1) continue
      const screen = Math.hypot((projected.x + 1) * context.width / 2 - x, (1 - projected.y) * context.height / 2 - y)
      if ((object as THREE.Points).isPoints && screen > 10) continue
      const material = Array.isArray(object.material) ? object.material[hit.face?.materialIndex ?? 0] : object.material
      if (!material || !materialAllowsPoint(material, point)) continue
      candidates.push({ point, object, hit, source: sourceForPoint(object, target, point, hit), screen,
        depth: point.clone().sub(ray.ray.origin).dot(ray.ray.direction),
        label: (object as THREE.Points).isPoints ? '点云采样点' : target.kind === 'bim' ? 'BIM 表面' : '表面点' })
    }
  }
  candidates.sort((a, b) => a.depth - b.depth || a.screen - b.screen)
  const first = candidates[0]
  if (!first) return null
  const depthBand = worldUnitsPerPixel(context.camera, first.point, context.height) * 12
  const front = candidates.filter(c => c.depth - first.depth <= depthBand)
  front.sort((a, b) => a.screen - b.screen || a.depth - b.depth)
  return front[0] ?? null
}

export function sampleMeasurementNeighbourhood(context: PickContext, picked: PickedMeasurementPoint, radius: number) {
  const points: XYZ[] = []
  const sources: MeasurementSource[] = []
  const p = new THREE.Vector3()
  const sphere = new THREE.Sphere(picked.point, radius)
  let visited = 0
  // Do not mix model/design vertices into the scan's fitted as-built geometry.
  for (const { object, target } of measurementObjects(context)) {
    if (target.kind !== picked.source.kind || target.assetId !== picked.source.assetId) continue
    const g = object.geometry
    if (!g.boundingBox) g.computeBoundingBox()
    if (g.boundingBox && !g.boundingBox.clone().applyMatrix4(object.matrixWorld).intersectsSphere(sphere)) continue
    const positions = g.getAttribute('position')
    if (!positions) continue
    const count = g.index?.count ?? positions.count
    const start = Math.max(0, g.drawRange.start)
    const end = Math.min(count, start + g.drawRange.count)
    const indices = new Set<number>()
    for (let i = start; i < end; i++) {
      const index = g.index ? g.index.getX(i) : i
      if (indices.has(index)) continue
      if (++visited > 2_000_000) return { points: [] as XYZ[], sources, error: '邻域数据过多，请缩小搜索半径后重试' }
      p.fromBufferAttribute(positions, index).applyMatrix4(object.matrixWorld)
      if (!Number.isFinite(p.lengthSq()) || p.distanceToSquared(picked.point) > radius * radius || !context.contains(p)) continue
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      const allowed = Array.isArray(object.material) && g.groups.length
        ? g.groups.some(group => i >= group.start && i < group.start + group.count && materials[group.materialIndex ?? 0] && materialAllowsPoint(materials[group.materialIndex ?? 0]!, p))
        : materials.some(m => materialAllowsPoint(m, p))
      if (!allowed) continue
      indices.add(index)
      points.push(p.toArray() as XYZ)
      if (points.length > 100_000) return { points: [] as XYZ[], sources, error: '邻域超过 10 万点，请缩小搜索半径' }
    }
    sources.push(sourceForPoint(object, target, picked.point))
  }
  // Coincident triangle vertices must not create fictitious geometric support.
  const unique = new Map<string, XYZ>()
  for (const point of points) unique.set(point.map(v => Math.round(v * 1e7)).join(','), point)
  return { points: [...unique.values()], sources, error: '' }
}

export function planeAtMeshHit(picked: PickedMeasurementPoint, radius: number) {
  if (!(picked.object as THREE.Mesh).isMesh || !picked.hit.face) return null
  const normal = picked.hit.face.normal.clone().applyMatrix3(new THREE.Matrix3().getNormalMatrix(picked.object.matrixWorld)).normalize()
  if (!Number.isFinite(normal.lengthSq()) || normal.lengthSq() < 0.9) return null
  return { kind: 'plane' as const, origin: picked.point.toArray() as XYZ, normal: normal.toArray() as XYZ, radius,
    rmse: 0, pointCount: 3, inlierCount: 3, support: [picked.hit.face.a, picked.hit.face.b, picked.hit.face.c].map(index =>
      new THREE.Vector3().fromBufferAttribute(picked.object.geometry.getAttribute('position'), index).applyMatrix4(picked.object.matrixWorld).toArray() as XYZ),
    algorithm: 'mesh-face-normal-v1' }
}
