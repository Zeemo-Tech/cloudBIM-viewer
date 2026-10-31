import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { test } from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { createInspectionAppearance } from '../packages/alignment/src/rebarInspectionAppearance.ts'

// Run the actual page handlers with real Three.js intersections. These catch
// premature selection before OrbitControls can distinguish a click from a drag.
const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8')
  .split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('page.ts', source, ts.ScriptTarget.Latest, true)
const names = ['handleViewportPointerDown', 'handleRebarInspectionPointerDown', 'handleRebarInspectionClick', 'canPickInspectionBar',
  'getComparisonBarIdFromHit', 'onViewportPointerMove', 'onViewportPointerUp', 'onViewportPointerCancel',
  'cancelInspectionPointerGesture', 'inspectionCandidatesAt', 'handleRebarInspectionDoubleClick', 'onAnalysisKeydown']
const functions = ast.statements.filter(n => ts.isFunctionDeclaration(n) && names.includes(n.name?.text))
const compiled = ts.transpileModule(functions.map(n => n.getText(ast)).join('\n').replaceAll('import.meta.env.DEV', 'false'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const helperUrl = new URL('../packages/alignment/src/rebarInspectionInteraction.ts', import.meta.url)
const helpers = existsSync(helperUrl) ? await import(helperUrl) : {}
const ref = value => ({ value })
function fixture() {
  const camera = new THREE.PerspectiveCamera(50, 1, .01, 100)
  camera.position.set(0, 0, 4); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true)
  const bar = { ifcGlobalId: 'target', instanceIds: [7], vertexStart: 0, vertexCount: 3 }
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial())
  mesh.userData.ifcGlobalId = bar.ifcGlobalId
  const group = new THREE.Group(); group.add(mesh); group.updateMatrixWorld(true)
  const picked = [], clipStarts = [], focused = []
  const s = { THREE, ...helpers, comparisonBars: ref([bar]), comparison: ref({ bars: [bar] }),
    activeWorkflowStep: ref(3), analysisMode: ref('none'), rebarDebugActive: ref(false), rebarInspectionActive: ref(true), selectedComparisonBarId: ref(''),
    rebarInspectionAutoplay: ref(false), rebarInspectionClickReady: false,
    activeCamera: camera, controls: { enabled: true, target: new THREE.Vector3() }, raycaster: new THREE.Raycaster(),
    viewportEl: ref({ clientHeight: 1000, getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000, right: 1000, bottom: 1000 }) }),
    renderer: { domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000, right: 1000, bottom: 1000 }) } },
    c2mSceneGroup: group, c2mAnalysisSession: null, bimPivot: null, denoisePreview: null, pointcloudGroup: null,
    sectionBox: null, contentGroup: group, enableClipping: ref(false), showBounds: ref(false), clipHandlePickers: [],
    beginClipDrag: (_, handle) => clipStarts.push(handle),
    pointcloudPointSize: ref(2), pointcloudMaxDim: 2, orthoViewSize: 2,
    isPerspectiveCamera: c => c.isPerspectiveCamera,
    getPointerNdc: e => new THREE.Vector2(e.clientX / 500 - 1, 1 - e.clientY / 500),
    guessIfcId: () => '', findMetadataElementById: () => null,
    selectInspectionBarById: id => { picked.push(id); s.rebarInspectionActive.value = true; return true }, stopRebarCameraAnimation: () => {},
    stopRebarAutoplay: () => { s.rebarInspectionAutoplay.value = false },
    focusComparisonBar: bar => focused.push(bar.ifcGlobalId),
    exitRebarInspection: () => { s.rebarInspectionActive.value = false; s.rebarInspectionAutoplay.value = false },
    clipDragState: null, clipPointerCaptureId: null, analysisPointerDown: null,
    rebarInspectionClick: helpers.createInspectionClickGesture?.(),
  }
  const api = runInNewContext(`${compiled}\n({ ${functions.map(n => n.name.text).join(', ')} })`, s)
  const event = (overrides = {}) => ({ pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 1,
    clientX: 500, clientY: 500, timeStamp: 0, preventDefault() {}, ...overrides })
  return { api, s, picked, event, mesh, bar, clipStarts, focused }
}

