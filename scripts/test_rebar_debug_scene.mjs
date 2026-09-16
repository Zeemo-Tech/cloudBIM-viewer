import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import * as THREE from 'three'
import { filterComparisonGeometry } from '../src/views/alignment/rebarComparison.ts'
import { applyDenoisePreviewAppearance } from '../src/views/alignment/denoisePreview.ts'
import { debugGeometryBounds, debugNormalArrows, debugFaceNormalArrows, debugMeshCounts, observedRadialNormal } from '../src/views/alignment/rebarDebug.ts'

// Run the actual scene mutators on real Three.js objects; no WebGL or browser is required.
const source = readFileSync(new URL('../src/views/alignment/BimPointcloudAlignView.vue', import.meta.url), 'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
const names = ['comparisonMeshMatches', 'rebarDebugDesignObjects', 'rebarDebugBounds', 'clearRebarDebugOverlay', 'updateRebarDebugOverlay', 'applyComparisonSelection', 'applySceneVisibility', 'disposeObject3D', 'selectRebarDebugResult', 'showRebarDebugResult', 'showRebarDebugPair']
const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map(node => node.getText(ast)).join('\n')
assert.equal(functions.match(/^(async )?function /gm).length, names.length)
const ref = value => ({ value })
const defaultStatements = ast.statements.filter(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(d => ['rebarDebugSurface', 'rebarDebugScan', 'rebarDebugAllNormals'].includes(d.name.getText(ast)))).map(node => node.getText(ast)).join('\n')
const defaults = runInNewContext(ts.transpileModule(defaultStatements, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText + '\n({ surface: rebarDebugSurface.value, scan: rebarDebugScan.value, allNormals: rebarDebugAllNormals.value })', { ref })
assert.equal(defaults.surface, 'result', 'fresh debug page opens the deviation layer')
assert.equal(defaults.scan, false, 'fresh debug page does not cover colors with scan points')
assert.equal(defaults.allNormals, true, 'design normals default to full mesh density')
const bimPivot = new THREE.Group(), parentA = new THREE.Group(), parentB = new THREE.Group()
parentA.name = 'A'; parentB.name = 'B'; bimPivot.add(parentA, parentB)
const meshA = new THREE.Mesh(new THREE.BoxGeometry(.01, 1, .01), new THREE.MeshBasicMaterial())
const meshB = meshA.clone(); meshB.geometry = meshA.geometry.clone(); parentA.add(meshA); parentB.add(meshB); parentB.position.x = 100
const resultGeometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, 1, 0, 1, 0, 0, 100, 0, 0, 100, 1, 0, 101, 0, 0], 3))
resultGeometry.setIndex([0, 1, 2, 3, 4, 5]); resultGeometry.computeVertexNormals()
const resultMesh = new THREE.Mesh(resultGeometry, new THREE.MeshBasicMaterial({ vertexColors: true }))
const c2mSceneGroup = new THREE.Group(); c2mSceneGroup.add(resultMesh)
const pointGeometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0, .1, 0, 100, .1, 0, .1, .2, 0], 3))
pointGeometry.setAttribute('label', new THREE.Float32BufferAttribute([3, 3, 3], 1))
pointGeometry.setAttribute('instance', new THREE.Float32BufferAttribute([10, 20, 30], 1))
const denoisePreview = new THREE.Points(pointGeometry, new THREE.PointsMaterial())
const barA = { ifcGlobalId: 'A', instanceIds: [10], reviewInstanceIds: [30], vertexStart: 0, vertexCount: 3 }
const barB = { ifcGlobalId: 'B', instanceIds: [20], vertexStart: 3, vertexCount: 3 }
const missing = { ifcGlobalId: 'MISSING', instanceIds: [], vertexStart: 6, vertexCount: 0 }
const scene = new THREE.Scene(); scene.add(bimPivot, c2mSceneGroup, denoisePreview)
const context = {
  THREE, scene, bimPivot, c2mSceneGroup, denoisePreview,
  filterComparisonGeometry, applyDenoisePreviewAppearance, debugGeometryBounds, debugNormalArrows, debugFaceNormalArrows, debugMeshCounts, observedRadialNormal,
  rebarDebugActive: ref(true), rebarDebugBar: ref(barA), selectedComparisonBar: ref(undefined),
  comparison: ref({ bars: [barA, barB, missing] }), comparisonInventory: ref({ inventory: { bars: [barA, barB] } }),
  comparisonBimVisibility: new WeakMap(), rebarDebugSurface: ref('source'), rebarDebugScan: ref(true),
  rebarDebugCluster: ref('matched'), rebarDebugNormalMode: ref('vertex'), rebarDebugMeshCounts: ref({ vertices: 0, faces: 0 }), rebarDebugNormals: ref(true), rebarDebugScanNormals: ref(false),
  rebarDebugLengthMm: ref(10), rebarDebugLimit: ref(200), rebarDebugAllNormals: ref(defaults.allNormals), rebarDebugNormalCount: ref(0), rebarDebugScanNormalCount: ref(0),
  rebarDebugMaterials: new Map(), rebarDebugOverlay: null, rebarDebugFocusPending: false,
  activeWorkflowStep: ref(3), c2mSceneActive: ref(true), remeshSceneGroup: null,
  pointcloudVisible: ref(true), denoiseView: ref('cleaned'), bimVisible: ref(false), remeshMeshLoaded: ref(false),
  denoiseVisiblePointCount: ref(0), pointcloudWrapper: new THREE.Group(),
  syncDenoisePreviewTransform() {}, requestRender() {}, focusRebarDebug() {}, guessIfcId: () => '', findMetadataElementById: () => undefined,
  computed: fn => ({ get value() { return fn() } }), canUseC2MResult: ref(true), c2mSceneLoaded: ref(true), c2mSceneLoading: ref(false),
  loadC2MToScene: async () => { throw Error('Already loaded result must not be downloaded again') },
  getSelectedObject: () => null, updateSelectionHighlight() {}, clearPickedElement() {}, syncBoundsHelpers() {}, scheduleClipRangeUpdate() {},
}
const displayStatement = ast.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(d => d.name.getText(ast) === 'rebarDebugDisplaySurface'))
context.rebarDebugDisplaySurface = runInNewContext(ts.transpileModule(displayStatement.getText(ast), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText + '\nrebarDebugDisplaySurface', context)
const code = ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const api = runInNewContext(`${code}\n({ ${names.join(', ')} })`, context)
api.applySceneVisibility()
assert.equal(bimPivot.visible, true)
assert.equal(meshA.visible, true)
assert.equal(meshB.visible, false)
assert.equal(c2mSceneGroup.visible, false)
assert.equal(pointGeometry.drawRange.count, 1)
assert.equal(pointGeometry.index.getX(0), 0)
assert.ok(context.rebarDebugNormalCount.value > 0)
assert.ok(api.rebarDebugBounds().max.x < 2, 'focus must ignore the other bar 100 meters away')
let disposed = 0
context.rebarDebugOverlay.children[0].geometry.addEventListener('dispose', () => disposed++)
context.rebarDebugSurface.value = 'mesh'
api.applySceneVisibility()
assert.equal(disposed, 1, 'rebuilding normals disposes old GPU buffers')
assert.equal(bimPivot.visible, false)
assert.equal(c2mSceneGroup.visible, true)
assert.equal(resultMesh.material.vertexColors, false)
assert.deepEqual([...resultGeometry.index.array], [0, 1, 2])
assert.deepEqual({ ...context.rebarDebugMeshCounts.value }, { vertices: 3, faces: 1 })
context.rebarDebugNormalMode.value = 'face'
api.applySceneVisibility()
assert.equal(context.rebarDebugNormalCount.value, 1, 'face mode samples the selected calculation triangle')
context.rebarDebugNormalMode.value = 'vertex'
context.rebarDebugBar.value = barB
context.rebarDebugSurface.value = 'result'
api.applySceneVisibility()
assert.equal(resultMesh.material.vertexColors, true)
assert.deepEqual([...resultGeometry.index.array], [3, 4, 5])
assert.equal(pointGeometry.index.getX(0), 1)
context.rebarDebugBar.value = barA
context.rebarDebugCluster.value = 'review'
api.applySceneVisibility()
assert.equal(pointGeometry.drawRange.count, 1)
assert.equal(pointGeometry.index.getX(0), 2)
context.rebarDebugBar.value = missing
api.applySceneVisibility()
assert.equal(pointGeometry.drawRange.count, 0)
assert.equal(resultGeometry.index.count, 0)
assert.equal(api.rebarDebugBounds(), null)
context.rebarDebugActive.value = false
api.applySceneVisibility()
assert.equal(meshA.visible, true)
assert.equal(meshB.visible, true)
assert.equal(bimPivot.visible, false, 'normal result mode restores the original coplanar-BIM hiding policy')
assert.equal(c2mSceneGroup.visible, true)
assert.equal(pointGeometry.drawRange.count, 3)
assert.deepEqual([...resultGeometry.index.array], [0, 1, 2, 3, 4, 5])
assert.equal(context.rebarDebugOverlay, null)
assert.equal(context.rebarDebugMaterials.size, 0)
context.rebarDebugActive.value = true
context.rebarDebugBar.value = barA
context.rebarDebugSurface.value = 'mesh'
api.applySceneVisibility()
await api.showRebarDebugResult()
assert.equal(context.rebarDebugSurface.value, 'result')
assert.equal(resultMesh.material.vertexColors, true, 'result shortcut restores colors from neutral mesh')
assert.equal(c2mSceneGroup.visible, true)
assert.equal(bimPivot.visible, false)
assert.equal(denoisePreview.visible, false, 'scan must not occlude the colored design surface')
const savedComparison = context.comparison.value
context.comparison.value = undefined
api.applySceneVisibility()
assert.equal(context.rebarDebugDisplaySurface.value, 'source')
assert.equal(context.rebarDebugSurface.value, 'result', 'temporary missing result must not overwrite the requested display')
assert.equal(bimPivot.visible, true)
context.comparison.value = savedComparison
api.applySceneVisibility()
assert.equal(context.rebarDebugDisplaySurface.value, 'result')
assert.equal(c2mSceneGroup.visible, true, 'late result returns to colored display')
await api.showRebarDebugPair()
assert.equal(bimPivot.visible, false)
assert.equal(denoisePreview.visible, true)
assert.equal(c2mSceneGroup.visible, true, 'pair shortcut uses the subdivided comparison mesh')
assert.equal(context.rebarDebugSurface.value, 'mesh')
context.comparison.value = undefined
context.canUseC2MResult.value = false
await api.showRebarDebugPair()
assert.equal(bimPivot.visible, true, 'before computation, pairing can still inspect the original model')
assert.equal(context.rebarDebugSurface.value, 'source')
api.clearRebarDebugOverlay()
// The design overlay must expose the dense comparison mesh, not silently
// reduce thousands of normals to the shared 200-arrow preview budget.
context.comparison.value = savedComparison
context.canUseC2MResult.value = true
context.rebarDebugSurface.value = 'mesh'
context.rebarDebugAllNormals = ref(true)
const denseGeometry = new THREE.CylinderGeometry(.004, .004, 1, 16, 100)
resultMesh.geometry = denseGeometry
const denseCounts = debugMeshCounts(denseGeometry)
barA.vertexCount = denseGeometry.getAttribute('position').count
for (const [mode, count] of [['vertex', denseCounts.vertices], ['face', denseCounts.faces]]) {
  context.rebarDebugNormalMode.value = mode
  api.applySceneVisibility()
  assert.equal(context.rebarDebugNormalCount.value, count, `${mode}: all design normals must survive the 200-arrow preview limit`)
  context.rebarDebugAllNormals.value = false
  api.applySceneVisibility()
  assert.ok(context.rebarDebugNormalCount.value <= 200, 'explicit sampling retains the preview budget')
  context.rebarDebugAllNormals.value = true
}
api.clearRebarDebugOverlay()
denseGeometry.dispose()
console.log('PASS: real scene isolation, next bar, source/result/neutral layers, review and missing clusters, focus bounds, normal disposal, full restoration')
