import * as THREE from 'three'

// Shared sRGB contract for the CPU, shader, legend and exported PLY.
// Tolerance is a classification boundary: green inside, cool/warm outside.
export const C2M_UNKNOWN_COLOR = '#a8b2c1'
const BLUE = [59, 130, 246] as const
const CYAN = [34, 211, 238] as const
const GREEN_EDGE = [134, 239, 172] as const
const GREEN = [34, 197, 94] as const
const AMBER = [251, 191, 36] as const
const RED = [255, 82, 82] as const

export function c2mDivergingColor(value: number, target = new THREE.Color()) {
  const t = THREE.MathUtils.clamp(value, 0, 1)
  const [start, end, fraction] = t < 0.25 ? [BLUE, CYAN, t * 4] as const
    : t <= 0.5 ? [GREEN_EDGE, GREEN, (t - 0.25) * 4] as const
    : t <= 0.75 ? [GREEN, GREEN_EDGE, (t - 0.5) * 4] as const
    : [AMBER, RED, (t - 0.75) * 4] as const
  return target.setRGB(
    THREE.MathUtils.lerp(start[0], end[0], fraction) / 255,
    THREE.MathUtils.lerp(start[1], end[1], fraction) / 255,
    THREE.MathUtils.lerp(start[2], end[2], fraction) / 255,
    THREE.SRGBColorSpace,
  )
}

// Quantize within each classification so a barely failing value never turns green.
export function c2mQuantizePosition(position: number, bands: number) {
  const steps = Math.max(2, Math.round(bands))
  if (position < 0.25) return Math.min(0.249999, Math.floor(position * 4 * steps) / steps / 4)
  if (position > 0.75) return Math.max(0.750001, 0.75 + Math.ceil((position - 0.75) * 4 * steps) / steps / 4)
  return 0.25 + Math.round((position - 0.25) * 2 * steps) / steps / 2
}

export function c2mColorCss(distance: number, tolerance: number, limit: number, discrete = false, bands = 7) {
  let position = c2mDistancePosition(distance, tolerance, limit)
  if (position === null) return C2M_UNKNOWN_COLOR
  if (discrete) position = c2mQuantizePosition(position, bands)
  return '#' + c2mDivergingColor(position).getHexString(THREE.SRGBColorSpace)
}

export function c2mDistancePosition(
  distance: number,
  toleranceLimit: number,
  colormapLimit: number,
) {
  const tolerance = Math.max(toleranceLimit, Number.EPSILON)
  const limit = Math.max(colormapLimit, tolerance + Number.EPSILON)
  if (!Number.isFinite(distance)) return null
  if (distance < -limit) return 0
  if (distance > limit) return 1

  if (distance < -tolerance) {
    return 0.25 * (distance + limit) / (limit - tolerance)
  }
  if (distance <= 0) {
    return 0.25 + 0.25 * (distance + tolerance) / tolerance
  }
  if (distance <= tolerance) {
    return 0.5 + 0.25 * distance / tolerance
  }
  return 0.75 + 0.25 * (distance - tolerance) / (limit - tolerance)
}

export function applyC2MVertexColors(
  geometry: THREE.BufferGeometry,
  distances: Float32Array,
  colormapLimit: number,
  toleranceLimit = 0.05,
  discrete = false,
  bands = 7,
) {
  const positions = geometry.getAttribute('position')
  if (!positions || positions.count !== distances.length) return false

  let colors = geometry.getAttribute('color') as THREE.BufferAttribute | undefined
  if (!colors || colors.count !== distances.length || colors.itemSize !== 3) {
    colors = new THREE.BufferAttribute(new Float32Array(distances.length * 3), 3)
    geometry.setAttribute('color', colors)
  }

  const color = new THREE.Color()
  const unknown = new THREE.Color(C2M_UNKNOWN_COLOR)
  for (let index = 0; index < distances.length; index += 1) {
    const position = c2mDistancePosition(distances[index], toleranceLimit, colormapLimit)
    if (position === null) color.copy(unknown)
    else c2mDivergingColor(discrete ? c2mQuantizePosition(position, bands) : position, color)
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
  let underflowCount = 0
  let positiveOverflowCount = 0
  let unknownCount = 0

  distances.forEach((distance) => {
    if (!Number.isFinite(distance)) { unknownCount += 1; return }
    if (distance < -limit || distance > limit) {
      overflowCount += 1
      if (distance < -limit) underflowCount += 1
      else positiveOverflowCount += 1
      return
    }
    const index = Math.min(bins - 1, Math.floor(((distance + limit) / (limit * 2)) * bins))
    counts[index] += 1
  })

  return { binEdges, counts, overflowCount, underflowCount, positiveOverflowCount, unknownCount }
}

export function parseC2MDistances(
  buffer: ArrayBuffer,
  expectedVertexCount: number,
  allowUnknown = false,
): Float32Array | null {
  if (buffer.byteLength % Float32Array.BYTES_PER_ELEMENT !== 0) return null
  const count = buffer.byteLength / Float32Array.BYTES_PER_ELEMENT
  if (count !== expectedVertexCount) return null
  const view = new DataView(buffer)
  const distances = new Float32Array(count)
  for (let index = 0; index < count; index += 1) {
    const value = view.getFloat32(index * Float32Array.BYTES_PER_ELEMENT, true)
    if (!Number.isFinite(value) && !(allowUnknown && Number.isNaN(value))) return null
    distances[index] = value
  }
  return distances
}
