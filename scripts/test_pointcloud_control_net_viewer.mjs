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
  functionSource('controlNetInputStage'),
  functionSource('loadControlNetPreview'),
  'return loadControlNetPreview',
].join('\n'))()

function fixture() {
  return {
    preview: {pointCount: 5, control_statusUrl: 'status', control_instanceUrl: 'instance'},
    controlNet: {
      version: 'control-net-v2', mode: 'aligned', inputStage: 'post-fusion', inputPolicy: 'fusion-class-3-non-table',
      registration: {method: 'saved-coarse-pose'},
      counts: {input: 5, table: 1, matched: 1, pending: 1, removed: 1, excluded: 1, designUnits: 2, fittedUnits: 1, designBars: 1},
      layers: [
        {id: 1, name: '下层钢筋', inputPoints: 1, matched: 1, pending: 0, designUnits: 1, fittedUnits: 1, elapsedS: .12},
        {id: 2, name: '上层钢筋', inputPoints: 1, matched: 0, pending: 1, designUnits: 1, fittedUnits: 0, elapsedS: .08},
        {id: 3, name: '腹杆层', inputPoints: 1, matched: 0, pending: 0, designUnits: 0, fittedUnits: 0, elapsedS: .03},
      ],
      inventory: {units: [
        {designUnitId: 'U-1', designBarId: 'B-1', layerId: 17, startM: [10, 20, 30], endM: [10.5, 20, 30], direction: [1, 0, 0], lengthM: .5, diameterM: .02, kind: 'straight'},
        {designUnitId: 'U-2', designBarId: 'B-1', layerId: 18, startM: [10, 20.2, 30], endM: [10.3, 20.2, 30], direction: [1, 0, 0], lengthM: .3, diameterM: .02, kind: 'short'},
      ]},
      instances: [
        {id: 1, designUnitId: 'U-1', designBarId: 'B-1', fitLayerId: 1, kind: 'straight', status: 'fitted', reason: 'supported', pointCount: 1, diameterM: .02, designLengthM: .5, observedLengthM: .4, rmseM: .001, centerlineM: [[10, 20, 30], [10.5, 20, 30]]},
        {id: 2, designUnitId: 'U-2', designBarId: 'B-1', fitLayerId: 2, kind: 'short', status: 'missing', reason: 'not_observed', pointCount: 0, diameterM: .02, designLengthM: .3, observedLengthM: null, rmseM: null, centerlineM: []},
      ],
      warnings: ['automatic rotation remains ambiguous'], elapsedS: .4,
    },
  }
}

const buffers = {
  status: Uint8Array.from([0, 1, 2, 3, 4]).buffer,
  instance: Uint32Array.from([0, 1, 0, 0, 0]).buffer,
}

test('single-unit source inspection includes nearby pending points without assigning their identity', () => {
  const near = new Function(`${functionSource('controlNetLocalPredicate')}\nreturn controlNetLocalPredicate`)()
  const predicate = near(fixture().controlNet.instances[0], fixture().controlNet.inventory.units[0], [10,20,30])
  const points = new Float32Array([.2,.02,0, .2,.4,0, .1,0,0])
  assert.equal(predicate(points,0),true)
  assert.equal(predicate(points,1),false)
  assert.equal(predicate(points,2),true)
})

test('ambiguous pose candidates render as dashed axes without confirmed cylinder meshes', async () => {
  const THREE = await import('three')
  const create = new Function('THREE', `${functionSource('controlNetIdentityColor')}\n${functionSource('createControlNetOverlay')}\nreturn createControlNetOverlay`)(THREE)
  const manifest = fixture(), item = manifest.controlNet.instances[0]
  item.status = 'pending'; item.candidateCenterlineM = item.centerlineM; item.centerlineM = []; item.pointCount = 0
  const group = create(manifest.controlNet, [10,20,30], {showFit:true})
  assert.equal(group.children.length,1)
  assert.equal(group.children[0].userData.kind,'candidate')
  assert.equal(group.children[0].material.type,'LineDashedMaterial')
  assert.equal(group.children[0].isMesh,undefined)
  group.traverse(object=>{object.geometry?.dispose();object.material?.dispose()})
})

