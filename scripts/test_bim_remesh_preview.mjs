import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as Vue from 'vue'
import * as THREE from 'three'
import * as Tiles from '3d-tiles-renderer'
import * as TilePlugins from '3d-tiles-renderer/three/plugins'
import * as tableVisibility from '../src/features/pointcloud/tableVisibility.ts'
import { buildInstancePalette } from '../src/features/rebar-visualization/instancePalette.js'
import { useBimRemeshDisplay } from '../src/views/preview/bimRemeshDisplay.ts'
import * as groundGrid from '../src/components/preview/InfiniteGroundGrid.ts'

const require = createRequire(import.meta.url)
// Execute the real component setup/watchers in Node. Only mounting a WebGL
// canvas and unrelated service/UI dependencies are stubbed.
function setupComponent(path, input, dependencies = {}) {
  const { descriptor } = parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
  const script = compileScript(descriptor, { id: path })
  const compiled = ts.transpileModule(script.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const exports = {}
  const context = vm.createContext({
    exports, console, setTimeout, clearTimeout, TextDecoder, Error,
    require(name) {
      if (name === 'vue') return { ...Vue, onMounted() {}, onBeforeUnmount() {} }
      if (name === 'three') return THREE
      if (name === '3d-tiles-renderer') return Tiles
      if (name === '3d-tiles-renderer/three/plugins') return TilePlugins
      if (name.endsWith('/tableVisibility')) return tableVisibility
      if (name.endsWith('/instancePalette.js')) return { buildInstancePalette }
      if (name === './bimRemeshDisplay') return { useBimRemeshDisplay }
      if (name === './InfiniteGroundGrid') return groundGrid
      if (name === '@/api/backend-mesh') return { REBAR_SWEEP_ALGORITHM: 'rebar_sweep', DEFAULT_REBAR_SWEEP_PARAMS: { cross_section_sides: 16, axial_spacing: 0.01, max_chord_error: 0.0001 }, ...dependencies[name] }
      if (name in dependencies) return dependencies[name]
      if (name === 'vue-router') return { useRouter: () => ({}), useRoute: () => ({ path: '/preview/asset', query: {} }) }
      if (name === 'element-plus') return { ElMessage: {} }
      if (name.endsWith('.vue') || name.startsWith('@/') || name.startsWith('./')) return {}
      return require(name)
    },
  })
  vm.runInContext(compiled, context)
  const component = exports.default
  const props = Vue.reactive(Object.fromEntries(Object.entries(component.props ?? {}).map(([name, option]) => [
    name, typeof option.default === 'function' ? option.default() : option.default,
  ])))
  Object.assign(props, input)
  const events = []
  const scope = Vue.effectScope()
  const bindings = scope.run(() => component.setup(props, { expose() {}, emit: (...event) => events.push(event) }))
  return { props, bindings, events, stop: () => scope.stop() }
}

const ply = () => new Blob([`ply
format ascii 1.0
element vertex 3
property float x
property float y
property float z
element face 1
property list uchar int vertex_indices
end_header
10 0 0
11 0 0
10 1 0
3 0 1 2
`])

function makeViewer(download = async () => ply()) {
  const viewer = setupComponent('../src/components/preview/UnifiedViewer3D.vue', { type: 'bim', assetId: 7 }, {
    '@/api/backend-mesh': { downloadRemeshResult: download },
  })
  const b = viewer.bindings
  const root = new THREE.Group()
  const original = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial())
  root.add(original)
  root.position.set(7, 2, 3)
  b.bimRoot = root
  b.bimSourceMatrix = new THREE.Matrix4().makeTranslation(10, 0, 0)
  b.loaded.value = true
  b.camera = new THREE.PerspectiveCamera()
  b.camera.position.set(4, 5, 6)
  b.controls = { target: new THREE.Vector3(1, 2, 3) }
  return { ...viewer, b, root, original }
}

const settleDisplay = () => new Promise(resolve => setImmediate(resolve))

