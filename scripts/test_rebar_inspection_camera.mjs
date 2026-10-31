import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { NavigationOrbitControls as OrbitControls } from '../packages/viewer-core/src/viewer/NavigationOrbitControls.ts'
import { orderRebarInspection } from '../packages/alignment/src/rebarInspectionOrder.ts'
import { isRebarInspectionAbnormal } from '../packages/alignment/src/rebarComparison.ts'
import { appendInspectionMeshPoints, inspectionCameraFrame } from '../packages/alignment/src/rebarInspectionCamera.ts'

// Execute the page's real navigation and animation with the installed controls.
// Stubbing controls.update would miss the mismatch between its orbit axis and up.
const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8')
  .split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
const names = [
  'stopRebarCameraAnimation', 'focusComparisonBar', 'selectInspectionBar', 'stepRebarInspection',
  'resetRebarInspection', 'exitRebarInspection', 'setPresetView', 'orbitPointcloudFromCube',
  'isPerspectiveCamera', 'isOrthographicCamera', 'updateOrthographicFrustum',
  'setPointcloudViewDirection', 'setProjectionMode', 'mountControls', 'rollPointcloudView', 'fitCameraToBox',
  'lockRebarInspectionOrder', 'selectInspectionBarById', 'onInspectionFilterChange',
  'stopRebarAutoplay', 'scheduleRebarInspection', 'toggleRebarAutoplay', 'interruptRebarInspection', 'cancelInspectionPointerGesture',
]
const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(functions.length, names.length)
const computedNames = ['rebarInspectionOrderedBars', 'rebarInspectionBars', 'rebarInspectionIndex']
const computedDeclarations = ast.statements.filter(node => ts.isVariableStatement(node) &&
  node.declarationList.declarations.some(d => computedNames.includes(d.name.getText(ast))))