test('run form keeps baseline default and moves aligned/auto experiments after fusion', () => {
  assert.match(page, /id="controlNetMode"[\s\S]*value="off" selected[\s\S]*value="aligned"[\s\S]*value="auto"/)
  assert.match(page, /id="controlNetStep"[^>]*><span class="num">03X<\/span>分层控制网/)
  assert.ok(page.indexOf('id="controlNetStep"') > page.indexOf('id="fusionStep"'))
  assert.ok(page.indexOf('id="controlNetStep"') < page.indexOf('id="refinementStep"'))
  const elements = {controlNetMode: {value: 'off'}, k: {value: '32'}, workers: {value: '8'}, throughStep: {value: '8'}, priorMode: {value: 'topology'}}
  const payload = new Function('$', `${functionSource('runRequestPayload')}\nreturn runRequestPayload`) ((id) => elements[id])
  assert.deepEqual(payload(), {k: 32, workers: 8, throughStep: 8, priorMode: 'topology', controlNetMode: 'off'})
  elements.controlNetMode.value = 'aligned'
  assert.deepEqual(payload(), {k: 32, workers: 8, throughStep: 4, priorMode: 'off', controlNetMode: 'aligned'})
  elements.controlNetMode.value = 'auto'
  assert.deepEqual(payload(), {k: 32, workers: 8, throughStep: 4, priorMode: 'off', controlNetMode: 'auto'})
})

test('control-net loader accepts exact typed attributes and validates unit/status identity', async () => {
  const manifest = fixture()
  const loaded = await loadControlNet(manifest, async (url) => buffers[url])
  assert.deepEqual([...loaded.status], [0, 1, 2, 3, 4])
  assert.deepEqual([...loaded.instance], [0, 1, 0, 0, 0])
  assert.equal(loaded.instanceById.get(1).designBarId, 'B-1')
  assert.equal(loaded.instanceById.get(1).fitLayerId, 1)
  assert.notEqual(loaded.instanceById.get(1).fitLayerId, manifest.controlNet.inventory.units[0].layerId)
  const badIdentity = fixture()
  const wrongInstance = {...buffers, instance: Uint32Array.from([0, 1, 2, 0, 0]).buffer}
  await assert.rejects(loadControlNet(badIdentity, async (url) => wrongInstance[url]), /状态与设计单元身份不一致/)
  const badLength = fixture(); badLength.controlNet.instances[0].centerlineM[1][0] = 10.49
  await assert.rejects(loadControlNet(badLength, async (url) => buffers[url]), /设计长度几何/)
})

test('legacy early reports load with default excluded count and without layer fields', async () => {
  const legacy = fixture()
  delete legacy.controlNet.inputStage
  delete legacy.controlNet.counts.excluded
  delete legacy.controlNet.layers
  legacy.preview.pointCount = 4
  legacy.controlNet.counts.input = 4
  for (const item of legacy.controlNet.instances) delete item.fitLayerId
  const legacyBuffers = {status: Uint8Array.from([0, 1, 2, 3]).buffer, instance: Uint32Array.from([0, 1, 0, 0]).buffer}
  const loaded = await loadControlNet(legacy, async (url) => legacyBuffers[url])
  assert.deepEqual([...loaded.status], [0, 1, 2, 3])
  const hasLayers = new Function(`${functionSource('controlNetHasSemanticLayers')}\nreturn controlNetHasSemanticLayers`)()
  assert.equal(hasLayers(legacy.controlNet), false, 'all-zero shared-layer placeholders do not make a legacy report layer-aware')
  assert.equal(hasLayers(fixture().controlNet), true)
})

test('post-fusion conservation includes excluded points and history labels retain early runs', async () => {
  const bad = fixture()
  bad.controlNet.counts.excluded = 0
  await assert.rejects(loadControlNet(bad, async (url) => buffers[url]), /计数无效或不守恒/)
  const historyLabel = new Function(`${functionSource('controlNetHistoryModeLabel')}\nreturn controlNetHistoryModeLabel`)()
  assert.equal(historyLabel({controlNetMode: 'aligned', throughStep: 2}), '控制网·早期·粗对齐')
  assert.equal(historyLabel({controlNetMode: 'auto', throughStep: 4}), '控制网·03X分层·自动')
  assert.equal(historyLabel({controlNetMode: 'off', throughStep: 8}), '现有流程')
  const stageLabel = new Function(`${functionSource('controlNetInputStage')}\n${functionSource('controlNetStageLabel')}\nreturn controlNetStageLabel`)()
  assert.equal(stageLabel({}), '01C-X · 控制网早期拟合')
  assert.equal(stageLabel({inputStage: 'post-fusion'}), '03X · 分层控制网')
})

