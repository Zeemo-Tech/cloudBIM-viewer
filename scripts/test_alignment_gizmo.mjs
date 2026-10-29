import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import { bindTransformChangeEvents } from '../packages/alignment/src/transformChangeEvents.ts'
import { bindTransformHandleInteraction, styleEngineeringTransformHandles } from '../packages/alignment/src/transformHandleInteraction.ts'

// Real page wiring, real Three.js picking and transforms, DOM event ordering.
// No WebGL or saved project is needed to reproduce an overlapping-handle drag.
class Surface {
  listeners = new Map()
  style = {}
  captures = new Set()
  clientWidth = 1000
  clientHeight = 800
  ownerDocument = this
  defaultView = this
  setAttribute() {}
  addEventListener(type, listener, options) {
    const capture = options === true || options?.capture === true
    const list = this.listeners.get(type) ?? []
    if (!list.some(item => item.listener === listener && item.capture === capture)) list.push({ listener, capture })
    this.listeners.set(type, list)
  }
  removeEventListener(type, listener, options) {
    const capture = options === true || options?.capture === true
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter(item => item.listener !== listener || item.capture !== capture))
  }
  getRootNode() { return this.ownerDocument }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight } }
  setPointerCapture(id) { this.captures.add(id) }
  hasPointerCapture(id) { return this.captures.has(id) }
  releasePointerCapture(id) { this.captures.delete(id) }
  focus() {}
  emit(type, x, y, extra = {}) {
    let stopped = false
    const event = { type, clientX: x, clientY: y, pageX: x, pageY: y, pointerId: 1,
      pointerType: 'mouse', button: type === 'pointermove' ? -1 : 0, buttons: type === 'pointermove' ? 1 : 0,
      preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() { stopped = true }, ...extra }
    for (const capture of [true, false]) {
      for (const item of [...(this.listeners.get(type) ?? [])]) {
        if (stopped) break
        if (item.capture === capture && this.listeners.get(type).includes(item)) item.listener(event)
      }
    }
    return event
  }
}
const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', source, ts.ScriptTarget.Latest, true)
const names = ['mountControls', 'applyTransformSelection', 'syncTransformHandleVisibility']
const compiled = ts.transpileModule(ast.statements.filter(n => ts.isFunctionDeclaration(n) && names.includes(n.name?.text)).map(n => n.getText(ast)).join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const ref = value => ({ value })
const noop = () => {}

function fixture(orthographic = false, dimensions = null) {
  const surface = new Surface()
  globalThis.document = surface
  const camera = orthographic ? new THREE.OrthographicCamera(-7.5, 7.5, 6, -6, .01, 1000) : new THREE.PerspectiveCamera(50, 1.25, .01, 1000)
  camera.position.set(6, 5, 8)
  const scene = new THREE.Scene(), engineeringRoot = new THREE.Group(), target = new THREE.Group()
  if (dimensions) target.add(new THREE.Mesh(new THREE.BoxGeometry(...dimensions), new THREE.MeshBasicMaterial()))
  engineeringRoot.rotation.x = -Math.PI / 2
  target.quaternion.setFromEuler(new THREE.Euler(.31, -.27, .48))
  scene.add(engineeringRoot); engineeringRoot.add(target)
  const s = { THREE, OrbitControls, TransformControls, bindTransformChangeEvents, bindTransformHandleInteraction, styleEngineeringTransformHandles,
    transformHandleInteraction: null, transformHandleState: ref(null), transformHandleSize: ref(1),
    renderer: { domElement: surface }, scene, controls: null, transformControls: null, rotationControls: null,
    transformHelper: null, rotationHelper: null, selectedItemId: ref('bim'), editMode: ref(true),
    showTransformHandles: ref(true), activeWorkflowStep: ref(1), enableClipping: ref(false), analysisMode: ref('none'),
    registrationStage: ref('coarse'), coarseAlignmentDirty: ref(false), transformMode: ref('translate'),
    getSelectedObject: () => target, syncBoundsHelpers: noop, syncPointcloudCameraPose: noop,
    syncTransformFixFromSelected: noop, syncAllTransformFixValuesFromSelected: noop, syncTransformModeForSelection: noop,
    updateClipRangeFromContent: noop, applyClippingState: noop, requestRender: noop,
    ensureInitialTransformState: noop, ensureOrientationBase: noop, ensurePositionBase: noop, updateSelectionHighlight: noop,
  }
  const api = runInNewContext(`${compiled}\n({ ${names.join(', ')} })`, s)
  api.mountControls(camera)
  scene.updateMatrixWorld(true)
  return { s, surface, scene, target, camera, api }
}

function overlap(f) {
  for (let y = 300; y <= 500; y += 2) for (let x = 400; x <= 650; x += 2) {
    const pointer = { x: x / 500 - 1, y: 1 - y / 400, button: 0 }
    f.s.transformControls.pointerHover(pointer)
    f.s.rotationControls.pointerHover(pointer)
    if (['X', 'Z', 'XZ'].includes(f.s.transformControls.axis) && f.s.rotationControls.axis === 'Y') return { x, y, axis: f.s.transformControls.axis }
  }
  assert.fail('fixture must contain a real horizontal translation / yaw picker overlap')
}

function findHandle(f, axis, rotation = false) {
  for (let y = 250; y <= 550; y += 3) for (let x = 350; x <= 700; x += 3) {
    const pointer = { x: x / 500 - 1, y: 1 - y / 400, button: 0 }
    f.s.transformControls.pointerHover(pointer)
    f.s.rotationControls.pointerHover(pointer)
    if (rotation ? !f.s.transformControls.axis && f.s.rotationControls.axis === axis : f.s.transformControls.axis === axis) return { x, y }
  }
  assert.fail(`no visible ${rotation ? 'rotation' : 'translation'} ${axis} handle`)
}
function close(f) {
  f.s.transformHandleInteraction.dispose(); f.s.controls.dispose(); f.s.transformControls.dispose(); f.s.rotationControls.dispose()
}
function begin(f, hit, extra = {}) {
  f.surface.emit('pointerdown', hit.x, hit.y, extra)
  f.surface.emit('pointermove', hit.x + 30, hit.y + 12, extra)
  f.scene.updateMatrixWorld(true)
}

// Measure actual rendered vertices in CSS pixels, not TransformControls.size
// (which is a camera-dependent multiplier, not a screen-space limit).
function visualHandleDiameter(f) {
  f.s.transformHandleInteraction.updateSize?.()
  f.scene.updateMatrixWorld(true)
  f.camera.updateMatrixWorld(true)
  const center = f.target.getWorldPosition(new THREE.Vector3()).project(f.camera)
  let radius = 0
  for (const controller of [f.s.transformControls, f.s.rotationControls]) {
    const gizmo = controller.getHelper().children.find(child => child.gizmo)?.gizmo[controller.mode]
    gizmo.traverseVisible(object => {
      const positions = object.geometry?.attributes.position
      if (!positions) return
      for (let i = 0; i < positions.count; i++) {
        const point = new THREE.Vector3().fromBufferAttribute(positions, i).applyMatrix4(object.matrixWorld).project(f.camera)
        radius = Math.max(radius, Math.hypot((point.x - center.x) * f.surface.clientWidth / 2,
          (point.y - center.y) * f.surface.clientHeight / 2))
      }
    })
  }
  return radius * 2
}

for (const ortho of [false, true]) test(`${ortho ? 'orthographic' : 'perspective'}: screen size stays bounded across zoom, large displays and manual enlargement`, t => {
  const f = fixture(ortho); t.after(() => close(f))
  f.surface.clientWidth = 2560; f.surface.clientHeight = 1384
  if (!ortho) f.camera.aspect = f.surface.clientWidth / f.surface.clientHeight
  for (let i = 0; i < 40; i++) f.surface.emit('wheel', 500, 400, { ctrlKey: true, deltaY: -100 })
  for (const zoom of [.02, 1, 25, 100]) {
    f.camera.zoom = zoom; f.camera.updateProjectionMatrix()
    const diameter = visualHandleDiameter(f)
    assert.ok(diameter <= 280.5, `zoom ${zoom}: visible handles exceed 280 CSS px (${diameter.toFixed(2)} px)`)
    assert.ok(diameter >= 100, `zoom ${zoom}: handles must remain pickable (${diameter.toFixed(2)} px)`)
  }
})

test('perspective: zoom near an off-center pivot cannot inflate the handles', t => {
  const f = fixture(); t.after(() => close(f))
  f.camera.position.set(12, 3, .8)
  f.camera.lookAt(12, 3, -10)
  const diameter = visualHandleDiameter(f)
  assert.ok(diameter <= 240.5, `off-center pivot inflated handles to ${diameter.toFixed(2)} CSS px`)
})

for (const ortho of [false, true]) test(`${ortho ? 'orthographic' : 'perspective'}: default size follows the model within a readable floor and a close-up ceiling`, t => {
  const f = fixture(ortho, [6, 3, 1]); t.after(() => close(f))
  const position = f.camera.position.clone(), model = f.target.matrixWorld.toArray()
  const diameters = []
  for (const distance of [10, .6, .25, .05]) {
    if (ortho) { f.camera.zoom = 1 / distance; f.camera.updateProjectionMatrix() }
    else f.camera.position.copy(position).multiplyScalar(distance)
    diameters.push(visualHandleDiameter(f))
  }
  assert.ok(diameters[0] >= 120 && diameters[0] <= 140, `readable floor: ${diameters}`)
  assert.ok(diameters[1] > diameters[0] + 10, `model size should influence the handle: ${diameters}`)
  assert.ok(diameters.every(value => value <= 220.5), `close-up ceiling: ${diameters}`)
  assert.ok(Math.abs(diameters[2] - diameters[3]) < 1, `further zoom cannot keep enlarging handles: ${diameters}`)
  assert.deepEqual(f.target.matrixWorld.toArray(), model)
  assert.equal(f.s.coarseAlignmentDirty.value, false)
})

test('resize, minimum preference and reset preserve the object and keep handles inside the viewport cap', t => {
  const f = fixture(false, [6, 3, 1]); t.after(() => close(f))
  const matrix = f.target.matrixWorld.toArray(), camera = f.camera.matrixWorld.toArray()
  const initial = visualHandleDiameter(f)
  for (let i = 0; i < 60; i++) f.surface.emit('wheel', 500, 400, { ctrlKey: true, deltaY: 100 })
  const minimum = visualHandleDiameter(f)
  assert.ok(minimum >= 100 && minimum < initial)
  f.s.transformHandleInteraction.resetSize()
  assert.equal(f.s.transformHandleSize.value, 1)
  assert.ok(Math.abs(visualHandleDiameter(f) - initial) < 1e-8)
  assert.deepEqual(f.target.matrixWorld.toArray(), matrix)
  assert.deepEqual(f.camera.matrixWorld.toArray(), camera)
  f.surface.clientWidth = 360; f.surface.clientHeight = 220
  f.camera.aspect = 360 / 220; f.camera.updateProjectionMatrix()
  assert.ok(visualHandleDiameter(f) <= 66.5, 'small viewport cap takes precedence over the pixel floor')
})

test('physical units do not affect the screen size, and a drag freezes the adaptive diameter', t => {
  const f = fixture(false, [6, 3, 1]); t.after(() => close(f))
  const size = visualHandleDiameter(f)
  f.target.parent.scale.setScalar(1000)
  f.camera.position.multiplyScalar(1000)
  assert.ok(Math.abs(visualHandleDiameter(f) - size) < 1e-8)
  const hit = findHandle(f, 'Y')
  f.surface.emit('pointerdown', hit.x, hit.y)
  const frozen = f.s.transformHandleInteraction.updateSize()
  f.surface.emit('wheel', hit.x, hit.y, { ctrlKey: true, deltaY: -100 })
  f.s.transformHandleInteraction.resetSize()
  f.surface.emit('pointermove', hit.x + 15, hit.y + 15)
  assert.equal(f.s.transformHandleInteraction.updateSize(), frozen)
  assert.equal(f.s.transformHandleSize.value, 1)
  f.surface.emit('keydown', 0, 0, { key: 'Escape' })
})

test('a pivot at or behind the near plane hides finite handles and recovers when brought back', t => {
  const f = fixture(); t.after(() => close(f))
  const position = f.camera.position.clone(), quaternion = f.camera.quaternion.clone()
  for (const z of [0, -.1]) {
    f.camera.position.set(0, 0, z); f.camera.quaternion.identity()
    f.s.transformHandleInteraction.updateSize()
    assert.equal(f.s.transformControls.size, 0)
    assert.equal(f.s.rotationControls.size, 0)
  }
  f.camera.position.copy(position); f.camera.quaternion.copy(quaternion)
  assert.ok(visualHandleDiameter(f) > 100)
})

for (const ortho of [false, true]) test(`${ortho ? 'orthographic' : 'perspective'}: horizontal handle drag never rotates the BIM or camera`, () => {
  const f = fixture(ortho), hit = overlap(f)
  const rotation = f.target.quaternion.clone(), cameraPose = f.camera.matrixWorld.clone(), start = f.target.position.clone()
  f.surface.emit('pointerdown', hit.x, hit.y)
  assert.equal(f.s.rotationControls.dragging, false, 'only the translation controller owns the pointer')
  f.surface.emit('pointermove', hit.x + 35, hit.y + 8)
  f.scene.updateMatrixWorld(true)
  f.surface.emit('pointerup', hit.x + 35, hit.y + 8)
  assert.ok(f.target.position.distanceTo(start) > .001, 'translation must actually move the target')
  const degrees = THREE.MathUtils.radToDeg(f.target.quaternion.angleTo(rotation))
  assert.ok(degrees < 1e-5, `horizontal ${hit.axis} drag also rotated model by ${degrees.toFixed(6)} degrees at (${hit.x}, ${hit.y})`)
  assert.deepEqual(f.camera.matrixWorld.elements, cameraPose.elements, 'gizmo must not orbit the camera')
  f.s.transformHandleInteraction.dispose(); f.s.controls.dispose(); f.s.transformControls.dispose(); f.s.rotationControls.dispose()
})

for (const axis of ['X', 'Y', 'Z', 'XY', 'YZ', 'XZ', 'XYZ']) test(`${axis}: translation preserves orientation, scale and asymmetric geometry`, t => {
  const f = fixture(); t.after(() => close(f))
  const hit = findHandle(f, axis)
  const before = f.target.matrixWorld.clone(), rotation = f.target.quaternion.clone(), scale = f.target.scale.clone()
  const origin = f.target.getWorldPosition(new THREE.Vector3())
  begin(f, hit)
  const delta = f.target.getWorldPosition(new THREE.Vector3()).sub(origin)
  assert.ok(delta.length() > .001)
  assert.deepEqual(f.target.quaternion.toArray(), rotation.toArray())
  assert.deepEqual(f.target.scale.toArray(), scale.toArray())
  for (const [index, name] of ['X', 'Y', 'Z'].entries()) {
    if (!axis.includes(name)) assert.ok(Math.abs(delta.getComponent(index)) < 1e-10)
  }
  for (const p of [[1, 3, -2], [-7, .5, 4], [2, -4, 9]]) {
    const expected = new THREE.Vector3(...p).applyMatrix4(before).add(delta)
    assert.ok(new THREE.Vector3(...p).applyMatrix4(f.target.matrixWorld).distanceTo(expected) < 1e-10)
  }
  assert.deepEqual(f.s.transformHandleState.value.translation, [delta.x, -delta.z, delta.y])
  f.surface.emit('pointerup', hit.x + 30, hit.y + 12)
  assert.equal(f.s.controls.enabled, true)
  assert.equal(f.s.coarseAlignmentDirty.value, true, 'releasing a changed pose commits the dirty state')
})

for (const ortho of [false, true]) test(`${ortho ? 'orthographic' : 'perspective'}: yaw ring only rotates around engineering Z`, t => {
  const f = fixture(ortho); t.after(() => close(f))
  const hit = findHandle(f, 'Y', true), position = f.target.position.clone(), before = f.target.getWorldQuaternion(new THREE.Quaternion())
  begin(f, hit)
  assert.equal(f.s.transformControls.dragging, false)
  assert.equal(f.s.transformMode.value, 'rotate')
  assert.deepEqual(f.target.position.toArray(), position.toArray())
  const delta = f.target.getWorldQuaternion(new THREE.Quaternion()).multiply(before.invert())
  assert.ok(Math.abs(delta.y) > .001)
  assert.ok(Math.abs(delta.x) < 1e-10 && Math.abs(delta.z) < 1e-10)
  assert.ok(Math.abs(f.s.transformHandleState.value.angle) > .01)
  f.surface.emit('pointerup', hit.x + 30, hit.y + 12)
})

for (const reason of ['Escape', 'pointercancel', 'lostpointercapture', 'blur', 'hidden', 'disabled', 'dispose']) test(`${reason}: interrupted drag rolls back and releases the pointer`, t => {
  const f = fixture(); t.after(() => close(f))
  const hit = overlap(f), before = f.target.matrixWorld.clone()
  begin(f, hit)
  assert.equal(f.s.coarseAlignmentDirty.value, false, 'preview must not invalidate saved analysis before commit')
  if (reason === 'Escape') f.surface.emit('keydown', 0, 0, { key: 'Escape' })
  else if (reason === 'hidden') { f.s.showTransformHandles.value = false; f.api.syncTransformHandleVisibility() }
  else if (reason === 'disabled') { f.s.editMode.value = false; f.api.applyTransformSelection() }
  else if (reason === 'dispose') f.s.transformHandleInteraction.dispose()
  else f.surface.emit(reason, hit.x, hit.y)
  f.scene.updateMatrixWorld(true)
  assert.deepEqual(f.target.matrixWorld.elements, before.elements)
  assert.equal(f.s.coarseAlignmentDirty.value, false)
  assert.equal(f.s.controls.enabled, true)
  assert.equal(f.s.transformControls.dragging, false)
  assert.equal(f.s.rotationControls.dragging, false)
  assert.equal(f.surface.captures.size, 0)
  assert.equal(f.s.transformHandleState.value, null)
  // A subsequent gesture remains usable, including after remounting.
  if (reason === 'dispose') f.api.mountControls(f.camera)
  f.s.editMode.value = true; f.s.showTransformHandles.value = true
  f.api.applyTransformSelection(); f.scene.updateMatrixWorld(true)
  begin(f, hit)
  assert.equal(f.s.transformControls.dragging, true)
  f.surface.emit('pointerup', hit.x + 30, hit.y + 12)
})

test('touch starts without hover; a second pointer cannot take over or release the drag', t => {
  const f = fixture(); t.after(() => close(f))
  const hit = overlap(f)
  f.surface.emit('pointerdown', hit.x, hit.y, { pointerType: 'touch' })
  f.surface.emit('pointermove', hit.x + 20, hit.y, { pointerType: 'touch' })
  const position = f.target.position.clone()
  f.surface.emit('pointerdown', hit.x + 100, hit.y, { pointerType: 'touch', pointerId: 2, isPrimary: false })
  f.surface.emit('pointermove', hit.x + 150, hit.y, { pointerType: 'touch', pointerId: 2 })
  f.surface.emit('pointerup', hit.x + 150, hit.y, { pointerType: 'touch', pointerId: 2 })
  assert.deepEqual(f.target.position.toArray(), position.toArray())
  assert.equal(f.s.transformControls.dragging, true)
  f.surface.emit('pointerup', hit.x + 20, hit.y, { pointerType: 'touch' })
  assert.equal(f.s.controls.enabled, true)
})

test('touch with no prior hover keeps its drag plane stable across the first render frame', t => {
  const f = fixture(); t.after(() => close(f))
  const hit = findHandle(f, 'X'), before = f.target.position.clone()
  f.s.transformControls.axis = null; f.s.rotationControls.axis = null
  f.scene.updateMatrixWorld(true)
  f.surface.emit('pointerdown', hit.x, hit.y, { pointerType: 'touch' })
  // In the browser a frame may run before the first movement event arrives.
  f.scene.updateMatrixWorld(true)
  f.surface.emit('pointermove', hit.x, hit.y, { pointerType: 'touch' })
  assert.ok(f.target.position.distanceTo(before) < 1e-10, 'a stationary pointer must not jump after the drag plane updates')
})

test('hover highlights one handle; Ctrl wheel changes only its size and survives projection remount', t => {
  const f = fixture(); t.after(() => close(f))
  const hit = overlap(f), matrix = f.target.matrixWorld.clone(), camera = f.camera.matrixWorld.clone()
  f.surface.emit('pointermove', hit.x, hit.y, { buttons: 0 })
  assert.ok(f.s.transformControls.axis)
  assert.equal(f.s.rotationControls.axis, null)
  assert.equal(f.s.coarseAlignmentDirty.value, false)
  f.surface.emit('wheel', hit.x, hit.y, { ctrlKey: true, deltaY: -100 })
  assert.ok(f.s.transformHandleSize.value > 1)
  const size = f.s.transformControls.size
  assert.deepEqual(f.camera.matrixWorld.elements, camera.elements)
  assert.deepEqual(f.target.matrixWorld.elements, matrix.elements)
  f.api.mountControls(f.camera)
  assert.equal(f.s.transformControls.size, size)
  for (let i = 0; i < 100; i++) f.surface.emit('wheel', hit.x, hit.y, { ctrlKey: true, deltaY: -100 })
  assert.equal(f.s.transformHandleSize.value, 2)
})

test('empty-space and right-button gestures remain camera navigation; hidden handles cannot pick', t => {
  const f = fixture(); t.after(() => close(f))
  const matrix = f.target.matrixWorld.clone(), camera = f.camera.position.clone()
  f.surface.emit('pointerdown', 50, 50)
  f.surface.emit('pointermove', 85, 70)
  f.surface.emit('pointerup', 85, 70)
  assert.ok(f.camera.position.distanceTo(camera) > .01)
  assert.deepEqual(f.target.matrixWorld.elements, matrix.elements)
  f.s.showTransformHandles.value = false; f.api.syncTransformHandleVisibility()
  f.surface.emit('pointerdown', 500, 400, { button: 2 })
  f.surface.emit('pointermove', 530, 400, { buttons: 2 })
  f.surface.emit('pointerup', 530, 400, { button: 2 })
  assert.equal(f.s.transformControls.dragging, false)
  assert.equal(f.s.rotationControls.dragging, false)
  assert.equal(f.s.coarseAlignmentDirty.value, false)
})

test('drag completion preserves a previously disabled camera and a preexisting dirty pose', t => {
  const f = fixture(); t.after(() => close(f))
  const hit = overlap(f)
  f.s.controls.enabled = false; f.s.coarseAlignmentDirty.value = true
  begin(f, hit)
  f.surface.emit('keydown', 0, 0, { key: 'Escape' })
  assert.equal(f.s.controls.enabled, false)
  assert.equal(f.s.coarseAlignmentDirty.value, true)
})

for (const frame of ['viewport', 'engineering']) test(`orientation triad uses the ${frame} basis with its original handedness`, () => {
  const triad = readFileSync(new URL('../packages/viewer-core/src/components/preview/PointcloudAxesTriad.vue', import.meta.url), 'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
  const ast = ts.createSourceFile('triad.ts', triad, ts.ScriptTarget.Latest, true)
  const fn = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'syncAxesPose')
  const basis = ast.statements.find(n => ts.isVariableStatement(n) && n.declarationList.declarations.some(d => d.name.getText(ast) === 'ENGINEERING_TO_VIEWPORT'))
  const pose = { camera: new THREE.Vector3(4, 7, 11), target: new THREE.Vector3(1, -2, 3), up: new THREE.Vector3(0, 1, 0) }
  const axesGroup = new THREE.Group(), mainCameraProxy = new THREE.PerspectiveCamera()
  const code = ts.transpileModule(`${basis.getText(ast)}\n${fn.getText(ast)}\nsyncAxesPose()`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  runInNewContext(code, { THREE, props: { pose, coordinateFrame: frame }, axesGroup, mainCameraProxy, DEFAULT_UP: pose.up })
  const directions = frame === 'engineering' ? [[1, 0, 0], [0, 0, -1], [0, 1, 0]] : [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
  const inverseCamera = mainCameraProxy.quaternion.clone().invert()
  directions.forEach((direction, index) => {
    const axis = new THREE.Vector3().setComponent(index, 1).applyQuaternion(axesGroup.quaternion)
    assert.ok(axis.distanceTo(new THREE.Vector3(...direction).applyQuaternion(inverseCamera)) < 1e-10)
  })
  assert.ok(Math.abs(axesGroup.matrixWorld.determinant() - 1) < 1e-10)
})
