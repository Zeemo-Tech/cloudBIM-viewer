import type { CylinderGeometry, FeatureGeometry, FitOptions, FitResult, PlaneGeometry, XYZ } from './types'

// Fits operate in a seed-centred, radius-normalised copy of the supplied metre
// frame. No axis permutation or handedness change is made here.
const MAX_SAMPLE = 4096
const MAX_SUPPORT = 256
const MIN_PLANE_POINTS = 12
const MIN_CYLINDER_POINTS = 36
const MIN_PLANE_ANGLE = Math.sin(12 * Math.PI / 180)
const TAU = 2 * Math.PI

interface LocalPoints { points: XYZ[]; count: number; tolerance: number; radius: number; seed: XYZ }
interface PlaneFit { center: XYZ; normal: XYZ; indices: number[]; error: number }
interface CylinderFit { center: XYZ; axis: XYZ; radius: number; indices: number[]; error: number }
interface EigenSystem { values: number[]; vectors: XYZ[] }

const add = (a: XYZ, b: XYZ): XYZ => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const sub = (a: XYZ, b: XYZ): XYZ => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
const scale = (a: XYZ, s: number): XYZ => [a[0] * s, a[1] * s, a[2] * s]
const dot = (a: XYZ, b: XYZ): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross = (a: XYZ, b: XYZ): XYZ => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const norm = (a: XYZ): number => Math.sqrt(dot(a, a))
const unit = (a: XYZ): XYZ => scale(a, 1 / Math.max(norm(a), 1e-30))
const finitePoint = (p: XYZ): boolean => Number.isFinite(p[0]) && Number.isFinite(p[1]) && Number.isFinite(p[2])
const fail = <T>(reason: string): FitResult<T> => ({ ok: false, reason })

function random(seed = 0x69e2f38b): () => number {
  let state = seed >>> 0
  return () => {
    state ^= state << 13
    state ^= state >>> 17
    state ^= state << 5
    return (state >>> 0) / 4294967296
  }
}

function canonical(direction: XYZ, facing?: XYZ): XYZ {
  let sign = 1
  if (facing && finitePoint(facing) && Math.abs(dot(direction, facing)) > 1e-12) {
    sign = dot(direction, facing) < 0 ? -1 : 1
  } else {
    let largest = 0
    for (let k = 1; k < 3; k++) if (Math.abs(direction[k]!) > Math.abs(direction[largest]!)) largest = k
    if (direction[largest]! < 0) sign = -1
  }
  return scale(direction, sign)
}

function prepare(input: readonly XYZ[], seed: XYZ, options: FitOptions): FitResult<LocalPoints> {
  if (!finitePoint(seed) || !Number.isFinite(options.radius) || options.radius <= 0 || !Number.isFinite(options.tolerance) || options.tolerance <= 0) {
    return fail('邻域半径、容差和取点坐标必须为有效数值')
  }
  if (options.tolerance > options.radius / 4) return fail('拟合容差相对邻域过大，请减小容差')
  const points: XYZ[] = []
  const rng = random()
  let count = 0
  for (const p of input) {
    if (!finitePoint(p)) continue
    const q = scale(sub(p, seed), 1 / options.radius)
    if (dot(q, q) > 1 + 1e-12) continue
    count++
    if (points.length < MAX_SAMPLE) points.push(q)
    else {
      const index = Math.floor(rng() * count)
      if (index < MAX_SAMPLE) points[index] = q
    }
  }
  return { ok: true, value: { points, count, tolerance: options.tolerance / options.radius, radius: options.radius, seed: [...seed] } }
}

