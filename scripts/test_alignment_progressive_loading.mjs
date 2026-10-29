import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { createPointcloudLoadLifecycle, getTilesetWorldBounds } from '@cloudbim/viewer-core'

const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8')
  .split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
function harness(names, globals) {
  const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
  assert.equal(functions.length, names.length)
  const code = ts.transpileModule(functions.map(node => node.getText(ast)).join('\n'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText
  return runInNewContext(`${code}\n({ ${names.join(', ')} })`, globals)
}
const ref = value => ({ value })
const noop = () => {}

function renderFixture() {
  const contentGroup = new THREE.Group()
  const pointcloudWrapper = new THREE.Group()
  const bimPivot = new THREE.Group()
  contentGroup.add(pointcloudWrapper, bimPivot)
  const frames = []
  const globals = {
    animationId: 0, requestAnimationFrame: () => 1,
    scene: {}, activeCamera: {}, activeWorkflowStep: ref(1),
    renderer: { render: () => frames.push({ cloud: contentGroup.visible && pointcloudWrapper.visible, bim: contentGroup.visible && bimPivot.visible }) },
    tileset: { setCamera: noop, update: noop }, c2mTileset: null,
    pointcloudWrapper, contentGroup, bimPivot, props: { pointcloudAssetId: 14, bimAssetId: 13 },
    initialSceneReady: false, pointcloudRootReady: true, loggedSavedAlignmentKey: '',
    loadingBim: ref(true), loadingPointcloud: ref(false), pointcloudPreprocessRequired: ref(false),
    edlPipeline: null, edlEnabled: ref(true), isPerspectiveCamera: () => true,
    pointcloudLoadLifecycle: { allowEdl: false },
    transformHandleInteraction: null,
    runWithSuppressedConsoleAssert: callback => callback(), applyTilesErrorTarget: noop,
    updateTilesetResolution: noop, syncDenoisePreviewTransform: noop, syncBoundsHelpers: noop,
    syncAnalysisLineResolutions: noop, syncMeasurementBadges: noop, syncPointcloudCameraPose: noop,
  }
  const api = harness(['requestRender'], globals)
  return { api, globals, frames }
}

test('a ready point-cloud tile renders while the BIM and saved pose are pending', () => {
  const { api, globals, frames } = renderFixture()
  api.requestRender()
  assert.equal(frames[0].cloud, true, 'downloaded point-cloud tiles must be visible before the BIM finishes')
  assert.equal(frames[0].bim, false, 'the BIM must not flash at an unrestored pose')
  assert.equal(globals.bimPivot.visible, true, 'the temporary render gate must preserve selection visibility')
})

test('initial tile streaming renders directly and enables EDL after refinement settles', () => {
  const { api, globals, frames } = renderFixture()
  const lifecycle = createPointcloudLoadLifecycle()
  globals.pointcloudLoadLifecycle = lifecycle
  let edlFrames = 0
  globals.edlPipeline = { render: () => { edlFrames++; return true } }
  lifecycle.markModelReady()
  api.requestRender()
  assert.equal(frames.length, 1)
  assert.equal(edlFrames, 0, 'EDL must not compete with first-tile rendering')
  lifecycle.markInitialLoadSettled()
  globals.animationId = 0
  api.requestRender()
  assert.equal(edlFrames, 1, 'the chosen appearance is restored automatically')
})

test('a failed point-cloud request leaves the BIM available', () => {
  const { api, globals, frames } = renderFixture()
  globals.pointcloudRootReady = false
  globals.loadingBim.value = false
  globals.loadingPointcloud.value = false
  api.requestRender()
  assert.equal(frames[0].bim, true)
})

test('recentring for a late BIM preserves projected asymmetric scan points and user camera pose', () => {
  const contentGroup = new THREE.Group()
  const cloud = new THREE.Group()
  contentGroup.add(cloud)
  const bounds = new THREE.Box3(new THREE.Vector3(-2, 10, -4), new THREE.Vector3(6, 11, 2))
  const tileset = { group: cloud, getBoundingBox: target => { target.copy(bounds); return true } }
  const camera = new THREE.PerspectiveCamera(50, 1.5, .01, 1000)
  camera.position.set(10, 15, 20)
  const target = new THREE.Vector3(2, 10, -1)
  camera.lookAt(target)
  const globals = {
    THREE, contentGroup, tileset, pointcloudRootReady: true, pointcloudCameraFramed: true,
    getTilesetWorldBounds, activeCamera: camera, controls: { target, update: () => camera.updateMatrixWorld() },
    syncDenoisePreviewTransform: noop,
  }
  const points = [new THREE.Vector3(1, 10.5, -3), new THREE.Vector3(-1, 11, 1), new THREE.Vector3(5, 10, -2)]
  const projections = () => {
    contentGroup.updateMatrixWorld(true); camera.updateMatrixWorld()
    return points.map(point => point.clone().applyMatrix4(cloud.matrixWorld).project(camera))
  }
  const before = projections()
  const quaternion = camera.quaternion.clone()
  const bim = new THREE.Mesh(new THREE.BoxGeometry(3, 4, 2))
  bim.position.set(20, -5, 12)
  contentGroup.add(bim)
  const api = harness(['recenterLoadedContentAsWhole'], globals)
  api.recenterLoadedContentAsWhole()
  projections().forEach((point, index) => assert.ok(point.distanceTo(before[index]) < 1e-10))
  assert.ok(camera.quaternion.angleTo(quaternion) < 1e-7)
  const position = contentGroup.position.clone()
  api.recenterLoadedContentAsWhole()
  assert.ok(contentGroup.position.distanceTo(position) < 1e-10, 'normalization is independent of partial tile geometry')
})

test('late saved-pose reveal does not reset an already framed point-cloud camera', () => {
  let fits = 0
  const globals = {
    initialSceneReady: false, bimPivot: {}, props: { pointcloudAssetId: 14 },
    pointcloudRootReady: true, loggedSavedAlignmentKey: '14:13', pointcloudCameraFramed: true,
    fitCameraToContent: () => fits++, syncBoundsHelpers: noop, requestRender: noop,
  }
  harness(['revealInitialSceneWhenReady'], globals).revealInitialSceneWhenReady()
  assert.equal(globals.initialSceneReady, true)
  assert.equal(fits, 0)
})

test('startup, BIM and tile callbacks share one saved-alignment request, including no saved pose', async () => {
  let rejectRequest
  let requests = 0
  const globals = {
    props: { pointcloudAssetId: 14, bimAssetId: 13 },
    latestAlignmentResult: ref(null), hasSavedAlignmentMatrix: ref(false), coarseAlignmentDirty: ref(false),
    loggedSavedAlignmentKey: '', savedAlignmentLoadPromise: null,
    getBimAlignment: () => { requests++; return new Promise((_, reject) => { rejectRequest = reject }) },
    bimPivot: null, pointcloudGroup: null, pointcloudRootReady: false,
    revealInitialSceneWhenReady: noop, console,
  }
  const api = harness(['fetchAndLogSavedAlignmentIfExists'], globals)
  const pending = [api.fetchAndLogSavedAlignmentIfExists(), api.fetchAndLogSavedAlignmentIfExists(), api.fetchAndLogSavedAlignmentIfExists()]
  assert.equal(requests, 1)
  rejectRequest({ response: { status: 404 } })
  await Promise.all(pending)
  await api.fetchAndLogSavedAlignmentIfExists()
  assert.equal(requests, 1, 'a confirmed absence of saved alignment is not fetched repeatedly')
  assert.equal(globals.loggedSavedAlignmentKey, '14:13')
})

test('startup requests geometry and saved pose without waiting for remesh status or C2M history', async () => {
  const mount = ast.statements.filter(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
    && node.expression.expression.getText(ast) === 'onMounted').at(-1).expression.arguments[0]
  const code = ts.transpileModule(`const mount = ${mount.getText(ast)}; mount`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText
  const calls = []
  let releaseMesh
  const pendingMesh = new Promise(resolve => { releaseMesh = resolve })
  const startup = runInNewContext(code, {
    window: { addEventListener: noop }, onAnalysisKeydown: noop, cancelInspectionPointerGesture: noop,
    syncPositionStepPreset: noop, syncRotationStepPreset: noop,
    refreshMeshStatus: () => { calls.push('mesh'); return pendingMesh },
    initScene: async () => {}, preloadFromRoute: async () => { calls.push('geometry') },
    loadLatestDenoise: async () => { calls.push('denoise') },
    loadLatestC2M: async () => { calls.push('c2m') },
    fetchAndLogSavedAlignmentIfExists: async () => { calls.push('pose') }, workflowRouteReady: ref(false),
  })
  const pending = startup()
  await new Promise(resolve => setImmediate(resolve))
  assert.ok(calls.includes('geometry'))
  assert.ok(calls.includes('pose'))
  assert.ok(!calls.includes('c2m'), 'step one must not fetch deviation artifacts')
  releaseMesh(); await pending
})

for (const step of [1, 2, 3, 4]) test(`workflow step ${step} fetches deviation history only when needed`, () => {
  let requests = 0
  const globals = {
    showPointcloudSettings: ref(false), showAdvancedSettings: ref(false), workflowStepDisabled: () => false,
    activeWorkflowStep: ref(0), reportEditing: ref(false), reportFullscreen: ref(false), showPanel: ref(true),
    editMode: ref(false), c2mResult: ref(null), loadLatestC2M: async () => requests++,
    denoiseResult: ref(null), prepareRebarComparisonScene: async () => {}, showDenoisePreview: async () => {},
    c2mSceneLoaded: ref(false), restoreBimVisibilityAfterC2M: noop,
    applySceneVisibility: noop, syncWireframeStateFromCurrentMesh: noop,
  }
  harness(['openWorkflowStep'], globals).openWorkflowStep(step)
  assert.equal(requests, step >= 3 ? 1 : 0)
})
