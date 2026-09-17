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
    exports, console, setTimeout, clearTimeout,
    require(name) {
      if (name === 'vue') return { ...Vue, onMounted() {}, onBeforeUnmount() {} }
      if (name === 'three') return THREE
      if (name === '3d-tiles-renderer') return Tiles
      if (name === '3d-tiles-renderer/three/plugins') return TilePlugins
      if (name.endsWith('/tableVisibility')) return tableVisibility
      if (name.endsWith('/instancePalette.js')) return { buildInstancePalette }
      if (name === './bimRemeshDisplay') return { useBimRemeshDisplay }
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

for (const mode of ['intensity', 'table-class']) test(`${mode}: preview toggle reaches cached LODs without replacing source, recoloring, loading events or camera changes`, async () => {
  let requests = 0
  const plane = { origin: [0, 0, 0], slopes: [0, 0], clearanceM: .005 }
  const preview = setupComponent('../src/views/preview/AssetPreviewView.vue', { assetId: 5, previewType: 'pointcloud' }, {
    '@/api/backend-file': { getAssetDetail: async () => { requests++; return { data: { tilesetUrl: '/asset5/source/tileset.json' } } } },
    '@/api/backend-pointcloud-preprocess': { getPointcloudPreprocess: async () => { requests++; return { data: { version: 'v1', result: { plane } } } } },
  })
  await preview.bindings.loadPointcloudResources()
  await Vue.nextTick()
  assert.equal(preview.bindings.pointcloudTableShown.value, false)
  assert.equal(preview.bindings.pointcloudDisplayColorMode.value, 'table-class')
  const viewer = setupComponent('../src/components/preview/UnifiedViewer3D.vue', {
    assetId: 5, type: 'pointcloud', pointcloudTilesetUrl: preview.bindings.pointcloudSourceUrl.value,
    pointcloudTablePlane: preview.bindings.pointcloudTablePlane.value, pointcloudTableVisible: false,
  })
  try {
    const models = Array.from({ length: 2 }, () => {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, .1], 3))
      geometry.setAttribute('color', new THREE.Float32BufferAttribute([.1, .1, .1, .8, .8, .8], 3))
      return new THREE.Points(geometry, new THREE.PointsMaterial())
    })
    let disposals = 0
    const group = new THREE.Group()
    group.add(models[0]) // The second LOD is cached but detached.
    const tileset = { group, forEachLoadedModel: fn => models.forEach(fn), dispose: () => disposals++ }
    viewer.bindings.tileset = tileset
    viewer.bindings.isMountedReady = true
    viewer.bindings.loaded.value = true
    viewer.bindings.camera = new THREE.PerspectiveCamera()
    viewer.bindings.camera.position.set(3, 4, 5)
    viewer.bindings.controls = { target: new THREE.Vector3(1, 2, 3) }
    const pose = JSON.stringify(viewer.bindings.getCameraPose())
    models.forEach(viewer.bindings.applyPointcloudMaterial)
    preview.bindings.pointcloudPanelRef.value = { setPointcloudColorDisplay: viewer.bindings.setPointcloudColorDisplay }
    preview.bindings.pointcloudControls.colorMode = mode
    await Vue.nextTick()
    const colors = models.map(model => model.geometry.getAttribute('color'))
    const materialVersions = models.map(model => model.material.version)
    const indices = models.map(model => model.geometry.index)
    const sync = Vue.watchEffect(() => {
      viewer.props.pointcloudTilesetUrl = preview.bindings.pointcloudSourceUrl.value
      viewer.props.pointcloudTablePlane = preview.bindings.pointcloudTablePlane.value
      viewer.props.pointcloudTableVisible = preview.bindings.pointcloudTableShown.value
    })
    for (let i = 0; i < 10; i++) {
      preview.bindings.pointcloudTableVisible.value = true
      await Vue.nextTick()
      models.forEach(model => assert.equal(model.geometry.index, null))
      preview.bindings.pointcloudTableVisible.value = false
      await Vue.nextTick()
      models.forEach((model, index) => {
        assert.equal(model.geometry.index, indices[index])
        assert.deepEqual(Array.from(model.geometry.index.array), [1])
        assert.equal(model.geometry.getAttribute('color'), colors[index])
        assert.equal(model.material.version, materialVersions[index], 'visibility does not recompile materials')
      })
      assert.equal(JSON.stringify(viewer.bindings.getCameraPose()), pose)
      assert.equal(viewer.bindings.loaded.value, true)
      assert.equal(viewer.bindings.tileset, tileset)
    }
    sync()
    assert.equal(requests, 2, 'no resource requests after initial metadata loading')
    assert.equal(disposals, 0)
    assert.deepEqual(viewer.events, [])
  } finally {
    preview.stop()
    viewer.stop()
  }
})