test('double-click enters and focuses a member without a selection-mode switch', () => {
  const { api, s, event, picked, focused } = fixture()
  s.rebarInspectionActive.value = false
  api.handleViewportPointerDown(event())
  api.onViewportPointerUp(event({ buttons: 0 }))
  assert.deepEqual(picked, [], 'a single click outside inspection does not enter')
  api.handleViewportPointerDown(event())
  api.onViewportPointerUp(event({ buttons: 0 }))
  api.handleRebarInspectionDoubleClick(event())
  assert.deepEqual(picked, ['target'])
  assert.deepEqual(focused, ['target'])
  assert.equal(s.rebarInspectionActive.value, true)
})

test('double-click on blank space and Escape both exit inspection', () => {
  for (const escape of [false, true]) {
    const { api, s, event } = fixture()
    s.rebarInspectionAutoplay.value = true
    if (escape) api.onAnalysisKeydown({ key: 'Escape' })
    else {
      api.handleViewportPointerDown(event({ clientX: 10, clientY: 10 }))
      api.onViewportPointerUp(event({ clientX: 10, clientY: 10, buttons: 0 }))
      api.handleRebarInspectionDoubleClick(event({ clientX: 10, clientY: 10 }))
    }
    assert.equal(s.rebarInspectionActive.value, false)
    assert.equal(s.rebarInspectionAutoplay.value, false)
  }
})

test('double-click after a drag or over a clipping handle never starts inspection', () => {
  const { api, s, event, focused, mesh } = fixture()
  api.handleViewportPointerDown(event())
  api.onViewportPointerMove(event({ clientX: 600 }))
  api.onViewportPointerUp(event({ buttons: 0 }))
  api.handleRebarInspectionDoubleClick(event())
  assert.deepEqual(focused, [])
  s.enableClipping.value = true; s.showBounds.value = true
  // SectionBoxController captures this gesture and calls interruptRebarInspection.
  // Its real ray picking/propagation is exercised in test_section_box.mjs.
  api.cancelInspectionPointerGesture()
  api.handleRebarInspectionDoubleClick(event())
  assert.deepEqual(focused, [])
})

test('pointer down over a member leaves selection and orbit alone until release', () => {
  const { api, s, event, picked } = fixture()
  api.handleViewportPointerDown(event())
  assert.deepEqual(picked, [], 'pressing a member must not select or start a camera flight')
  assert.equal(s.controls.enabled, true)
  api.onViewportPointerUp(event({ buttons: 0, timeStamp: 100 }))
  assert.deepEqual(picked, ['target'])
})

test('a clipping gesture cancels an earlier completed inspection click', () => {
  const { api, s, event, focused } = fixture()
  api.handleViewportPointerDown(event())
  api.onViewportPointerUp(event({ buttons: 0 }))
  assert.equal(s.rebarInspectionClickReady, true)
  api.cancelInspectionPointerGesture()
  api.handleRebarInspectionDoubleClick(event())
  assert.deepEqual(focused, [])
})

test('an orbit drag that returns to its starting pixel never selects a member', () => {
  const { api, event, picked } = fixture()
  api.handleRebarInspectionPointerDown(event())
  api.onViewportPointerMove(event({ clientX: 550, timeStamp: 40 }))
  api.onViewportPointerMove(event({ clientX: 500, timeStamp: 80 }))
  api.onViewportPointerUp(event({ buttons: 0, timeStamp: 100 }))
  assert.deepEqual(picked, [])
})

test('legacy merged comparison triangles resolve ownership from original vertex ranges', () => {
  const { api, mesh } = fixture()
  mesh.userData = {}
  mesh.geometry = new THREE.BufferGeometry()
  mesh.geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3))
  assert.equal(api.getComparisonBarIdFromHit({ object: mesh, faceIndex: 0, face: { a: 0, b: 1, c: 2 } }), 'target')
})