test('preview ground follows asset bottom and scale without moving the asset or following the camera', () => {
  const v = setupComponent('../src/components/preview/UnifiedViewer3D.vue', { type: 'bim' })
  const b = v.bindings
  b.gridHelper = new groundGrid.InfiniteGroundGrid()
  b.camera = new THREE.PerspectiveCamera(50, 1.6)
  b.controls = { target: new THREE.Vector3(), update() { b.camera.lookAt(this.target) } }
  try {
    for (const scale of [0.001, 1, 1000]) {
      const box = new THREE.Box3(new THREE.Vector3(7, 12, -4), new THREE.Vector3(9, 13, -1))
      box.min.multiplyScalar(scale)
      box.max.multiplyScalar(scale)
      const original = box.clone()
      b.setSectionState({ box })
      const ground = b.gridHelper.position.clone()
      assert.ok(Math.abs(box.min.y - ground.y - 3 * scale * 0.002) < scale * 1e-9)
      assert.ok(Math.abs(b.gridHelper.material.uniforms.cellSize.value - 3 * scale / 20) < scale * 1e-9)
      assert.equal(b.gridHelper.material.uniforms.gridOrigin.value.x, box.getCenter(new THREE.Vector3()).x)
      assert.ok(box.equals(original))
      b.fitCameraToBox(box)
      b.fitCameraToBox(new THREE.Box3(box.min.clone(), box.getCenter(new THREE.Vector3())))
      assert.ok(b.gridHelper.position.equals(ground), 'inspection focus must not move the floor')
    }
    b.setShowGrid(false)
    b.setSectionState({ box: new THREE.Box3(new THREE.Vector3(), new THREE.Vector3(1, 1, 1)) })
    assert.equal(b.gridHelper.visible, false)
  } finally {
    b.gridHelper.dispose()
    v.stop()
  }
})

test('preview framing contains every corner on wide and narrow viewports at different asset scales', () => {
  const v = setupComponent('../src/components/preview/UnifiedViewer3D.vue', { type: 'bim' })
  const b = v.bindings
  try {
    for (const aspect of [0.35, 1, 2.5]) for (const scale of [0.001, 1, 1000]) {
      const box = new THREE.Box3(new THREE.Vector3(-2, -0.5, -1), new THREE.Vector3(2, 0.5, 1))
      box.min.multiplyScalar(scale)
      box.max.multiplyScalar(scale)
      b.camera = new THREE.PerspectiveCamera(50, aspect)
      b.camera.up.set(0, 0, -1) // Reset after a top view must restore the vertical axis.
      b.controls = { target: new THREE.Vector3(), update() { b.camera.lookAt(this.target) } }
      b.fitCameraToBox(box)
      b.camera.updateMatrixWorld(true)
      assert.ok(b.camera.up.equals(new THREE.Vector3(0, 1, 0)))
      assert.ok(b.controls.target.equals(box.getCenter(new THREE.Vector3())))
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
        const projected = new THREE.Vector3(x, y, z).project(b.camera)
        assert.ok(Math.abs(projected.x) < 1 && Math.abs(projected.y) < 1 && Math.abs(projected.z) < 1)
      }
      if (aspect >= 1) assert.ok(b.getCameraDistance() < 8 * scale, 'initial view should not be excessively distant')
      const distance = b.getCameraDistance()
      b.setStandardView('top')
      assert.ok(Math.abs(b.getCameraDistance() - distance) < scale * 1e-9)
      b.setViewDirection([1, 1, 1])
      assert.ok(Math.abs(b.getCameraDistance() - distance) < scale * 1e-9, 'view cube must not zoom small assets out')
    }
  } finally { v.stop() }
})

test('tileset ground bounds include parent rotation/translation/scale and do not use sphere bottom', () => {
  const v = setupComponent('../src/components/preview/UnifiedViewer3D.vue', { type: 'pointcloud' })
  const wrapper = new THREE.Group()
  wrapper.rotation.x = -Math.PI / 2
  wrapper.position.set(10, 20, 30)
  wrapper.scale.setScalar(2)
  const group = new THREE.Group()
  wrapper.add(group)
  const sourceBox = new THREE.Box3(new THREE.Vector3(-5, -4, 2), new THREE.Vector3(5, 4, 3))
  try {
    const actual = groundGrid.getTilesetWorldBounds({
      group,
      getBoundingBox(box) { box.copy(sourceBox); return true },
      getBoundingSphere() { throw new Error('must prefer asset box over bounding sphere') },
    })
    assert.ok(Math.abs(actual.min.y - 24) < 1e-9)
    assert.ok(Math.abs(actual.max.y - 26) < 1e-9)
    assert.ok(actual.equals(sourceBox.clone().applyMatrix4(group.matrixWorld)))
    const fallback = groundGrid.getTilesetWorldBounds({
      group, getBoundingBox() { return false },
      getBoundingSphere(sphere) { sphere.set(new THREE.Vector3(), 3); return true },
    })
    assert.equal(fallback.getSize(new THREE.Vector3()).x, 12)
  } finally { v.stop() }
})

