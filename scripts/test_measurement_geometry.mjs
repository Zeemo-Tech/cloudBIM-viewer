import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import ts from 'typescript'

const repo = path.resolve(import.meta.dirname, '..')
const sourcePath = path.join(repo, 'packages/viewer-core/src/viewer/measurement/geometry.ts')
await fs.mkdir(path.join(repo, '.cloudbim'), { recursive: true })
const temp = await fs.mkdtemp(path.join(repo, '.cloudbim/measurement-geometry-'))
const source = await fs.readFile(sourcePath, 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 }, reportDiagnostics: true })
const syntaxErrors = compiled.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error) ?? []
assert.equal(syntaxErrors.length, 0, ts.formatDiagnosticsWithColorAndContext(syntaxErrors, { getCurrentDirectory: () => repo, getCanonicalFileName: p => p, getNewLine: () => '\n' }))
await fs.writeFile(path.join(temp, 'geometry.mjs'), compiled.outputText)
const { fitLocalPlane, fitEdgeOrCorner, fitCylinder } = await import(pathToFileURL(path.join(temp, 'geometry.mjs')))

const dot = (a, b) => a.reduce((sum, x, i) => sum + x * b[i], 0)
const add = (a, b) => a.map((x, i) => x + b[i])
const sub = (a, b) => a.map((x, i) => x - b[i])
const scale = (a, s) => a.map(x => x * s)
const norm = a => Math.hypot(...a)
const unit = a => scale(a, 1 / norm(a))
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const distance = (a, b) => norm(sub(a, b))
const close = (a, b, tolerance, message) => assert.ok(Math.abs(a - b) <= tolerance, `${message}: ${a} vs ${b} (tol ${tolerance})`)
const directionClose = (a, b, tolerance, message) => assert.ok(norm(cross(a, b)) <= tolerance, `${message}: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`)
const accepted = (result, label) => { assert.ok(result.ok, `${label}: ${result.reason}`); return result.value }
const rejected = (result, label) => { assert.equal(result.ok, false, label); assert.ok(result.reason.length > 0, `${label}: UI reason`); return result.reason }

function random(seed) { let state = seed >>> 0; return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 4294967296 } }
const rng = random(0x773afdef)
const noise = amplitude => (rng() * 2 - 1) * amplitude
const basis = axis => { const u = unit(cross(axis, Math.abs(axis[0]) < 0.8 ? [1, 0, 0] : [0, 1, 0])); return [u, cross(axis, u)] }
function planePoints(origin, normal, size = 0.65, count = 21, jitter = 0) {
  const [u, v] = basis(normal), points = []
  for (let i = 0; i < count; i++) for (let j = 0; j < count; j++) {
    const a = size * (2 * i / (count - 1) - 1), b = size * (2 * j / (count - 1) - 1)
    points.push(add(add(add(origin, scale(u, a)), scale(v, b)), scale(normal, noise(jitter))))
  }
  return points
}
function outliers(origin, count, radius = 0.95) {
  const points = []
  while (points.length < count) { const q = [noise(radius), noise(radius), noise(radius)]; if (norm(q) < radius) points.push(add(origin, q)) }
  return points
}
function cylinderPoints(center, axis, radius, length, arc = 360, jitter = 0, angularCount = 61, axialCount = 17) {
  const [u, v] = basis(axis), points = []
  for (let i = 0; i < angularCount; i++) for (let j = 0; j < axialCount; j++) {
    const angle = (-arc / 2 + arc * i / (angularCount - 1)) * Math.PI / 180
    const radial = add(scale(u, Math.cos(angle)), scale(v, Math.sin(angle)))
    points.push(add(add(center, scale(axis, length * (j / (axialCount - 1) - 0.5) + noise(jitter))), scale(radial, radius + noise(jitter))))
  }
  return points
}
function truePlaneStats(points, seed, radius, tolerance, geometry) {
  const local = points.filter(p => p.every(Number.isFinite) && distance(p, seed) <= radius * (1 + 1e-10))
  const residuals = local.map(p => dot(sub(p, geometry.origin), geometry.normal)).filter(d => Math.abs(d) <= tolerance * (1 + 1e-8))
  assert.equal(geometry.pointCount, local.length, 'true local point count')
  assert.equal(geometry.inlierCount, residuals.length, 'true plane inlier count')
  close(geometry.rmse, Math.sqrt(residuals.reduce((sum, d) => sum + d * d, 0) / residuals.length), 1e-10, 'true plane RMSE')
  assert.ok(geometry.support.length <= 256)
  for (const p of geometry.support) assert.ok(points.some(source => source.every((x, i) => x === p[i])), 'plane support preserves source coordinates')
}
function trueCylinderStats(points, seed, options, geometry) {
  const local = points.filter(p => p.every(Number.isFinite) && distance(p, seed) <= options.radius * (1 + 1e-10))
  const residuals = local.map(p => { const q = sub(p, geometry.center); return norm(sub(q, scale(geometry.axis, dot(q, geometry.axis)))) - geometry.radius }).filter(d => Math.abs(d) <= options.tolerance * (1 + 1e-8))
  assert.equal(geometry.pointCount, local.length, 'true cylinder local point count')
  assert.equal(geometry.inlierCount, residuals.length, 'true cylinder inlier count')
  close(geometry.rmse, Math.sqrt(residuals.reduce((sum, d) => sum + d * d, 0) / residuals.length), 1e-10, 'true cylinder RMSE')
  assert.ok(geometry.support.length <= 256)
}

