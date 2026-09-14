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

const cylinderLoader = new Function(`${functionSource('cylinderPreviewUrl')}\n${functionSource('loadCylinderDenoisePreview')}\nreturn loadCylinderDenoisePreview`)()

test('cylinder denoise is a visible step 07 and runs through step 8 with topology', () => {
  assert.match(page, /id="cylinderDenoiseStep" data-step="cylinderDenoise"[^>]*>\s*<span class="num">07<\/span>/)
  assert.match(page, /<option value="8" selected>07 圆柱先验拟合去噪<\/option>/)
  assert.match(viewer, /else \$\('throughStep'\)\.value = '8'/)
  assert.match(viewer, /step === 'cylinderDenoise' \? '07 · 圆柱先验拟合去噪'/)
})

test('cylinder preview requires source-aligned keep/remove masks and complete-instance identity', () => {
  assert.match(viewer, /\['cylinder_keep', 'cylinder_removed'\]/)
  assert.match(viewer, /keep\.length !== count \|\| removed\.length !== count/)
  assert.match(viewer, /const instances = current\._complete\.complete_instance/)
  assert.match(viewer, /const before = classes\[index\] === 3 && report/)
  assert.match(viewer, /before && keep\[index\] === 1/)
  assert.match(viewer, /before && removed\[index\] === 1/)
})

test('retained rows with null fit fields load, while only applied rows require a fitted axis', async () => {
  const applied = { id: 2, pointsBefore: 3, pointsAfter: 2, removedPointCount: 1, status: 'applied', reason: 'radial_outlier', diameterM: .02, expectedLengthM: 1, fitRmseM: .001, radiusM: .01, centerlineM: [[0, 0, 0], [0, 0, 1]] }
  const retained = { id: 1, pointsBefore: 2, pointsAfter: 2, removedPointCount: 0, status: 'retained', reason: 'bent_or_hook_protected', diameterM: .02, expectedLengthM: 1, fitRmseM: null, radiusM: null, centerlineM: null }
  const manifest = { completeRebar: {}, preview: { pointCount: 3, cylinder_keepUrl: 'keep', cylinder_removedUrl: 'removed' }, cylinderDenoise: { instances: [retained, applied], validation: { status: 'applied' } } }
  const bytes = { keep: Uint8Array.from([1, 1, 1]).buffer, removed: Uint8Array.from([0, 0, 1]).buffer }
  const loaded = await cylinderLoader(manifest, async (url) => bytes[url])
  assert.equal(loaded.instanceById.get(1).centerlineM, null)
  assert.equal(loaded.instanceById.get(2).radiusM, .01)
  assert.match(viewer, /if \(!Array\.isArray\(item\.centerlineM\) \|\| !Number\.isFinite\(item\.radiusM\) \|\| item\.radiusM <= 0\) continue/)
})

