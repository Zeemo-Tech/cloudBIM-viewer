import { MathUtils, Vector3, type BufferGeometry } from 'three'

export const resultPalette = { measured: '#8292a6', low: '#ce7378', medium: '#bf4351', high: '#851c38', missing: '#d99b23', review: '#80649b' }

// Display bands only. The existing > tolerance issue rule is unchanged.
export function operatorResultColor(state: string, p95: number | null | undefined, tolerance: number | null) {
  if (state === 'missing') return resultPalette.missing
  if (state === 'review' || !Number.isFinite(p95) || !tolerance || tolerance <= 0) return resultPalette.review
  if (p95! <= tolerance) return resultPalette.measured
  if (p95! <= tolerance * 1.5) return resultPalette.low
  if (p95! <= tolerance * 2) return resultPalette.medium
  return resultPalette.high
}

export function operatorResultLegend(tolerance: number | null) {
  const mm = (value: number) => Number((value * 1000).toFixed(2)).toString()
  const bands = tolerance && tolerance > 0 ? [
    { color: resultPalette.measured, label: `≤${mm(tolerance)}` },
    { color: resultPalette.low, label: `${mm(tolerance)}–${mm(tolerance * 1.5)}` },
    { color: resultPalette.medium, label: `${mm(tolerance * 1.5)}–${mm(tolerance * 2)}` },
    { color: resultPalette.high, label: `>${mm(tolerance * 2)}` },
  ] : []
  return [...bands, { color: resultPalette.missing, label: '缺测' }, { color: resultPalette.review, label: '待复核' }]
}

export interface OperatorBarFrame { center: Vector3; axis: Vector3; up: Vector3; side: Vector3; halfSize: Vector3; corners: Vector3[] }

// Geometry is already BIM/GLB XYZ in metres, +Y up. PCA describes the design
// bar's dominant axis for display only; it is not an observed centreline fit.
export function operatorBarFrame(geometry: BufferGeometry, start: number, count: number): OperatorBarFrame | null {
  const positions = geometry.getAttribute('position')
  if (!positions || count < 2 || start < 0 || start + count > positions.count) return null
  const mean = new Vector3(), point = new Vector3()
  for (let i = start; i < start + count; i++) mean.add(point.fromBufferAttribute(positions, i))
  mean.divideScalar(count)
  const covariance = new Array<number>(9).fill(0)
  for (let i = start; i < start + count; i++) {
    point.fromBufferAttribute(positions, i).sub(mean)
    const a = point.toArray()
    for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) covariance[r * 3 + c]! += a[r]! * a[c]!
  }
  const diagonal = [covariance[0]!, covariance[4]!, covariance[8]!]
  const dominant = diagonal.indexOf(Math.max(...diagonal))
  const axis = new Vector3().setComponent(dominant, 1)
  for (let i = 0; i < 32; i++) {
    point.set(covariance[0]! * axis.x + covariance[1]! * axis.y + covariance[2]! * axis.z,
      covariance[3]! * axis.x + covariance[4]! * axis.y + covariance[5]! * axis.z,
      covariance[6]! * axis.x + covariance[7]! * axis.y + covariance[8]! * axis.z)
    if (point.lengthSq() < 1e-20) break
    axis.copy(point.normalize())
  }
  if (axis.getComponent(dominant) < 0) axis.negate()
  const up = Math.abs(axis.y) < .95 ? new Vector3(0, 1, 0) : new Vector3(0, 0, 1)
  up.addScaledVector(axis, -up.dot(axis)).normalize()
  const side = new Vector3().crossVectors(axis, up).normalize()
  const min = new Vector3(Infinity, Infinity, Infinity), max = new Vector3(-Infinity, -Infinity, -Infinity)
  for (let i = start; i < start + count; i++) {
    point.fromBufferAttribute(positions, i).sub(mean)
    const local = new Vector3(point.dot(axis), point.dot(up), point.dot(side))
    min.min(local); max.max(local)
  }
  const middle = min.clone().add(max).multiplyScalar(.5)
  const center = mean.clone().addScaledVector(axis, middle.x).addScaledVector(up, middle.y).addScaledVector(side, middle.z)
  // 20 mm breathing room makes the wire box readable without covering steel.
  const halfSize = max.clone().sub(min).multiplyScalar(.5).addScalar(.02)
  const corners: Vector3[] = []
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1])
    corners.push(center.clone().addScaledVector(axis, x * halfSize.x).addScaledVector(up, y * halfSize.y).addScaledVector(side, z * halfSize.z))
  return { center, axis, up, side, halfSize, corners }
}

export function operatorBoxEdges(frame: OperatorBarFrame) {
  const points: number[] = []
  for (let i = 0; i < 8; i++) for (const bit of [1, 2, 4]) if ((i & bit) === 0)
    points.push(...frame.corners[i]!.toArray(), ...frame.corners[i | bit]!.toArray())
  return points
}

export function operatorFitDistance(frame: OperatorBarFrame, direction: Vector3, fov: number, aspect: number) {
  const right = new Vector3().crossVectors(new Vector3(0, 1, 0), direction).normalize()
  const up = new Vector3().crossVectors(direction, right).normalize()
  const tan = Math.tan(MathUtils.degToRad(fov) / 2)
  let distance = .05
  for (const corner of frame.corners) {
    const offset = corner.clone().sub(frame.center), toward = offset.dot(direction)
    distance = Math.max(distance, toward + Math.abs(offset.dot(right)) / (tan * aspect), toward + Math.abs(offset.dot(up)) / tan)
  }
  return distance * 1.18
}

export const wrappedAngle = (angle: number) => Math.atan2(Math.sin(angle), Math.cos(angle))
export function operatorAxisHeading(frame: OperatorBarFrame, reference: number) {
  const axis = frame.axis.clone().setY(0)
  if (axis.lengthSq() < 1e-6) return reference // Vertical bars have no horizontal axial heading.
  const heading = Math.atan2(axis.x, axis.z)
  return Math.abs(wrappedAngle(heading - reference)) <= Math.PI / 2 + 1e-8 ? heading : heading + Math.PI
}