/** Symmetric 3x3 Jacobi decomposition; values and vectors are ascending. */
function eigen(matrix: number[][]): EigenSystem {
  const a = matrix.map(row => [...row])
  const vectors = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
  for (let iter = 0; iter < 40; iter++) {
    let p = 0, q = 1
    for (const [i, j] of [[0, 2], [1, 2]]) if (Math.abs(a[i!]![j!]!) > Math.abs(a[p]![q]!)) { p = i!; q = j! }
    const apq = a[p]![q]!
    if (Math.abs(apq) < 1e-15) break
    const angle = 0.5 * Math.atan2(2 * apq, a[q]![q]! - a[p]![p]!)
    const c = Math.cos(angle), s = Math.sin(angle)
    const app = a[p]![p]!, aqq = a[q]![q]!
    a[p]![p] = c * c * app - 2 * s * c * apq + s * s * aqq
    a[q]![q] = s * s * app + 2 * s * c * apq + c * c * aqq
    a[p]![q] = a[q]![p] = 0
    for (let k = 0; k < 3; k++) {
      if (k !== p && k !== q) {
        const akp = a[k]![p]!, akq = a[k]![q]!
        a[k]![p] = a[p]![k] = c * akp - s * akq
        a[k]![q] = a[q]![k] = s * akp + c * akq
      }
      const vkp = vectors[k]![p]!, vkq = vectors[k]![q]!
      vectors[k]![p] = c * vkp - s * vkq
      vectors[k]![q] = s * vkp + c * vkq
    }
  }
  const order = [0, 1, 2].sort((i, j) => a[i]![i]! - a[j]![j]!)
  return { values: order.map(i => Math.max(0, a[i]![i]!)), vectors: order.map(i => unit([vectors[0]![i]!, vectors[1]![i]!, vectors[2]![i]!])) }
}

function covariance(points: readonly XYZ[], indices: readonly number[]): { center: XYZ; eigen: EigenSystem } {
  let center: XYZ = [0, 0, 0]
  for (const i of indices) center = add(center, points[i]!)
  center = scale(center, 1 / indices.length)
  const matrix = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
  for (const i of indices) {
    const p = sub(points[i]!, center)
    for (let j = 0; j < 3; j++) for (let k = j; k < 3; k++) matrix[j]![k] = matrix[j]![k]! + p[j]! * p[k]! / indices.length
  }
  for (let j = 0; j < 3; j++) for (let k = j + 1; k < 3; k++) matrix[k]![j] = matrix[j]![k]!
  return { center, eigen: eigen(matrix) }
}

function planeInliers(points: readonly XYZ[], available: readonly number[], center: XYZ, normal: XYZ, tolerance: number): { indices: number[]; error: number } {
  const indices: number[] = []
  let error = 0
  for (const i of available) {
    const distance = dot(sub(points[i]!, center), normal)
    if (Math.abs(distance) <= tolerance) { indices.push(i); error += distance * distance }
  }
  return { indices, error }
}

function supportedPlane(points: readonly XYZ[], indices: readonly number[], tolerance: number): boolean {
  if (indices.length < MIN_PLANE_POINTS) return false
  const eig = covariance(points, indices).eigen.values
  // Two independent dimensions must exceed noise. A line with a thick noisy
  // cross-section is not accepted as a locally supported plane.
  return Math.sqrt(eig[1]!) >= Math.max(0.035, 2.5 * tolerance) && eig[1]! / Math.max(eig[2]!, 1e-20) > 0.008
}

function ransacPlane(points: readonly XYZ[], available: readonly number[], tolerance: number, minimum: number): PlaneFit | null {
  if (available.length < minimum) return null
  const rng = random(0x752fe5d1 + available.length)
  let best: PlaneFit | null = null
  const consider = (center: XYZ, normal: XYZ) => {
    const result = planeInliers(points, available, center, normal, tolerance)
    if (result.indices.length < minimum) return
    if (!best || result.indices.length > best.indices.length || (result.indices.length === best.indices.length && result.error < best.error)) best = { center, normal, ...result }
  }
  const pca = covariance(points, available)
  consider(pca.center, pca.eigen.vectors[0]!)
  for (let trial = 0; trial < 384; trial++) {
    const ia = Math.floor(rng() * available.length), ib = Math.floor(rng() * available.length), ic = Math.floor(rng() * available.length)
    if (ia === ib || ia === ic || ib === ic) continue
    const a = points[available[ia]!]!, b = points[available[ib]!]!, c = points[available[ic]!]!
    const n = cross(sub(b, a), sub(c, a))
    if (norm(n) < 1e-5) continue
    consider(a, unit(n))
  }
  if (!best) return null
  // Reclassify after each orthogonal least-squares refinement.
  let result: PlaneFit = best
  for (let iter = 0; iter < 5; iter++) {
    const refined = covariance(points, result.indices)
    const inliers = planeInliers(points, available, refined.center, refined.eigen.vectors[0]!, tolerance)
    if (inliers.indices.length < minimum) return null
    result = { center: refined.center, normal: refined.eigen.vectors[0]!, ...inliers }
  }
  if (!supportedPlane(points, result.indices, tolerance)) return null
  return result
}

