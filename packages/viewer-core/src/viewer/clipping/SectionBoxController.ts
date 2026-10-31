import * as THREE from 'three'
import { ClipBoxGizmo, type ClipAxis, type ClipHandle } from './clipBoxGizmo'
import { beginClipRotation, updateClipRotation, type ClipRotationDrag } from './clipBoxRotation'
import { clipFrameFromBox, orientedClipPlanes, rotateOrientedClipFrame, type OrientedClipFrame } from './clipVolume'

type Camera = THREE.PerspectiveCamera | THREE.OrthographicCamera
type Options = {
  element: HTMLElement
  scene: THREE.Scene
  camera: () => Camera | null
  orbit: () => { enabled: boolean } | null
  bounds: () => THREE.Box3 | null
  canInteract?: () => boolean
  onChange: (planes: THREE.Plane[]) => void
  onStart?: () => void
  onFeedback?: (text: string | null) => void
}
type Drag = {
  pointerId: number
  orbit: { enabled: boolean } | null
  orbitEnabled: boolean
  frame: OrientedClipFrame
  bounds: THREE.Box3
} & ({ kind: 'rotate'; rotation: ClipRotationDrag } | {
  kind: 'face'; axis: ClipAxis; side: 0 | 1; direction: THREE.Vector3
  plane: THREE.Plane; startPoint: THREE.Vector3
})

/** Shared analysis/preview section box. Only its six planes change; never the source pose. */
export class SectionBoxController {
  readonly gizmo = new ClipBoxGizmo()
  enabled = false
  frame: OrientedClipFrame | null = null
  bounds: THREE.Box3 | null = null
  private drag: Drag | null = null
  private raycaster = new THREE.Raycaster()
  private bindings: Array<() => void> = []
  private readonly options: Options

  constructor(options: Options) {
    this.options = options
    options.scene.add(this.gizmo.group)
    this.gizmo.group.visible = false
    options.element.style.touchAction = 'none'
    const listen = (target: EventTarget, type: string, handler: EventListener) => {
      target.addEventListener(type, handler, true)
      this.bindings.push(() => target.removeEventListener(type, handler, true))
    }
    listen(options.element, 'pointerdown', this.down as EventListener)
    listen(options.element, 'pointermove', this.move as EventListener)
    listen(options.element, 'pointerup', this.up as EventListener)
    listen(options.element, 'pointercancel', this.cancelPointer as EventListener)
    listen(options.element, 'lostpointercapture', this.cancelPointer as EventListener)
    listen(options.element, 'pointerleave', (() => { if (!this.drag) this.clearHover() }) as EventListener)
    const view = options.element.ownerDocument.defaultView
    if (view) {
      listen(view, 'keydown', ((e: KeyboardEvent) => {
        if (e.key === 'Escape' && this.drag) { this.consume(e); this.cancel() }
      }) as EventListener)
      // Captured blur also fires when focus moves from the toolbar to the
      // canvas. Only losing the browser window should cancel this gesture.
      listen(view, 'blur', ((event: Event) => { if (event.target === view) this.cancel() }) as EventListener)
    }
    listen(options.element.ownerDocument, 'visibilitychange', (() => {
      if (options.element.ownerDocument.hidden) this.cancel()
    }) as EventListener)
  }

  get dragging() { return this.drag !== null }

  clear() {
    this.cancel()
    this.frame = null
    this.bounds = null
    this.publish()
  }

  setEnabled(enabled: boolean) {
    if (!enabled) this.cancel()
    this.enabled = enabled
    if (enabled && !this.bounds) this.fit()
    this.publish()
  }

  /** Explicit reset is the only time a live section box follows streaming bounds. */
  fit(box = this.options.bounds()) {
    this.cancel()
    if (!box || box.isEmpty() || ![...box.min.toArray(), ...box.max.toArray()].every(Number.isFinite)) return
    const full = box.clone()
    const size = full.getSize(new THREE.Vector3())
    // Match the tunnel's modest breathing room, scaled for small BIM objects.
    full.expandByScalar(THREE.MathUtils.clamp(Math.min(...size.toArray()) * .03, .01, .15))
    this.frame = clipFrameFromBox(full)
    this.bounds = full.clone()
    this.publish()
  }

  setRatio(ratio: number) {
    if (!this.frame || !this.bounds || !Number.isFinite(ratio)) return
    this.cancel()
    this.bounds.max.z = THREE.MathUtils.lerp(this.bounds.min.z, this.frame.fullBounds.max.z, THREE.MathUtils.clamp(ratio, 1, 100) / 100)
    this.publish()
  }

  /** Used by picking so clipped geometry cannot steal a visible hit. */
  contains(point: THREE.Vector3) {
    return !this.enabled || this.planes().every(plane => plane.distanceToPoint(point) >= -1e-7)
  }

  planes() {
    return this.enabled && this.frame && this.bounds ? orientedClipPlanes(this.frame, this.bounds) : []
  }

  update() {
    if (!this.enabled) return
    if (this.drag && this.options.canInteract?.() === false) this.cancel()
    const camera = this.options.camera()
    if (camera && this.bounds) this.gizmo.updateCamera(camera, this.options.element.clientHeight)
  }

  private publish() {
    this.gizmo.group.visible = this.enabled && !!this.frame && !!this.bounds
    if (this.frame && this.bounds) this.gizmo.updateBounds(this.bounds, this.frame.worldFromLocal)
    this.update()
    this.options.onChange(this.planes())
  }

