import { Box3, MathUtils, Matrix4, Vector3 } from 'three'
import type { BufferGeometry, Object3D, OrthographicCamera, PerspectiveCamera } from 'three'
import type { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'

export type TransformHandleState = {
  mode: 'translate' | 'rotate'
  axis: string
  dragging: boolean
  /** Engineering-frame displacement (m), or signed angle (degrees). */
  translation?: [number, number, number]
  angle?: number
}

type Options = {
  element: HTMLElement
  translate: TransformControls
  rotate: TransformControls
  orbit: { enabled: boolean }
  onState: (state: TransformHandleState | null) => void
  onStart?: (mode: 'translate' | 'rotate') => void
  onFinish?: (cancelled: boolean, changed: boolean) => void
  onSize?: (scale: number) => void
  initialSize?: number
}

type NormalizedPointer = { x: number; y: number; button: number }
// Installed Three r173 takes NDC here; @types/three declares DOM PointerEvent.
// Keep that version-specific boundary in one place instead of casting events.
type RoutedControls = Omit<TransformControls, 'pointerHover' | 'pointerDown' | 'pointerMove' | 'pointerUp'> & {
  rotationAngle: number
  pointerHover: (pointer: NormalizedPointer) => void
  pointerDown: (pointer: NormalizedPointer) => void
  pointerMove: (pointer: NormalizedPointer) => void
  pointerUp: (pointer: NormalizedPointer) => void
}

/** One pointer owns one handle. The controls must not also listen to the canvas. */
export function bindTransformHandleInteraction(options: Options) {
  const { element, orbit } = options
  const translate = options.translate as unknown as RoutedControls
  const rotate = options.rotate as unknown as RoutedControls
  // Keep Three's real picking, drag planes and parent-aware transform math;
  // take ownership only of routing, highlighting and gesture lifetime.
  translate.disconnect()
  rotate.disconnect()
  element.style.touchAction = 'none'
  const view = element.ownerDocument.defaultView
  const sizing = createTransformHandleSizing(element, translate, rotate)
  let sizeScale = MathUtils.clamp(Number.isFinite(options.initialSize) ? options.initialSize! : 1, .6, 2)
  let hovered: RoutedControls | null = null
  let gesture: {
    controller: RoutedControls
    pointerId: number
    orbitEnabled: boolean
    worldStart: Vector3
    startMatrix: number[]
    diameter: number
  } | null = null

  function updateSize() {
    return sizing.update(sizeScale, gesture?.diameter)
  }

  function pointer(event: PointerEvent) {
    const rect = element.getBoundingClientRect()
    return { x: (event.clientX - rect.left) / rect.width * 2 - 1,
      y: -(event.clientY - rect.top) / rect.height * 2 + 1, button: event.button }
  }

  function usable(controller: RoutedControls) {
    return controller.enabled && controller.size > 0 && !!controller.object && controller.getHelper().visible
  }

  function clearHover() {
    hovered = null
    translate.axis = null
    rotate.axis = null
    element.style.cursor = ''
    options.onState(null)
  }

  function publish(controller: RoutedControls) {
    if (!controller.axis) return
    const state: TransformHandleState = {
      mode: controller === translate ? 'translate' : 'rotate',
      axis: controller.axis, dragging: !!gesture,
    }
    if (gesture && controller.object) {
      if (controller === translate) {
        const delta = controller.object.getWorldPosition(new Vector3()).sub(gesture.worldStart)
        // B(x,y,z)=(x,z,-y); only the feedback vector is converted here.
        state.translation = [delta.x, -delta.z, delta.y]
      } else {
        state.angle = MathUtils.radToDeg(controller.rotationAngle)
      }
    }
    options.onState(state)
  }

  function pick(event: PointerEvent) {
    // Never steal a camera drag which started away from the manipulator.
    if (event.type === 'pointermove' && event.buttons !== 0) return null
    updateSize()
    const p = pointer(event)
    for (const controller of [translate, rotate]) {
      if (usable(controller)) {
        controller.getHelper().updateMatrixWorld(true)
        controller.pointerHover(p)
      } else controller.axis = null
    }
    // Arrows and plane squares win their overlap with the broad ring picker.
    // The same winner is highlighted and used on pointerdown (including touch).
    hovered = translate.axis ? translate : rotate.axis ? rotate : null
    if (hovered !== translate) translate.axis = null
    if (hovered !== rotate) rotate.axis = null
    // pointerDown intersects the drag plane immediately. Update it after the
    // new axis is known, even if touch/click supplied no preceding hover frame.
    hovered?.getHelper().updateMatrixWorld(true)
    element.style.cursor = hovered ? 'grab' : ''
    if (hovered) publish(hovered)
    else options.onState(null)
    return hovered
  }

  function consume(event: Event) {
    event.preventDefault()
    event.stopImmediatePropagation()
  }

  function finish(cancelled: boolean) {
    if (!gesture) return
    const current = gesture
    current.controller.object?.updateMatrix()
    const changed = current.controller.object?.matrix.elements.some((value, index) => value !== current.startMatrix[index]) ?? false
    // Clear first: releasePointerCapture may synchronously emit lost capture.
    gesture = null
    if (cancelled && current.controller.object) {
      const enabled = current.controller.enabled
      current.controller.enabled = true
      current.controller.reset()
      current.controller.enabled = enabled
    }
    current.controller.pointerUp({ x: 0, y: 0, button: 0 })
    orbit.enabled = current.orbitEnabled
    if (element.hasPointerCapture(current.pointerId)) element.releasePointerCapture(current.pointerId)
    clearHover()
    options.onFinish?.(cancelled, !cancelled && changed)
    updateSize()
  }

  function down(event: PointerEvent) {
    if (gesture) { consume(event); return }
    if (event.button !== 0 || event.isPrimary === false) return
    const controller = pick(event)
    if (!controller || !controller.object || !controller.axis) return
    controller.object.updateMatrix()
    gesture = { controller, pointerId: event.pointerId, orbitEnabled: orbit.enabled,
      worldStart: controller.object.getWorldPosition(new Vector3()), startMatrix: controller.object.matrix.toArray(),
      diameter: updateSize() }
    orbit.enabled = false
    controller.rotationAngle = 0
    controller.pointerDown(pointer(event))
    if (!controller.dragging) { finish(false); return }
    consume(event)
    element.setPointerCapture(event.pointerId)
    element.focus({ preventScroll: true })
    options.onStart?.(controller === translate ? 'translate' : 'rotate')
    element.style.cursor = 'grabbing'
    publish(controller)
  }

  function move(event: PointerEvent) {
    if (!gesture) { pick(event); return }
    consume(event)
    if (event.pointerId !== gesture.pointerId) return
    if (!usable(gesture.controller)) { finish(true); return }
    if (event.pointerType === 'mouse' && !(event.buttons & 1)) { finish(true); return }
    // Touch/pen buttons are browser-dependent. Three expects -1 for movement.
    gesture.controller.pointerMove({ ...pointer(event), button: -1 })
    publish(gesture.controller)
  }

  function up(event: PointerEvent) {
    if (!gesture) return
    consume(event)
    if (event.pointerId === gesture.pointerId && event.button === 0) finish(false)
  }

  function cancelPointer(event: PointerEvent) {
    if (gesture?.pointerId === event.pointerId) finish(true)
  }
  function leave() { if (!gesture) clearHover() }
  function cancel() { finish(true); clearHover() }
  function keydown(event: KeyboardEvent) {
    if (gesture && event.key === 'Escape') { consume(event); finish(true) }
  }
  function wheel(event: WheelEvent) {
    if (gesture) { consume(event); return }
    if (!event.ctrlKey || !usable(translate) || !Number.isFinite(event.deltaY)) return
    consume(event)
    sizeScale = MathUtils.clamp(sizeScale * Math.exp(-Math.sign(event.deltaY) * .1), .6, 2)
    updateSize()
    clearHover()
    options.onSize?.(sizeScale)
  }

  element.addEventListener('pointerdown', down, true)
  element.addEventListener('pointermove', move, true)
  element.addEventListener('pointerup', up, true)
  element.addEventListener('pointercancel', cancelPointer, true)
  element.addEventListener('lostpointercapture', cancelPointer)
  element.addEventListener('pointerleave', leave)
  element.addEventListener('wheel', wheel, { capture: true, passive: false })
  view?.addEventListener('keydown', keydown, true)
  view?.addEventListener('blur', cancel)

  return {
    cancel,
    updateSize,
    resetSize() {
      if (gesture) return
      sizeScale = 1
      updateSize()
      clearHover()
      options.onSize?.(sizeScale)
    },
    refresh() {
      sizing.invalidateBounds()
      updateSize()
      if (gesture && !usable(gesture.controller)) cancel()
      else if (hovered && !usable(hovered)) clearHover()
    },
    dispose() {
      cancel()
      element.removeEventListener('pointerdown', down, true)
      element.removeEventListener('pointermove', move, true)
      element.removeEventListener('pointerup', up, true)
      element.removeEventListener('pointercancel', cancelPointer, true)
      element.removeEventListener('lostpointercapture', cancelPointer)
      element.removeEventListener('pointerleave', leave)
      element.removeEventListener('wheel', wheel, true)
      view?.removeEventListener('keydown', keydown, true)
      view?.removeEventListener('blur', cancel)
    },
  }
}

/** CSS-pixel envelope of the combined arrows and ring, independent of DPR.
 * Bounds are cached in the selected rigid object's local frame; a frame update
 * projects just eight corners, never traverses the BIM or streamed scan again.
 */
type SizedControls = Pick<TransformControls, 'object' | 'camera' | 'getHelper' | 'size' | 'setSize'>
function createTransformHandleSizing(element: HTMLElement, translate: SizedControls, rotate: SizedControls) {
  const arrowToRing = 1.35 / 1.55
  // Three r173: arrow tip radius <= .61 * arrowToRing; ring radius <= .51.
  // Enclose both visible geometries. Pickers share the same controller size.
  const glyphRadius = .54
  let boundsTarget: Object3D | null = null
  let corners: Vector3[] = []
  const point = new Vector3(), pivot = new Vector3(), cameraPosition = new Vector3()

  function cacheBounds(target: Object3D) {
    boundsTarget = target
    target.updateWorldMatrix(true, true)
    const inverse = target.matrixWorld.clone().invert()
    const relative = new Matrix4(), box = new Box3(), part = new Box3()
    target.traverse(child => {
      const mesh = child as Object3D & { geometry?: BufferGeometry; isInstancedMesh?: boolean; boundingBox?: Box3 | null; computeBoundingBox?: () => void }
      if (!mesh.geometry) return
      if (mesh.isInstancedMesh) {
        if (!mesh.boundingBox) mesh.computeBoundingBox?.()
      } else if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox()
      const geometryBounds = mesh.isInstancedMesh ? mesh.boundingBox : mesh.geometry.boundingBox
      if (!geometryBounds || geometryBounds.isEmpty()) return
      relative.multiplyMatrices(inverse, child.matrixWorld)
      box.union(part.copy(geometryBounds).applyMatrix4(relative))
    })
    corners = []
    if (box.isEmpty()) return
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      corners.push(new Vector3(x, y, z))
    }
  }

  return {
    invalidateBounds() { boundsTarget = null },
    update(scale: number, frozenDiameter?: number) {
      const target = translate.object
      if (!target || !translate.getHelper().visible) return 0
      const width = element.clientWidth, height = element.clientHeight
      if (!(width > 0 && height > 0)) return 0
      const camera = translate.camera as PerspectiveCamera | OrthographicCamera
      camera.updateWorldMatrix(true, false)
      target.updateWorldMatrix(true, false)
      if (boundsTarget !== target) cacheBounds(target)
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const corner of corners) {
        point.copy(corner).applyMatrix4(target.matrixWorld).applyMatrix4(camera.matrixWorldInverse)
        // A partially clipped/close-up object is already large enough to use
        // the default ceiling; never project through or behind the eye plane.
        if (-point.z <= camera.near) { maxX = Infinity; minX = 0; break }
        point.applyMatrix4(camera.projectionMatrix)
        minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x)
        minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y)
      }
      const objectPixels = corners.length ? Math.max((maxX - minX) * width / 2, (maxY - minY) * height / 2) : 600
      const baseDiameter = MathUtils.clamp(objectPixels * .30, 140, 220)
      const ceiling = Math.min(280, Math.min(width, height) * .30)
      const diameter = MathUtils.clamp(frozenDiameter ?? baseDiameter * scale, Math.min(120, ceiling), ceiling)
      const radius = diameter / 2
      pivot.setFromMatrixPosition(target.matrixWorld).applyMatrix4(camera.matrixWorldInverse)
      const depth = -pivot.z
      const focalPixels = Math.max(Math.abs(camera.projectionMatrix.elements[0]!) * width / 2,
        Math.abs(camera.projectionMatrix.elements[5]!) * height / 2)
      let worldRadius = 0, nativeFactor = 1
      if (depth > camera.near && Number.isFinite(focalPixels) && focalPixels > 0) {
        if ('isOrthographicCamera' in camera && camera.isOrthographicCamera) {
          worldRadius = radius / focalPixels
          nativeFactor = (camera.top - camera.bottom) / camera.zoom / 4
        } else {
          const perspective = camera as PerspectiveCamera
          // Bound the projected sphere about the pivot, including perspective
          // depth variation and off-axis magnification (distance alone fails).
          const offset = Math.hypot(pivot.x, pivot.y) / depth
          worldRadius = Math.min(radius * depth / Math.hypot(focalPixels, focalPixels * offset + radius),
            Math.max(0, depth - camera.near * 1.01))
          cameraPosition.setFromMatrixPosition(camera.matrixWorld)
          point.setFromMatrixPosition(target.matrixWorld)
          nativeFactor = point.distanceTo(cameraPosition) * Math.min(1.9 * Math.tan(Math.PI * perspective.fov / 360) / camera.zoom, 7) / 4
        }
      }
      const ringSize = nativeFactor > 0 ? worldRadius / glyphRadius / nativeFactor : 0
      rotate.setSize(Number.isFinite(ringSize) ? ringSize : 0)
      translate.setSize(rotate.size * arrowToRing)
      return diameter
    },
  }
}

/** Three world Y is engineering Z; world Z is engineering -Y. */
export function styleEngineeringTransformHandles(controller: TransformControls) {
  controller.getHelper().traverse((object: any) => {
    if (!object.material) return
    const materials = Array.isArray(object.material) ? object.material : [object.material]
    for (const material of materials) {
      material.depthTest = false
      material.depthWrite = false
      // Recolor shared materials once, before Three captures its hover colors.
      if (!material.color || material.userData.engineeringAxisColor) continue
      const color = material.color.getHex()
      if (color === 0x00ff00) material.color.setHex(0x3986ff)
      if (color === 0x0000ff) material.color.setHex(0x36c77b)
      if (color === 0xff0000) material.color.setHex(0xef5350)
      material.userData.engineeringAxisColor = true
    }
  })
}