function toMetres(local: LocalPoints, point: XYZ): XYZ { return add(local.seed, scale(point, local.radius)) }

function eachLocal(input: readonly XYZ[], local: LocalPoints, visit: (point: XYZ, original: XYZ) => void): void {
  for (const original of input) {
    if (!finitePoint(original)) continue
    const p = scale(sub(original, local.seed), 1 / local.radius)
    if (dot(p, p) <= 1 + 1e-12) visit(p, original)
  }
}

function planeGeometry(input: readonly XYZ[], local: LocalPoints, fit: PlaneFit, facing?: XYZ): PlaneGeometry {
  const normal = canonical(fit.normal, facing)
  const origin = scale(normal, dot(normal, fit.center)) // Orthogonal seed projection.
  const support: XYZ[] = []
  const rng = random(0xd05289e1)
  let inlierCount = 0, error = 0
  eachLocal(input, local, (p, original) => {
    const distance = dot(sub(p, fit.center), normal)
    if (Math.abs(distance) > local.tolerance) return
    inlierCount++
    error += distance * distance
    if (support.length < MAX_SUPPORT) support.push([...original])
    else { const index = Math.floor(rng() * inlierCount); if (index < MAX_SUPPORT) support[index] = [...original] }
  })
  return { kind: 'plane', origin: toMetres(local, origin), normal, radius: local.radius,
    rmse: Math.sqrt(error / Math.max(inlierCount, 1)) * local.radius, pointCount: local.count, inlierCount, support,
    algorithm: 'local-ransac-pca-v1' }
}

export function fitLocalPlane(points: readonly XYZ[], seed: XYZ, options: FitOptions): FitResult<PlaneGeometry> {
  const prepared = prepare(points, seed, options)
  if (!prepared.ok) return prepared
  const local = prepared.value
  if (local.count < MIN_PLANE_POINTS) return fail('邻域点数不足，无法拟合平面')
  const fit = ransacPlane(local.points, local.points.map((_, i) => i), local.tolerance, Math.max(MIN_PLANE_POINTS, Math.ceil(local.points.length * 0.3)))
  if (!fit) return fail('邻域缺少稳定的二维平面支持，请调整取点或邻域')
  if (Math.abs(dot(fit.center, fit.normal)) > Math.max(3 * local.tolerance, 0.35)) return fail('拟合平面距离取点过远，请重新取点')
  return { ok: true, value: planeGeometry(points, local, fit, options.facing) }
}

