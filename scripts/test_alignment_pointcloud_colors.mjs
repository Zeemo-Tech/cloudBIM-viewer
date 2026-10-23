import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { PNTSLoader } from '3d-tiles-renderer'
import { selectPointcloudRepresentation } from '@cloudbim/viewer-core'

// Execute the analysis page's real load-model -> appearance path. Only WebGL
// mounting and API transport are replaced; geometry and materials are real.
const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8')
  .split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
const functions = [
  'loadPointcloudFromApi', 'normalizePointcloudColor', 'pointcloudAttribute', 'pointcloudScalarSource',
  'samplePointcloudColorRamp', 'collectPointcloudColorStats', 'restorePointcloudOriginalColors',
  'applyPointcloudIntensityColoring', 'applyPointcloudColor', 'applyPointcloudDisplay',
  'applyPointcloudMaterialMode', 'normalizePointMaterial', 'getMaterialClone',
  'getOrCreatePointcloudUnlitMaterial', 'applyPointcloudMaterialAppearance', 'applySharedMaterialFlags',
  // Included when present so this harness also covers cached, detached LODs.
  'forEachLoadedPointcloudModel', 'setPointcloudColorMode',
]
const variables = [
  'pointcloudColor', 'persistedPointcloudColor', 'pointcloudColorOverridden',
  'pointcloudColorMode', 'pointcloudColorRamp', 'pointcloudColorRange', 'pointcloudIntensityHistogram',
  'originalPointcloudColors', 'originalMaterialStore', 'pointcloudUnlitMaterialCache',
  'materialMode', 'pointcloudPointSize', 'tilesErrorTarget',
]
const nodes = ast.statements.filter(node =>
  ts.isFunctionDeclaration(node) ? functions.includes(node.name?.text) :
    ts.isVariableStatement(node) && node.declarationList.declarations.some(item => variables.includes(item.name.getText(ast))),
)
const compiled = ts.transpileModule(nodes.map(node => node.getText(ast)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const ref = value => ({ value })
const noop = () => {}

function fixture(savedColor = '') {
  const models = []
  class TilesRenderer extends THREE.EventDispatcher {
    group = new THREE.Group()
    manager = new THREE.LoadingManager()
    downloadQueue = {}
    parseQueue = {}
    setCamera() {}
    forEachLoadedModel(callback) { models.forEach(callback) }
  }
  const state = {
    THREE, ref, console, scene: new THREE.Scene(), contentGroup: new THREE.Group(), engineeringRoot: null,
    renderer: {}, rendererMode: 'webgl', activeCamera: new THREE.PerspectiveCamera(),
    controls: {}, initScene: async () => {}, pointcloudLoadToken: 0,
    loadingPointcloud: ref(false), pointcloudLoaded: ref(false), pointcloudVisible: ref(true),
    pointcloudPreprocessRequired: ref(false), pointcloudPreprocessError: ref(''), pointcloudPreprocessResult: ref(null),
    sceneAlignmentReady: ref(false), statusText: ref(''), props: { pointcloudAssetId: 14 },
    tileset: null, pointcloudWrapper: null, pointcloudGroup: null,
    getAssetDetail: async () => ({ data: { type: 'pointcloud', status: 'ready', pointcloudColor: savedColor } }),
    getAssetRepresentations: async () => ({ data: { list: [{ kind: 'table-free', format: '3d-tiles', status: 'ready', version: 'v1', url: '/table-free/tileset.json' }] } }),
    getPointcloudPreprocess: async () => ({ data: { version: 'v1' } }), selectPointcloudRepresentation,
    getPointcloudTilesetUrl: url => url, createUploadHeaders: () => ({}), TilesRenderer,
    DRACOLoader: class { setDecoderPath() {} preload() {} }, GLTFExtensionsPlugin: class {},
    updateTilesetResolution: noop, clearDenoisePreview: noop, ensureInitialTransformState: noop,
    recenterLoadedContentAsWhole: noop, applySceneVisibility: noop, updateClipRangeFromContent: noop,
    applyClippingState: noop, requestRender: noop,
    clearPointcloudColorSaveTimer: noop,
    ElMessage: { error: message => assert.fail(message) },
  }
  TilesRenderer.prototype.registerPlugin = noop
  const available = functions.filter(name => nodes.some(node => ts.isFunctionDeclaration(node) && node.name?.text === name))
  const api = runInNewContext(`${compiled}\n({ ${[...available, ...variables].join(', ')} })`, state)
  function tile() {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 2, 3], 3))
    geometry.setAttribute('color', new THREE.Uint8BufferAttribute([255, 32, 8, 16, 64, 255], 3, true))
    geometry.setAttribute('intensity', new THREE.Float32BufferAttribute([10, 90], 1))
    const points = new THREE.Points(geometry, new THREE.PointsMaterial({ vertexColors: true }))
    models.push(points)
    return points
  }
  return { api, state, models, tile }
}

