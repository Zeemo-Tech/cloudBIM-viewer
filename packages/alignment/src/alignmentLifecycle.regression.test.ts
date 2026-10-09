import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'

const source = readFileSync(new URL('./AlignmentPage.vue', import.meta.url), 'utf8').split('<script setup lang="ts">')[1]!.split('</script>')[0]!
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
function harness(names: string[], globals: Record<string, unknown>) {
  const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text ?? '')).map(node => node.getText(ast)).join('\n')
  assert.equal(functions.match(/^async function |^function /gm)?.length, names.length)
  const code = ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  return runInNewContext(`${code}\n({ ${names.join(', ')} })`, globals)
}
function deferred() {
  let resolve!: (value?: any) => void
  const promise = new Promise<any>(done => { resolve = done })
  return { promise, resolve }
}

for (const scenario of ['new-version', 'same-version', 'stale-input', 'repeated-conflict', 'superseded']) {
  test(`artifact conflict recovery: ${scenario}`, async () => {
    const old = { resultVersion: 'old', fresh: true, coloredPlyAvailable: true }
    const latest = { ...old, resultVersion: scenario === 'same-version' ? 'old' : 'new',
      fresh: scenario !== 'stale-input', staleReason: '配准已变化，请重新计算' }
    const downloads: string[] = [], distanceVersions: string[] = [], errors: string[] = []
    let latestCalls = 0
    const geometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3))
    const globals: any = {
      THREE, PLYLoader: class { async loadAsync() { return geometry } },
      URL: { createObjectURL: () => 'blob:fixture', revokeObjectURL() {} },
      c2mResult: { value: old }, c2mResultRequestId: 0, c2mSceneLoadRequestId: 0,
      c2mSceneLoading: { value: false }, c2mSceneLoaded: { value: false }, c2mError: { value: '' },
      c2mSceneArtifactAvailable: { value: true }, props: { pointcloudAssetId: 5, bimAssetId: 7 },
      scene: new THREE.Scene(), bimPivot: null, c2mSceneGroup: null,
      c2mDistances: { value: null }, c2mDistancesRequestId: 0,
      rebarDebugActive: { value: true }, rebarDebugDisplaySurface: { value: 'result' },
      showMeshWireframe: { value: false }, isC2MResultFresh: (r: any) => r?.fresh === true,
      getC2MColoredPlyUrl: (_scan: number, _bim: number, version: string) => version,
      backendRequest: async (version: string) => {
        downloads.push(version)
        if (version === 'old' || scenario === 'repeated-conflict') throw Object.assign(new Error('C2M 结果版本已变化，请刷新后重试'), { response: { status: 409 } })
        return new Blob(['fixture'])
      },
      getLatestC2M: async () => {
        latestCalls++
        if (scenario === 'superseded') { globals.c2mSceneLoadRequestId++; globals.c2mResult.value = { ...old, resultVersion: 'user-newer' } }
        return { data: latest }
      },
      syncC2MControls() {}, scheduleC2MAnalysisPolling() {},
      syncC2MDistances: async (r: any) => { distanceVersions.push(r.resultVersion) },
      clearC2MScene(invalidate = true) { if (invalidate) globals.c2mSceneLoadRequestId++; globals.c2mSceneLoaded.value = false },
      applyComparisonSelection() {}, setC2MWireframe() {}, hideBimWhileC2MIsLoaded() {},
      restoreBimVisibilityAfterC2M() {}, requestRender() {},
      ElMessage: { success() {}, error: (message: string) => errors.push(message) },
    }
    const names = ['loadC2MToScene', 'loadC2MSceneAttempt', 'recoverC2MSceneVersion'].filter(name => ast.statements.some(node => ts.isFunctionDeclaration(node) && node.name?.text === name))
    const api = harness(names, globals)
    await api.loadC2MToScene()
    assert.equal(latestCalls, 1, '409 refreshes metadata once')
    if (scenario === 'new-version') {
      assert.deepEqual(downloads, ['old', 'new'])
      assert.deepEqual(distanceVersions, ['new'])
      assert.equal(globals.c2mSceneLoaded.value, true)
      assert.deepEqual(errors, [])
    } else if (scenario === 'repeated-conflict') {
      assert.deepEqual(downloads, ['old', 'new'], 'a second 409 must not loop')
      assert.equal(errors.length, 1)
    } else {
      assert.deepEqual(downloads, ['old'])
      if (scenario === 'superseded') {
        assert.equal(globals.c2mResult.value.resultVersion, 'user-newer')
        assert.deepEqual(errors, [])
      } else if (scenario === 'stale-input') assert.equal(errors[0], latest.staleReason)
      else assert.equal(errors.length, 1)
    }
    if (scenario !== 'superseded') assert.equal(globals.c2mSceneLoading.value, false)
    geometry.dispose()
    if (globals.c2mSceneGroup) globals.c2mSceneGroup.traverse((object: any) => object.material?.dispose())
  })
}