export function fitEdgeOrCorner(points: readonly XYZ[], seed: XYZ, options: FitOptions, kind: 'edge' | 'corner'): FitResult<FeatureGeometry> {
  const prepared = prepare(points, seed, options)
  if (!prepared.ok) return prepared
  const local = prepared.value
  const required = kind === 'edge' ? 2 : 3
  if (local.count < required * MIN_PLANE_POINTS) return fail('邻域点数不足，无法拟合相交表面')
  let remaining = local.points.map((_, i) => i)
  const fits: PlaneFit[] = []
  const minimum = Math.max(MIN_PLANE_POINTS, Math.ceil(local.points.length * 0.1))
  let parallel = false
  for (let attempt = 0; attempt < 7 && fits.length < required && remaining.length >= minimum; attempt++) {
    const fit = ransacPlane(local.points, remaining, local.tolerance, minimum)
    if (!fit) break
    remaining = remaining.filter(i => Math.abs(dot(sub(local.points[i]!, fit.center), fit.normal)) > local.tolerance * 1.35)
    if (fits.some(previous => norm(cross(previous.normal, fit.normal)) < MIN_PLANE_ANGLE)) { parallel = true; continue }
    fits.push(fit)
  }
  if (fits.length < required) return fail(parallel ? '相邻表面近似平行，无法稳定确定交点' : `邻域中未找到${required === 2 ? '两个' : '三个'}有足够支持的相交平面`)
  let position: XYZ
  let direction: XYZ | undefined
  const n1 = fits[0]!.normal, n2 = fits[1]!.normal
  const d1 = dot(n1, fits[0]!.center), d2 = dot(n2, fits[1]!.center)
  if (kind === 'edge') {
    const intersection = cross(n1, n2)
    const denominator = dot(intersection, intersection)
    if (denominator < MIN_PLANE_ANGLE * MIN_PLANE_ANGLE) return fail('两表面夹角过小，交线不稳定')
    position = scale(add(scale(cross(n2, intersection), d1), scale(cross(intersection, n1), d2)), 1 / denominator)
    direction = canonical(unit(intersection))
  } else {
    const n3 = fits[2]!.normal, d3 = dot(n3, fits[2]!.center)
    const determinant = dot(n1, cross(n2, n3))
    const normalGram = [n1, n2, n3].map(a => [n1, n2, n3].map(b => dot(a, b)))
    const conditioning = eigen(normalGram).values
    if (Math.abs(determinant) < 1e-6 || conditioning[0]! < conditioning[2]! / 225) return fail('三个表面方向接近退化，角点不稳定')
    position = scale(add(add(scale(cross(n2, n3), d1), scale(cross(n3, n1), d2)), scale(cross(n1, n2), d3)), 1 / determinant)
  }
  if (norm(position) > 0.85) return fail('交点超出取点附近的可靠范围，请靠近交点重新取点')
  for (const fit of fits) {
    let nearest = Infinity
    for (const i of fit.indices) {
      const offset = sub(local.points[i]!, position)
      const distance = direction ? norm(sub(offset, scale(direction, dot(offset, direction)))) : norm(offset)
      nearest = Math.min(nearest, distance)
    }
    if (nearest > 0.65) return fail('交点距离实际表面支持过远，请扩大邻域或重新取点')
  }
  const planes = fits.map(fit => planeGeometry(points, local, fit, options.facing))
  const count = planes.reduce((sum, plane) => sum + plane.inlierCount, 0)
  const error = planes.reduce((sum, plane) => sum + plane.rmse * plane.rmse * plane.inlierCount, 0)
  return { ok: true, value: { kind, position: toMetres(local, position), ...(direction ? { direction } : {}), planes,
    rmse: Math.sqrt(error / Math.max(count, 1)), algorithm: 'local-multiplane-ransac-pca-v1' } }
}

function basis(axis: XYZ): [XYZ, XYZ] {
  const reference: XYZ = Math.abs(axis[0]) < 0.75 ? [1, 0, 0] : [0, 1, 0]
  const u = unit(cross(axis, reference))
  return [u, cross(axis, u)]
}

function solve(matrix: number[][], rhs: number[]): number[] | null {
  const a = matrix.map((row, i) => [...row, rhs[i]!])
  const n = rhs.length
  for (let col = 0; col < n; col++) {
    let pivot = col
    for (let row = col + 1; row < n; row++) if (Math.abs(a[row]![col]!) > Math.abs(a[pivot]![col]!)) pivot = row
    if (Math.abs(a[pivot]![col]!) < 1e-14) return null
    const temp = a[col]!; a[col] = a[pivot]!; a[pivot] = temp
    const divisor = a[col]![col]!
    for (let k = col; k <= n; k++) a[col]![k] = a[col]![k]! / divisor
    for (let row = 0; row < n; row++) {
      if (row === col) continue
      const factor = a[row]![col]!
      for (let k = col; k <= n; k++) a[row]![k] = a[row]![k]! - factor * a[col]![k]!
    }
  }
  const result = a.map(row => row[n]!)
  return result.every(Number.isFinite) ? result : null
}

function circleFromThree(a: number[], b: number[], c: number[]): [number, number, number] | null {
  const bx = b[0]! - a[0]!, by = b[1]! - a[1]!, cx = c[0]! - a[0]!, cy = c[1]! - a[1]!
  const determinant = 2 * (bx * cy - by * cx)
  if (Math.abs(determinant) < 1e-5) return null
  const b2 = bx * bx + by * by, c2 = cx * cx + cy * cy
  const x = a[0]! + (cy * b2 - by * c2) / determinant
  const y = a[1]! + (bx * c2 - cx * b2) / determinant
  return [x, y, Math.hypot(a[0]! - x, a[1]! - y)]
}