assert.equal(computedDeclarations.length, computedNames.length)
const compiled = ts.transpileModule([...functions, ...computedDeclarations].map(node => node.getText(ast)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const ref = value => ({ value })
const noop = () => {}
const worldUp = new THREE.Vector3(0, 1, 0)
const boxPoints = bounds => Array.from({ length: 8 }, (_, i) => new THREE.Vector3(
  i & 1 ? bounds.max.x : bounds.min.x, i & 2 ? bounds.max.y : bounds.min.y, i & 4 ? bounds.max.z : bounds.min.z))

function fixture(orthographic = false) {
  const perspectiveCamera = new THREE.PerspectiveCamera(50, 1.6, .01, 5000)
  const orthographicCamera = new THREE.OrthographicCamera(-8, 8, 5, -5, .01, 5000)
  const camera = orthographic ? orthographicCamera : perspectiveCamera
  camera.position.set(3, 4, 7)
  const document = { addEventListener: noop, removeEventListener: noop }
  const domElement = { style: {}, addEventListener: noop, removeEventListener: noop, getRootNode: () => document }
  const controls = new OrbitControls(camera, domElement)
  controls.enableDamping = false
  // Asymmetric boxes in viewer world space, matching comparisonBarBounds output.
  const bars = [
    { ifcGlobalId: 'a', bounds: new THREE.Box3(new THREE.Vector3(-2, .3, -1), new THREE.Vector3(1, .5, -.9)) },
    { ifcGlobalId: 'b', bounds: new THREE.Box3(new THREE.Vector3(.7, .6, -1.7), new THREE.Vector3(.8, .8, .4)) },
    { ifcGlobalId: 'c', bounds: new THREE.Box3(new THREE.Vector3(-.8, .9, .3), new THREE.Vector3(-.5, 1.2, .8)) },
  ]
  let now = 0, frameId = 0
  const frames = new Map(), timers = new Map()
  const timerDelays = []
  const s = {
    THREE, OrbitControls, orderRebarInspection, isRebarInspectionAbnormal, inspectionCameraFrame,
    computed: callback => ({ get value() { return callback() } }),
    activeCamera: camera, controls, orthoViewSize: 5, activeView: ref(''),
    perspectiveCamera, orthographicCamera, projectionMode: ref(orthographic ? 'orthographic' : 'perspective'),
    renderer: { domElement }, scene: null, tileset: null, syncBoundsHelpers: noop, transformHandleInteraction: null,
    viewportEl: ref({ clientWidth: 1600, clientHeight: 1000, getBoundingClientRect: () => ({ width: 1600, height: 1000 }) }),
    comparisonBars: ref(bars), rebarInspectionOrderIds: ref([]), c2mToleranceMm: ref(10), selectedComparisonBarId: ref(''),
    rebarInspectionActive: ref(false), rebarInspectionAutoplay: ref(false), rebarInspectionTimer: null, rebarInspectionClickReady: false,
    comparison: ref({ bars }), c2mSceneLoaded: ref(true), activeWorkflowStep: ref(3), document: { hidden: false },
    analysisMode: ref('none'), cancelActiveAnalysis: noop,
    rebarInspectionAbnormalOnly: ref(false), rebarCameraAnimationFrame: null, rebarInspectionClick: { cancel: noop },
    rebarCameraControlsWasEnabled: null, performance: { now: () => now },
    window: {
      requestAnimationFrame: cb => { frames.set(++frameId, cb); return frameId },
      cancelAnimationFrame: id => frames.delete(id),
      setTimeout: (cb, delay) => { timerDelays.push(delay); timers.set(++frameId, cb); return frameId },
      clearTimeout: id => timers.delete(id),
    },
    comparisonBarBounds: bar => bar.bounds.clone(),
    comparisonBarPoints: bar => boxPoints(bar.bounds),
    reportLayout: ref({ members: new Map() }),
    getVisibleContentBox: () => bars.reduce((box, bar) => box.union(bar.bounds), new THREE.Box3()),
    nextTick: cb => cb(), applyComparisonSelection: noop, requestRender: noop,
    syncPointcloudCameraPose: noop,
    ElMessage: { info: noop, warning: message => assert.fail(message) },
  }
  const api = runInNewContext(`${compiled}\n({ ${[...names, ...computedNames].join(', ')} })`, s)
  function advance(ms) {
    now += ms
    const pending = [...frames.values()]
    frames.clear()
    pending.forEach(cb => cb(now))
  }
  const tickTour = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(cb => cb()) }
  return { api, s, bars, advance, pending: () => frames.size, tickTour, timers, timerDelays }
}

test('autoplay cycles the spatial route, wraps, and exit clears all pending work', () => {
  const { api, s, advance, tickTour, timers, pending, timerDelays } = fixture()
  api.toggleRebarAutoplay()
  const first = s.selectedComparisonBarId.value
  assert.equal(s.rebarInspectionAutoplay.value, true)
  assert.equal(timers.size, 1)
  advance(900); tickTour(); advance(900)
  assert.notEqual(s.selectedComparisonBarId.value, first)
  tickTour(); advance(900); tickTour(); advance(900)
  assert.equal(s.selectedComparisonBarId.value, first)
  assert.deepEqual(timerDelays, [3000, 3000, 3000, 3000])
  api.exitRebarInspection()
  assert.equal(timers.size, 0); assert.equal(pending(), 0)
  assert.equal(s.controls.enabled, true)
})

test('manual navigation and hidden documents pause autoplay', () => {
  for (const reason of ['interrupt', 'hidden', 'step', 'preset']) {
    const { api, s, tickTour, timers } = fixture()
    api.toggleRebarAutoplay()
    if (reason === 'interrupt') api.interruptRebarInspection()
    if (reason === 'hidden') { s.document.hidden = true; tickTour() }
    if (reason === 'step') { s.activeWorkflowStep.value = 2; tickTour() }
    if (reason === 'preset') api.setPresetView('top')
    assert.equal(s.rebarInspectionAutoplay.value, false)
    assert.equal(timers.size, 0)
  }
})

function assertUpright(camera, target) {
  camera.updateMatrixWorld(true)
  // Engineering +Z is viewer +Y. Its screen projection must stay vertical and up.
  const origin = target.clone().project(camera)
  const above = target.clone().addScaledVector(worldUp, .01).project(camera)
  assert.ok(Math.abs(above.x - origin.x) < 1e-8, `engineering Z tilted sideways: ${above.x - origin.x}`)
  assert.ok(above.y > origin.y, 'engineering +Z must project upward')
}

function assertFrame(camera, target, axis, points, landscape) {
  camera.updateMatrixWorld(true)
  const origin = target.clone().project(camera), along = target.clone().addScaledVector(axis, .1).project(camera)
  const dx = along.x - origin.x, dy = along.y - origin.y
  if (landscape !== null) {
    assert.ok(Math.abs(landscape ? dy : dx) < 1e-7, `axis is not aligned: ${dx}, ${dy}`)
    assert.ok(Math.abs(landscape ? dx : dy) > 1e-6, 'axis must not point into the camera')
  }
  for (const point of points) {
    const p = point.clone().project(camera)
    assert.ok(Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && Math.abs(p.z) < 1, `design vertex clipped: ${p.toArray()}`)
  }
}

test('design vertex extraction respects ownership and the full metre-scale engineering-to-viewer transform', () => {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([-8, .2, .3, -2, -.01, -.02, 2, .01, .02, 9, -.3, -.4], 3))
  const mesh = new THREE.Mesh(geometry)
  mesh.scale.set(1.5, 2, 3)
  mesh.rotation.z = Math.PI / 2
  const pivot = new THREE.Group()
  pivot.rotation.x = -Math.PI / 2
  pivot.position.set(17, -29, 43)
  pivot.add(mesh)
  const before = geometry.attributes.position.array.slice(), points = []
  appendInspectionMeshPoints(mesh, points, 1, 2)
  assert.equal(points.length, 2)
  // Scale, then Z rotation, then Z-up -> Y-up rotation, then translation.
  assert.ok(points[0].distanceTo(new THREE.Vector3(17.02, -29.06, 46)) < 1e-7)
  assert.ok(points[1].distanceTo(new THREE.Vector3(16.98, -28.94, 40)) < 1e-7)
  assert.deepEqual(geometry.attributes.position.array, before)
})