  private pointer(event: PointerEvent) {
    const rect = this.options.element.getBoundingClientRect()
    return new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1)
  }

  private hit(event: PointerEvent) {
    const camera = this.options.camera()
    if (!this.enabled || !camera || this.options.canInteract?.() === false) return
    camera.updateMatrixWorld(true)
    this.update()
    this.raycaster.setFromCamera(this.pointer(event), camera)
    return this.gizmo.pick(this.raycaster, camera, event.shiftKey)
  }

  private feedback(hit: ClipHandle, dragging = false) {
    const text = hit.kind === 'face' ? '拖动面或箭头调整范围' : '拖动边或角旋转剖切框'
    this.options.onFeedback?.(dragging ? `${text} · Esc 取消` : `${text} · Shift 选择背面`)
  }

  private consume(event: Event) { event.preventDefault(); event.stopImmediatePropagation() }

  private down = (event: PointerEvent) => {
    if (this.drag) { this.consume(event); return }
    if (event.button !== 0 || event.isPrimary === false) return
    const hit = this.hit(event), camera = this.options.camera()
    if (!hit || !camera || !this.frame || !this.bounds) return
    const axis = new THREE.Vector3()
    axis[hit.axis] = 1
    axis.applyQuaternion(this.frame.worldFromLocal)
    const orbit = this.options.orbit()
    const base = { pointerId: event.pointerId, orbit, orbitEnabled: orbit?.enabled ?? false,
      frame: this.frame, bounds: this.bounds.clone() }
    if (hit.kind === 'face') {
      const normal = camera.getWorldDirection(new THREE.Vector3())
      normal.addScaledVector(axis, -normal.dot(axis))
      if (normal.lengthSq() < 1e-6) return
      const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal.normalize(), hit.point)
      const startPoint = this.raycaster.ray.intersectPlane(plane, new THREE.Vector3())
      if (!startPoint) return
      this.drag = { ...base, kind: 'face', axis: hit.axis, side: hit.side, direction: axis, plane, startPoint }
    } else {
      const rect = this.options.element.getBoundingClientRect()
      this.drag = { ...base, kind: 'rotate', rotation: beginClipRotation(this.raycaster.ray, camera,
        this.bounds.getCenter(new THREE.Vector3()).applyQuaternion(this.frame.worldFromLocal), axis,
        hit.point, this.pointer(event), new THREE.Vector2(rect.width, rect.height)) }
    }
    this.consume(event)
    this.options.onStart?.()
    if (orbit) orbit.enabled = false
    this.options.element.setPointerCapture(event.pointerId)
    this.options.element.focus({ preventScroll: true })
    this.options.element.style.cursor = 'grabbing'
    this.gizmo.setActive(hit)
    this.feedback(hit, true)
  }

  private move = (event: PointerEvent) => {
    const drag = this.drag
    if (!drag) {
      if (!this.enabled || event.buttons) return
      const hit = this.hit(event)
      if (hit) { this.gizmo.setActive(hit); this.options.element.style.cursor = 'grab'; this.feedback(hit) }
      else this.clearHover()
      return
    }
    this.consume(event)
    if (event.pointerId !== drag.pointerId) return
    if (event.pointerType === 'mouse' && !(event.buttons & 1)) { this.cancel(); return }
    const camera = this.options.camera()
    if (!camera) { this.cancel(); return }
    const pointer = this.pointer(event)
    this.raycaster.setFromCamera(pointer, camera)
    if (drag.kind === 'rotate') {
      this.frame = rotateOrientedClipFrame(drag.frame, drag.bounds, drag.rotation.axis,
        updateClipRotation(drag.rotation, this.raycaster.ray, pointer))
      const shift = this.frame.fullBounds.min.clone().sub(drag.frame.fullBounds.min)
      this.bounds = drag.bounds.clone().translate(shift)
    } else {
      const point = this.raycaster.ray.intersectPlane(drag.plane, new THREE.Vector3())
      if (!point) return
      const delta = point.sub(drag.startPoint).dot(drag.direction)
      const { axis, side } = drag
      const span = drag.frame.fullBounds.max[axis] - drag.frame.fullBounds.min[axis]
      const minimum = Math.min(.05, span * .01)
      this.bounds = drag.bounds.clone()
      if (side === 0) this.bounds.min[axis] = THREE.MathUtils.clamp(drag.bounds.min[axis] + delta, drag.frame.fullBounds.min[axis], drag.bounds.max[axis] - minimum)
      else this.bounds.max[axis] = THREE.MathUtils.clamp(drag.bounds.max[axis] + delta, drag.bounds.min[axis] + minimum, drag.frame.fullBounds.max[axis])
    }
    this.publish()
  }

  private up = (event: PointerEvent) => {
    if (this.drag?.pointerId !== event.pointerId || event.button !== 0) return
    this.consume(event); this.finish(false)
  }
  private cancelPointer = (event: PointerEvent) => {
    if (this.drag?.pointerId === event.pointerId) this.cancel()
  }
  private clearHover() {
    this.gizmo.setActive()
    if (this.enabled) this.options.element.style.cursor = ''
    this.options.onFeedback?.(null)
  }
  private finish(cancelled: boolean) {
    const drag = this.drag
    this.drag = null
    if (drag) {
      if (cancelled) { this.frame = drag.frame; this.bounds = drag.bounds }
      if (drag.orbit) drag.orbit.enabled = drag.orbitEnabled
      if (this.options.element.hasPointerCapture(drag.pointerId)) this.options.element.releasePointerCapture(drag.pointerId)
      this.publish()
    }
    this.clearHover()
  }
  cancel() { this.finish(true) }
  dispose() {
    this.cancel()
    this.bindings.forEach(remove => remove())
    this.bindings = []
    this.gizmo.dispose()
  }
}