test('preview automatically displays real PLY, preserves coordinates/camera and can restore original BIM', async () => {
  const v = makeViewer()
  const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 }, {
    '@/api/backend-mesh': { getRemeshStatus: async () => ({ data: { supported: true, status: 'succeeded', algorithm: 'rebar_sweep', resultFileId: 7 } }) },
  })
  const p = page.bindings
  try {
    await p.refreshBimRemeshStatus()
    p.bimPanelRef.value = { setBimRemeshVisible: v.b.setBimRemeshVisible }
    p.bimLoaded.value = true
    assert.equal(p.bimRemeshReady.value, true)
    const pose = JSON.stringify(v.b.getCameraPose())
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, true)
    assert.equal(v.root.children.length, 1)
    const mesh = v.root.children[0]
    assert.equal(mesh.name, 'bim-remesh-result')
    assert.equal(mesh.geometry.index.count, 3)
    assert.equal(v.original.parent, null, 'original cannot occlude or intercept result picking')
    v.root.updateMatrixWorld(true)
    assert.deepEqual(new THREE.Vector3(10, 0, 0).applyMatrix4(mesh.matrixWorld).toArray(), [7, 2, 3])
    assert.equal(JSON.stringify(v.b.getCameraPose()), pose)
    v.b.setWireframe(true)
    assert.equal(mesh.material.wireframe, true)
    let disposed = 0
    mesh.geometry.addEventListener('dispose', () => disposed++)
    p.toggleBimRemesh()
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, false)
    assert.equal(v.root.children[0], v.original)
    assert.equal(v.original.material.wireframe, true)
    assert.equal(disposed, 1)
  } finally { page.stop(); v.stop() }
})

test('failed result download keeps original geometry and reports the error without enabling toggle', async () => {
  const v = makeViewer(async () => { throw new Error('下载失败') })
  const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 })
  const p = page.bindings
  try {
    p.bimRemeshStatus.value = { supported: true, status: 'succeeded', algorithm: 'rebar_sweep', resultFileId: 7 }
    p.bimLoaded.value = true
    p.bimPanelRef.value = { setBimRemeshVisible: v.b.setBimRemeshVisible }
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, false)
    assert.equal(p.bimRemeshDisplayError.value, '下载失败')
    assert.equal(p.bimRemeshBusy.value, false)
    assert.equal(v.root.children[0], v.original)
  } finally { page.stop(); v.stop() }
})

test('turning off or unloading while downloading prevents a late result attaching to the scene', async () => {
  let resolve
  const v = makeViewer(() => new Promise(r => { resolve = r }))
  try {
    const pending = v.b.setBimRemeshVisible(true)
    await v.b.setBimRemeshVisible(false)
    resolve(ply())
    await pending
    assert.equal(v.root.children[0], v.original)
    assert.equal(v.b.bimRemesh, null)
  } finally { v.stop() }
})

test('pending status for previous asset cannot overwrite the next preview', async () => {
  let resolve
  const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 }, {
    '@/api/backend-mesh': { getRemeshStatus: () => new Promise(r => { resolve = r }) },
  })
  try {
    const pending = page.bindings.refreshBimRemeshStatus()
    page.bindings.resetBimRemeshState()
    resolve({ data: { supported: true, status: 'succeeded', resultFileId: 7 } })
    await pending
    assert.equal(page.bindings.bimRemeshStatus.value, null)
    assert.equal(page.bindings.bimRemeshVisible.value, false)
  } finally { page.stop() }
})


