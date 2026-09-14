import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const viewer = readFileSync(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8')
const page = readFileSync(new URL('./pointcloud-debug/index.html', import.meta.url), 'utf8')

function functionSource(name) {
  const functionStart = viewer.indexOf(`function ${name}(`)
  assert.ok(functionStart >= 0, `${name} is present`)
  const start = viewer.slice(Math.max(0, functionStart - 6), functionStart) === 'async ' ? functionStart - 6 : functionStart
  const open = viewer.indexOf('{', start)
  let depth = 0
  for (let index = open; index < viewer.length; index++) {
    if (viewer[index] === '{') depth++
    if (viewer[index] === '}' && --depth === 0) return viewer.slice(start, index + 1)
  }
  throw new Error(`could not isolate ${name}`)
}

const loadControlNet = new Function([
  functionSource('controlPreviewUrl'),
  functionSource('controlNetVec3'),
  functionSource('controlNetPolylineLength'),
  functionSource('controlNetCurvedPiecesForUnit'),
  functionSource('controlNetCurveStats'),
  functionSource('validateControlNetCurvedPieces'),
  functionSource('controlNetInputStage'),
  functionSource('loadControlNetPreview'),
  'return loadControlNetPreview',
].join('\n'))()

const curveLength = Math.hypot(.05, .05) * 2

function fixture() {
  return {
    preview: {pointCount: 3, control_statusUrl: 'status', control_instanceUrl: 'instance'},
    controlNet: {
      version: 'design-control-net-v12', mode: 'aligned', inputPolicy: 'post-table-candidates',
      registration: {method: 'saved-coarse-pose'},
      counts: {input: 3, table: 0, matched: 2, pending: 1, removed: 0, designUnits: 2, fittedUnits: 2, designBars: 1},
      inventory: {units: [
        {designUnitId: 'U-1', designBarId: 'B-1', startM: [0, 0, 0], endM: [.5, 0, 0], direction: [1, 0, 0], lengthM: .5, diameterM: .02, kind: 'straight'},
        {designUnitId: 'U-2', designBarId: 'B-1', startM: [.6, .1, 0], endM: [1.1, .1, 0], direction: [1, 0, 0], lengthM: .5, diameterM: .02, kind: 'straight'},
      ]},
      instances: [
        {id: 1, designUnitId: 'U-1', designBarId: 'B-1', kind: 'straight', status: 'fitted', reason: 'supported', pointCount: 4, diameterM: .02, designLengthM: .5, observedLengthM: .45, rmseM: .001, centerlineM: [[0, 0, 0], [.5, 0, 0]], bodyDisplayCenterlineM: [[0, 0, 0], [.45, 0, 0]]},
        {id: 2, designUnitId: 'U-2', designBarId: 'B-1', kind: 'straight', status: 'fitted', reason: 'supported', pointCount: 3, diameterM: .02, designLengthM: .5, observedLengthM: .5, rmseM: .002, centerlineM: [[.6, .1, 0], [1.1, .1, 0]]},
      ],
      curvedPieces: [
        {id: 'C-join', designBarId: 'B-1', kind: 'join', unitIds: [1, 2], status: 'fitted', reason: 'surface-supported', diameterM: .02, designLengthM: .15, fittedLengthM: curveLength, attachmentLengthM: .02, centerlineM: [[.5, 0, 0], [.55, .05, 0], [.6, .1, 0]], designCenterlineM: [[.5, 0, 0], [.55, .04, 0], [.6, .1, 0]], pointCount: 4, geometrySource: 'scan-fit'},
        {id: 'C-terminal', designBarId: 'B-1', kind: 'terminal', unitIds: [1], status: 'pending', reason: 'insufficient-support', diameterM: .02, designLengthM: .12, fittedLengthM: .11, rmseM: .01, designCenterlineM: [[0, 0, 0], [-.08, .04, 0]], pointCount: 0, geometrySource: 'design-prior'},
      ],
      curveSummary: {designPieces: 2, fittedPieces: 1, pendingPieces: 1, matchedPoints: 4},
      warnings: [], elapsedS: .3,
    },
  }
}

const buffers = {
  status: Uint8Array.from([1, 1, 2]).buffer,
  instance: Uint32Array.from([1, 2, 0]).buffer,
}

test('v12 loader accepts additive curves, trimmed body display and absent fit diagnostics', async () => {
  const manifest = fixture()
  const loaded = await loadControlNet(manifest, async (url) => buffers[url])
  assert.equal(loaded.instanceById.size, 2)
  assert.equal(manifest.controlNet.instances[0].centerlineM[1][0], .5, 'body reference geometry remains unchanged')
  assert.equal(manifest.controlNet.instances[0].bodyDisplayCenterlineM[1][0], .45)
  assert.notEqual(manifest.controlNet.curvedPieces[0].designLengthM, manifest.controlNet.curvedPieces[0].fittedLengthM)
  assert.equal(manifest.controlNet.curvedPieces[0].rmseM, undefined)
})

test('historical v11 report remains valid without curve fields or body display geometry', async () => {
  const manifest = fixture()
  manifest.controlNet.version = 'design-control-net-v11'
  delete manifest.controlNet.curvedPieces
  delete manifest.controlNet.curveSummary
  for (const item of manifest.controlNet.instances) delete item.bodyDisplayCenterlineM
  await loadControlNet(manifest, async (url) => buffers[url])
})

test('curve references reject duplicates, missing units and cross-parent joins clearly', async () => {
  const duplicate = fixture()
  duplicate.controlNet.curvedPieces[0].unitIds = [1, 1]
  await assert.rejects(loadControlNet(duplicate, async (url) => buffers[url]), /引用重复/)

  const missing = fixture()
  missing.controlNet.curvedPieces[0].unitIds = [1, 99]
  await assert.rejects(loadControlNet(missing, async (url) => buffers[url]), /引用不存在/)

  const crossParent = fixture()
  crossParent.controlNet.instances[1].designBarId = 'B-2'
  crossParent.controlNet.inventory.units[1].designBarId = 'B-2'
  crossParent.controlNet.counts.designBars = 2
  await assert.rejects(loadControlNet(crossParent, async (url) => buffers[url]), /跨越物理母筋/)
})

test('curve status, geometry, fitted length and summary remain internally consistent', async () => {
  const pendingFit = fixture()
  pendingFit.controlNet.curvedPieces[1].centerlineM = [[0, 0, 0], [-.08, .04, 0]]
  await assert.rejects(loadControlNet(pendingFit, async (url) => buffers[url]), /状态、引用或长度几何/)

  const lengthMismatch = fixture()
  lengthMismatch.controlNet.curvedPieces[0].fittedLengthM += .01
  await assert.rejects(loadControlNet(lengthMismatch, async (url) => buffers[url]), /状态、引用或长度几何/)

  const badDisplay = fixture()
  badDisplay.controlNet.instances[0].bodyDisplayCenterlineM[1][0] = Number.NaN
  await assert.rejects(loadControlNet(badDisplay, async (url) => buffers[url]), /控制网实例/)

  const badSummary = fixture()
  badSummary.controlNet.curveSummary.designPieces = 210
  await assert.rejects(loadControlNet(badSummary, async (url) => buffers[url]), /汇总与清单不一致/)
})

test('curve statistics stay separate from the fixed design-unit count', () => {
  const report = fixture().controlNet
  report.counts.designUnits = 210
  const stats = new Function([
    functionSource('controlNetPolylineLength'),
    functionSource('controlNetCurvedPiecesForUnit'),
    functionSource('controlNetCurveStats'),
    'return controlNetCurveStats',
  ].join('\n'))()(report)
  assert.deepEqual({design: stats.designPieces, fitted: stats.fittedPieces, pending: stats.pendingPieces, points: stats.matchedPoints},
    {design: 2, fitted: 1, pending: 1, points: 4})
})

test('selected adjoining units render the same fitted orange curve and trimmed body', async () => {
  const THREE = await import('three')
  const create = new Function('THREE', [
    functionSource('controlNetIdentityColor'),
    functionSource('createControlNetOverlay'),
    'return createControlNetOverlay',
  ].join('\n'))(THREE)
  const report = fixture().controlNet
  const group = create(report, [0, 0, 0], {selectedId: '2', colorMode: 'parents', showFit: true, showInitialization: false})
  const curve = group.children.find((child) => child.userData.kind === 'curve-fit')
  assert.deepEqual(curve.userData.unitIds, [1, 2])
  assert.equal(curve.children[0].material.color.getHex(), 0xff8a2b)
  assert.ok(curve.children.some((child) => child.isMesh), 'fitted curve has tube geometry')
  assert.equal(group.children.some((child) => child.userData.kind === 'curve-design-pending'), false)

  const first = create(report, [0, 0, 0], {selectedId: '1', colorMode: 'parents', showFit: true, showInitialization: false})
  const body = first.children.find((child) => child.userData.kind === 'fit')
  assert.ok(Math.abs(body.children[0].geometry.attributes.position.array[3] - .45) < 1e-6)
  for (const model of [group, first]) model.traverse((object) => { object.geometry?.dispose?.(); object.material?.dispose?.() })
})

test('pending design curves appear only with the initialization/design toggle', async () => {
  const THREE = await import('three')
  const create = new Function('THREE', [
    functionSource('controlNetIdentityColor'),
    functionSource('createControlNetOverlay'),
    'return createControlNetOverlay',
  ].join('\n'))(THREE)
  const report = fixture().controlNet
  const fitOnly = create(report, [0, 0, 0], {selectedId: '1', colorMode: 'parents', showFit: true, showInitialization: false})
  assert.equal(fitOnly.children.some((child) => child.userData.kind === 'curve-design-pending'), false)
  const design = create(report, [0, 0, 0], {selectedId: '1', colorMode: 'parents', showFit: false, showInitialization: true})
  const pending = design.children.find((child) => child.userData.kind === 'curve-design-pending')
  assert.equal(pending.children[0].material.type, 'LineDashedMaterial')
  assert.equal(pending.children[0].material.color.getHex(), 0x38bdf8)
  assert.equal(pending.children.some((child) => child.isMesh), false, 'design-only curve never gets a fitted tube')
  for (const model of [fitOnly, design]) model.traverse((object) => { object.geometry?.dispose?.(); object.material?.dispose?.() })
})

test('curve neighborhood and viewer wording cover filtering, focus and fitted-vs-design meaning', () => {
  const predicate = new Function(`${functionSource('controlNetCurveLocalPredicate')}\nreturn controlNetCurveLocalPredicate`)()
    (fixture().controlNet.curvedPieces, [0, 0, 0])
  const positions = new Float32Array([.55, .05, 0, .55, .5, 0])
  assert.equal(predicate(positions, 0), true)
  assert.equal(predicate(positions, 1), false)
  assert.match(page, /显示已拟合直段与弯曲段/)
  assert.match(page, /设计弯曲线（含未确认）/)
  assert.match(viewer, /new THREE\.Box3\(\)\.setFromObject\(controlNetOverlay\)/)
  assert.match(viewer, /未确认设计弯曲线（未拟合）/)
  assert.match(viewer, /主体观测 \/ 设计直段长度/)
  assert.match(viewer, /弯曲段拟合 \/ 待定 \/ 设计/)
})