test('instance navigation, selected fit framing, and evidence-based validation remain available', () => {
  assert.match(page, /id="cylinderPrevious"/)
  assert.match(page, /id="cylinderNext"/)
  assert.match(viewer, /rightScene === 'cylinderDenoise' && \$\('cylinderInstanceFilter'\)\.value !== 'all'/)
  assert.match(viewer, /new THREE\.CylinderGeometry\(item\.radiusM, item\.radiusM, length/)
  assert.match(viewer, /new THREE\.Color\(\)\.setRGB\(red, green, blue\)/)
  assert.match(viewer, /数量核对不等同于圆柱拟合有效/)
  assert.match(viewer, /\$\('semanticFilters'\)\.hidden = step === 'cylinderDenoise'/)
  assert.match(page, /显示已知直径 \/ 扫描拟合轴线/)
})

test('step 07 keeps shared coloring visible and applies layer/instance palettes without changing point selection', async () => {
  const THREE = await import('three')
  const availability = new Function(`${functionSource('resultColorModeAvailable')}\nreturn resultColorModeAvailable`)()
  const manifest = {
    preview: { pointCount: 3 },
    completeRebar: { instances: [{ id: 7, type: 1 }, { id: 19, type: 2 }] },
    _complete: { complete_instance: new Uint32Array([7, 19, 7]), complete_class: new Uint8Array([3, 3, 3]) },
    _refinedZones: new Uint8Array([1, 1, 1]),
    _cylinderDenoise: { keep: new Uint8Array([1, 1, 0]), removed: new Uint8Array([0, 0, 1]), instanceById: new Map([[7, {}], [19, {}]]) },
  }
  for (const mode of ['categories', 'layers', 'instances']) assert.equal(availability(mode, 'cylinderDenoise', manifest), true)
  // The color selector remains outside the independently hidden filter group.
  assert.ok(page.indexOf('id="resultColorMode"') < page.indexOf('id="semanticFilters"'))
  assert.match(viewer, /\$\('semanticControls'\)\.hidden = false/)
  const elements = { resultColorMode: { value: 'layers' }, cylinderCompare: { value: 'after' }, cylinderInstanceFilter: { value: 'all' }, cylinderHint: {} }
  const geometry = new THREE.BufferGeometry()
  const palette = Array.from({length: 12}, (_, code) => [code / 12, .25, .5])
  const byInstance = (id, step) => { assert.equal(step, 'completeRebar'); return id === 7 ? [.1, .2, .3] : [.7, .8, .9] }
  const apply = new Function('THREE', 'current', '$', 'cylinderDenoiseGeometry', 'semanticPalette', 'categoryPalette', 'rebarInstanceColor', 'rebuildCylinderFitOverlay', 'requestRender', 'fmt', 'fmtHeight', 'cylinderReason',
    ['stepSemanticData', 'semanticColors', 'categoryColors', 'resultDisplayColors', 'applyCylinderDenoiseAppearance'].map(functionSource).join('\n') + '\nreturn applyCylinderDenoiseAppearance')(
      THREE, manifest, id => elements[id], geometry, palette, palette.map(() => [.2, .8, .6]), byInstance, () => {}, () => {}, String, String, String)
  apply()
  assert.deepEqual([...geometry.index.array], [0, 1])
  const layerColors = [...geometry.attributes.color.array]
  assert.notDeepEqual(layerColors.slice(0, 3), layerColors.slice(3, 6))
  elements.resultColorMode.value = 'instances'; apply()
  assert.deepEqual([...geometry.index.array], [0, 1])
  assert.notDeepEqual([...geometry.attributes.color.array], layerColors)
  const originalColor = [...geometry.attributes.color.array].slice(0, 3)
  elements.cylinderInstanceFilter.value = '7'; apply()
  assert.deepEqual([...geometry.index.array], [0])
  assert.deepEqual([...geometry.attributes.color.array].slice(0, 3), originalColor)
  elements.cylinderCompare.value = 'removed'; elements.resultColorMode.value = 'categories'; apply()
  assert.deepEqual([...geometry.index.array], [2])
  assert.ok(Math.abs(geometry.attributes.color.array[6] - .94) < 1e-6)
  geometry.dispose()
})

test('retained short bar still renders one model with the exact design length; malformed length is rejected', async () => {
  const THREE = await import('three')
  const retained = { id: 14, pointsBefore: 2, pointsAfter: 2, removedPointCount: 0, status: 'retained', reason: 'observed_span_exceeds_dimension',
    fitStatus: 'fitted', radiusM: .004, fitRmseM: .0002, expectedLengthM: .28, fittedLengthM: .28,
    centerlineM: [[4, -2, .05], [4, -1.86, .05], [4, -1.72, .05]] }
  const manifest = { completeRebar: {}, preview: { pointCount: 2, origin: [3, -2, 0], cylinder_keepUrl: 'keep', cylinder_removedUrl: 'removed' },
    cylinderDenoise: { version: 'instance-cylinder-denoise-v2', instances: [retained], validation: { status: 'not_applied' } } }
  const bytes = { keep: Uint8Array.from([1, 1]).buffer, removed: Uint8Array.from([0, 0]).buffer }
  manifest._cylinderDenoise = await cylinderLoader(manifest, async url => bytes[url])
  const selected = { value: 'all', checked: true }
  const scene = new THREE.Scene()
  const rebuild = new Function('THREE', 'current', '$', 'cylinderDenoiseScene', 'rebarInstanceColor',
    `let cylinderFitOverlay = null;\n${functionSource('clearCylinderFitOverlay')}\n${functionSource('rebuildCylinderFitOverlay')}\nreturn () => { rebuildCylinderFitOverlay(); return cylinderFitOverlay; }`)(
      THREE, manifest, () => selected, scene, () => [.1, .2, .3])
  const group = rebuild()
  assert.equal(group.children.length, 1)
  assert.equal(group.children[0].userData.instanceId, 14)
  const meshes = group.children[0].children.filter(child => child.isMesh)
  assert.ok(Math.abs(meshes.reduce((sum, mesh) => sum + mesh.geometry.parameters.height, 0) - .28) < 1e-8)
  selected.value = '14'; assert.equal(rebuild().children.length, 1)
  selected.value = '49'; assert.equal(rebuild().children.length, 0)
  const broken = structuredClone({ ...manifest, _cylinderDenoise: undefined })
  broken.cylinderDenoise.instances[0].centerlineM[2][1] = -1.70
  await assert.rejects(cylinderLoader(broken, async url => bytes[url]), /长度与设计长度不一致/)
})

test('single-instance camera includes design-length ends outside the observed point span', async () => {
  const THREE = await import('three')
  const camera = new THREE.PerspectiveCamera(50, 1, .001, 100)
  camera.up.set(0, 1, 0)
  const controls = { target: new THREE.Vector3(), update() { camera.lookAt(this.target); camera.updateMatrixWorld(true) } }
  const overlay = new THREE.Group()
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(.004, .004, 1., 16), new THREE.MeshBasicMaterial())
  overlay.add(mesh)
  const geometry = new THREE.BufferGeometry(); geometry.setIndex([0])
  const current = { preview: { bounds: { min: [-.01, -.01, -.01], max: [.01, .01, .01] } }, _positions: new Float32Array([0, 0, 0]) }
  const fit = new Function('THREE', 'camera', 'controls', 'current', 'cylinderFitOverlay', 'cylinderDenoiseGeometry', '$',
    `let lastView; const rightScene = 'cylinderDenoise', compareSource = false; function resizeRenderer(){} function frameForCurrentStep(){ return null; } function requestRender(){}\n${functionSource('fit')}\nreturn fit`)(
      THREE, camera, controls, current, overlay, geometry, () => ({ value: '14' }))
  fit('top')
  for (const y of [-.5, .5]) {
    const projected = new THREE.Vector3(0, y, 0).project(camera)
    assert.ok(Math.abs(projected.y) < 1, 'both designed ends fit in view despite a tiny observed span')
  }
  mesh.geometry.dispose(); mesh.material.dispose(); geometry.dispose()
})