try {
  const options = { radius: 1, tolerance: 0.006 }
  const origin = [123.71, -46.32, 8.91], normal = unit([2, -3, 5])
  const points = [...planePoints(origin, normal, 0.64, 25, 0.001), ...outliers(origin, 130)]
  const snapshot = JSON.stringify(points)
  const seed = add(origin, scale(normal, 0.0017))
  const plane = accepted(fitLocalPlane(points, seed, { ...options, facing: scale(normal, -7) }), 'rotated noisy plane with outliers')
  directionClose(plane.normal, normal, 0.003, 'plane normal')
  assert.ok(dot(plane.normal, normal) < 0, 'facing changes normal sign')
  close(dot(sub(plane.origin, origin), normal), 0, 0.0003, 'plane offset')
  truePlaneStats(points, seed, options.radius, options.tolerance, plane)
  assert.equal(JSON.stringify(points), snapshot, 'plane does not mutate source')
  assert.deepEqual(fitLocalPlane(points, seed, { ...options, facing: scale(normal, -7) }), { ok: true, value: plane }, 'deterministic plane')
  const opposite = accepted(fitLocalPlane(points, seed, { ...options, facing: normal }), 'opposite facing')
  directionClose(opposite.normal, plane.normal, 1e-10, 'facing preserves fitted plane')
  close(dot(opposite.normal, plane.normal), -1, 1e-10, 'facing selects sign only')
  close(distance(opposite.origin, plane.origin), 0, 1e-10, 'facing preserves origin')
  rejected(fitLocalPlane(points.slice(0, 8), seed, options), 'sparse plane fails')
  const line = Array.from({ length: 100 }, (_, i) => add(origin, scale(unit([3, 7, -2]), (i / 99 - 0.5) * 1.4)))
  rejected(fitLocalPlane(line, origin, options), 'collinear plane fails')
  rejected(fitLocalPlane(Array.from({ length: 50 }, () => [...origin]), origin, options), 'duplicate degenerate plane fails')
  rejected(fitLocalPlane(points, seed, { radius: 0, tolerance: 0.006 }), 'invalid radius fails')
  const remote = planePoints(add(origin, scale(normal, 0.6)), normal, 0.3)
  rejected(fitLocalPlane(remote, origin, options), 'remote surface fails seed constraint')
  const outside = [...points, ...planePoints(add(origin, [4, -3, 2]), normal)]
  assert.deepEqual(accepted(fitLocalPlane(outside, seed, { ...options, facing: scale(normal, -7) }), 'outside radius ignored'), plane)
  console.log('PASS plane: rotated/noisy/outliers, normal sign, counts/RMSE, locality, sparse/collinear/degenerate rejection')

  // Independent asymmetric normals define non-orthogonal surfaces through a
  // known corner. The expected intersection is not inferred from the fit.
  const corner = [-4.21, 7.13, 2.81]
  const normals = [unit([1, 2, -1]), unit([-2, 1, 0.4]), unit([0.7, -0.2, 2])]
  const edgeDirection = unit(cross(normals[0], normals[1]))
  const edgePoints = [...planePoints(corner, normals[0], 0.61, 20, 0.0007), ...planePoints(corner, normals[1], 0.62, 20, 0.0007), ...outliers(corner, 70)]
  const edgeSeed = add(corner, add(scale(normals[0], 0.08), scale(edgeDirection, 0.12)))
  const edge = accepted(fitEdgeOrCorner(edgePoints, edgeSeed, options, 'edge'), 'non-right-angle edge')
  const expectedProjection = add(corner, scale(edgeDirection, dot(sub(edgeSeed, corner), edgeDirection)))
  close(distance(edge.position, expectedProjection), 0, 0.001, 'edge is seed projection onto intersection')
  directionClose(edge.direction, edgeDirection, 0.003, 'edge direction')
  close(Math.abs(dot(edge.planes[0].normal, edge.planes[1].normal)), Math.abs(dot(normals[0], normals[1])), 0.003, 'edge does not force orthogonality')
  const cornerPoints = [...edgePoints, ...planePoints(corner, normals[2], 0.59, 20, 0.0007)]
  const cornerSeed = add(corner, [0.07, -0.08, 0.04])
  const cornerBefore = JSON.stringify(cornerPoints)
  const fittedCorner = accepted(fitEdgeOrCorner(cornerPoints, cornerSeed, options, 'corner'), 'three non-right-angle surfaces')
  close(distance(fittedCorner.position, corner), 0, 0.0012, 'actual three-plane corner')
  assert.equal(fittedCorner.planes.length, 3)
  assert.equal(JSON.stringify(cornerPoints), cornerBefore, 'feature does not mutate source')
  rejected(fitEdgeOrCorner(edgePoints, corner, options, 'corner'), 'two planes cannot invent third plane')
  const n2 = unit(add(normals[0], scale(basis(normals[0])[0], 0.03)))
  const parallel = [...planePoints(add(corner, scale(normals[0], 0.12)), normals[0], 0.5, 20), ...planePoints(add(corner, scale(normals[0], -0.12)), n2, 0.5, 20)]
  rejected(fitEdgeOrCorner(parallel, corner, options, 'edge'), 'near parallel planes fail')
  const distant = [...planePoints(add(corner, [0.8, 0, 0]), [1, 0, 0], 0.3, 20), ...planePoints(add(corner, [0, 0.8, 0]), [0, 1, 0], 0.3, 20)]
  rejected(fitEdgeOrCorner(distant, corner, options, 'edge'), 'distant extrapolated edge fails')
  console.log('PASS features: non-orthogonal edge/corner, asymmetric known solutions, missing third/near parallel/distant rejection')

  const axis = unit([2, -5, 3]), cylinderCenter = [14.17, -5.89, 31.27], cylinderOptions = { radius: 1.2, tolerance: 0.006 }
  const cylinderSource = [...cylinderPoints(cylinderCenter, axis, 0.27, 1.1, 360, 0.001, 61, 17), ...outliers(cylinderCenter, 120, 0.85)]
  const cylinderBefore = JSON.stringify(cylinderSource)
  const cylinderSeed = cylinderCenter
  const cylinder = accepted(fitCylinder(cylinderSource, cylinderSeed, cylinderOptions), 'arbitrary-axis noisy cylinder')
  directionClose(cylinder.axis, axis, 0.004, 'arbitrary cylinder axis')
  close(cylinder.radius, 0.27, 0.001, 'cylinder radius')
  close(norm(cross(sub(cylinder.center, cylinderCenter), axis)), 0, 0.001, 'cylinder axis passes expected center')
  close(cylinder.length, 1.1, 0.04, 'cylinder length')
  assert.ok(cylinder.angularCoverage > 345, 'full cylinder angular support')
  trueCylinderStats(cylinderSource, cylinderSeed, cylinderOptions, cylinder)
  assert.equal(JSON.stringify(cylinderSource), cylinderBefore, 'cylinder does not mutate source')
  const partial = [...cylinderPoints(cylinderCenter, axis, 0.31, 0.9, 155, 0.0012, 49, 17), ...outliers(cylinderCenter, 75, 0.8)]
  const partialResult = accepted(fitCylinder(partial, cylinderCenter, cylinderOptions), 'partial cylinder arc with noise/outliers')
  directionClose(partialResult.axis, axis, 0.006, 'partial cylinder axis')
  close(partialResult.radius, 0.31, 0.002, 'partial cylinder radius')
  assert.ok(partialResult.angularCoverage > 150 && partialResult.angularCoverage < 185, `partial arc in degrees: ${partialResult.angularCoverage}`)
  const surfaceSeed = add(cylinderCenter, scale(basis(axis)[0], 0.31))
  const surfaceOptions = { radius: 0.55, tolerance: 0.006 }
  const surfaceFit = accepted(fitCylinder(partial, surfaceSeed, surfaceOptions), 'surface seed with clipped neighbourhood')
  directionClose(surfaceFit.axis, axis, 0.008, 'surface-seeded partial cylinder axis')
  close(surfaceFit.radius, 0.31, 0.002, 'surface-seeded cylinder radius')
  trueCylinderStats(partial, surfaceSeed, surfaceOptions, surfaceFit)
  const tinyArc = cylinderPoints(cylinderCenter, axis, 0.31, 0.9, 38, 0.0004, 41, 17)
  rejected(fitCylinder(tinyArc, cylinderCenter, cylinderOptions), 'ambiguous tiny arc fails')
  rejected(fitCylinder(planePoints(cylinderCenter, normal, 0.6, 25, 0.0006), cylinderCenter, cylinderOptions), 'planar patch fails cylinder fit')
  rejected(fitCylinder(cylinderPoints(cylinderCenter, axis, 0.3, 0.002, 360, 0, 61, 3), cylinderCenter, cylinderOptions), 'flat circular ring fails cylinder fit')
  const wide = cylinderPoints(cylinderCenter, axis, 0.52, 0.32, 360, 0.0006, 61, 13)
  const wideResult = accepted(fitCylinder(wide, cylinderCenter, cylinderOptions), 'short wide cylinder has arbitrary axis')
  directionClose(wideResult.axis, axis, 0.006, 'wide cylinder axis')
  close(wideResult.radius, 0.52, 0.002, 'wide cylinder radius')
  assert.deepEqual(fitCylinder(partial, cylinderCenter, cylinderOptions), { ok: true, value: partialResult }, 'deterministic cylinder')
  for (const [index, vector] of [[0, [0.01, 0.03, 1]], [1, [-0.7, 0.13, 0.08]], [2, [1, 2.1, -0.17]], [3, [-0.31, -0.72, -0.61]]]) {
    const testAxis = unit(vector), center = add(cylinderCenter, [index * 0.017, -index * 0.008, index * 0.023])
    const source = cylinderPoints(center, testAxis, 0.26, 0.95, 135 + index * 23, 0.0009, 41, 13)
    const result = accepted(fitCylinder(source, center, cylinderOptions), `axis/arc variation ${index}`)
    directionClose(result.axis, testAxis, 0.008, `varying cylinder axis ${index}`)
    close(result.radius, 0.26, 0.002, `varying cylinder radius ${index}`)
  }
  const millimetreSource = cylinderPoints([132941.27, -683219.45, 832.19], axis, 0.009, 0.03, 170, 0.00002, 41, 13)
  const millimetreResult = accepted(fitCylinder(millimetreSource, [132941.27, -683219.45, 832.19], { radius: 0.05, tolerance: 0.0001 }), 'small metre scale at large absolute coordinates')
  close(millimetreResult.radius, 0.009, 0.00003, 'small cylinder remains in metres')
  directionClose(millimetreResult.axis, axis, 0.008, 'large-coordinate cylinder axis')
  console.log('PASS cylinders: arbitrary axes, short/wide support, 155 degree noisy arc, planar/tiny-arc/ring rejection, exact counts/RMSE')

  // Exercise bounded fitting while retaining statistics from the complete
  // neighbourhood, including points beyond the internal 4096-point sample.
  const dense = planePoints(origin, normal, 0.64, 91, 0.0007)
  const denseResult = accepted(fitLocalPlane(dense, origin, options), 'dense bounded plane')
  assert.ok(denseResult.inlierCount > 4096)
  truePlaneStats(dense, origin, options.radius, options.tolerance, denseResult)
  assert.deepEqual(fitLocalPlane(dense, origin, options), { ok: true, value: denseResult }, 'deterministic bounded sample')
  const denseCylinder = cylinderPoints(cylinderCenter, axis, 0.27, 1.1, 200, 0.0007, 101, 51)
  const denseCylinderResult = accepted(fitCylinder(denseCylinder, cylinderCenter, cylinderOptions), 'dense bounded cylinder')
  assert.ok(denseCylinderResult.inlierCount > 4096)
  trueCylinderStats(denseCylinder, cylinderCenter, cylinderOptions, denseCylinderResult)
  console.log('PASS bounded sampling: >4096-point input retains full-neighbourhood counts/RMSE and <=256 original support points')
  console.log('Measurement geometry checks passed')
} finally {
  await fs.rm(temp, { recursive: true, force: true })
}