test('level framing preserves engineering up and fits members in landscape and portrait projections', () => {
  for (const axis of [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
    new THREE.Vector3(2, 5, -3).normalize(), new THREE.Vector3(1, 0, 1).normalize()]) {
    const rotation = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(1, 0, 0), axis)
    const points = boxPoints(new THREE.Box3(new THREE.Vector3(-2, -.012, -.025), new THREE.Vector3(2, .012, .025)))
      .map(p => p.applyQuaternion(rotation).add(new THREE.Vector3(17, -29, 43)))
    for (const aspect of [1.8, .65]) for (const ortho of [false, true]) {
      const frame = inspectionCameraFrame(points, new THREE.Box3().setFromPoints(points), aspect, 50, 1.1)
      const camera = ortho
        ? new THREE.OrthographicCamera(-frame.viewSize * aspect, frame.viewSize * aspect, frame.viewSize, -frame.viewSize, .01, 5000)
        : new THREE.PerspectiveCamera(50, aspect, .01, 5000)
      camera.position.copy(frame.position); camera.up.copy(frame.up); camera.lookAt(frame.target)
      assertUpright(camera, frame.target)
      assert.ok(camera.up.equals(worldUp))
      assertFrame(camera, frame.target, axis, points, axis.y === 0 ? true : axis.y === 1 ? false : null)
      const spans = ['x', 'y'].map(component => {
        const values = points.map(p => p.clone().project(camera)[component])
        return Math.max(...values) - Math.min(...values)
      })
      assert.ok(Math.max(...spans) > 1.5, 'fit the available viewport without rolling the member')
    }
  }
})

