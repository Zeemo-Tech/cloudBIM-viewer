import * as THREE from 'three'

// Keep interpolation in sRGB so browser-side recoloring matches the PLY bytes
// produced by the mesh service and the CSS legend gradient. Three.js vertex
// attributes are linear, so setRGB performs the sRGB -> linear conversion last.
const COLOR_STOPS = [
  [0x0d / 255, 0x47 / 255, 0xa1 / 255],
  [0x00 / 255, 0xbc / 255, 0xd4 / 255],
  [0x00 / 255, 0xc8 / 255, 0x53 / 255],
  [0xff / 255, 0xd6 / 255, 0x00 / 255],
  [0xd5 / 255, 0x00 / 255, 0x00 / 255],
] as const

const OUT_OF_RANGE_COLOR = new THREE.Color(0x3a3a3a)

export function c2mDivergingColor(value: number, target = new THREE.Color()) {
  const scaled = THREE.MathUtils.clamp(value, 0, 1) * (COLOR_STOPS.length - 1)
  const lower = Math.min(Math.floor(scaled), COLOR_STOPS.length - 2)
  const fraction = scaled - lower
  const start = COLOR_STOPS[lower]
  const end = COLOR_STOPS[lower + 1]
  return target.setRGB(
    THREE.MathUtils.lerp(start[0], end[0], fraction),
    THREE.MathUtils.lerp(start[1], end[1], fraction),
    THREE.MathUtils.lerp(start[2], end[2], fraction),
    THREE.SRGBColorSpace,
  )
}

export function c2mDistancePosition(
  distance: number,
  toleranceLimit: number,
  colormapLimit: number,
) {
  const tolerance = Math.max(toleranceLimit, Number.EPSILON)
  const limit = Math.max(colormapLimit, tolerance + Number.EPSILON)
  if (!Number.isFinite(distance) || Math.abs(distance) > limit) return null

  if (distance <= -tolerance) {
    return 0.25 * (distance + limit) / (limit - tolerance)
  }
  if (distance <= 0) {
    return 0.25 + 0.25 * (distance + tolerance) / tolerance
  }
  if (distance < tolerance) {
    return 0.5 + 0.25 * distance / tolerance
  }
  return 0.75 + 0.25 * (distance - tolerance) / (limit - tolerance)
}

export function applyC2MVertexColors(
  geometry: THREE.BufferGeometry,
  distances: Float32Array,
  colormapLimit: number,
  toleranceLimit = 0.05,
) {
  const positions = geometry.getAttribute('position')
  if (!positions || positions.count !== distances.length) return false

  let colors = geometry.getAttribute('color') as THREE.BufferAttribute | undefined
  if (!colors || colors.count !== distances.length || colors.itemSize !== 3) {
    colors = new THREE.BufferAttribute(new Float32Array(distances.length * 3), 3)
    geometry.setAttribute('color', colors)
  }

  const color = new THREE.Color()
  for (let index = 0; index < distances.length; index += 1) {
    const position = c2mDistancePosition(distances[index], toleranceLimit, colormapLimit)
    if (position === null) color.copy(OUT_OF_RANGE_COLOR)
    else c2mDivergingColor(position, color)
    colors.setXYZ(index, color.r, color.g, color.b)
  }
  colors.needsUpdate = true
  return true
}

export function histogramFromC2MDistances(
  distances: Float32Array,
  maxDistance: number,
  binCount: number,
) {
  const limit = Math.max(maxDistance, Number.EPSILON)
  const bins = Math.max(10, Math.min(200, Math.floor(binCount)))
  const counts = Array.from({ length: bins }, () => 0)
  const binEdges = Array.from(
    { length: bins + 1 },
    (_, index) => -limit + (index / bins) * limit * 2,
  )
  let overflowCount = 0

  distances.forEach((distance) => {
    if (!Number.isFinite(distance) || distance < -limit || distance > limit) {
      overflowCount += 1
      return
    }
    const index = Math.min(bins - 1, Math.floor(((distance + limit) / (limit * 2)) * bins))
    counts[index] += 1
  })

  return { binEdges, counts, overflowCount }
}

export function parseC2MDistances(
  buffer: ArrayBuffer,
  expectedVertexCount: number,
): Float32Array | null {
  if (buffer.byteLength % Float32Array.BYTES_PER_ELEMENT !== 0) return null
  const count = buffer.byteLength / Float32Array.BYTES_PER_ELEMENT
  if (count !== expectedVertexCount) return null
  const view = new DataView(buffer)
  const distances = new Float32Array(count)
  for (let index = 0; index < count; index += 1) {
    const value = view.getFloat32(index * Float32Array.BYTES_PER_ELEMENT, true)
    if (!Number.isFinite(value)) return null
    distances[index] = value
  }
  return distances
}