test('rerunning succeeded mesh forces submission once, retires old PLY and preserves original selection', async () => {
  let finishSubmit
  const calls = []
  const displayed = []
  const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 }, {
    '@/api/backend-mesh': {
      remeshBimAsset: (id, payload) => { calls.push({ id, payload }); return new Promise(resolve => { finishSubmit = resolve }) },
      getRemeshStatus: async () => ({ data: { supported: true, status: 'queued' } }),
    },
  })
  const p = page.bindings
  try {
    p.bimPanelRef.value = { setBimRemeshVisible: async visible => { displayed.push(visible) } }
    p.bimLoaded.value = true
    p.bimRemeshStatus.value = { supported: true, status: 'succeeded', algorithm: 'rebar_sweep', resultFileId: 7, contentHash: 'old' }
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, true)
    // Clicking the already-selected segment must not turn it off.
    p.toggleBimRemesh(true)
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, true)
    const pending = p.retryBimRemesh()
    assert.equal(p.bimRemeshBusy.value, true)
    await p.retryBimRemesh()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].payload.force, true)
    assert.equal(calls[0].payload.algorithm, 'rebar_sweep')
    assert.deepEqual(JSON.parse(JSON.stringify(calls[0].payload.params)), { cross_section_sides: 16, axial_spacing: 0.01, max_chord_error: 0.0001 })
    finishSubmit({ data: { status: 'queued' } })
    await pending
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, false)
    assert.equal(displayed.at(-1), false)
    assert.equal(p.bimRemeshCanRun.value, false)
    assert.equal(p.bimRemeshBusy.value, false)
    p.bimRemeshStatus.value = { supported: true, status: 'succeeded', algorithm: 'rebar_sweep', resultFileId: 7, contentHash: 'new' }
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, true, 'completed rerun uses current automatic-display behavior')
    p.toggleBimRemesh(false)
    await settleDisplay()
    p.toggleBimRemesh(false)
    await settleDisplay()
    assert.equal(p.bimRemeshVisible.value, false, 'selected original segment is idempotent')
  } finally { p.resetBimRemeshState(); page.stop() }
})

test('idle/failed can retry without force; unsupported and active jobs cannot submit', async () => {
  for (const status of ['idle', 'failed', 'queued', 'processing', 'unsupported']) {
    const calls = []
    const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 }, {
      '@/api/backend-mesh': {
        remeshBimAsset: async (id, payload) => { calls.push(payload) },
        getRemeshStatus: async () => ({ data: { supported: true, status: 'queued' } }),
      },
    })
    const p = page.bindings
    try {
      p.bimRemeshStatus.value = { supported: status !== 'unsupported', status, canManualRetry: true }
      await p.retryBimRemesh()
      assert.equal(calls.length, ['idle', 'failed'].includes(status) ? 1 : 0, status)
      if (calls.length) assert.equal(calls[0].force, undefined)
    } finally { p.resetBimRemeshState(); page.stop() }
  }
})

test('failed rerun keeps current result, reports error, and permits retry', async () => {
  const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 }, {
    '@/api/backend-mesh': { remeshBimAsset: async () => { throw new Error('服务暂不可用') } },
  })
  const p = page.bindings
  try {
    p.bimPanelRef.value = { setBimRemeshVisible: async () => {} }
    p.bimLoaded.value = true
    p.bimRemeshStatus.value = { supported: true, status: 'succeeded', algorithm: 'rebar_sweep', resultFileId: 7 }
    await settleDisplay()
    await p.retryBimRemesh()
    assert.equal(p.bimRemeshVisible.value, true)
    assert.equal(p.bimRemeshError.value, '服务暂不可用')
    assert.equal(p.bimRemeshBusy.value, false)
    assert.equal(p.bimRemeshCanRun.value, true)
  } finally { p.resetBimRemeshState(); page.stop() }
})


test('BIM section switch passes the renderer state contract and applies six clipping planes', async () => {
  const v = makeViewer()
  const page = setupComponent('../src/views/preview/AssetPreviewView.vue', { previewType: 'bim', assetId: 7 })
  const p = page.bindings
  try {
    v.b.renderer = { clippingPlanes: [], localClippingEnabled: false }
    p.bimPanelRef.value = { setSectionState: v.b.setSectionState }
    p.bimControls.sectionEnabled = true
    p.applyPanelSettings()
    assert.equal(v.b.renderer.clippingPlanes.length, 6)
    assert.equal(v.b.renderer.localClippingEnabled, true)
    p.bimControls.sectionEnabled = false
    p.applyPanelSettings()
    assert.equal(v.b.renderer.clippingPlanes.length, 0)
    assert.equal(v.b.renderer.localClippingEnabled, false)
  } finally { page.stop(); v.stop() }
})
