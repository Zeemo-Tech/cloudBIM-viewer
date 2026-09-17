import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { rawWorldMatrixForAlignment, alignmentMatrixFromResult, scanToBimRigidTransform, modelPairsFromRigidTransform, desiredBimWorldMatrix, toLocalMatrix } from '@cloudbim/viewer-core'

const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
const names = [
  'ensureOrientationBase', 'ensurePositionBase', 'ensureInitialOrientation', 'ensureInitialPosition',
  'ensureInitialTransformState', 'getSelectedObject', 'selectSceneObject', 'resetOrientationFix', 'resetPositionFix',
  'normalizeDegrees', 'roundToStep', 'syncTransformModeForSelection', 'syncOrientationFixFromSelected',
  'syncPositionFixFromSelected', 'syncAllTransformFixValuesFromSelected', 'applyTransformSelection',
  'refreshSelectedTransformUi', 'onEditModeChange', 'setTransformMode', 'applyPositionFixRealtime',
  'setPositionOffsetAxis', 'onPositionNumberInput', 'onPositionNumberBlur', 'setOrientationOffsetAxis',
  'onOrientationNumberInput', 'applyOrientationFixRealtime',
  'recenterLoadedContentAsWhole', 'syncDenoisePreviewTransform',
  'getAlignmentRestoreKey', 'tryRestoreSavedAlignment', 'fetchAndLogSavedAlignmentIfExists',
  'collectCalibrationSnapshot', 'handleSaveAlignment', 'revealInitialSceneWhenReady',
]
const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map(node => node.getText(ast)).join('\n')
assert.equal(functions.match(/^async function |^function /gm)?.length, names.length)
const compiled = ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const ref = value => ({ value })
const noop = () => {}
function fixture() {
  const scene = new THREE.Scene(), content = new THREE.Group(), bim = new THREE.Group(), wrapper = new THREE.Group(), scan = new THREE.Group()
  scene.add(content); content.add(bim, wrapper); wrapper.add(scan)
  wrapper.rotation.x = -Math.PI / 2
  bim.add(new THREE.Mesh(new THREE.BoxGeometry(2, 3, 4)))
  bim.userData.__viewerNormalizationCenter = new THREE.Vector3(10, -20, 30)
  bim.userData.__viewerNormalizationMode = 'child'
  const globals = {
    THREE, console: { info: noop, error: noop }, scene, contentGroup: content, bimPivot: bim,
    pointcloudWrapper: wrapper, pointcloudGroup: scan, pointcloudRootReady: true,
    selectedItemId: ref('bim'), editMode: ref(true), enableElementPicking: ref(false), hasModel: ref(true),
    transformMode: ref('translate'), registrationStage: ref('coarse'), activeWorkflowStep: ref(1),
    positionOffsetX: ref(0), positionOffsetY: ref(0), positionOffsetZ: ref(0),
    orientationDegX: ref(0), orientationDegY: ref(0), orientationDegZ: ref(0), showOnlyVerticalAxis: ref(false),
    positionSliderRange: ref({ min: -55, max: 55 }), coarseAlignmentDirty: ref(false),
    transformControls: null, rotationControls: null, transformHelper: null, rotationHelper: null,
    syncBoundsHelpers: noop, requestRender: noop, updateSelectionHighlight: noop, focusSelected: noop,
    logBimRelativeTransform: noop, logCalibrationDiagnostics: noop, logSavedAlignmentMatrix: noop,
    vectorToPlainObject: noop, quaternionToPlainObject: noop, updateClipRangeFromContent: noop, applyClippingState: noop,
    getRawMatrixWorldForCalibration: rawWorldMatrixForAlignment,
    alignmentMatrixFromResult, scanToBimRigidTransform, modelPairsFromRigidTransform,
    desiredBimWorldMatrix, toLocalMatrix,
    nextTick: async cb => cb?.(), restoredSavedAlignmentKey: '', loggedSavedAlignmentKey: '',
    sceneAlignmentReady: ref(false), latestAlignmentResult: ref(null), hasSavedAlignmentMatrix: ref(false),
    initialSceneReady: false, fitCameraToContent: noop,
    props: { bimAssetId: 7, pointcloudAssetId: 5 }, denoisePreview: null, denoiseResult: ref(null),
    ElMessage: { success: noop, error: noop, warning: noop }, invalidateC2MResult: noop, clearDenoisePreview: noop,
    loadLatestDenoise: async () => {}, loadLatestC2M: async () => {},
    clamp: (v, min, max) => Math.max(min, Math.min(max, v)),
    window: { setTimeout: () => { throw new Error('unexpected retry') } },
  }
  const api = runInNewContext(`${compiled}\n({ ${[...names, 'getRawMatrixWorldForCalibration'].join(', ')} })`, globals)
  api.ensureOrientationBase(bim); api.ensurePositionBase(bim); api.ensureInitialTransformState(bim)
  return { api, state: globals }
}
function closeVector(actual, expected, epsilon = 1e-8) {
  assert.ok(actual.distanceTo(expected) < epsilon, `${actual.toArray()} != ${expected.toArray()}`)
}
function scanToBim(api, s) {
  s.scene.updateMatrixWorld(true)
  return api.getRawMatrixWorldForCalibration(s.bimPivot).invert().multiply(api.getRawMatrixWorldForCalibration(s.pointcloudGroup))
}
function savedResult(matrix) {
  return { modelId: 1, modelBimFileId: 7, modelScanFileId: 5, modelMatrix: matrix.toArray() }
}
function closeMatrix(actual, expected) {
  actual.elements.forEach((value, i) => assert.ok(Math.abs(value - expected.elements[i]) < 1e-8, `matrix[${i}]: ${value} != ${expected.elements[i]}`))
}

