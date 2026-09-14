import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const viewer = readFileSync(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8')

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

const buffers = {
  status: Uint8Array.from([1, 2, 3]).buffer,
  instance: Uint32Array.from([1, 0, 0]).buffer,
}

function fixture() {
  return {
    preview: {pointCount: 3, control_statusUrl: 'status', control_instanceUrl: 'instance'},
    controlNet: {
      version: 'design-control-net-v15', mode: 'aligned', inputStage: 'post-table',
      inputPolicy: 'finite non-table points', registration: {method: 'saved-coarse-pose'},
      counts: {input: 3, table: 0, matched: 1, pending: 1, removed: 1, excluded: 0,
        designUnits: 1, fittedUnits: 1, designBars: 1, extendedShortUnits: 1},
      inventory: {units: [{designUnitId: 'S-1', designBarId: 'B-1', startM: [0, 0, 0], endM: [.5, 0, 0],
        direction: [1, 0, 0], lengthM: .5, diameterM: .02, kind: 'short'}]},
      instances: [{id: 1, designUnitId: 'S-1', designBarId: 'B-1', kind: 'short', status: 'fitted',
        reason: 'fixed-radius-surface-supported', pointCount: 1, diameterM: .02, designLengthM: .5,
        observedLengthM: .548, rmseM: .001, fittedLengthM: .55,
        centerlineM: [[-.02, 0, 0], [.53, 0, 0]], lengthCheck: 'extended-observed-span',
        lengthEvidence: {method: 'continuous-fixed-radius-end-support', extensionStartM: .02,
          extensionEndM: .03, observedRangeM: [-.021, .531], supportPoints: 84, searchMarginM: .18}}],
      policy: {ownershipPolicy: 'confirmed unique points freeze after each stage; uncertain points remain',
        ownershipStages: [{kind: 'short', designUnits: 1, fittedUnits: 1, lockedPoints: 1, elapsedS: .02}]},
      warnings: [], elapsedS: .04,
    },
  }
}

test('v15 accepts evidence-supported short extension and rejects inconsistent evidence or counts', async () => {
  const valid = fixture()
  const loaded = await loadControlNet(valid, async url => buffers[url])
  assert.equal(loaded.instanceById.get(1).fittedLengthM, .55)

  const badEvidence = structuredClone(valid)
  badEvidence.controlNet.instances[0].lengthEvidence.extensionEndM = .02
  await assert.rejects(loadControlNet(badEvidence, async url => buffers[url]), /设计长度几何/)

  const badCount = structuredClone(valid)
  badCount.controlNet.counts.extendedShortUnits = 0
  await assert.rejects(loadControlNet(badCount, async url => buffers[url]), /延长计数与实例不一致/)
})

test('v15 accepts measured longer intervals with one old unsupported endpoint moved inward', async () => {
  const manifest = fixture()
  const row = manifest.controlNet.instances[0]
  row.centerlineM = [[.01, 0, 0], [.56, 0, 0]]
  Object.assign(row.lengthEvidence, {extensionStartM: 0, extensionEndM: .06,
    endpointShiftStartM: .01, endpointShiftEndM: .06, observedRangeM: [.009, .561]})
  await loadControlNet(manifest, async url => buffers[url])
  row.lengthEvidence.endpointShiftStartM = .02
  await assert.rejects(loadControlNet(manifest, async url => buffers[url]), /设计长度几何/)
})

test('v15 validates generic straight to short to web ownership stage order', async () => {
  const manifest = fixture()
  manifest.controlNet.policy.ownershipStages = [
    {kind: 'straight', designUnits: 2, fittedUnits: 1, lockedPoints: 40, elapsedS: .01},
    {kind: 'short', designUnits: 1, fittedUnits: 1, lockedPoints: 20, elapsedS: .02},
    {kind: 'web', designUnits: 2, fittedUnits: 0, lockedPoints: 0, elapsedS: .03},
  ]
  await loadControlNet(manifest, async url => buffers[url])
  manifest.controlNet.policy.ownershipStages.reverse()
  await assert.rejects(loadControlNet(manifest, async url => buffers[url]), /分阶段归属策略无效/)
})

test('progressive ownership and measured extension use concise viewer wording', () => {
  const fmt = value => String(value)
  const fmtHeight = value => `${Number(value).toFixed(3)} m`
  const summary = new Function('fmt', `${functionSource('controlNetOwnershipSummary')}\nreturn controlNetOwnershipSummary`)(fmt)
  const hint = new Function(`${functionSource('controlNetOwnershipHint')}\nreturn controlNetOwnershipHint`)()
  const detail = new Function('fmtHeight', `${functionSource('controlNetLengthDetail')}\nreturn controlNetLengthDetail`)(fmtHeight)
  const observation = new Function('fmtHeight', `${functionSource('controlNetLengthDetail')}\n${functionSource('controlNetLengthObservation')}\nreturn controlNetLengthObservation`)(fmtHeight)
  const report = {policy: {ownershipStages: [
    {kind: 'straight', designUnits: 3, fittedUnits: 2, lockedPoints: 40},
    {kind: 'short', designUnits: 2, fittedUnits: 1, lockedPoints: 20},
    {kind: 'web', designUnits: 1, fittedUnits: 0, lockedPoints: 0},
  ]}}
  assert.equal(summary(report), '直筋 2 / 3 · 锁定 40 点 → 短筋 1 / 2 · 锁定 20 点 → 腹杆 0 / 1 · 锁定 0 点；待定点保留')
  assert.equal(hint(report), '归属顺序为 直筋 → 短筋 → 腹杆；每阶段锁定唯一支持点，待定点继续保留。')
  assert.equal(detail(fixture().controlNet.instances[0]), '主体设计 / 拟合长度 0.500 m / 0.550 m；首端 / 末端延长 0.020 m / 0.030 m。')
  assert.equal(observation(fixture().controlNet.instances[0]), '主体观测跨度 0.548 m。主体设计 / 拟合长度 0.500 m / 0.550 m；首端 / 末端延长 0.020 m / 0.030 m。')
  assert.match(viewer, /以非台面点作为初始候选，按直筋、短筋、腹杆依次拟合；确认点锁定，待定点保留/)
})

test('v14 fixed-length review remains valid and keeps its review wording', async () => {
  const legacy = fixture()
  legacy.controlNet.version = 'design-control-net-v14'
  delete legacy.controlNet.counts.extendedShortUnits
  delete legacy.controlNet.policy
  const row = legacy.controlNet.instances[0]
  row.centerlineM = [[0, 0, 0], [.5, 0, 0]]
  row.fittedLengthM = .5
  row.lengthCheck = 'review-observed-span'
  delete row.lengthEvidence
  await loadControlNet(legacy, async url => buffers[url])
  assert.match(viewer, /观测跨度不等于实测钢筋长度[^；]+；本模型保持设计长度/)
})

test('short navigation includes extended bars even without pose recovery', () => {
  const select = new Function(`${functionSource('controlNetShortReviewUnits')}\nreturn controlNetShortReviewUnits`)()
  const extended = fixture().controlNet.instances[0]
  assert.deepEqual(select({instances: [extended, {id: 2, kind: 'short', status: 'fitted'},
    {id: 3, kind: 'web', status: 'pending'}]}).map(row => row.id), [1])
})
