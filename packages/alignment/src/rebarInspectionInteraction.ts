import * as THREE from 'three'

type PointerSample = Pick<PointerEvent, 'pointerId' | 'pointerType' | 'isPrimary' | 'button' | 'buttons' | 'clientX' | 'clientY' | 'shiftKey' | 'ctrlKey' | 'metaKey'>

/** Navigation owns any gesture that ever leaves the click slop, even if it returns. */
export function createInspectionClickGesture() {
  let down: { id: number; x: number; y: number; slop: number; dragged: boolean } | null = null
  const cancel = () => { down = null }
  const move = (event: PointerSample) => {
    if (!down || event.pointerId !== down.id) return
    if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > down.slop || (event.buttons & ~1) !== 0) down.dragged = true
  }
  return {
    cancel, move,
    begin(event: PointerSample) {
      if (down || !event.isPrimary || event.button !== 0 || event.shiftKey || event.ctrlKey || event.metaKey) {
        cancel(); return false
      }
      down = { id: event.pointerId, x: event.clientX, y: event.clientY, slop: event.pointerType === 'touch' ? 10 : 5, dragged: false }
      return true
    },
    end(event: PointerSample) {
      if (!down || event.pointerId !== down.id) { cancel(); return false }
      move(event)
      const click = !down.dragged && event.button === 0 && !event.shiftKey && !event.ctrlKey && !event.metaKey
      cancel()
      return click
    },
  }
}

export type InspectionPick = { id: string; distance: number; pixels: number }

/** Mesh hits first; nearby point samples must pass a true CSS-pixel distance test.
 * Raycast both sides without changing rendered materials. Hidden/clipped objects
 * never enter the candidate list. Alt-click can cycle the remaining depth hits.
 */
export function pickInspectionCandidates(
  roots: THREE.Object3D[], camera: THREE.PerspectiveCamera | THREE.OrthographicCamera,
  pointer: THREE.Vector2, viewport: { width: number; height: number },
  resolve: (hit: THREE.Intersection) => string, radius = 6,
): InspectionPick[] {
  if (viewport.width <= 0 || viewport.height <= 0) return []
  const meshes: THREE.Object3D[] = [], points: THREE.Points[] = []
  const visited = new Set<THREE.Object3D>()
  for (const root of roots) {
    root.updateWorldMatrix(true, true)
    let hidden = false
    for (let parent: THREE.Object3D | null = root; parent; parent = parent.parent) if (!parent.visible) hidden = true
    if (hidden) continue
    root.traverseVisible(object => {
      if (visited.has(object) || object.userData.__viewerPickIgnore) return
      visited.add(object)
      if ((object as THREE.Points).isPoints) points.push(object as THREE.Points)
      else if ((object as THREE.Mesh).isMesh) meshes.push(object)
    })
  }
  camera.updateMatrixWorld(true)
  const ray = new THREE.Raycaster()
  const candidates = new Map<string, InspectionPick>()
  const position = new THREE.Vector3(), projected = new THREE.Vector3()
  const accept = (hit: THREE.Intersection, pixels: number) => {
    const object = hit.object as THREE.Mesh | THREE.Points
    const material = Array.isArray(object.material) ? object.material[hit.face?.materialIndex ?? 0] : object.material
    if (!material?.visible) return
    position.copy(hit.point)
    if ((object as THREE.Points).isPoints && hit.index !== undefined) {
      position.fromBufferAttribute(object.geometry.getAttribute('position'), hit.index).applyMatrix4(object.matrixWorld)
      projected.copy(position).project(camera)
      pixels = Math.hypot((projected.x - pointer.x) * viewport.width / 2, (projected.y - pointer.y) * viewport.height / 2)
      if (pixels > radius) return
    } else projected.copy(position).project(camera)
    if (projected.z < -1 || projected.z > 1) return
    const planes = material.clippingPlanes
    if (planes?.length && (material.clipIntersection
      ? planes.every(plane => plane.distanceToPoint(position) < 0)
      : planes.some(plane => plane.distanceToPoint(position) < 0))) return
    const id = resolve(hit)
    if (!id) return
    const candidate = { id, distance: position.distanceTo(camera.position), pixels }
    const previous = candidates.get(id)
    if (!previous || pixels < previous.pixels - .25 || (Math.abs(pixels - previous.pixels) <= .25 && candidate.distance < previous.distance)) candidates.set(id, candidate)
  }
  const originalSides = new Map<THREE.Material, THREE.Side>()
  for (const object of meshes) {
    const source = (object as THREE.Mesh).material
    for (const material of Array.isArray(source) ? source : [source]) {
      if (!originalSides.has(material)) originalSides.set(material, material.side)
      material.side = THREE.DoubleSide
    }
  }
  try {
    ray.setFromCamera(pointer, camera)
    ray.intersectObjects(meshes, false).forEach(hit => accept(hit, 0))
    // Only widen an empty mesh hit. Center hits should not be stolen by a
    // neighboring bar; eight probes bound the cost for dense real meshes.
    if (!candidates.size) {
      for (let i = 0; i < 8; i++) {
        const angle = i * Math.PI / 4, pixels = radius * .7
        ray.setFromCamera(new THREE.Vector2(pointer.x + Math.cos(angle) * pixels * 2 / viewport.width,
          pointer.y + Math.sin(angle) * pixels * 2 / viewport.height), camera)
        ray.intersectObjects(meshes, false).forEach(hit => accept(hit, pixels))
      }
    }
  } finally {
    for (const [material, side] of originalSides) material.side = side
  }
  // When the scan is the only visible evidence, choose by projected distance,
  // not a world-radius hit whose actual point may be many pixels off the cursor.
  if (!candidates.size) {
    ray.setFromCamera(pointer, camera)
    for (const object of points) {
      const geometry = object.geometry
      if (!geometry.boundingSphere) geometry.computeBoundingSphere()
      const sphere = geometry.boundingSphere!.clone().applyMatrix4(object.matrixWorld)
      const distance = camera.position.distanceTo(sphere.center) + sphere.radius
      ray.params.Points.threshold = camera instanceof THREE.PerspectiveCamera
        ? 2 * distance * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) / camera.zoom * radius / viewport.height
        : (camera.top - camera.bottom) / camera.zoom * radius / viewport.height
      ray.intersectObject(object, false).forEach(hit => accept(hit, 0))
    }
  }
  return [...candidates.values()].sort((a, b) => Math.abs(a.pixels - b.pixels) > .5 ? a.pixels - b.pixels : a.distance - b.distance)
}
