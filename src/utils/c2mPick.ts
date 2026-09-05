import * as THREE from 'three'

export function sampleC2MDeviationAtPick(
  intersection: THREE.Intersection,
  mesh: THREE.Mesh,
): number | null {
  const geometry = mesh.geometry
  const distances = geometry.getAttribute('distance')
  const positions = geometry.getAttribute('position')
  const faceIndex = intersection.faceIndex
  if (!distances || !positions || faceIndex === undefined || faceIndex === null || faceIndex < 0) {
    return null
  }

  const offset = faceIndex * 3
  const indices = geometry.index
    ? [geometry.index.getX(offset), geometry.index.getX(offset + 1), geometry.index.getX(offset + 2)]
    : [offset, offset + 1, offset + 2]
  if (indices.some((index) => index < 0 || index >= positions.count || index >= distances.count)) {
    return null
  }

  const a = new THREE.Vector3().fromBufferAttribute(positions, indices[0])
  const b = new THREE.Vector3().fromBufferAttribute(positions, indices[1])
  const c = new THREE.Vector3().fromBufferAttribute(positions, indices[2])
  const localPoint = mesh.worldToLocal(intersection.point.clone())
  const barycentric = THREE.Triangle.getBarycoord(localPoint, a, b, c, new THREE.Vector3())
  if (!barycentric) return null

  const deviation =
    barycentric.x * distances.getX(indices[0]) +
    barycentric.y * distances.getX(indices[1]) +
    barycentric.z * distances.getX(indices[2])
  return Number.isFinite(deviation) ? deviation : null
}
