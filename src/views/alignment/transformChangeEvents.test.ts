import assert from 'node:assert/strict'
import test from 'node:test'
import { Group, PerspectiveCamera, Scene } from 'three'
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'
// @ts-ignore Node's strip-types runner uses the explicit source extension.
import { bindTransformChangeEvents } from './transformChangeEvents.ts'

function editor() {
  const camera = new PerspectiveCamera(60, 1, 0.1, 100)
  camera.position.z = 5
  camera.updateMatrixWorld(true)
  const scene = new Scene()
  const model = new Group()
  const controller = new TransformControls(camera)
  scene.add(model, controller.getHelper())
  controller.attach(model)
  scene.updateMatrixWorld(true)
  return { scene, model, controller }
}

test('saved alignment stays clean when hovering, disabling, or reopening its editor', () => {
  const { model, controller } = editor()
  const savedMatrix = model.matrixWorld.clone()
  let dirty = false
  let displayUpdates = 0
  const unbind = bindTransformChangeEvents(controller, () => { dirty = true }, () => { displayUpdates++ })

  controller.axis = 'X' // Hovering the gizmo must not revoke the saved state.
  assert.equal(dirty, false)
  controller.detach() // Entering the denoise step closes the editor.
  controller.enabled = false
  assert.equal(dirty, false)
  controller.attach(model) // Returning to registration must also be safe.
  controller.enabled = true
  controller.setMode('rotate')
  controller.setSize(1.5)
  assert.equal(dirty, false)
  assert.ok(displayUpdates > 0, 'display changes should still render')
  assert.deepEqual(model.matrixWorld.elements, savedMatrix.elements)
  unbind()
})

test('a real gizmo drag invalidates the saved alignment', () => {
  const { scene, model, controller } = editor()
  let dirty = false
  bindTransformChangeEvents(controller, () => { dirty = true }, () => {})
  controller.axis = 'X'
  scene.updateMatrixWorld(true)
  // @ts-expect-error Three's internal method takes normalized coordinates, despite its DOM event typings.
  controller.pointerDown({ x: 0, y: 0, button: 0 })
  assert.equal(dirty, false, 'pressing without moving is not an edit')
  // @ts-expect-error Same normalized pointer contract; this runs without a browser DOM.
  controller.pointerMove({ x: 0.2, y: 0, button: -1 })
  assert.notEqual(model.position.x, 0, 'the real controller must move the model')
  assert.equal(dirty, true)
})