function leastSquaresCircle(points: number[][]): [number, number, number] | null {
  const matrix = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], rhs = [0, 0, 0]
  for (const [x, y] of points) {
    const row = [x!, y!, 1], target = -(x! * x! + y! * y!)
    for (let j = 0; j < 3; j++) { rhs[j] = rhs[j]! + row[j]! * target; for (let k = 0; k < 3; k++) matrix[j]![k] = matrix[j]![k]! + row[j]! * row[k]! }
  }
  const solution = solve(matrix, rhs)
  if (!solution) return null
  const x = -solution[0]! / 2, y = -solution[1]! / 2, r2 = x * x + y * y - solution[2]!
  return r2 > 0 ? [x, y, Math.sqrt(r2)] : null
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)] ?? 0
}

function initialCylinder(points: readonly XYZ[], center: XYZ, axis: XYZ, tolerance: number): CylinderFit | null {
  const [u, v] = basis(axis)
  const projected = points.map(p => { const q = sub(p, center); return [dot(q, u), dot(q, v)] })
  let best: [number, number, number] | null = null, bestCost = Infinity
  const rng = random(0x8173fc63)
  const consider = (circle: [number, number, number] | null) => {
    if (!circle || circle[2] < 4 * tolerance || circle[2] > 2) return
    const distances = projected.map(p => Math.abs(Math.hypot(p[0]! - circle[0], p[1]! - circle[1]) - circle[2]))
    // A trimmed error tolerates outliers without rewarding a tiny fitted strip.
    distances.sort((a, b) => a - b)
    let cost = 0
    const retained = Math.ceil(distances.length * 0.7)
    for (let i = 0; i < retained; i++) cost += distances[i]! * distances[i]!
    if (cost < bestCost) { bestCost = cost; best = circle }
  }
  consider(leastSquaresCircle(projected))
  for (let trial = 0; trial < 72; trial++) {
    const a = projected[Math.floor(rng() * projected.length)]!, b = projected[Math.floor(rng() * projected.length)]!, c = projected[Math.floor(rng() * projected.length)]!
    consider(circleFromThree(a, b, c))
  }
  if (!best) return null
  const circle: [number, number, number] = best
  return { center: add(center, add(scale(u, circle[0]), scale(v, circle[1]))), axis, radius: circle[2], indices: [], error: bestCost }
}

function cylinderResidual(p: XYZ, fit: CylinderFit): number {
  const q = sub(p, fit.center), height = dot(q, fit.axis)
  return norm(sub(q, scale(fit.axis, height))) - fit.radius
}

