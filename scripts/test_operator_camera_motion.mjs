import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { operatorBarFrame, operatorFitDistance, operatorAxisHeading, wrappedAngle } from '../src/features/operator/operator-presentation.ts'
import { parse } from '@vue/compiler-sfc'

// Execute the production transition functions with real Three/OrbitControls,
// a deterministic clock and asymmetric targets. Browser recordings cover feel.
const source = fs.readFileSync(new URL('../src/features/operator/OperatorRebarViewer.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const ast = ts.createSourceFile('viewer.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = new Set(['cameraTransition', 'stopCameraTransition', 'transitionCamera', 'updateCameraTransition', 'followSelected', 'setViewMode', 'selectedFrame', 'focusSelected'])
const statements = ast.statements.filter(node => names.has(node.name?.text) || ts.isVariableStatement(node) && node.declarationList.declarations.some(d => names.has(d.name.getText(ast))))
assert.equal(statements.length, names.size)
const camera = new THREE.PerspectiveCamera(42, 1.6, .001, 1000)
const controls = new OrbitControls(camera)
controls.enableDamping = true
controls.target.set(1, 2, 3); camera.position.set(5, 8, 12); controls.update()
let now = 0
const props = { displayMode: 'result', selectedId: 'b', reducedMotion: false }
const bounds = [new THREE.Box3(new THREE.Vector3(6, 1, -3), new THREE.Vector3(8, 2, -2)), new THREE.Box3(new THREE.Vector3(-3, 4, 2), new THREE.Vector3(-2, 5, 4))]
const geometry = new THREE.BoxGeometry(4, .01, .01)
const longFrame = operatorBarFrame(geometry, 0, geometry.attributes.position.count)
const shortGeometry = new THREE.BoxGeometry(.01, .01, 1.1).translate(2, .02, -.8)
const shortFrame = operatorBarFrame(shortGeometry, 0, shortGeometry.attributes.position.count)
const barFrames = [longFrame, shortFrame]
const activePointers = new Set()
const context = { operatorFitDistance, operatorAxisHeading, wrappedAngle, barFrames, followPose: null, activePointers, THREE, Math, camera, controls, props, bounds, bars: [{ ifcGlobalId: 'b' }, { ifcGlobalId: 'c' }], viewMode: 'all', performance: { now: () => now }, emit() {} }
vm.createContext(context)
vm.runInContext(ts.transpileModule(statements.map(node => node.getText(ast)).join('\n') + `\nglobalThis.motion = {${[...names].filter(x => x !== 'cameraTransition').join(',')}, active: () => cameraTransition !== null}`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText, context)
const m = context.motion, offset = camera.position.clone().sub(controls.target)
const near = (a, b) => assert.ok(a.distanceTo(b) < 1e-8, `${a.toArray()} != ${b.toArray()}`)
m.followSelected(); assert.equal(m.active(), false, 'selection alone must not enable follow')
m.focusSelected()
assert.equal(m.active(), true); assert.equal(controls.enabled, false)
const start = controls.target.clone(), end = longFrame.center
m.updateCameraTransition(390)
near(controls.target, start.clone().lerp(end, .5))
m.updateCameraTransition(780)
const axial = camera.position.clone().sub(controls.target).normalize()
assert.ok(Math.abs(axial.y - Math.SQRT1_2) < 1e-8)
assert.ok(Math.abs(axial.x) > .7 && Math.abs(axial.z) < 1e-8)
// A user adjustment is relative to the bar axis, including relative zoom.
const adjusted = camera.position.clone().sub(controls.target).multiplyScalar(.8).applyAxisAngle(new THREE.Vector3(0, 1, 0), .2)
camera.position.copy(controls.target).add(adjusted); controls.update()
now = 1000; props.selectedId = 'c'; m.followSelected()
const intermediate = camera.position.clone(); m.updateCameraTransition(now); near(camera.position, intermediate)
m.updateCameraTransition(1780)
near(controls.target, shortFrame.center)
const shortOffset = camera.position.clone().sub(controls.target), shortHeading = operatorAxisHeading(shortFrame, Math.PI / 2)
assert.ok(Math.abs(wrappedAngle(Math.atan2(shortOffset.x, shortOffset.z) - shortHeading) - .2) < 1e-8)
assert.ok(Math.abs(shortOffset.clone().normalize().y - Math.SQRT1_2) < 1e-8)
const previousFit = operatorFitDistance(longFrame, adjusted.clone().normalize(), camera.fov, camera.aspect)
const nextFit = operatorFitDistance(shortFrame, shortOffset.clone().normalize(), camera.fov, camera.aspect)
assert.ok(Math.abs(shortOffset.length() / nextFit - adjusted.length() / previousFit) < 1e-8)
// Changing a manual angle must not reset magnification on an equal-size bar.
const parallelFrame = { ...shortFrame, center: shortFrame.center.clone().add(new THREE.Vector3(0.2, 0, 0)), corners: shortFrame.corners.map(p => p.clone().add(new THREE.Vector3(.2, 0, 0))) }
context.bars.push({ ifcGlobalId: 'd' }); barFrames.push(parallelFrame)
const manual = shortOffset.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), .6)
camera.position.copy(controls.target).add(manual); controls.update()
now = 1800; props.selectedId = 'd'; m.followSelected(); m.updateCameraTransition(2580)
near(camera.position.clone().sub(controls.target), manual)
// Restore the established short-bar pose for the rapid-selection scenario.
props.selectedId = 'c'; context.followPose = { heading: shortHeading, fitDistance: nextFit, frame: shortFrame }
camera.position.copy(shortFrame.center).add(shortOffset); controls.target.copy(shortFrame.center); controls.update()

// Rapid retargeting starts at the actual pose but keeps the intended end angle.
now = 2000; props.selectedId = 'b'; m.followSelected(); m.updateCameraTransition(2100)
const middle = camera.position.clone(); now = 2100; props.selectedId = 'c'; m.followSelected()
near(camera.position, middle); m.updateCameraTransition(2880)
near(camera.position.clone().sub(controls.target), shortOffset)
assert.equal(controls.enabled, true); assert.equal(controls.enableDamping, true)
// User takeover cancels remaining frames without moving the view.
now = 3000; props.selectedId = 'b'; m.followSelected(); m.updateCameraTransition(3100)
const stopped = camera.position.clone(); m.stopCameraTransition(); m.updateCameraTransition(4000); near(camera.position, stopped)
assert.equal(controls.enabled, true); assert.equal(controls.enableDamping, true)
props.reducedMotion = true; m.focusSelected(); assert.equal(m.active(), false); near(controls.target, end)
m.setViewMode('all'); props.selectedId = 'c'; m.followSelected(); near(controls.target, end)
m.setViewMode('top'); m.followSelected(); near(controls.target, end)
props.displayMode = 'scan'; m.setViewMode('selected'); m.focusSelected(); m.followSelected(); near(controls.target, end)
// A held pointer keeps ownership even if a keyboard reset is requested.
props.displayMode = 'result'; props.reducedMotion = false
activePointers.add(1)
const held = camera.position.clone()
m.transitionCamera(new THREE.Vector3(20, 8, 2), new THREE.Vector3(1, 1, 1), 680)
assert.equal(m.active(), false); near(camera.position, held); assert.equal(controls.enabled, true)
activePointers.clear()
// Reset from the opposite direction must orbit, not cross through the target.
controls.target.set(0, 0, 0); camera.position.set(-4, -6, -9); controls.update()
now = 2000; const goal = new THREE.Vector3(4, 6, 9)
m.transitionCamera(goal, new THREE.Vector3(), 680)
let lastDirection = camera.position.clone().normalize()
for (let t = 2000; t <= 2680; t += 2) {
 m.updateCameraTransition(t)
 assert.ok(camera.position.length() > 5, 'orbit may not collapse through its target')
 const direction = camera.position.clone().normalize()
 assert.ok(direction.dot(lastDirection) > .99, 'no angular flip between adjacent frames')
 lastDirection = direction
}
near(camera.position, goal)
// No DOM was attached, so OrbitControls has no browser listeners to dispose.
console.log('PASS: axial 45-degree framing, manual angle and relative zoom, continuous retargeting, user cancellation, reduced motion, and follow gates')