test('same-asset concurrent loads share work; explicit preprocessing can reload', async () => {
  const pending = deferred()
  let calls = 0
  const globals = {
    props: { pointcloudAssetId: 5 }, pointcloudLoadPromise: null, pointcloudLoadAssetId: null,
    tileset: null as unknown, pointcloudWrapper: null as unknown, pointcloudLoaded: { value: false },
    loadPointcloudFromApi: () => { calls++; return pending.promise },
  }
  const api = harness(['handleLoadPointCloudFromApi'], globals)
  const first = api.handleLoadPointCloudFromApi()
  const second = api.handleLoadPointCloudFromApi()
  assert.equal(calls, 1)
  pending.resolve()
  await Promise.all([first, second])
  globals.tileset = {}; globals.pointcloudWrapper = {}; globals.pointcloudLoaded.value = true
  await api.handleLoadPointCloudFromApi()
  assert.equal(calls, 1)
  await api.handleLoadPointCloudFromApi(false, true)
  assert.equal(calls, 2)
})

test('returning to a recreated host reuses canvas and observes the new dimensions', async () => {
  const oldHost = {}, canvas = { parentElement: oldHost }
  const observed: unknown[] = [], unobserved: unknown[] = []
  let resized = 0, rendered = 0
  const host = { appendChild: (node: typeof canvas) => { node.parentElement = host } }
  const api = harness(['observeViewport', 'restoreViewportAfterWorkflowStep'], {
    nextTick: async () => {}, renderer: { domElement: canvas }, viewportEl: { value: host },
    resizeObserver: { observe: (el: unknown) => observed.push(el), unobserve: (el: unknown) => unobserved.push(el) },
    observedViewportEl: oldHost, syncRendererSize: () => resized++, requestRender: () => rendered++,
  })
  await api.restoreViewportAfterWorkflowStep()
  await api.restoreViewportAfterWorkflowStep()
  assert.equal(canvas.parentElement, host)
  assert.deepEqual(observed, [host]); assert.deepEqual(unobserved, [oldHost])
  assert.equal(resized, 2); assert.equal(rendered, 2)
})

test('preview requests coalesce while switching back to source keeps source selected', async () => {
  const pending = deferred()
  let loads = 0
  const globals = {
    denoiseRequestedView: 'source', denoiseView: { value: 'source' }, denoiseColorMode: { value: 'cleaned' },
    denoisePreview: null, denoisePreviewPromise: null, denoisePreviewRequestId: 0,
    denoiseResult: { value: { fresh: true, version: 'v1' } }, scene: {}, pointcloudGroup: {},
    rebarDebugActive: { value: false }, rebarDebugScan: { value: false },
    applySceneVisibility: () => {}, requestRender: () => {},
    loadDenoisePreview: () => { loads++; return pending.promise },
  }
  const api = harness(['showDenoisePreview'], globals)
  const first = api.showDenoisePreview('cleaned')
  const second = api.showDenoisePreview('classes')
  await api.showDenoisePreview('source')
  assert.equal(loads, 1)
  assert.equal(globals.denoiseRequestedView, 'source')
  assert.equal(globals.denoiseView.value, 'source')
  pending.resolve(); await Promise.all([first, second])
  assert.equal(globals.denoisePreviewPromise, null)
})