test('wide stirrups use a front elevation even when their longest extent is horizontal', () => {
  for (const aspect of [1.8, .65]) {
    const bounds = new THREE.Box3(new THREE.Vector3(-2, .7, -.01), new THREE.Vector3(2, 1.7, .01))
    const points = boxPoints(bounds)
    const frame = inspectionCameraFrame(points, bounds, aspect, 50, 1.1, { upright: true })
    const camera = new THREE.PerspectiveCamera(50, aspect, .01, 5000)
    camera.position.copy(frame.position); camera.up.copy(frame.up); camera.lookAt(frame.target)
    assert.equal(camera.position.y, frame.target.y, 'stirrup elevation has no top-down pitch')
    assertUpright(camera, frame.target)
    assertFrame(camera, frame.target, worldUp, points, false)
    // Asymmetric points on a front face retain a rectangular screen frame.
    camera.updateMatrixWorld(true)
    const a = new THREE.Vector3(-1, .8, .01).project(camera)
    const b = new THREE.Vector3(1.4, .8, .01).project(camera)
    const c = new THREE.Vector3(1.4, 1.6, .01).project(camera)
    assert.ok(Math.abs(a.y - b.y) < 1e-10)
    assert.ok(Math.abs(b.x - c.x) < 1e-10)
  }
})

for (const orthographic of [false, true]) {
  const mode = orthographic ? 'orthographic' : 'perspective'
  test(`${mode}: next/previous frames each complete member horizontally`, () => {
    const { api, s, bars, advance } = fixture(orthographic)
    for (const direction of [1, 1, 1, -1]) {
      api.stepRebarInspection(direction)
      assert.equal(s.controls.enabled, false)
      for (const ms of [200, 230, 430]) {
        advance(ms)
        assert.ok([...s.activeCamera.position.toArray(), ...s.activeCamera.up.toArray(), ...s.activeCamera.quaternion.toArray()].every(Number.isFinite))
        assert.ok(s.activeCamera.up.equals(worldUp))
        assertUpright(s.activeCamera, s.controls.target)
      }
      assert.equal(s.controls.enabled, true)
      const bar = bars.find(bar => bar.ifcGlobalId === s.selectedComparisonBarId.value)
      const center = bar.bounds.getCenter(new THREE.Vector3())
      assert.ok(s.controls.target.distanceTo(center) < 1e-10)
      const axis = bar.ifcGlobalId === 'a' ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1)
      assertFrame(s.activeCamera, center, axis, boxPoints(bar.bounds), true)
    }
  })

  test(`${mode}: inspection and subsequent orbit remain upright`, () => {
    const { api, s, advance } = fixture(orthographic)
    api.stepRebarInspection(1)
    advance(860)
    // Use the same angular input consumed by the real mouse-drag path.
    const expectedOffset = s.activeCamera.position.clone().sub(s.controls.target).applyAxisAngle(worldUp, -Math.PI / 3)
    s.controls._rotateLeft(Math.PI / 3)
    s.controls.update()
    assert.ok(s.activeCamera.position.clone().sub(s.controls.target).distanceTo(expectedOffset) < 1e-8)
    assertUpright(s.activeCamera, s.controls.target)
    api.orbitPointcloudFromCube({ lon: 35, lat: -10 })
    assertUpright(s.activeCamera, s.controls.target)
    for (const preset of ['front', 'side']) {
      api.setPresetView(preset)
      assertUpright(s.activeCamera, s.controls.target)
    }
  })

  test(`${mode}: manual and autoplay stirrup focus stay level throughout the transition`, () => {
    for (const manual of [true, false]) {
      const { api, s, bars, advance } = fixture(orthographic)
      bars[2].bounds.set(new THREE.Vector3(-.12, .3, -.02), new THREE.Vector3(.12, 2.5, .02))
      s.reportLayout.value.members.set('c', { chapterId: 'connector' })
      if (manual) api.focusComparisonBar(bars[2], { manual: true })
      else { api.selectInspectionBarById('c'); api.toggleRebarAutoplay() }
      for (const ms of [100, 200, 200, 360]) {
        advance(ms)
        assert.ok(s.activeCamera.up.equals(worldUp))
        assertUpright(s.activeCamera, s.controls.target)
      }
      assert.ok(Math.abs(s.activeCamera.position.y - s.controls.target.y) < 1e-10)
      assertFrame(s.activeCamera, s.controls.target, worldUp, boxPoints(bars[2].bounds), false)
    }
  })
}