test('saved numeric pose survives leaving, returning and selecting BIM; next edit preserves other axes', () => {
  const { api, state: s } = fixture()
  api.setPositionOffsetAxis('x', '12.345678'); api.setPositionOffsetAxis('y', '-5.4321'); api.setPositionOffsetAxis('z', '3.001')
  s.transformMode.value = 'rotate'; s.showOnlyVerticalAxis.value = true
  api.setOrientationOffsetAxis('y', '37.25')
  s.coarseAlignmentDirty.value = false
  const before = scanToBim(api, s)
  s.editMode.value = false; api.onEditModeChange()
  s.editMode.value = true; api.onEditModeChange()
  api.selectSceneObject('bim', { enableEdit: true })
  api.setTransformMode('translate')
  assert.equal(s.positionOffsetX.value, 12.345678)
  assert.equal(s.positionOffsetY.value, -5.4321)
  assert.equal(s.positionOffsetZ.value, 3.001)
  assert.equal(s.orientationDegY.value, 37.25)
  assert.equal(s.coarseAlignmentDirty.value, false)
  closeMatrix(scanToBim(api, s), before)
  api.setPositionOffsetAxis('x', '12.346678')
  closeVector(s.bimPivot.position, new THREE.Vector3(12.346678, 3.001, -5.4321))
})

test('clearing a number mid-edit does not apply a zero transform; blur restores the actual value', () => {
  const { api, state: s } = fixture()
  api.setPositionOffsetAxis('x', '-1.234')
  const event = { target: { value: '' } }
  api.onPositionNumberInput('x', event)
  assert.equal(s.positionOffsetX.value, -1.234)
  api.onPositionNumberBlur('x', event)
  assert.equal(event.target.value, '-1.234')
  closeVector(s.bimPivot.position, new THREE.Vector3(-1.234, 0, 0))
  s.transformMode.value = 'rotate'; s.showOnlyVerticalAxis.value = true
  api.setOrientationOffsetAxis('y', '29.5')
  const quaternion = s.bimPivot.quaternion.clone()
  api.onOrientationNumberInput('y', { target: { value: '' } })
  assert.equal(s.orientationDegY.value, 29.5)
  assert.ok(s.bimPivot.quaternion.angleTo(quaternion) < 1e-7)
})

test('numeric translations can exceed the current slider extent without silent clamping', () => {
  const { api, state: s } = fixture()
  api.setPositionOffsetAxis('x', '-1234.56789')
  assert.equal(s.positionOffsetX.value, -1234.56789)
  assert.equal(s.bimPivot.position.x, -1234.56789)
})

test('reopening a saved alignment restores real numeric offsets after the scan root becomes ready', () => {
  const first = fixture()
  first.api.setPositionOffsetAxis('x', 12.345); first.api.setPositionOffsetAxis('y', -6.789); first.api.setPositionOffsetAxis('z', .001)
  first.state.showOnlyVerticalAxis.value = true; first.state.transformMode.value = 'rotate'
  first.api.setOrientationOffsetAxis('y', '23.45')
  const matrix = scanToBim(first.api, first.state), result = savedResult(matrix)
  const { api, state: s } = fixture()
  s.pointcloudRootReady = false
  assert.equal(api.tryRestoreSavedAlignment(result), false)
  assert.equal(s.sceneAlignmentReady.value, false)
  s.pointcloudRootReady = true
  assert.equal(api.tryRestoreSavedAlignment(result), true)
  assert.equal(s.sceneAlignmentReady.value, true)
  closeMatrix(scanToBim(api, s), matrix)
  assert.equal(s.positionOffsetX.value, 12.345)
  assert.equal(s.positionOffsetY.value, -6.789)
  assert.equal(s.positionOffsetZ.value, .001)
  api.setTransformMode('rotate')
  assert.equal(s.orientationDegY.value, 23.45)
})

test('replacing a scan with the same asset IDs reapplies alignment even after it was logged', async () => {
  const { api, state: s } = fixture()
  const matrix = new THREE.Matrix4().makeTranslation(11, 22, 33), result = savedResult(matrix)
  api.tryRestoreSavedAlignment(result)
  s.latestAlignmentResult.value = result; s.hasSavedAlignmentMatrix.value = true
  s.loggedSavedAlignmentKey = '5:7'; s.activeWorkflowStep.value = 2; s.editMode.value = false
  const replacement = new THREE.Group(); replacement.position.set(5, 10, 15)
  s.pointcloudGroup.removeFromParent(); s.pointcloudWrapper.add(replacement); s.pointcloudGroup = replacement
  s.sceneAlignmentReady.value = false
  await api.fetchAndLogSavedAlignmentIfExists()
  assert.equal(s.sceneAlignmentReady.value, true)
  assert.equal(s.editMode.value, false, 'late restore must respect the active workflow step')
  closeMatrix(scanToBim(api, s), matrix)
})