for (const reason of ['cancel', 'outside', 'multitouch', 'wheel', 'right-button', 'mode-change']) {
  test(`${reason} abandons the pending selection`, () => {
    const { api, event, picked, s } = fixture()
    api.handleRebarInspectionPointerDown(event())
    if (reason === 'cancel') api.onViewportPointerCancel(event())
    if (reason === 'outside') api.onViewportPointerMove(event({ clientX: 1100 }))
    if (reason === 'multitouch') api.handleRebarInspectionPointerDown(event({ pointerId: 2, isPrimary: false, pointerType: 'touch' }))
    if (reason === 'wheel') api.cancelInspectionPointerGesture()
    if (reason === 'right-button') api.handleRebarInspectionPointerDown(event({ button: 2, buttons: 3 }))
    if (reason === 'mode-change') s.activeWorkflowStep.value = 2
    api.onViewportPointerUp(event({ buttons: 0 }))
    assert.deepEqual(picked, [])
  })
}

test('metadata names do not mask an IFC id carried by another field', () => {
  const { api, mesh } = fixture()
  mesh.userData.ifcGlobalId = 'unrelated'
  mesh.name = 'target'
  assert.equal(api.getComparisonBarIdFromHit({ object: mesh }), 'target')
})

test('BIM local vertex indices must never be mistaken for merged result ownership', () => {
  const { api, mesh, s } = fixture()
  mesh.userData = {}; s.c2mSceneGroup = new THREE.Group()
  assert.equal(api.getComparisonBarIdFromHit({ object: mesh, faceIndex: 0, face: { a: 0, b: 1, c: 2 } }), '')
})

for (const orthographic of [false, true]) {
  test(`${orthographic ? 'orthographic' : 'perspective'}: thin, back-facing and hidden members pick correctly`, () => {
    const camera = orthographic ? new THREE.OrthographicCamera(-2, 2, 2, -2, .01, 100) : new THREE.PerspectiveCamera(50, 1, .01, 100)
    camera.position.z = 4; camera.updateMatrixWorld(true)
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(.002, 2), new THREE.MeshBasicMaterial({ side: THREE.BackSide }))
    mesh.userData.id = 'thin'
    const root = new THREE.Group(); root.add(mesh)
    const resolve = hit => hit.object.userData.id
    const pick = (x = 0) => helpers.pickInspectionCandidates([root], camera, new THREE.Vector2(x, 0), { width: 1000, height: 1000 }, resolve)
    assert.equal(pick()[0]?.id, 'thin', 'back faces can be picked without changing the rendered side')
    assert.equal(mesh.material.side, THREE.BackSide)
    assert.equal(pick(4 / 500)[0]?.id, 'thin', 'a four-pixel near miss still selects the thin bar')
    assert.equal(pick(12 / 500).length, 0, 'do not grab neighboring members well outside the aperture')
    mesh.visible = false
    assert.equal(pick().length, 0)
    mesh.visible = true
    root.visible = false
    assert.equal(pick().length, 0)
  })
}

test('projected point distance rejects off-cursor foreground samples and honors clipping', () => {
  const camera = new THREE.PerspectiveCamera(50, 1, .01, 100)
  camera.position.z = 10; camera.updateMatrixWorld(true)
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([.02, 0, 9, 0, 0, 0], 3))
  const material = new THREE.PointsMaterial()
  const points = new THREE.Points(geometry, material)
  const pick = () => helpers.pickInspectionCandidates([points], camera, new THREE.Vector2(), { width: 1000, height: 1000 }, hit => String(hit.index))
  assert.deepEqual(pick().map(hit => hit.id), ['1'], 'near-camera point is over 20px away despite its broad-phase ray hit')
  material.clippingPlanes = [new THREE.Plane(new THREE.Vector3(0, 0, 1), -1)]
  assert.equal(pick().length, 0)
})

