import { Vector3, type OrthographicCamera, type PerspectiveCamera } from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

type NavigationCamera = PerspectiveCamera | OrthographicCamera

/** Orbit navigation with a usable working distance, even after repeated zooms. */
export class NavigationOrbitControls extends OrbitControls {
  // Present at runtime in Three r173; its older type package omits this member.
  declare state: number
  // OrbitControls calls update in its constructor, before these fields exist.
  private direction?: Vector3

  constructor(camera: NavigationCamera, element?: HTMLElement) {
    super(camera, element)
    this.screenSpacePanning = true
    this.zoomToCursor = true
    // Bubble phase is intentional: transform/measurement tools own capture and
    // can consume a gesture before either this handler or OrbitControls sees it.
    element?.addEventListener('wheel', this.navigateNearTarget, { passive: true })
    element?.addEventListener('pointerdown', this.prepareNavigation)
  }

  private workingDistance() {
    const camera = this.object as NavigationCamera
    const { x, y, z } = camera.position
    const precision = Math.max(Math.abs(x), Math.abs(y), Math.abs(z), 1) * Number.EPSILON * 64
    return Math.max(camera.near * 2, precision, 0.0001)
  }

  override update(deltaTime?: number) {
    const camera = this.object as NavigationCamera
    const floor = this.workingDistance()
    const minDistance = this.minDistance, minZoom = this.minZoom, maxZoom = this.maxZoom
    // Move a collapsed pivot forward along the existing sight line. Moving the
    // camera backward instead would cause a visible jump or flip at recovery.
    if (camera.position.distanceTo(this.target) < floor) {
      const direction = camera.getWorldDirection(this.direction ??= new Vector3())
      this.target.copy(camera.position).addScaledVector(direction, floor)
    }
    this.minDistance = Math.max(minDistance, Math.min(floor, this.maxDistance))
    if ('isOrthographicCamera' in camera) {
      const height = Math.abs(camera.top - camera.bottom)
      this.minZoom = Math.max(minZoom, height / Math.max(camera.far * 2, floor))
      this.maxZoom = Math.max(this.minZoom, Math.min(maxZoom, height / floor))
    }
    try {
      return super.update(deltaTime)
    } finally {
      // Bounds are derived afresh after fit/projection changes. Keep callers'
      // explicit limits intact, including when a later model is much smaller.
      this.minDistance = minDistance
      this.minZoom = minZoom
      this.maxZoom = maxZoom
    }
  }

  private prepareNavigation = () => {
    if (this.enabled) this.update()
  }

  private navigateNearTarget = (event: WheelEvent) => {
    if (!this.enabled || !this.enableZoom || this.state !== -1 || !Number.isFinite(event.deltaY)) return
    const camera = this.object as NavigationCamera
    this.update()
    if (!('isPerspectiveCamera' in camera) || event.deltaY >= 0) return
    const floor = this.workingDistance()
    // Respect deliberate caller distance limits. Only cross the navigation
    // safety floor, where ordinary multiplicative orbit zoom would stop.
    if (this.minDistance > floor || camera.position.distanceTo(this.target) > floor * 1.001) return
    const rect = this.domElement!.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const direction = this.direction ??= new Vector3()
    direction.set(
      (event.clientX - rect.left) / rect.width * 2 - 1,
      -(event.clientY - rect.top) / rect.height * 2 + 1,
      0.5,
    ).unproject(camera).sub(camera.position).normalize()
    const pixels = Math.abs(event.deltaY) * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1)
    const step = floor * (1 - Math.pow(0.95, Math.min(pixels, 1000) / 100 * this.zoomSpeed))
    camera.position.addScaledVector(direction, step)
    this.target.addScaledVector(direction, step)
    this.update()
  }

  override dispose() {
    this.domElement?.removeEventListener('wheel', this.navigateNearTarget)
    this.domElement?.removeEventListener('pointerdown', this.prepareNavigation)
    super.dispose()
  }
}