test('analysis load with no saved single color shows source RGB on its first tile', async () => {
  const { api, state, tile } = fixture()
  await api.loadPointcloudFromApi(14, false)
  const points = tile()
  state.tileset.group.add(points)
  state.tileset.dispatchEvent({ type: 'load-model', scene: points })
  assert.equal(points.material.vertexColors, true, 'an empty appearance must not install a gray override')
  assert.equal(points.material.color.getHex(), 0xffffff)
})

test('loading a new tile styles that tile without revisiting all previous tiles', async () => {
  const { api, state, tile } = fixture('#123456')
  await api.loadPointcloudFromApi(14, false)
  const first = tile()
  state.tileset.group.add(first)
  state.tileset.dispatchEvent({ type: 'load-model', scene: first })
  const version = first.material.version
  let previousTileVisits = 0
  const traverse = first.traverse.bind(first)
  first.traverse = callback => { previousTileVisits++; traverse(callback) }
  for (let i = 0; i < 40; i++) {
    const points = tile()
    state.tileset.group.add(points)
    state.tileset.dispatchEvent({ type: 'load-model', scene: points })
    assert.equal(points.material.color.getHex(), 0x123456, 'saved explicit color still applies to incoming tiles')
  }
  assert.equal(previousTileVisits, 0, 'each arriving LOD must not restyle the entire loaded cloud')
  assert.equal(first.material.version, version, 'an existing material must not recompile for every incoming tile')
})

test('RGB/intensity switches restore original colors in active and detached cached LODs', async () => {
  const { api, state, tile } = fixture()
  await api.loadPointcloudFromApi(14, false)
  const points = [tile(), tile()]
  state.tileset.group.add(points[0])
  const originals = points.map(item => item.geometry.getAttribute('color'))
  for (const item of points) state.tileset.dispatchEvent({ type: 'load-model', scene: item })
  for (const mode of ['intensity', 'rgb', 'intensity', 'rgb']) {
    api.setPointcloudColorMode(mode)
    points.forEach((item, index) => {
      assert.equal(item.material.vertexColors, true)
      if (mode === 'rgb') assert.equal(item.geometry.getAttribute('color'), originals[index], 'restore the source attribute without copying it')
      else assert.notEqual(item.geometry.getAttribute('color'), originals[index], 'the detached LOD also receives the chosen mode')
    })
  }
})

test('selecting true color clears an explicit tint, including the original material mode', async () => {
  const { api, state, tile } = fixture('#123456')
  await api.loadPointcloudFromApi(14, false)
  api.materialMode.value = 'original'
  const points = tile()
  state.tileset.group.add(points)
  const original = points.geometry.getAttribute('color')
  state.tileset.dispatchEvent({ type: 'load-model', scene: points })
  assert.equal(points.material.vertexColors, false)
  api.setPointcloudColorMode('rgb')
  assert.equal(api.pointcloudColorOverridden.value, false)
  assert.equal(points.material.vertexColors, true)
  assert.equal(points.material.color.getHex(), 0xffffff)
  assert.equal(points.geometry.getAttribute('color'), original)
})

test('points without RGB stay uncolored when switching display modes', async () => {
  const { api, state, tile } = fixture()
  await api.loadPointcloudFromApi(14, false)
  const points = tile()
  points.geometry.deleteAttribute('color')
  points.geometry.deleteAttribute('intensity')
  state.tileset.group.add(points)
  state.tileset.dispatchEvent({ type: 'load-model', scene: points })
  for (const mode of ['intensity', 'rgb']) {
    api.setPointcloudColorMode(mode)
    assert.equal(points.geometry.getAttribute('color'), undefined)
    assert.equal(points.material.vertexColors, false)
  }
})

test('real analysis PNTS retains RGB through loading and display switches', { skip: !process.env.CLOUDBIM_RGB_TILE }, async () => {
  const bytes = readFileSync(process.env.CLOUDBIM_RGB_TILE)
  const result = await new PNTSLoader().parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
  const points = result.scene
  const original = points.geometry.getAttribute('color')
  assert.ok(original?.count > 0, 'the fixture must contain measured RGB')
  assert.ok(Array.from({ length: original.count }, (_, i) => Math.abs(original.getX(i) - original.getY(i)) + Math.abs(original.getY(i) - original.getZ(i))).some(value => value > .01), 'the fixture must contain chromatic colors')
  const { api, state, models } = fixture()
  await api.loadPointcloudFromApi(14, false)
  models.push(points)
  state.tileset.group.add(points)
  state.tileset.dispatchEvent({ type: 'load-model', scene: points })
  for (const mode of ['rgb', 'intensity', 'rgb']) {
    api.setPointcloudColorMode(mode)
    assert.equal(points.material.vertexColors, true)
    if (mode === 'rgb') {
      assert.equal(points.geometry.getAttribute('color'), original)
      assert.equal(points.material.color.getHex(), 0xffffff)
    }
  }
})