test('v3 renders the entire hook as one selectable instance and checks straight and total lengths separately', async () => {
  const THREE = await import('three')
  const row = { id: 47, pointsBefore: 2, pointsAfter: 2, removedPointCount: 0, status: 'applied', reason: 'radial_outlier',
    fitStatus: 'fitted', radiusM: .004, expectedLengthM: 1, fittedStraightLengthM: 1, expectedShapeLengthM: 1.2, fittedLengthM: 1.2,
    shapeKind: 'straight-with-bends', straightCenterlineM: [[0, 0, 0], [1, 0, 0]],
    centerlineM: [[0, 0, 0], [1, 0, 0], [1, .1, 0], [.9, .1, 0]] }
  const manifest = { completeRebar: {}, preview: { pointCount: 2, origin: [0, 0, 0], cylinder_keepUrl: 'keep', cylinder_removedUrl: 'removed' },
    cylinderDenoise: { version: 'instance-cylinder-denoise-v3', instances: [row], validation: { status: 'applied' } } }
  const bytes = { keep: Uint8Array.from([1, 1]).buffer, removed: Uint8Array.from([0, 0]).buffer }
  manifest._cylinderDenoise = await cylinderLoader(manifest, async url => bytes[url])
  const selected = { value: 'all', checked: true }, scene = new THREE.Scene()
  const rebuild = new Function('THREE', 'current', '$', 'cylinderDenoiseScene', 'rebarInstanceColor',
    `let cylinderFitOverlay = null;\n${functionSource('clearCylinderFitOverlay')}\n${functionSource('rebuildCylinderFitOverlay')}\nreturn () => { rebuildCylinderFitOverlay(); return cylinderFitOverlay; }`)(THREE, manifest, () => selected, scene, () => [.1, .2, .3])
  const group = rebuild()
  assert.equal(group.children.length, 1)
  const segments = group.children[0].children.filter(child => child.geometry?.type === 'CylinderGeometry')
  assert.equal(segments.length, 3)
  assert.ok(Math.abs(segments.reduce((sum, child) => sum + child.geometry.parameters.height, 0) - 1.2) < 1e-8)
  const box = new THREE.Box3().setFromObject(group)
  assert.ok(box.max.y > .1, 'hook is present beyond the straight cylinder bounds')
  selected.value = '47'; assert.equal(rebuild().children.length, 1)
  selected.value = '49'; assert.equal(rebuild().children.length, 0)
  const bad = structuredClone({ ...manifest, _cylinderDenoise: undefined })
  bad.cylinderDenoise.instances[0].straightCenterlineM[1][0] = .9
  await assert.rejects(cylinderLoader(bad, async url => bytes[url]), /长度与设计长度不一致/)
})
