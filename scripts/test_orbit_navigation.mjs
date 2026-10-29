import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { NavigationOrbitControls as OrbitControls } from '../packages/viewer-core/src/viewer/NavigationOrbitControls.ts'

class Surface {
  style = {}
  clientWidth = 1200
  clientHeight = 800
  listeners = new Map()
  addEventListener(type, fn, options) {
    const entries = this.listeners.get(type) ?? []
    entries.push({ fn, capture: options === true || options?.capture === true })
    this.listeners.set(type, entries)
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter(x => x.fn !== fn))
  }
  getRootNode() { return this }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1200, height: 800 } }
  setPointerCapture() {}
  releasePointerCapture() {}
  emit(type, values = {}) {
    let stopped = false
    const e = { type, clientX: 600, clientY: 400, pointerId: 1, pointerType: 'mouse', button: 2,
      deltaMode: 0, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() { stopped = true }, ...values }
    for (const { fn } of [...(this.listeners.get(type) ?? [])].sort((a, b) => Number(b.capture) - Number(a.capture))) {
      if (stopped) break
      fn(e)
    }
  }
}

test('collapsed pivot recovers along the sight line without moving the camera', () => {
  const { camera, controls, surface } = fixture('preview')
  const position = camera.position.clone(), direction = camera.getWorldDirection(new THREE.Vector3())
  controls.target.copy(position)
  surface.emit('pointerdown')
  surface.emit('pointerup')
  assert.ok(camera.position.distanceTo(position) < 1e-10)
  assert.ok(camera.getWorldDirection(new THREE.Vector3()).dot(direction) > 1 - 1e-10)
  assert.ok(camera.position.distanceTo(controls.target) >= camera.near * 1.99)
  controls.dispose()
})

test('at the near pivot wheel moves camera and target forward together', () => {
  const { camera, controls, surface } = fixture('preview')
  controls.target.copy(camera.position).addScaledVector(camera.getWorldDirection(new THREE.Vector3()), camera.near * 2)
  controls.update()
  const before = camera.position.clone(), target = controls.target.clone(), rotation = camera.quaternion.clone()
  surface.emit('wheel', { deltaY: -100 })
  const delta = camera.position.clone().sub(before)
  assert.ok(delta.length() > .0001)
  assert.ok(delta.distanceTo(controls.target.clone().sub(target)) < 1e-10)
  assert.ok(camera.quaternion.angleTo(rotation) < 1e-7)
  controls.dispose()
})

test('orthographic zoom stays finite at both extremes and reverses immediately', () => {
  const camera = new THREE.OrthographicCamera(-15, 15, 10, -10, .01, 5000)
  camera.position.set(2, 3, 10)
  const surface = new Surface(), controls = new OrbitControls(camera, surface)
  for (const deltaY of [-100, 100]) {
    for (let i = 0; i < 2000; i++) surface.emit('wheel', { deltaY })
    const zoom = camera.zoom, before = camera.position.clone()
    assert.ok(Number.isFinite(zoom) && zoom > 0)
    surface.emit('pointerdown')
    surface.emit('pointermove', { clientX: 700 })
    surface.emit('pointerup')
    assert.ok(camera.position.distanceTo(before) > .001)
    surface.emit('wheel', { deltaY: -deltaY })
    assert.notEqual(camera.zoom, zoom)
  }
  controls.dispose()
})

test('cursor zoom preserves the projected point under the pointer', () => {
  const { camera, controls, surface } = fixture('preview')
  camera.updateMatrixWorld(true)
  const before = new THREE.Vector3(.3, .2, .5).unproject(camera)
  const ray = before.sub(camera.position).normalize()
  const normal = camera.getWorldDirection(new THREE.Vector3())
  const point = ray.multiplyScalar(controls.target.clone().sub(camera.position).dot(normal) / ray.dot(normal)).add(camera.position)
  surface.emit('wheel', { deltaY: -100, clientX: 780, clientY: 320 })
  const projected = point.project(camera)
  assert.ok(Math.abs(projected.x - .3) < 1e-9)
  assert.ok(Math.abs(projected.y - .2) < 1e-9)
  controls.dispose()
})