function refineCylinder(points: readonly XYZ[], initial: CylinderFit, tolerance: number, robust: boolean, iterations = 30): CylinderFit {
  let fit: CylinderFit = { ...initial, center: [...initial.center], axis: [...initial.axis] }
  const centroid = covariance(points, points.map((_, i) => i)).center
  let damping = 1e-4
  for (let iter = 0; iter < iterations; iter++) {
    const [u, v] = basis(fit.axis)
    const residuals = points.map(p => cylinderResidual(p, fit))
    const cutoff = robust ? Math.max(2 * tolerance, 2.5 * median(residuals.map(Math.abs))) : Infinity
    const weights = residuals.map(residual => Math.abs(residual) <= cutoff ? 1 : cutoff / Math.abs(residual))
    const matrix = Array.from({ length: 5 }, () => Array<number>(5).fill(0)), rhs = Array<number>(5).fill(0)
    let cost = 0
    for (let i = 0; i < points.length; i++) {
      const q = sub(points[i]!, fit.center), h = dot(q, fit.axis), radial = sub(q, scale(fit.axis, h)), distance = norm(radial)
      if (distance < 1e-10) continue
      const ru = dot(radial, u) / distance, rv = dot(radial, v) / distance
      const jacobian = [-ru, -rv, -h * ru, -h * rv, -1]
      const residual = residuals[i]!, weight = weights[i]!
      cost += weight * residual * residual
      for (let j = 0; j < 5; j++) {
        rhs[j] = rhs[j]! - weight * jacobian[j]! * residual
        for (let k = 0; k < 5; k++) matrix[j]![k] = matrix[j]![k]! + weight * jacobian[j]! * jacobian[k]!
      }
    }
    let accepted = false, stepSize = 0
    for (let attempt = 0; attempt < 7; attempt++) {
      const regularized = matrix.map((row, j) => row.map((value, k) => value + (j === k ? damping * Math.max(matrix[j]![j]!, 1e-6) : 0)))
      const delta = solve(regularized, rhs)
      if (!delta) break
      const limiter = Math.max(1, Math.hypot(delta[0]!, delta[1]!) / 0.3, Math.hypot(delta[2]!, delta[3]!) / 0.3, Math.abs(delta[4]!) / 0.3)
      for (let j = 0; j < 5; j++) delta[j] = delta[j]! / limiter
      let trialCenter = add(fit.center, add(scale(u, delta[0]!), scale(v, delta[1]!)))
      const trialAxis = unit(add(fit.axis, add(scale(u, delta[2]!), scale(v, delta[3]!))))
      trialCenter = add(trialCenter, scale(trialAxis, dot(sub(centroid, trialCenter), trialAxis)))
      const trial: CylinderFit = { center: trialCenter, axis: trialAxis, radius: fit.radius + delta[4]!, indices: [], error: Infinity }
      if (trial.radius > 4 * tolerance && trial.radius < 2) {
        let trialCost = 0
        for (let i = 0; i < points.length; i++) { const residual = cylinderResidual(points[i]!, trial); trialCost += weights[i]! * residual * residual }
        if (trialCost < cost) { fit = trial; damping = Math.max(1e-8, damping / 3); stepSize = Math.hypot(...delta); accepted = true; break }
      }
      damping *= 8
    }
    if (!accepted || stepSize < 1e-9) break
  }
  return fit
}

function cylinderInliers(points: readonly XYZ[], fit: CylinderFit, tolerance: number): CylinderFit {
  const indices: number[] = []
  let error = 0
  for (let i = 0; i < points.length; i++) { const residual = cylinderResidual(points[i]!, fit); if (Math.abs(residual) <= tolerance) { indices.push(i); error += residual * residual } }
  return { ...fit, indices, error }
}

function supportedAngularCoverage(inputAngles: number[]): number {
  const angles = [...inputAngles].sort((a, b) => a - b)
  if (angles.length < 2) return 0
  // A few unrelated points can coincidentally satisfy the radial tolerance.
  // They must not turn a small arc into apparent circumferential support.
  const wrapped = [...angles.map(a => a - TAU), ...angles, ...angles.map(a => a + TAU)]
  const window = 10 * Math.PI / 180 + 1e-12, minimum = Math.max(3, Math.ceil(angles.length * 0.005))
  const supported: number[] = []
  let left = 0, right = 0
  for (let i = angles.length; i < 2 * angles.length; i++) {
    while (wrapped[left]! < wrapped[i]! - window) left++
    while (right < wrapped.length && wrapped[right]! <= wrapped[i]! + window) right++
    if (right - left >= minimum) supported.push(wrapped[i]!)
  }
  if (supported.length < 2) return 0
  let largestGap = supported[0]! + TAU - supported[supported.length - 1]!
  for (let i = 1; i < supported.length; i++) largestGap = Math.max(largestGap, supported[i]! - supported[i - 1]!)
  return (TAU - largestGap) * 180 / Math.PI
}

function angularCoverage(points: readonly XYZ[], fit: CylinderFit): number {
  const [u, v] = basis(fit.axis)
  return supportedAngularCoverage(fit.indices.map(i => { const q = sub(points[i]!, fit.center); return (Math.atan2(dot(q, v), dot(q, u)) + TAU) % TAU }))
}