for (const action of ['fit', 'top']) {
  test(`orthographic ${action} restores the full view after extreme zoom`, () => {
    const { api, s } = fixture(true)
    s.activeCamera.zoom = 1000
    s.activeCamera.updateProjectionMatrix()
    if (action === 'fit') api.fitCameraToBox(s.getVisibleContentBox())
    else api.setPresetView('top')
    assert.equal(s.activeCamera.zoom, 1)
  })
}

for (const orthographic of [false, true]) {
  test(`${orthographic ? 'orthographic' : 'perspective'}: focus resets an extreme zoom and fits the whole member`, () => {
    const { api, s, bars, advance } = fixture(orthographic)
    s.activeCamera.zoom = 1000
    s.activeCamera.updateProjectionMatrix()
    api.focusComparisonBar(bars[0], { manual: true }); advance(680)
    assert.equal(s.activeCamera.zoom, 1)
    assertFrame(s.activeCamera, s.controls.target, new THREE.Vector3(1, 0, 0), boxPoints(bars[0].bounds), true)
  })
}

test('rapid next/previous and exiting mid-animation release controls and pending frames', () => {
  const { api, s, advance, pending } = fixture()
  api.stepRebarInspection(1)
  advance(220)
  api.stepRebarInspection(1)
  advance(180)
  api.stepRebarInspection(-1)
  assert.equal(pending(), 1)
  advance(200)
  api.exitRebarInspection()
  assert.equal(pending(), 0)
  assert.equal(s.controls.enabled, true)
  assert.ok(s.activeCamera.up.toArray().every(Number.isFinite))
})

test('manual focus aligns the member after a rolled or top cube view without changing the orbit axis', () => {
  const { api, s, bars, advance } = fixture()
  s.activeCamera.up.set(0, 0, -1)
  api.focusComparisonBar(bars[1], { manual: true })
  advance(680)
  assertFrame(s.activeCamera, s.controls.target, new THREE.Vector3(0, 0, 1), boxPoints(bars[1].bounds), true)
  const expectedOffset = s.activeCamera.position.clone().sub(s.controls.target).applyAxisAngle(worldUp, -.7)
  s.controls._rotateLeft(.7)
  s.controls.update()
  assert.ok(s.activeCamera.position.clone().sub(s.controls.target).distanceTo(expectedOffset) < 1e-8)
})

test('manual member selection keeps the current camera and starts no flight', () => {
  const { api, s, pending } = fixture()
  const position = s.activeCamera.position.clone(), quaternion = s.activeCamera.quaternion.clone(), target = s.controls.target.clone()
  api.selectInspectionBarById('b')
  assert.equal(s.selectedComparisonBarId.value, 'b')
  assert.ok(s.activeCamera.position.equals(position))
  assert.ok(s.activeCamera.quaternion.equals(quaternion))
  assert.ok(s.controls.target.equals(target))
  assert.equal(pending(), 0)
  assert.equal(s.controls.enabled, true)
})

