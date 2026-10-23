import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

// Execute the page's real navigation and animation with the installed controls.
// Stubbing controls.update would miss the mismatch between its orbit axis and up.
const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8')
  .split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
const names = [
  'stopRebarCameraAnimation', 'focusComparisonBar', 'selectInspectionBar', 'stepRebarInspection',
  'resetRebarInspection', 'exitRebarInspection', 'setPresetView', 'orbitPointcloudFromCube',
  'isPerspectiveCamera', 'isOrthographicCamera',
  'setPointcloudViewDirection', 'setProjectionMode', 'mountControls', 'rollPointcloudView', 'fitCameraToBox',
]
const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(functions.length, names.length)
const compiled = ts.transpileModule(functions.map(node => node.getText(ast)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const ref = value => ({ value })
const noop = () => {}
const worldUp = new THREE.Vector3(0, 1, 0)

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
  const frames = new Map()
  const s = {
    THREE, OrbitControls, activeCamera: camera, controls, orthoViewSize: 5, activeView: ref(''),
    perspectiveCamera, orthographicCamera, projectionMode: ref(orthographic ? 'orthographic' : 'perspective'),
    renderer: { domElement }, scene: null, tileset: null, syncBoundsHelpers: noop,
    viewportEl: ref({ clientWidth: 1600, clientHeight: 1000 }),
    rebarInspectionBars: ref(bars), selectedComparisonBarId: ref(''),
    rebarInspectionActive: ref(false), rebarInspectionManualSelect: ref(false),
    rebarInspectionAbnormalOnly: ref(false), rebarCameraAnimationFrame: null,
    rebarCameraControlsWasEnabled: null, performance: { now: () => now },
    window: {
      requestAnimationFrame: cb => { frames.set(++frameId, cb); return frameId },
      cancelAnimationFrame: id => frames.delete(id),
    },
    comparisonBarBounds: bar => bar.bounds.clone(),
    getVisibleContentBox: () => bars.reduce((box, bar) => box.union(bar.bounds), new THREE.Box3()),
    nextTick: cb => cb(), applyComparisonSelection: noop, requestRender: noop,
    updateOrthographicFrustum: noop, syncPointcloudCameraPose: noop,
    ElMessage: { info: noop, warning: message => assert.fail(message) },
  }
  s.rebarInspectionIndex = { get value() { return bars.findIndex(bar => bar.ifcGlobalId === s.selectedComparisonBarId.value) } }
  const api = runInNewContext(`${compiled}\n({ ${names.join(', ')} })`, s)
  function advance(ms) {
    now += ms
    const pending = [...frames.values()]
    frames.clear()
    pending.forEach(cb => cb(now))
  }
  return { api, s, bars, advance, pending: () => frames.size }
}

function assertUpright(camera, target) {
  camera.updateMatrixWorld(true)
  // Engineering +Z is viewer +Y. Its screen projection must stay vertical and up.
  const origin = target.clone().project(camera)
  const above = target.clone().addScaledVector(worldUp, .01).project(camera)
  assert.ok(Math.abs(above.x - origin.x) < 1e-8, `engineering Z tilted sideways: ${above.x - origin.x}`)
  assert.ok(above.y > origin.y, 'engineering +Z must project upward')
}

for (const orthographic of [false, true]) {
  const mode = orthographic ? 'orthographic' : 'perspective'
  test(`${mode}: next/previous inspection keeps world up throughout animation`, () => {
    const { api, s, bars, advance } = fixture(orthographic)
    for (const direction of [1, 1, 1, -1]) {
      api.stepRebarInspection(direction)
      assert.equal(s.controls.enabled, false)
      for (const ms of [200, 230, 430]) {
        advance(ms)
        assert.ok(s.activeCamera.up.distanceTo(worldUp) < 1e-10, `up changed to ${s.activeCamera.up.toArray()}`)
      }
      assert.equal(s.controls.enabled, true)
      const center = bars[s.rebarInspectionIndex.value].bounds.getCenter(new THREE.Vector3())
      assert.ok(s.controls.target.distanceTo(center) < 1e-10)
      const directionToCamera = s.activeCamera.position.clone().sub(center).normalize()
      assert.ok(directionToCamera.distanceTo(new THREE.Vector3(0, Math.cos(Math.PI / 10), Math.sin(Math.PI / 10))) < 1e-8)
    }
  })

  test(`${mode}: orbit and preset views after next keep engineering Z upright`, () => {
    const { api, s, advance } = fixture(orthographic)
    api.stepRebarInspection(1)
    advance(860)
    // Use the same angular input consumed by the real mouse-drag path.
    s.controls._rotateLeft(Math.PI / 3)
    s.controls._rotateUp(-Math.PI / 6)
    s.controls.update()
    assertUpright(s.activeCamera, s.controls.target)
    api.orbitPointcloudFromCube({ lon: 35, lat: -10 })
    assertUpright(s.activeCamera, s.controls.target)
    for (const preset of ['front', 'side']) {
      api.setPresetView(preset)
      assertUpright(s.activeCamera, s.controls.target)
    }
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
  assert.ok(s.activeCamera.up.distanceTo(worldUp) < 1e-10)
})

test('manual focus recovers world up after a rolled or top cube view', () => {
  const { api, s, bars, advance } = fixture()
  s.activeCamera.up.set(0, 0, -1)
  api.focusComparisonBar(bars[1], { manual: true })
  advance(680)
  assert.ok(s.activeCamera.up.distanceTo(worldUp) < 1e-10)
  s.controls._rotateLeft(.7)
  s.controls.update()
  assertUpright(s.activeCamera, s.controls.target)
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