test('Alt-click cycles real depth candidates without automatically moving the camera', () => {
  const { api, mesh, s, event, picked } = fixture()
  const rear = mesh.clone(); rear.position.z = -2; rear.userData.ifcGlobalId = 'rear'
  s.c2mSceneGroup.add(rear)
  s.comparisonBars.value.push({ ifcGlobalId: 'rear', instanceIds: [] })
  s.selectedComparisonBarId.value = 'target'
  api.handleRebarInspectionPointerDown(event({ altKey: true }))
  api.onViewportPointerUp(event({ altKey: true, buttons: 0 }))
  assert.deepEqual(picked, ['rear'])
})

for (const pointCloud of [false, true]) {
  test(`${pointCloud ? 'scan' : 'merged mesh'}: opaque selected evidence, translucent context, exact restoration`, () => {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 0, 0, 3, 0, 0, 2, 1, 0], 3))
    geometry.setAttribute('instance', new THREE.Float32BufferAttribute([1, 1, 1, 2, 2, 2], 1))
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(Array.from({ length: 18 }, (_, i) => (i % 3 + 1) / 4), 3))
    geometry.setIndex([0, 1, 2, 3, 4, 5])
    const material = pointCloud ? new THREE.PointsMaterial({ size: 3, vertexColors: true }) : new THREE.MeshBasicMaterial({ vertexColors: true })
    const object = pointCloud ? new THREE.Points(geometry, material) : new THREE.Mesh(geometry, material)
    const originalIndex = geometry.index, positions = geometry.getAttribute('position'), ownership = geometry.getAttribute('instance')
    const colors = geometry.attributes.color.array.slice()
    const appearance = createInspectionAppearance()
    appearance.fade(object, .2, index => index < 3)
    assert.equal(object.material.opacity, .2)
    assert.equal(object.material.depthWrite, false)
    const selected = object.children[0]
    assert.equal(selected.material.opacity, 1)
    assert.equal(selected.material.depthWrite, true)
    assert.equal(material.vertexColors, true, 'source materials must keep their classification/deviation colors')
    let disposed = false
    if (pointCloud) {
      for (const displayed of [object.material, selected.material]) {
        assert.equal(displayed.vertexColors, false)
        assert.equal(displayed.color.r, displayed.color.g)
        assert.equal(displayed.color.g, displayed.color.b)
        assert.notEqual(displayed, material)
      }
      selected.material.addEventListener('dispose', () => { disposed = true })
      appearance.setPointSize(5)
      assert.equal(selected.material.size, 5)
      assert.equal(object.material.size, 5)
      assert.equal(material.size, 5)
    } else {
      assert.equal(object.material.vertexColors, true)
      assert.equal(selected.material, material, 'selected mesh retains the Scan-vs-BIM palette')
    }
    assert.deepEqual(geometry.attributes.color.array, colors)
    assert.deepEqual([...selected.geometry.index.array], [0, 1, 2])
    assert.equal(selected.geometry.getAttribute('position'), positions)
    appearance.fade(object, .1, index => index >= 3)
    assert.equal(object.children.length, 1)
    assert.deepEqual([...selected.geometry.index.array], [3, 4, 5])
    assert.equal(geometry.index, originalIndex)
    assert.equal(geometry.getAttribute('instance'), ownership)
    appearance.clear()
    assert.equal(object.material, material)
    assert.equal(object.children.length, 0)
    assert.equal(material.opacity, 1)
    assert.equal(geometry.index, originalIndex)
    assert.equal(material.vertexColors, true)
    assert.deepEqual(geometry.attributes.color.array, colors)
    if (pointCloud) assert.equal(disposed, true)
  })
}

test('shared materials remain opaque on the selected component', () => {
  const material = new THREE.MeshBasicMaterial()
  const selected = new THREE.Mesh(new THREE.BoxGeometry(), material), context = selected.clone()
  const appearance = createInspectionAppearance()
  appearance.fade(context, .2)
  assert.equal(selected.material.opacity, 1)
  assert.notEqual(context.material, material)
  appearance.clear()
  assert.equal(context.material, material)
})