export function fitCylinder(points: readonly XYZ[], seed: XYZ, options: FitOptions): FitResult<CylinderGeometry> {
  const prepared = prepare(points, seed, options)
  if (!prepared.ok) return prepared
  const local = prepared.value
  if (local.count < MIN_CYLINDER_POINTS) return fail('邻域点数不足，无法拟合圆柱')
  const all = local.points.map((_, i) => i)
  const pca = covariance(local.points, all)
  if (Math.sqrt(pca.eigen.values[0]!) < local.tolerance * 1.25) return fail('邻域近似平面，不能可靠确定圆柱')
  const axes = pca.eigen.vectors.map(axis => [...axis] as XYZ)
  // PCA need not identify a cylinder axis (for example a short, wide cylinder).
  // A fixed hemisphere supplies deterministic starts for the full 5-DOF fit.
  const goldenAngle = Math.PI * (3 - Math.sqrt(5))
  for (let i = 0; i < 24; i++) {
    const z = (i + 0.5) / 24, radial = Math.sqrt(1 - z * z), angle = i * goldenAngle
    axes.push([radial * Math.cos(angle), radial * Math.sin(angle), z])
  }
  const starts = axes.map(axis => initialCylinder(local.points, pca.center, axis, local.tolerance)).filter((fit): fit is CylinderFit => !!fit)
  starts.sort((a, b) => a.error - b.error)
  let best: CylinderFit | null = null
  for (const start of starts.slice(0, 10)) {
    let fit = refineCylinder(local.points, start, local.tolerance, true)
    fit = cylinderInliers(local.points, fit, local.tolerance)
    if (fit.indices.length < Math.max(MIN_CYLINDER_POINTS, local.points.length * 0.55)) continue
    for (let iter = 0; iter < 3; iter++) {
      fit = refineCylinder(fit.indices.map(i => local.points[i]!), fit, local.tolerance, false, 12)
      fit = cylinderInliers(local.points, fit, local.tolerance)
    }
    if (fit.indices.length < Math.max(MIN_CYLINDER_POINTS, local.points.length * 0.55)) continue
    if (angularCoverage(local.points, fit) < 120) continue
    if (!best || fit.indices.length > best.indices.length || (fit.indices.length === best.indices.length && fit.error < best.error)) best = fit
  }
  if (!best) return fail('圆柱支持不足或可见圆弧小于 120°，请扩大邻域或换个位置')
  const fit = best
  const supportCovariance = covariance(local.points, fit.indices)
  if (Math.sqrt(supportCovariance.eigen.values[0]!) < local.tolerance * 1.5) return fail('有效支持近似平面，不能可靠确定圆柱')
  const axis = canonical(unit(fit.axis)), [u, v] = basis(axis)
  const support: XYZ[] = [], angles: number[] = []
  const rng = random(0x419be3c5), angleRng = random(0x317cc7d2)
  let inlierCount = 0, error = 0, minHeight = Infinity, maxHeight = -Infinity
  eachLocal(points, local, (p, original) => {
    const residual = cylinderResidual(p, fit)
    if (Math.abs(residual) > local.tolerance) return
    inlierCount++
    error += residual * residual
    const q = sub(p, fit.center), height = dot(q, axis), angle = (Math.atan2(dot(q, v), dot(q, u)) + TAU) % TAU
    minHeight = Math.min(minHeight, height); maxHeight = Math.max(maxHeight, height)
    if (support.length < MAX_SUPPORT) support.push([...original])
    else { const index = Math.floor(rng() * inlierCount); if (index < MAX_SUPPORT) support[index] = [...original] }
    if (angles.length < MAX_SAMPLE) angles.push(angle)
    else { const index = Math.floor(angleRng() * inlierCount); if (index < MAX_SAMPLE) angles[index] = angle }
  })
  if (maxHeight - minHeight < Math.max(8 * local.tolerance, 0.25 * fit.radius)) return fail('圆柱轴向支持过短，无法稳定确定轴线')
  // Degrees of observed support, ignoring isolated radial-coincidence outliers.
  const coverage = supportedAngularCoverage(angles)
  if (coverage < 120) return fail('可见圆弧小于 120°，无法稳定确定圆柱')
  const center = add(fit.center, scale(axis, (minHeight + maxHeight) / 2))
  return { ok: true, value: { kind: 'cylinder', center: toMetres(local, center), axis, radius: fit.radius * local.radius,
    length: (maxHeight - minHeight) * local.radius, rmse: Math.sqrt(error / inlierCount) * local.radius,
    pointCount: local.count, inlierCount, angularCoverage: coverage, support, algorithm: 'local-robust-cylinder-v1' } }
}