test('a changed matrix under the same saved record ID is restored', () => {
  const { api, state: s } = fixture()
  api.tryRestoreSavedAlignment(savedResult(new THREE.Matrix4().makeTranslation(1, 2, 3)))
  const matrix = new THREE.Matrix4().makeTranslation(4, 5, 6)
  api.tryRestoreSavedAlignment(savedResult(matrix))
  closeMatrix(scanToBim(api, s), matrix)
})

test('classification preview follows late source transforms and repeated recentering is idempotent', () => {
  const { api, state: s } = fixture()
  s.denoiseResult.value = { result: { previewOrigin: [100, 200, 300] } }
  s.denoisePreview = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial())
  s.denoisePreview.matrixAutoUpdate = false; s.scene.add(s.denoisePreview)
  const vertex = new THREE.Vector3(.1, .2, .3), rawVertex = vertex.clone().add(new THREE.Vector3(100, 200, 300))
  const check = () => {
    api.syncDenoisePreviewTransform()
    closeVector(vertex.clone().applyMatrix4(s.denoisePreview.matrixWorld), rawVertex.clone().applyMatrix4(api.getRawMatrixWorldForCalibration(s.pointcloudGroup)))
  }
  check()
  s.pointcloudGroup.position.set(3, 4, 5); s.pointcloudGroup.rotation.z = .3
  s.pointcloudGroup.userData.__viewerNormalizationCenter = new THREE.Vector3(7, 8, 9)
  s.pointcloudGroup.userData.__viewerNormalizationMode = 'child'
  s.pointcloudWrapper.visible = false
  check()
  s.bimPivot.position.set(10, 20, 30)
  api.recenterLoadedContentAsWhole(); check()
  const position = s.contentGroup.position.clone()
  api.recenterLoadedContentAsWhole(); check()
  closeVector(s.contentGroup.position, position)
  api.tryRestoreSavedAlignment(savedResult(new THREE.Matrix4().makeTranslation(1, 2, 3))); check()
})

test('saving records the applied scene so a later restore callback cannot discard new edits', async () => {
  const { api, state: s } = fixture()
  api.setPositionOffsetAxis('x', 1.234)
  s.createBimAlignment = async payload => {
    const matrix = scanToBim(api, s)
    for (const pair of payload.modelPairs) {
      closeVector(new THREE.Vector3(pair.modelScanX, pair.modelScanY, pair.modelScanZ).applyMatrix4(matrix), new THREE.Vector3(pair.modelBimX, pair.modelBimY, pair.modelBimZ))
    }
    return { data: savedResult(matrix) }
  }
  assert.equal(await api.handleSaveAlignment(), true)
  assert.equal(s.coarseAlignmentDirty.value, false)
  api.setPositionOffsetAxis('x', 1.235)
  await api.fetchAndLogSavedAlignmentIfExists()
  assert.equal(s.positionOffsetX.value, 1.235)
  assert.equal(s.bimPivot.position.x, 1.235)
})

test('yaw numeric edits preserve saved pitch and roll', () => {
  const { api, state: s } = fixture()
  s.bimPivot.quaternion.setFromEuler(new THREE.Euler(.12, .3, -.15, 'YXZ'))
  s.transformMode.value = 'rotate'; s.showOnlyVerticalAxis.value = true
  api.refreshSelectedTransformUi()
  api.setOrientationOffsetAxis('y', '25')
  const actual = new THREE.Euler().setFromQuaternion(s.bimPivot.quaternion, 'YXZ')
  assert.ok(Math.abs(actual.x - .12) < 1e-8)
  assert.ok(Math.abs(actual.z + .15) < 1e-8)
  assert.ok(Math.abs(actual.y - THREE.MathUtils.degToRad(25)) < 1e-8)
})

test('classification stays unavailable until a saved matrix is applied to the scene', () => {
  const declaration = ast.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(d => d.name.getText(ast) === 'canOpenDenoiseStep'))
  const s = { computed: fn => ({ get value() { return fn() } }), props: { bimAssetId: 7, pointcloudAssetId: 5 },
    pointcloudLoaded: ref(true), pointcloudPreprocessRequired: ref(false), hasSavedAlignmentMatrix: ref(true),
    sceneAlignmentReady: ref(false), coarseAlignmentDirty: ref(false) }
  const code = ts.transpileModule(declaration.getText(ast), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  const ready = runInNewContext(`${code}\ncanOpenDenoiseStep`, s)
  assert.equal(ready.value, false)
  s.sceneAlignmentReady.value = true; assert.equal(ready.value, true)
  s.coarseAlignmentDirty.value = true; assert.equal(ready.value, false)
})