test('manual selection resumes the spatial route and locks it through geometry and tolerance changes', () => {
  const { api, s, bars } = fixture()
  assert.equal(api.selectInspectionBarById('b'), true)
  assert.deepEqual(Array.from(s.rebarInspectionOrderIds.value), ['c', 'b', 'a'])
  bars[0].bounds.translate(new THREE.Vector3(-20, 3, -20))
  s.c2mToleranceMm.value = 25
  api.stepRebarInspection(1)
  assert.equal(s.selectedComparisonBarId.value, 'a')
  api.stepRebarInspection(-1)
  assert.equal(s.selectedComparisonBarId.value, 'b')
  assert.deepEqual(Array.from(s.rebarInspectionOrderIds.value), ['c', 'b', 'a'])
  api.resetRebarInspection()
  assert.equal(s.rebarInspectionOrderIds.value.length, 0)
})

test('abnormal filtering preserves the selected bar or continues to the next in the same route', () => {
  const { api, s, bars } = fixture()
  bars.forEach(item => { item.status = 'matched'; item.stats = { p95Abs: item.ifcGlobalId === 'b' ? .001 : .02 } })
  api.selectInspectionBarById('c')
  s.rebarInspectionAbnormalOnly.value = true
  api.onInspectionFilterChange()
  assert.equal(s.selectedComparisonBarId.value, 'c')
  assert.deepEqual(Array.from(api.rebarInspectionBars.value, item => item.ifcGlobalId), ['c', 'a'])
  s.rebarInspectionAbnormalOnly.value = false
  api.onInspectionFilterChange()
  api.stepRebarInspection(1)
  assert.equal(s.selectedComparisonBarId.value, 'b')
  s.rebarInspectionAbnormalOnly.value = true
  api.onInspectionFilterChange()
  assert.equal(s.selectedComparisonBarId.value, 'a', 'filtering must not restart at c')
})

test('an empty abnormal filter cancels focus animation and releases controls', () => {
  const { api, s, bars, pending } = fixture()
  bars.forEach(item => { item.status = 'matched'; item.stats = { p95Abs: .001 } })
  api.stepRebarInspection(1)
  assert.equal(pending(), 1)
  s.rebarInspectionAbnormalOnly.value = true
  api.onInspectionFilterChange()
  assert.equal(pending(), 0)
  assert.equal(s.controls.enabled, true)
  assert.equal(s.selectedComparisonBarId.value, '')
})

test('manually picking a normal bar with abnormal-only enabled continues to its adjacent anomalies', () => {
  const { api, s, bars } = fixture()
  bars.forEach(item => { item.status = 'matched'; item.stats = { p95Abs: item.ifcGlobalId === 'b' ? .001 : .02 } })
  s.rebarInspectionAbnormalOnly.value = true
  api.selectInspectionBarById('b')
  api.stepRebarInspection(1)
  assert.equal(s.selectedComparisonBarId.value, 'a')
  api.selectInspectionBarById('b')
  api.stepRebarInspection(-1)
  assert.equal(s.selectedComparisonBarId.value, 'c')
})

for (const orthographic of [false, true]) {
  const mode = orthographic ? 'orthographic' : 'perspective'
  for (const pole of [1, -1]) {
    test(`${mode}: cube ${pole === 1 ? 'top' : 'bottom'} preserves engineering directions and subsequent orbit`, () => {
      const { api, s } = fixture(orthographic)
      api.setPointcloudViewDirection([0, pole, 0])
      assert.ok(s.activeCamera.up.distanceTo(worldUp) < 1e-10)
      s.activeCamera.updateMatrixWorld(true)
      const target = s.controls.target
      const origin = target.clone().project(s.activeCamera)
      // Non-symmetric engineering displacements: X -> +viewerX, Y -> -viewerZ.
      const pointX = target.clone().add(new THREE.Vector3(.13, 0, 0)).project(s.activeCamera)
      const pointY = target.clone().add(new THREE.Vector3(0, 0, -.27)).project(s.activeCamera)
      assert.ok(pointX.x > origin.x && Math.abs(pointX.y - origin.y) < 1e-8)
      assert.ok((pointY.y - origin.y) * pole > 0 && Math.abs(pointY.x - origin.x) < 1e-8)
      assert.ok(s.activeCamera.position.clone().sub(target).normalize().distanceTo(new THREE.Vector3(0, pole, 0)) < 2e-6)
      s.controls._rotateLeft(.6)
      s.controls._rotateUp(-pole * .45)
      s.controls.update()
      assertUpright(s.activeCamera, target)
      api.orbitPointcloudFromCube({ lon: 25, lat: -pole * 15 })
      assertUpright(s.activeCamera, target)
    })
  }
}