test('isolated result defers hidden scan data until the scan is shown or isolation exits', async () => {
  let loads = 0
  const globals = {
    denoiseRequestedView: 'source', denoiseView: { value: 'source' }, denoiseColorMode: { value: 'cleaned' },
    denoisePreview: null, denoisePreviewPromise: null, denoisePreviewRequestId: 0,
    denoiseResult: { value: { fresh: true, version: 'v1' } }, scene: {}, pointcloudGroup: {},
    rebarDebugActive: { value: true }, rebarDebugScan: { value: false },
    applySceneVisibility() {}, requestRender() {},
    loadDenoisePreview: async () => { loads++ },
  }
  const api = harness(['showDenoisePreview'], globals)
  await api.showDenoisePreview('cleaned')
  assert.equal(loads, 0, 'opening a colored result must not download or parse the hidden 500,000-point preview')
  globals.rebarDebugScan.value = true
  await api.showDenoisePreview('cleaned')
  assert.equal(loads, 1, 'showing the scan requests its preview')
  globals.rebarDebugScan.value = false
  globals.rebarDebugActive.value = false
  await api.showDenoisePreview('cleaned')
  assert.equal(loads, 2, 'ordinary comparison retains its scan preview')
})

test('late instance maps cannot repopulate an invalidated workflow', async () => {
  const pending = deferred()
  const globals = {
    backendRequest: () => pending.promise, denoiseArtifactUrl: () => '/instance-map.json',
    props: { pointcloudAssetId: 5, bimAssetId: 7 }, denoisePreviewRequestId: 2,
    denoiseResult: { value: { version: 'v1' } }, comparisonInventory: { value: null }, c2mError: { value: '' },
  }
  const api = harness(['loadComparisonInventory'], globals)
  const load = api.loadComparisonInventory({ version: 'v1' }, 1)
  pending.resolve({ inventory: { bars: [] } }); await load
  assert.equal(globals.comparisonInventory.value, null)
})

test('deviation computation uses full retained points and automatically displays a fresh result', async () => {
  let submitted: any, loaded = 0
  const globals = {
    canRunC2M: { value: true }, props: { pointcloudAssetId: 5, bimAssetId: 7 }, meshTaskActive: { value: false },
    c2mRunning: { value: false }, c2mError: { value: '' }, c2mResultRequestId: 0,
    clearC2MScene() {}, c2mDistancesRequestId: 0, c2mDistances: { value: null },
    denoiseResult: { value: { version: 'denoise-v1' } }, c2mVoxelSize: { value: .05 },
    c2mNormalConstraintEnabled: { value: true }, c2mNormalMaxAngleDeg: { value: 30 },
    c2mMaxSearchDistanceMm: { value: 200 },
    c2mRequestedVisualization: { value: {} }, c2mResult: { value: null },
    computeC2M: async (request: any) => { submitted = request; return { data: { fresh: true } } },
    syncC2MControls() {}, syncC2MDistances: async () => {}, scheduleC2MAnalysisPolling() {},
    rebarDebugActive: { value: false },
    rebarDebugSurface: { value: 'source' }, rebarDebugScan: { value: true }, rebarDebugFocusPending: false,
    isC2MResultFresh: (result: any) => result?.fresh === true, activeWorkflowStep: { value: 3 },
    loadC2MToScene: async () => { loaded++ }, ElMessage: { success() {}, error() {} },
  }
  const api = harness(['runC2M', 'selectRebarDebugResult'], globals)
  await api.runC2M()
  assert.equal(submitted.downsampleEnabled, false)
  assert.equal(submitted.normalConstraintEnabled, true)
  assert.equal(submitted.normalHalfSpaceOnly, false)
  assert.equal(submitted.normalFallbackMode, 'unknown')
  assert.equal(submitted.normalMaxAngleDeg, 30)
  assert.equal(submitted.denoiseVersion, 'denoise-v1')
  assert.equal(loaded, 1)
  globals.rebarDebugActive.value = true
  await api.runC2M()
  assert.equal(globals.rebarDebugSurface.value, 'result', 'debug recalculation switches away from the original BIM')
  assert.equal(globals.rebarDebugScan.value, false, 'dense scan points cannot cover the new deviation colors')
  assert.equal(loaded, 2)
  assert.equal(globals.c2mRunning.value, false)
})

