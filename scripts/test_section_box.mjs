import assert from 'node:assert/strict'
import { readFileSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

// Compile the actual shared modules, retaining real Three picking and plane math.
mkdirSync('.cloudbim', { recursive: true })
const temp = mkdtempSync('.cloudbim/section-box-test-')
for (const name of ['clipBoxGizmo', 'clipBoxRotation', 'clipVolume', 'SectionBoxController']) {
  const source = readFileSync(`packages/viewer-core/src/viewer/clipping/${name}.ts`, 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
  writeFileSync(`${temp}/${name}.mjs`, js.replace(/from (['"])(\.\/[^'"]+)\1/g, 'from $1$2.mjs$1'))
}
const { SectionBoxController } = await import(pathToFileURL(`${process.cwd()}/${temp}/SectionBoxController.mjs`))
process.on('exit', () => rmSync(temp, { recursive: true, force: true }))

class Surface {
  listeners = new Map(); style = {}; captures = new Set()
  clientWidth = 800; clientHeight = 600; ownerDocument = this; defaultView = this; hidden = false
  addEventListener(type, fn, option) {
    const entries = this.listeners.get(type) ?? []
    entries.push({ fn, capture: option === true || option?.capture === true }); this.listeners.set(type, entries)
  }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter(e => e.fn !== fn)) }
  getRootNode() { return this }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight } }
  setPointerCapture(id) { this.captures.add(id) }
  hasPointerCapture(id) { return this.captures.has(id) }
  releasePointerCapture(id) { this.captures.delete(id) }
  focus() {}
  emit(type, x = 0, y = 0, extra = {}) {
    let stopped = false
    const event = { type, target: this, clientX: x, clientY: y, pageX: x, pageY: y, pointerId: 1, isPrimary: true,
      pointerType: 'mouse', button: type === 'pointermove' ? -1 : 0, buttons: type === 'pointermove' ? 1 : 0,
      preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() { stopped = true }, ...extra }
    for (const capture of [true, false]) for (const entry of [...(this.listeners.get(type) ?? [])]) {
      if (!stopped && entry.capture === capture) entry.fn(event)
    }
  }
}
function fixture(orthographic = false, position = [8, 6, 11], onStart) {
  const surface = new Surface(); globalThis.document = surface
  const camera = orthographic ? new THREE.OrthographicCamera(-8, 8, 6, -6, .01, 1000) : new THREE.PerspectiveCamera(50, 4 / 3, .01, 1000)
  camera.position.fromArray(position)
  const scene = new THREE.Scene()
  const orbit = new OrbitControls(camera, surface); orbit.enableDamping = false; orbit.target.set(.5, .5, .5); orbit.update()
  let content = new THREE.Box3(new THREE.Vector3(-2, -1, -3), new THREE.Vector3(3, 2, 4))
  const original = new THREE.Mesh(new THREE.BoxGeometry(5, 3, 7), new THREE.MeshBasicMaterial())
  original.position.set(.5, .5, .5); scene.add(original)
  const states = []
  const controller = new SectionBoxController({ element: surface, scene, camera: () => camera, orbit: () => orbit,
    bounds: () => content, onStart: () => onStart?.(orbit), onChange: p => states.push(p.map(v => [...v.normal.toArray(), v.constant])) })
  controller.setEnabled(true); scene.updateMatrixWorld(true); camera.updateMatrixWorld(true); controller.update()
  return { surface, camera, scene, orbit, controller, original, states, stream: () => { content = content.clone().expandByScalar(12) },
    dispose: () => { controller.dispose(); orbit.dispose() } }
}
const snapshot = f => ({ bounds: [...f.controller.bounds.min.toArray(), ...f.controller.bounds.max.toArray()],
  frame: f.controller.frame.worldFromLocal.toArray(), camera: f.camera.matrixWorld.toArray(), model: f.original.matrixWorld.toArray() })
function findHit(f, kind, axis, side) {
  const ray = new THREE.Raycaster()
  for (let y = 24; y < 580; y += 4) for (let x = 24; x < 780; x += 4) {
    ray.setFromCamera(new THREE.Vector2(x / 400 - 1, 1 - y / 300), f.camera)
    const h = f.controller.gizmo.pick(ray, f.camera)
    if (h?.kind === kind && (!axis || h.axis === axis) && (side === undefined || h.side === side)) return { x, y, ...h }
  }
  throw new Error(`No actual ${kind} ${axis ?? ''} ${side ?? ''} picker`)
}
function drag(f, hit) {
  f.surface.emit('pointerdown', hit.x, hit.y)
  assert.equal(f.controller.dragging, true)
  assert.equal(f.orbit.enabled, false)
  let dx = 26, dy = 17
  if (hit.kind === 'face') {
    const inward = new THREE.Vector3(); inward[hit.axis] = hit.side ? -1 : 1
    const a = hit.point.clone().project(f.camera), b = hit.point.clone().add(inward).project(f.camera)
    const direction = new THREE.Vector2((b.x - a.x) * 400, -(b.y - a.y) * 300).setLength(35)
    dx = direction.x; dy = direction.y
  }
  f.surface.emit('pointermove', hit.x + dx, hit.y + dy)
  f.scene.updateMatrixWorld(true); f.camera.updateMatrixWorld(true)
}
for (const ortho of [false, true]) {
  for (const axis of ['x', 'y', 'z']) for (const side of [0, 1]) test(`${ortho ? 'orthographic' : 'perspective'} ${axis}${side}: face drag commits, camera/source pose stable`, () => {
    const position = [8, 6, 11]; position[['x','y','z'].indexOf(axis)] *= side ? 1 : -1
    const f = fixture(ortho, position)
    try {
      const initial = snapshot(f), hit = findHit(f, 'face', axis, side)
      drag(f, hit)
      const during = snapshot(f)
      assert.notDeepEqual(during.bounds, initial.bounds)
      assert.deepEqual(during.frame, initial.frame)
      assert.deepEqual(during.camera, initial.camera); assert.deepEqual(during.model, initial.model)
      f.surface.emit('pointerup', hit.x, hit.y)
      assert.deepEqual(snapshot(f), during); assert.equal(f.orbit.enabled, true)
    } finally { f.dispose() }
  })
  test(`${ortho ? 'orthographic' : 'perspective'}: rotated box keeps centre, inward planes and asymmetric point membership`, () => {
    const f = fixture(ortho)
    try {
      const before = snapshot(f), center = f.controller.bounds.getCenter(new THREE.Vector3())
      drag(f, findHit(f, 'edge'))
      assert.notDeepEqual(f.controller.frame.worldFromLocal.toArray(), before.frame)
      const worldCenter = f.controller.bounds.getCenter(new THREE.Vector3()).applyQuaternion(f.controller.frame.worldFromLocal)
      assert.ok(worldCenter.distanceTo(center) < 1e-10)
      const points = [[0,0,0],[.5,.8,-.3],[-.4,-.6,.7],[.8,-.2,-.5]]
      for (const p of points) {
        const local = f.controller.bounds.getCenter(new THREE.Vector3()).add(new THREE.Vector3(...p))
        assert.ok(f.controller.contains(local.applyQuaternion(f.controller.frame.worldFromLocal)))
      }
      for (const axis of ['x','y','z']) {
        const outside = f.controller.bounds.max.clone(); outside[axis] += .1
        assert.equal(f.controller.contains(outside.applyQuaternion(f.controller.frame.worldFromLocal)), false)
      }
      assert.deepEqual(snapshot(f).camera, before.camera); assert.deepEqual(snapshot(f).model, before.model)
    } finally { f.dispose() }
  })
}
for (const kind of ['face','edge']) for (const end of ['escape','pointercancel','lostpointercapture','blur','hidden','disable','dispose']) test(`${kind}: ${end} restores exact box and camera controls`, () => {
  const f = fixture()
  try {
    const before = snapshot(f); drag(f, findHit(f, kind))
    if (end === 'escape') f.surface.emit('keydown', 0, 0, { key: 'Escape' })
    else if (end === 'hidden') { f.surface.hidden = true; f.surface.emit('visibilitychange') }
    else if (end === 'disable') f.controller.setEnabled(false)
    else if (end === 'dispose') f.controller.dispose()
    else f.surface.emit(end)
    assert.deepEqual(snapshot(f), before); assert.equal(f.orbit.enabled, true); assert.equal(f.surface.captures.size, 0)
  } finally { f.dispose() }
})
test('streamed bounds stay fixed until explicit reset; disabled clipping and teardown', () => {
  const f=fixture()
  const before=snapshot(f)
  f.stream(); f.controller.update(); assert.deepEqual(snapshot(f),before)
  f.controller.fit(); assert.notDeepEqual(snapshot(f).bounds,before.bounds)
  f.controller.setEnabled(false); assert.deepEqual(f.controller.planes(),[])
  assert.equal(f.controller.contains(new THREE.Vector3(1e6,0,0)),true)
  f.dispose(); assert.equal(f.scene.children.includes(f.controller.gizmo.group),false)
  assert.equal([...f.surface.listeners.values()].flat().length,0)
})
test('touch starts without hover; another pointer cannot move or finish the box', () => {
  const f=fixture(); const hit=findHit(f,'face'); const before=snapshot(f)
  try {
    f.surface.emit('pointerdown',hit.x,hit.y,{pointerType:'touch'})
    f.surface.emit('pointermove',hit.x+25,hit.y+25,{pointerId:2,pointerType:'touch'})
    f.surface.emit('pointerup',hit.x,hit.y,{pointerId:2,pointerType:'touch'})
    assert.deepEqual(snapshot(f),before); assert.equal(f.controller.dragging,true)
    f.surface.emit('pointermove',hit.x+25,hit.y+25,{pointerType:'touch'})
    f.controller.cancel(); assert.deepEqual(snapshot(f),before)
  } finally { f.dispose() }
})
test('section handle consumes the gesture before downstream member picking', () => {
  const f=fixture(); let picks=0
  const pick=()=>picks++
  f.surface.addEventListener('pointerdown',pick)
  try {
    drag(f,findHit(f,'face'))
    assert.equal(picks,0)
    f.controller.cancel()
    f.surface.emit('pointerdown',0,0)
    assert.equal(picks,1)
  } finally { f.surface.removeEventListener('pointerdown',pick); f.dispose() }
})
test('analysis cleanup at gesture start cannot re-enable camera rotation', () => {
  const f=fixture(false, [8,6,11], orbit => { orbit.enabled=true })
  try {
    const before=snapshot(f)
    drag(f,findHit(f,'face'))
    assert.equal(f.orbit.enabled,false)
    assert.deepEqual(snapshot(f).camera,before.camera)
  } finally { f.dispose() }
})
test('moving focus from a toolbar button to the canvas does not cancel the first drag', () => {
  const f=fixture()
  f.surface.focus=()=>f.surface.emit('blur',0,0,{target:{tagName:'BUTTON'}})
  try {
    const before=snapshot(f)
    drag(f,findHit(f,'face'))
    assert.notDeepEqual(snapshot(f).bounds,before.bounds)
    assert.equal(f.controller.dragging,true)
  } finally { f.dispose() }
})
test('top orthographic view rotates from a corner and cancels without changing the model', () => {
  const f=fixture(true,[.5,20,.5])
  try {
    const before=snapshot(f)
    drag(f,findHit(f,'corner'))
    assert.notDeepEqual(snapshot(f).frame,before.frame)
    assert.deepEqual(snapshot(f).camera,before.camera)
    assert.deepEqual(snapshot(f).model,before.model)
    f.controller.cancel();assert.deepEqual(snapshot(f),before)
  } finally { f.dispose() }
})
test('Shift can select the back face without changing the clipping frame', () => {
  const f=fixture(),ray=new THREE.Raycaster()
  try {
    let rear
    for(let y=24;y<580&&!rear;y+=4)for(let x=24;x<780;x+=4){
      ray.setFromCamera(new THREE.Vector2(x/400-1,1-y/300),f.camera)
      const front=f.controller.gizmo.pick(ray,f.camera),back=f.controller.gizmo.pick(ray,f.camera,true)
      if(front?.kind==='face'&&back?.kind==='face'&&(front.axis!==back.axis||front.side!==back.side)){rear={x,y,...back};break}
    }
    assert.ok(rear,'a rear face must be reachable through Shift picking')
    const before=snapshot(f)
    f.surface.emit('pointerdown',rear.x,rear.y,{shiftKey:true})
    assert.equal(f.controller.dragging,true)
    f.controller.cancel();assert.deepEqual(snapshot(f),before)
  } finally { f.dispose() }
})