test('projection changes preserve intentional screen roll but never change the world orbit axis', () => {
  const { api, s } = fixture()
  api.setPointcloudViewDirection([0, 1, 0])
  api.orbitPointcloudFromCube({ lon: 25, lat: -35 })
  api.rollPointcloudView(1)
  for (const mode of ['orthographic', 'perspective', 'orthographic']) {
    const before = s.activeCamera.quaternion.clone()
    const position = s.activeCamera.position.clone(), target = s.controls.target.clone(), up = s.activeCamera.up.clone()
    api.setProjectionMode(mode)
    assert.ok(s.activeCamera.quaternion.angleTo(before) < 1e-7, 'projection switch changed screen orientation')
    assert.ok(s.activeCamera.up.distanceTo(up) < 1e-10)
    assert.ok(s.activeCamera.position.distanceTo(position) < 1e-8)
    assert.ok(s.controls.target.distanceTo(target) < 1e-8)
    const expectedOffset = position.clone().sub(target).applyAxisAngle(worldUp, -.4)
    s.controls._rotateLeft(.4)
    s.controls.update()
    assert.ok(s.activeCamera.position.clone().sub(target).distanceTo(expectedOffset) < 1e-8, 'orbit axis changed after projection switch')
  }
  api.setPresetView('front')
  assert.ok(s.activeCamera.up.distanceTo(worldUp) < 1e-10)
  assertUpright(s.activeCamera, s.controls.target)
  api.rollPointcloudView(-1)
  api.fitCameraToBox(s.getVisibleContentBox())
  assert.ok(s.activeCamera.up.distanceTo(worldUp) < 1e-10)
})

test('rebuilding controls near a nonzero pole target cannot introduce a 180 degree flip', () => {
  const { api, s } = fixture()
  s.activeCamera.position.set(0, 1000, 0)
  s.controls.target.set(0, 900, .0001)
  s.activeCamera.lookAt(s.controls.target)
  const position = s.activeCamera.position.clone(), quaternion = s.activeCamera.quaternion.clone()
  api.setProjectionMode('orthographic')
  assert.ok(s.activeCamera.position.distanceTo(position) < 1e-8)
  assert.ok(s.activeCamera.quaternion.angleTo(quaternion) < 1e-7)
})

for (const action of ['top', 'preset', 'fit', 'projection', 'orbit', 'roll']) {
  test(`${action} cancels inspection animation so it cannot overwrite the requested view`, () => {
    const { api, s, advance, pending } = fixture()
    api.stepRebarInspection(1)
    advance(200)
    if (action === 'top') api.setPointcloudViewDirection([0, 1, 0])
    if (action === 'preset') api.setPresetView('front')
    if (action === 'fit') api.fitCameraToBox(s.getVisibleContentBox())
    if (action === 'projection') api.setProjectionMode('orthographic')
    if (action === 'orbit') api.orbitPointcloudFromCube({ lon: 20, lat: -10 })
    if (action === 'roll') api.rollPointcloudView(1)
    assert.equal(pending(), 0)
    assert.equal(s.controls.enabled, true)
    const position = s.activeCamera.position.clone(), rotation = s.activeCamera.quaternion.clone()
    advance(1500)
    assert.ok(s.activeCamera.position.distanceTo(position) < 1e-10)
    assert.ok(s.activeCamera.quaternion.angleTo(rotation) < 1e-7)
  })
}