for (const scenario of [
  { name: 'empty ready scene', step: 3, fresh: true, loaded: false, loading: false, expected: 1 },
  { name: 'already loaded scene', step: 3, fresh: true, loaded: true, loading: false, expected: 0 },
  { name: 'scene loading', step: 3, fresh: true, loaded: false, loading: true, expected: 0 },
  { name: 'left deviation step', step: 2, fresh: true, loaded: false, loading: false, expected: 0 },
  { name: 'stale result', step: 3, fresh: false, loaded: false, loading: false, expected: 0 },
]) test(`result polling: ${scenario.name}`, async () => {
  let loaded = 0
  const result = { resultVersion: 'v1', fresh: scenario.fresh, analysis: { status: 'ready' } }
  const globals = {
    props: { pointcloudAssetId: 5, bimAssetId: 7 }, c2mResultRequestId: 0,
    c2mResult: { value: result }, c2mSceneLoaded: { value: scenario.loaded }, c2mTileset: {},
    getLatestC2M: async () => ({ data: result }), scheduleC2MAnalysisPolling() {},
    isC2MResultFresh: (value: any) => value.fresh, clearC2MScene() {},
    activeWorkflowStep: { value: scenario.step }, c2mSceneLoading: { value: scenario.loading },
    c2mSceneArtifactAvailable: { value: true }, loadC2MToScene: async () => { loaded++ },
    clearC2MAnalysisPolling() {}, c2mDistancesRequestId: 0, c2mDistances: { value: null },
  }
  await harness(['loadLatestC2M'], globals).loadLatestC2M()
  assert.equal(loaded, scenario.expected)
  assert.equal(globals.c2mResult.value, result, 'polling must preserve the result')
})

test('completion delegates one success notice and blocks a second in-flight save', async () => {
  const pending = deferred()
  const notices: string[] = []
  let saves = 0
  const globals = {
    canSaveCalibration: { value: true }, savingCalibration: { value: false },
    handleSaveAlignment: async () => { saves++; await pending.promise; notices.push('校准结果已保存'); return true },
    ElMessage: { success: (message: string) => notices.push(message) },
  }
  const api = harness(['handleCalibrationComplete'], globals)
  const first = api.handleCalibrationComplete()
  assert.equal(globals.savingCalibration.value, true)
  await api.handleCalibrationComplete()
  assert.equal(saves, 1)
  pending.resolve(); await first
  assert.deepEqual(notices, ['校准结果已保存'])
  assert.equal(globals.savingCalibration.value, false)
})

test('completion releases saving state after failure and respects readiness', async () => {
  const globals = {
    canSaveCalibration: { value: false }, savingCalibration: { value: false },
    handleSaveAlignment: async () => { throw new Error('network failure') },
  }
  const api = harness(['handleCalibrationComplete'], globals)
  await api.handleCalibrationComplete()
  globals.canSaveCalibration.value = true
  await assert.rejects(api.handleCalibrationComplete(), /network failure/)
  assert.equal(globals.savingCalibration.value, false)
})