test('upload preview has only table/non-table colors, restores source appearance and recolors when the plane changes', async () => {
  const plane = { origin: [0, 0, 0], slopes: [0, 0], clearanceM: .005 }
  const viewer = setupComponent('../src/components/preview/UnifiedViewer3D.vue', {
    assetId: 5, type: 'pointcloud', pointcloudTablePlane: plane, pointcloudTableVisible: true,
  })
  try {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, .2, .1], 3))
    const sourceColors = [.1, .2, .3, .4, .5, .6]
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(sourceColors, 3))
    const points = new THREE.Points(geometry, new THREE.PointsMaterial())
    viewer.bindings.tileset = { forEachLoadedModel: fn => fn(points) }
    viewer.bindings.setPointcloudColorDisplay('table-class', 'grayscale', { min: 0, max: 1 })
    const colors = geometry.getAttribute('color')
    const expected = Object.values(tableVisibility.POINTCLOUD_CATEGORY_COLORS).flatMap(color => new THREE.Color(color).toArray())
    assert.deepEqual(Array.from(colors.array), Array.from(new Float32Array(expected)))
    assert.equal(geometry.index, null, 'both classes can be shown on initial load')
    viewer.bindings.setPointcloudColorDisplay('rgb', 'grayscale', { min: 0, max: 1 })
    assert.deepEqual(Array.from(geometry.getAttribute('color').array), Array.from(new Float32Array(sourceColors)))
    viewer.bindings.setPointcloudColorDisplay('table-class', 'grayscale', { min: 0, max: 1 })
    assert.equal(geometry.getAttribute('color'), colors, 'mode switches reuse category colors')
    viewer.props.pointcloudTablePlane = { ...plane, clearanceM: .2 }
    await Vue.nextTick()
    assert.deepEqual(Array.from(geometry.getAttribute('color').array), Array.from(new Float32Array([...expected.slice(0, 3), ...expected.slice(0, 3)])))
    viewer.props.pointcloudTablePlane = null
    await Vue.nextTick()
    assert.deepEqual(Array.from(geometry.getAttribute('color').array), Array.from(new Float32Array(sourceColors)))
  } finally {
    viewer.stop()
  }
})

test('category colors never become source intensity or RGB when the upload has no colors', () => {
  const viewer = setupComponent('../src/components/preview/UnifiedViewer3D.vue', {
    assetId: 5, type: 'pointcloud', pointcloudTablePlane: { origin: [0, 0, 0], slopes: [0, 0], clearanceM: .005 },
  })
  try {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, .2, .1], 3))
    const points = new THREE.Points(geometry, new THREE.PointsMaterial())
    viewer.bindings.tileset = { forEachLoadedModel: fn => fn(points) }
    viewer.bindings.setPointcloudColorDisplay('table-class', 'grayscale', { min: 0, max: 1 })
    const source = viewer.bindings.getPointScalarSource(geometry)
    assert.equal(source.valueAt(0), 0)
    assert.equal(source.valueAt(1), geometry.getAttribute('position').getY(1))
    viewer.bindings.collectPointcloudColorStats(points)
    assert.equal(viewer.events.at(-1)[1].hasRgb, false)
    viewer.bindings.setPointcloudColorDisplay('rgb', 'grayscale', { min: 0, max: 1 })
    assert.equal(geometry.getAttribute('color'), undefined)
    assert.equal(points.material.vertexColors, false)
  } finally {
    viewer.stop()
  }
})

test('missing table results use true color; asynchronous appearance loading does not override two-color mode', async () => {
  const preview = setupComponent('../src/views/preview/AssetPreviewView.vue', { assetId: 5, previewType: 'pointcloud' }, {
    '@/api/backend-file': { getAssetDetail: async () => ({ data: { pointcloudColor: '#ffffff' } }) },
  })
  try {
    assert.equal(preview.bindings.pointcloudDisplayColorMode.value, 'rgb')
    await preview.bindings.loadPointcloudAppearance()
    preview.bindings.pointcloudPreprocess.value = { result: { plane: { origin: [0, 0, 0], slopes: [0, 0], clearanceM: .005 } } }
    assert.equal(preview.bindings.pointcloudDisplayColorMode.value, 'table-class')
    preview.bindings.pointcloudControls.colorMode = 'intensity'
    await preview.bindings.loadPointcloudAppearance()
    assert.equal(preview.bindings.pointcloudDisplayColorMode.value, 'intensity')
  } finally {
    preview.stop()
  }
})