test('fit layer IDs override legacy design layers and web neighborhoods retain cross-layer endpoints', () => {
  const fitLayer = new Function(`${functionSource('controlNetFitLayerId')}\nreturn controlNetFitLayerId`)()
  const pointLayer = new Function(`${functionSource('controlNetFitLayerId')}\n${functionSource('controlNetPointLayer')}\nreturn controlNetPointLayer`)()
  const matches = new Function(`${functionSource('controlNetFitLayerId')}\n${functionSource('controlNetPointMatchesLayer')}\nreturn controlNetPointMatchesLayer`)()
  const item = fixture().controlNet.instances[0]
  assert.equal(fitLayer(item), 1)
  assert.equal(pointLayer(item, 2), 1)
  assert.equal(pointLayer(null, 2), 2)
  assert.equal(matches('1', 2, item, true), false)
  assert.equal(matches('3', 1, {kind: 'web', fitLayerId: 3}, true), true)
  assert.equal(matches('3', 1, {kind: 'web', fitLayerId: 3}, false), false)
})

test('fit and initialization overlays use native XYZ minus the preview origin once', async () => {
  const THREE = await import('three')
  const create = new Function('THREE', `${functionSource('controlNetIdentityColor')}\n${functionSource('createControlNetOverlay')}\nreturn createControlNetOverlay`)(THREE)
  const group = create(fixture().controlNet, [9, 19, 29], {showFit: true, showInitialization: true, colorMode: 'parents'})
  const initialization = group.children.find((child) => child.userData.kind === 'initialization' && child.userData.instanceId === 1)
  assert.deepEqual([...initialization.geometry.attributes.position.array], [1, 1, 1, 1.5, 1, 1])
  const fit = group.children.find((child) => child.userData.kind === 'fit')
  assert.deepEqual(fit.userData, {kind: 'fit', instanceId: 1, designUnitId: 'U-1', designBarId: 'B-1'})
  const tube = fit.children.find((child) => child.geometry?.type === 'CylinderGeometry')
  assert.equal(tube.geometry.parameters.radiusTop, .01)
  assert.ok(Math.abs(tube.geometry.parameters.height - .5) < 1e-12)
  group.traverse((object) => { object.geometry?.dispose?.(); object.material?.dispose?.() })
})

test('the layered view exposes fusion exclusions, layer controls, downloads and old-history fallback', () => {
  for (const value of ['source', 'steel', 'pending', 'removed', 'excluded']) assert.match(page, new RegExp(`<option value="${value}"`))
  for (const value of ['layers', 'units', 'parents']) assert.match(page, new RegExp(`<option value="${value}"`))
  for (const id of ['controlNetLayerFilter', 'controlNetWarnings', 'controlNetSteelLas', 'controlNetPendingLas', 'controlNetRemovedLas', 'controlNetExcludedLas', 'controlNetReport']) assert.match(page, new RegExp(`id="${id}"`))
  assert.ok(page.indexOf('id="controlNetControls"') < page.indexOf('class="advancedControls"'))
  assert.match(viewer, /controlNetInputStage\(current\?\.controlNet\) === 'post-fusion' \? fusionScene : tableRemovalScene/)
  assert.match(viewer, /view === 'source' \? code > 0 && code < 4/)
  assert.match(viewer, /inputStage === 'post-fusion' \? \$\('fusionStep'\) : \$\('tableRemovalStep'\)/)
  const fallback = new Function(`${functionSource('isFusionPassThrough')}\n${functionSource('defaultPreviewStep')}\nreturn defaultPreviewStep`)()
  assert.equal(fallback({}, {controlNet: null, sharedTableMask: true}), 'tableRemoval')
  assert.equal(fallback({}, {controlNet: {}, sharedTableMask: true}), 'controlNet')
})


test('stage validation respects 1=table and keeps all fusion exclusions outside fitted ownership', () => {
  const valid = new Function(`${functionSource('controlNetStageStatusValid')}\nreturn controlNetStageStatusValid`)()
  for (const postFusion of [false, true]) {
    assert.equal(valid(0, 1, 1, postFusion), true)
    assert.equal(valid(1, 1, 3, postFusion), false)
    assert.equal(valid(0, 0, 3, postFusion), false)
    for (const code of [1, 2, 3]) assert.equal(valid(code, 0, 3, postFusion), true)
  }
  assert.equal(valid(4, 0, 2, true), true)
  assert.equal(valid(4, 0, 4, true), true)
  assert.equal(valid(4, 0, 3, true), false)
  assert.equal(valid(1, 0, 2, true), false)
  assert.equal(valid(4, 0, 2, false), false)
})