test('gizmo visibility is independent of numeric edits and obeys clipping/workflow gates', () => {
  const controller = () => ({ visible: true, enabled: true })
  const globals = {
    showTransformHandles: { value: true }, editMode: { value: true }, activeWorkflowStep: { value: 1 },
    enableClipping: { value: false }, analysisMode: { value: 'none' },
    getSelectedObject: () => ({}), transformControls: controller(), rotationControls: controller(),
    transformHelper: { visible: true }, rotationHelper: { visible: true }, requestRender() {},
    positionOffsetX: { value: 1.234 }, coarseAlignmentDirty: { value: false },
  }
  const api = harness(['syncTransformHandleVisibility'], globals)
  globals.showTransformHandles.value = false; api.syncTransformHandleVisibility()
  assert.equal(globals.transformControls.enabled, false)
  assert.equal(globals.rotationHelper.visible, false)
  assert.equal(globals.editMode.value, true)
  assert.equal(globals.positionOffsetX.value, 1.234)
  assert.equal(globals.coarseAlignmentDirty.value, false)
  globals.showTransformHandles.value = true; api.syncTransformHandleVisibility()
  assert.equal(globals.transformControls.enabled, true)
  globals.enableClipping.value = true; api.syncTransformHandleVisibility()
  assert.equal(globals.transformHelper.visible, false)
  globals.enableClipping.value = false; globals.activeWorkflowStep.value = 2; api.syncTransformHandleVisibility()
  assert.equal(globals.transformControls.enabled, false)
})

for (const projection of ['perspective', 'orthographic']) test(`clip arrows retain pixel size and geometry across box resize and camera zoom: ${projection}`, () => {
  const camera = projection === 'perspective'
    ? new THREE.PerspectiveCamera(50, 1.6, .1, 1000)
    : new THREE.OrthographicCamera(-8, 8, 5, -5, .1, 1000)
  camera.position.set(0, 0, 20); camera.updateMatrixWorld()
  const globals = {
    THREE, scene: new THREE.Scene(), clipHandlesGroup: null as THREE.Group | null, clipHandlePickers: [],
    activeCamera: camera, viewportEl: { value: { clientHeight: 1000 } },
    isPerspectiveCamera: (value: THREE.Camera) => value instanceof THREE.PerspectiveCamera,
    clipAxis: { value: 'y' }, clipInvert: { value: true },
  }
  const api = harness(['ensureClipHandlesGroup', 'clipHandleWorldUnitsPerPixel', 'updateClipHandles'], globals)
  const box = new THREE.Box3(new THREE.Vector3(-2, -2, -2), new THREE.Vector3(2, 2, 2))
  api.updateClipHandles(box)
  const handles = globals.clipHandlesGroup!
  const handle = handles.children.find(item => item.userData.axis === 'y' && item.userData.invert)!
  const geometry = handles.children.map(item => item.userData.shaft.geometry)
  const projectedLength = () => {
    const base = new THREE.Vector3(0, 0, 0).applyMatrix4(handle.matrixWorld).project(camera)
    const tip = new THREE.Vector3(0, 48, 0).applyMatrix4(handle.matrixWorld).project(camera)
    return Math.abs(base.y - tip.y) * globals.viewportEl.value.clientHeight / 2
  }
  assert.ok(Math.abs(projectedLength() - 48) < 1e-6)
  for (const factor of [.1, 1, 10]) {
    const resized = box.clone().min.multiplyScalar(factor)
    const changed = new THREE.Box3(resized, box.max.clone().multiplyScalar(factor))
    api.updateClipHandles(changed)
    assert.ok(Math.abs(projectedLength() - 48) < 1e-6, 'box dimensions must not set arrow size')
    assert.deepEqual(handles.children.map(item => item.userData.shaft.geometry), geometry)
  }
  camera.zoom = 2; camera.updateProjectionMatrix(); camera.position.z = 40
  globals.viewportEl.value.clientHeight = 720
  api.updateClipHandles(box)
  assert.ok(Math.abs(projectedLength() - 48) < 1e-6, 'zoom and viewport height retain CSS size')
  const tipCenter = new THREE.Vector3(0, 24, 0).applyMatrix4(handle.matrixWorld)
  const ray = new THREE.Raycaster(tipCenter.clone().add(new THREE.Vector3(0, 0, 5)), new THREE.Vector3(0, 0, -1))
  assert.ok(ray.intersectObject(handle.userData.hitArea).length > 0, 'scaled picker still receives pointer hits')
})