test('disabled controls, captured gestures, and disposed controls cannot move the camera', () => {
  const { camera, controls, surface } = fixture('preview')
  const before = camera.position.clone()
  controls.enabled = false
  surface.emit('wheel', { deltaY: -100 })
  assert.ok(camera.position.equals(before))
  controls.enabled = true
  const consume = e => e.stopImmediatePropagation()
  surface.addEventListener('wheel', consume, true)
  surface.emit('wheel', { deltaY: -100 })
  assert.ok(camera.position.equals(before))
  surface.removeEventListener('wheel', consume)
  controls.dispose()
  surface.emit('wheel', { deltaY: -100 })
  assert.ok(camera.position.equals(before))
  assert.equal((surface.listeners.get('wheel') ?? []).length, 0)
})

test('explicit distance limits survive updates and do not become forward travel', () => {
  const { camera, controls, surface } = fixture('preview')
  controls.minDistance = 5
  for (let i = 0; i < 200; i++) surface.emit('wheel', { deltaY: -100 })
  assert.ok(Math.abs(camera.position.distanceTo(controls.target) - 5) < 1e-10)
  const before = camera.position.clone()
  surface.emit('wheel', { deltaY: -100 })
  assert.ok(camera.position.distanceTo(before) < 1e-10)
  assert.equal(controls.minDistance, 5)
  controls.dispose()
})

function fixture(page) {
  const camera = new THREE.PerspectiveCamera(50, 1.5, .01, 5000)
  camera.position.set(100, 20, 130)
  const surface = new Surface()
  const state = { THREE, OrbitControls, camera, renderer: { domElement: surface }, controls: null,
    scene: null, transformHandleInteraction: null, syncBoundsHelpers() {}, syncPointcloudCameraPose() {},
    emitCameraPose() {}, syncMeasurementBadges() {} }
  const path = page === 'alignment' ? '../packages/alignment/src/AlignmentPage.vue' : '../packages/viewer-core/src/components/preview/UnifiedViewer3D.vue'
  const source = readFileSync(new URL(path, import.meta.url), 'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
  let body
  if (page === 'alignment') {
    const ast = ts.createSourceFile('page.ts', source, ts.ScriptTarget.Latest, true)
    body = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'mountControls').getText(ast) + '\nmountControls(camera)'
  } else {
    const start = source.indexOf('  controls = new OrbitControls(')
    body = source.slice(start, source.indexOf('  // 交互事件监听', start))
  }
  runInNewContext(ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, state)
  state.controls.target.set(100, -20, 30)
  state.controls.update()
  return { camera, controls: state.controls, surface }
}

for (const page of ['alignment', 'preview']) {
  test(`${page}: prolonged zoom keeps pan and reverse zoom usable`, () => {
    const { camera, controls, surface } = fixture(page)
    for (let i = 0; i < 1000; i++) surface.emit('wheel', { deltaY: -100 })
    const distance = camera.position.distanceTo(controls.target)
    const beforePan = camera.position.clone()
    surface.emit('pointerdown')
    surface.emit('pointermove', { clientX: 700 })
    surface.emit('pointerup')
    const pan = camera.position.distanceTo(beforePan)
    const beforeZoom = camera.position.clone()
    for (let i = 0; i < 10; i++) surface.emit('wheel', { deltaY: 100 })
    const zoomOut = camera.position.distanceTo(beforeZoom)
    console.log(JSON.stringify({ page, distance, pan, zoomOut, enabled: controls.enabled }))
    controls.dispose()
    assert.ok(pan > .001, `100px pan only moved ${pan} world units`)
    assert.ok(zoomOut > .001, `10 reverse wheel steps only moved ${zoomOut} world units`)
  })
}
