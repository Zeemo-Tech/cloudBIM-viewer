<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import type { C2MResult, RebarComparisonBar } from '@cloudbim/viewer-core'
import { mapOperatorGeometry, operatorBarState, validateOperatorPlySurface } from './operator-geometry'

const props = withDefaults(defineProps<{
  result: C2MResult | null
  selectedId: string
  tolerance: number | null
  loadGeometry: (result: C2MResult) => Promise<ArrayBuffer>
  displayMode?: 'neutral' | 'scan' | 'result'
  scanProgress?: number
  reducedMotion?: boolean
}>(), { displayMode: 'result', scanProgress: 0, reducedMotion: false })
const emit = defineEmits<{ select: [id: string]; loaded: [value: boolean]; error: [message: string] }>()
const host = ref<HTMLDivElement>()
const loading = ref(false)
const errorMessage = ref('')
const hasModel = ref(false)
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial[]> | null = null
let bounds: THREE.Box3[] = []
let pickIds: string[] = []
let bars: RebarComparisonBar[] = []
let requestToken = 0
let animationFrame = 0
let observer: ResizeObserver | null = null
let viewMode: 'all' | 'top' | 'selected' = 'all'
let layoutClass = -1
let mounted = false
let contextUnavailable = false
let scanOverlay: THREE.Group | null = null
let scanFan: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial> | null = null
let scanBox: THREE.Box3 | null = null
let scanAxis: 'x' | 'z' = 'x'
let scanCrossAxis: 'x' | 'z' = 'z'
let scanFrame: THREE.Box3 | null = null
let scanStart = 0
let scanEnd = 1
const scanUniforms = {
  scanAxis: { value: new THREE.Vector3(1, 0, 0) },
  scanPosition: { value: 0 },
  bandWidth: { value: 0.01 },
  scanOpacity: { value: 1 },
  emitterCoordinate: { value: 0 },
  emitterHeight: { value: 1 },
  floorHeight: { value: 0 },
}
const raycaster = new THREE.Raycaster()
const pointer = new THREE.Vector2()
const axisY = new THREE.Vector3(0, 1, 0)
let pointerStart: { x: number; y: number; id: number } | null = null
let dragged = false
const activePointers = new Set<number>()

function disposeModel() {
  disposeScanOverlay()
  if (mesh) {
    scene?.remove(mesh)
    mesh.geometry.dispose()
    mesh.material.forEach(material => material.dispose())
  }
  mesh = null
  bounds = []; bars = []; pickIds = []
  hasModel.value = false
}

function fail(message: string) {
  errorMessage.value = message
  loading.value = false
  hasModel.value = false
  emit('loaded', false)
  emit('error', message)
}

function updateColors() {
  if (!mesh) return
  bars.forEach((bar, i) => {
    const selected = props.selectedId === bar.ifcGlobalId || props.selectedId === bar.designBarId
    const state = operatorBarState(bar, props.tolerance)
    const showResult = props.displayMode === 'result'
    const color = !showResult ? '#8292a6' : selected ? '#1674e8' : state === 'missing' ? '#d99b23' : state === 'outlier' || state === 'review' ? '#d74f55' : '#8292a6'
    mesh!.material[i]!.color.set(color)
    mesh!.material[i]!.emissive.set(showResult && selected ? '#123a78' : '#000000')
    mesh!.material[i]!.emissiveIntensity = showResult && selected ? 0.25 : 0
  })
}

function updateAriaLabel() {
  const state = props.displayMode === 'result' ? '钢筋三维定位视图。点击选择钢筋；' : props.displayMode === 'scan' ? '钢筋三维扫描进行中。' : '钢筋三维模型，等待检测。'
  renderer?.domElement.setAttribute('aria-label', `${state}拖动旋转，滚轮或双指缩放；方向键旋转，加减键缩放，R键恢复全景。`)
}

function disposeScanOverlay() {
  if (scanOverlay) {
    scene?.remove(scanOverlay)
    scanOverlay.traverse(object => {
      if (!(object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Points)) return
      // The surface light shares the original metre-based geometry.
      if (object.geometry !== mesh?.geometry) object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach(material => material.dispose())
    })
  }
  scanOverlay = null; scanFan = null; scanBox = null; scanFrame = null
}

function updateScanPosition() {
  if (!scanBox || !scanFan) return
  const progress = THREE.MathUtils.clamp(Number.isFinite(props.scanProgress) ? props.scanProgress : 0, 0, 1)
  const position = THREE.MathUtils.lerp(scanStart, scanEnd, progress)
  scanUniforms.scanPosition.value = position
  scanUniforms.scanOpacity.value = Math.min(progress / 0.035, (1 - progress) / 0.035, 1)
  // Only the lower fan edge moves. The overhead projector stays fixed, like
  // Blender V5.1's stationary camera with internal swing-line acquisition.
  const vertices = scanFan.geometry.getAttribute('position') as THREE.BufferAttribute
  if (scanAxis === 'x') { vertices.setX(1, position); vertices.setX(2, position) }
  else { vertices.setZ(1, position); vertices.setZ(2, position) }
  vertices.needsUpdate = true
}

function syncDisplayMode() {
  updateColors()
  updateAriaLabel()
  const wasScanning = Boolean(scanOverlay)
  disposeScanOverlay()
  if (wasScanning && mesh?.geometry.boundingBox) resetView()
  if (!scene || !mesh?.geometry.boundingBox || props.displayMode !== 'scan' || props.reducedMotion) return

  // C2M PLY retains BIM/GLB XYZ metres: IFC (x,y,z) -> GLB (x,z,-y)
  // (services/mesh-service/rebar_bim.py). +Y is up; scan only along X/Z.
  // No axis flip, translation, scale, or alignment is applied to the steel.
  scanBox = mesh.geometry.boundingBox.clone()
  const size = scanBox.getSize(new THREE.Vector3())
  const center = scanBox.getCenter(new THREE.Vector3())
  scanAxis = size.x >= size.z ? 'x' : 'z'
  scanCrossAxis = scanAxis === 'x' ? 'z' : 'x'
  scanUniforms.scanAxis.value.set(scanAxis === 'x' ? 1 : 0, 0, scanAxis === 'z' ? 1 : 0)
  const extent = Math.max(size[scanAxis], 0.001)
  scanUniforms.bandWidth.value = Math.max(extent * 0.009, 0.002)
  scanOverlay = new THREE.Group()

  // A sharp blue line appears only where the projected sheet meets actual
  // steel. Open mesh gaps stay open; the brief afterglow never means "passed".
  const surfaceMaterial = new THREE.ShaderMaterial({
    uniforms: scanUniforms,
    vertexShader: `uniform vec3 scanAxis; varying float scanCoordinate; varying float surfaceHeight;
      void main() { scanCoordinate = dot(position, scanAxis); surfaceHeight = position.y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform float scanPosition; uniform float bandWidth; uniform float scanOpacity;
      uniform float emitterCoordinate; uniform float emitterHeight; uniform float floorHeight;
      varying float scanCoordinate; varying float surfaceHeight;
      void main() {
        float heightFraction = clamp((surfaceHeight - floorHeight) / (emitterHeight - floorHeight), 0.0, 1.0);
        float projectedPosition = mix(scanPosition, emitterCoordinate, heightFraction);
        float distance = projectedPosition - scanCoordinate;
        float core = 1.0 - smoothstep(bandWidth * 0.18, bandWidth, abs(distance));
        float trail = distance < 0.0 ? 0.0 : (1.0 - smoothstep(0.0, bandWidth * 6.0, distance)) * 0.13;
        float alpha = (core * 0.94 + trail) * scanOpacity;
        if (alpha < 0.005) discard;
        gl_FragColor = vec4(mix(vec3(0.05, 0.30, 0.92), vec3(0.40, 0.75, 1.0), core), alpha);
      }`,
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
    toneMapped: false,
  })
  const surface = new THREE.Mesh(mesh.geometry, surfaceMaterial)
  surface.renderOrder = 2
  scanOverlay.add(surface)

  const emitterHeight = scanBox.max.y + Math.max(extent * 0.27, size.y * 0.65, 0.08)
  scanUniforms.emitterCoordinate.value = center[scanAxis]
  scanUniforms.emitterHeight.value = emitterHeight
  scanUniforms.floorHeight.value = scanBox.min.y
  const topFraction = size.y / (emitterHeight - scanBox.min.y)
  // Extend the footprint so the tilted sheet reaches elevated bars at both ends.
  const projectionScale = 1 / Math.max(1 - topFraction, 0.1)
  scanStart = center[scanAxis] - (size[scanAxis] / 2 + scanUniforms.bandWidth.value) * projectionScale
  scanEnd = center[scanAxis] + (size[scanAxis] / 2 + scanUniforms.bandWidth.value) * projectionScale
  const apex = center.clone(); apex.y = emitterHeight
  const left = center.clone(); left.y = scanBox.min.y
  const right = left.clone()
  left[scanCrossAxis] = center[scanCrossAxis] - size[scanCrossAxis] / 2 * projectionScale
  right[scanCrossAxis] = center[scanCrossAxis] + size[scanCrossAxis] / 2 * projectionScale
  left[scanAxis] = right[scanAxis] = scanStart
  // One light sheet, not a bounding plane or box. It stops at the mesh envelope;
  // the surface shader above supplies the actual steel intersection pattern.
  const fanGeometry = new THREE.BufferGeometry().setFromPoints([apex, left, right])
  fanGeometry.setAttribute('uv', new THREE.Float32BufferAttribute([0.5, 1, 0, 0, 1, 0], 2))
  // The triangle changes shape without reallocating. Fix its conservative bounds
  // once so culling is correct at either end of the sweep.
  scanFrame = scanBox.clone().expandByPoint(apex).expandByPoint(left).expandByPoint(right)
  const farCorner = right.clone(); farCorner[scanAxis] = scanEnd
  scanFrame.expandByPoint(farCorner)
  fanGeometry.boundingBox = scanFrame.clone()
  fanGeometry.boundingSphere = scanFrame.getBoundingSphere(new THREE.Sphere())
  const fanMaterial = new THREE.ShaderMaterial({
    uniforms: { scanOpacity: scanUniforms.scanOpacity },
    vertexShader: `varying vec2 beamUv; void main() { beamUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform float scanOpacity; varying vec2 beamUv; void main() {
      float edge = smoothstep(0.0, 0.16, beamUv.x) * smoothstep(0.0, 0.16, 1.0 - beamUv.x);
      float density = 0.035 + 0.09 * pow(beamUv.y, 3.0) + 0.07 * pow(1.0 - beamUv.y, 10.0);
      gl_FragColor = vec4(0.08, 0.38, 1.0, density * edge * scanOpacity);
    }`,
    transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false,
  })
  scanFan = new THREE.Mesh(fanGeometry, fanMaterial)
  scanFan.renderOrder = 1
  scanOverlay.add(scanFan)

  // A small fixed projector gives the beam a readable origin. Dimensions and
  // false-colour light are presentation aids, not specified hardware geometry.
  const radius = Math.max(extent * 0.018, 0.008)
  const projector = new THREE.Mesh(
    new THREE.CylinderGeometry(radius, radius, radius * 0.8, 20),
    new THREE.MeshStandardMaterial({ color: '#466183', metalness: 0.35, roughness: 0.4 }),
  )
  projector.position.copy(apex); projector.position.y += radius * 0.4
  scanOverlay.add(projector)
  const lens = new THREE.Mesh(
    new THREE.CylinderGeometry(radius * 0.58, radius * 0.58, radius * 0.08, 20),
    new THREE.MeshBasicMaterial({ color: '#4f9aff', toneMapped: false }),
  )
  lens.position.copy(apex)
  scanOverlay.add(lens)
  scanFrame.max.y += radius
  scene.add(scanOverlay)
  frameBox(scanFrame)
  updateScanPosition()
}

function frameBox(box: THREE.Box3, top = false, currentDirection?: THREE.Vector3) {
  if (!camera || !controls || box.isEmpty()) return
  const center = box.getCenter(new THREE.Vector3())
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.025)
  const verticalFov = THREE.MathUtils.degToRad(camera.fov)
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * camera.aspect)
  const direction = currentDirection?.normalize() ?? (top ? new THREE.Vector3(0, 1, 0.001).normalize() : new THREE.Vector3(1, 1.35, 1.25).normalize())
  // Fit all eight actual box corners in this camera's horizontal and vertical
  // frustum. A bounding sphere wastes most of the viewport on long flat meshes.
  const right = new THREE.Vector3().crossVectors(axisY, direction).normalize()
  const up = new THREE.Vector3().crossVectors(direction, right).normalize()
  let distance = radius * 0.1
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
    const offset = new THREE.Vector3(x, y, z).sub(center)
    const towardCamera = offset.dot(direction)
    distance = Math.max(distance,
      towardCamera + Math.abs(offset.dot(right)) / Math.tan(horizontalFov / 2),
      towardCamera + Math.abs(offset.dot(up)) / Math.tan(verticalFov / 2))
  }
  distance *= 1.12
  camera.up.set(0, 1, 0)
  camera.position.copy(center).addScaledVector(direction, distance)
  const modelRadius = Math.max(mesh?.geometry.boundingSphere?.radius ?? radius, radius)
  camera.near = Math.max(modelRadius / 10000, 0.0001)
  camera.far = Math.max(distance + modelRadius * 100, 100)
  camera.updateProjectionMatrix()
  controls.target.copy(center)
  controls.minDistance = Math.max(modelRadius / 200, 0.005)
  controls.maxDistance = Math.max(modelRadius * 40, distance * 2)
  controls.update()
}

function resetView() { viewMode = 'all'; const box = scanFrame ?? mesh?.geometry.boundingBox; if (box) frameBox(box) }
function topView() { viewMode = 'top'; const box = scanFrame ?? mesh?.geometry.boundingBox; if (box) frameBox(box, true) }
function focusSelected() {
  if (props.displayMode !== 'result') return
  const index = bars.findIndex(bar => bar.ifcGlobalId === props.selectedId || bar.designBarId === props.selectedId)
  if (index >= 0 && bounds[index]) { viewMode = 'selected'; frameBox(bounds[index]!) }
}
function orbit(horizontal: number, vertical = 0) {
  if (!camera || !controls || !hasModel.value) return
  const offset = camera.position.clone().sub(controls.target)
  if (vertical) {
    const spherical = new THREE.Spherical().setFromVector3(offset)
    spherical.phi = THREE.MathUtils.clamp(spherical.phi + vertical, 0.001, Math.PI - 0.001)
    offset.setFromSpherical(spherical)
  }
  offset.applyAxisAngle(axisY, horizontal)
  camera.position.copy(controls.target).add(offset)
  controls.update()
}
function rotateLeft() { orbit(-Math.PI / 12) }
function rotateRight() { orbit(Math.PI / 12) }
function zoom(factor: number) {
  if (!camera || !controls || !hasModel.value) return
  const offset = camera.position.clone().sub(controls.target)
  offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance))
  camera.position.copy(controls.target).add(offset)
  controls.update()
}
function zoomIn() { zoom(0.8) }
function zoomOut() { zoom(1.25) }
defineExpose({ resetView, topView, focusSelected, rotateLeft, rotateRight, zoomIn, zoomOut })

function keydown(event: KeyboardEvent) {
  const key = event.key.toLowerCase()
  const actions: Record<string, () => void> = {
    arrowleft: rotateLeft, arrowright: rotateRight,
    arrowup: () => orbit(0, -Math.PI / 12), arrowdown: () => orbit(0, Math.PI / 12),
    '+': zoomIn, '=': zoomIn, '-': zoomOut, r: resetView,
  }
  if (actions[key]) { event.preventDefault(); actions[key]!() }
}
function pointerDown(event: PointerEvent) {
  activePointers.add(event.pointerId)
  if (activePointers.size > 1) { dragged = true; return }
  if (event.button !== 0) { pointerStart = null; return }
  pointerStart = { x: event.clientX, y: event.clientY, id: event.pointerId }
  dragged = false
  renderer?.domElement.focus({ preventScroll: true })
}
function pointerMove(event: PointerEvent) {
  if (pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) dragged = true
}
function pointerUp(event: PointerEvent) {
  activePointers.delete(event.pointerId)
  const start = pointerStart
  pointerStart = null
  if (!start || start.id !== event.pointerId || dragged || !mesh || !camera || !renderer || !hasModel.value || props.displayMode !== 'result') return
  if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return
  const rect = renderer.domElement.getBoundingClientRect()
  pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1)
  raycaster.setFromCamera(pointer, camera)
  const hit = raycaster.intersectObject(mesh, false)[0]
  const id = hit?.face ? pickIds[hit.face.materialIndex] : undefined
  if (id) emit('select', id)
}
function pointerCancel(event: PointerEvent) {
  activePointers.delete(event.pointerId)
  pointerStart = null; dragged = true
}
function contextLost(event: Event) {
  event.preventDefault()
  contextUnavailable = true
  requestToken++
  cancelAnimationFrame(animationFrame)
  disposeModel()
  fail('三维显示已中断，请刷新页面后重试。')
}
function resize() {
  if (!host.value || !renderer || !camera) return
  const nextLayout = window.innerWidth <= 1050 ? 0 : window.innerWidth < 1400 ? 1 : 2
  const layoutChanged = layoutClass !== nextLayout
  layoutClass = nextLayout
  const width = Math.max(host.value.clientWidth, 1), height = Math.max(host.value.clientHeight, 1)
  renderer.setSize(width, height, false)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  // Only reframe on a responsive layout transition. Small resizes preserve the
  // user's zoom, while crossing the breakpoint keeps the current viewing angle.
  if (layoutChanged && mesh?.geometry.boundingBox && controls) {
    const selected = bars.findIndex(bar => bar.ifcGlobalId === props.selectedId || bar.designBarId === props.selectedId)
    const box = viewMode === 'selected' && bounds[selected] ? bounds[selected]! : scanFrame ?? mesh.geometry.boundingBox
    frameBox(box, viewMode === 'top', camera.position.clone().sub(controls.target))
  }
}

async function loadResult() {
  const token = ++requestToken
  disposeModel()
  emit('loaded', false)
  errorMessage.value = ''
  const result = props.result
  if (contextUnavailable || !renderer || !scene) {
    loading.value = false
    errorMessage.value = '此设备暂时无法显示三维模型，请刷新页面或更换支持三维显示的浏览器。'
    return
  }
  if (!result) { loading.value = false; return }
  loading.value = true
  let geometry: THREE.BufferGeometry | null = null
  try {
    if (result.fresh !== true) throw new Error('检测结果已过期或尚未确认，请重新分析后查看。')
    const buffer = await props.loadGeometry(result)
    if (!mounted || token !== requestToken) return
    validateOperatorPlySurface(buffer)
    geometry = new PLYLoader().parse(buffer)
    bars = result.diagnostics?.rebarComparison?.bars ?? []
    const mapping = mapOperatorGeometry(geometry, bars, result.meshVertexCount)
    bounds = mapping.bounds; pickIds = mapping.pickIds
    const materials = pickIds.map(() => new THREE.MeshStandardMaterial({ color: '#8292a6', roughness: 0.6, metalness: 0.12, side: THREE.DoubleSide }))
    mesh = new THREE.Mesh(geometry, materials)
    scene.add(mesh)
    geometry = null // The mesh now owns disposal.
    hasModel.value = true
    loading.value = false
    syncDisplayMode(); resetView()
    emit('loaded', true)
  } catch (error) {
    geometry?.dispose()
    if (!mounted || token !== requestToken) return
    disposeModel()
    fail(error instanceof Error ? error.message : '三维模型加载失败，请稍后重试。')
  }
}

onMounted(() => {
  mounted = true
  if (!host.value) return
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setClearColor('#eef3f9')
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.domElement.tabIndex = 0
    renderer.domElement.setAttribute('role', 'application')
    updateAriaLabel()
    host.value.appendChild(renderer.domElement)
    scene = new THREE.Scene()
    camera = new THREE.PerspectiveCamera(42, 1, 0.001, 10000)
    camera.up.set(0, 1, 0)
    scene.add(new THREE.HemisphereLight('#ffffff', '#7a8ba3', 2.5))
    const light = new THREE.DirectionalLight('#ffffff', 2.2)
    light.position.set(1, 3, 2)
    scene.add(light)
    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.1
    controls.enablePan = true
    controls.enableZoom = true
    controls.minPolarAngle = 0.001
    controls.maxPolarAngle = Math.PI - 0.001
    controls.touches.ONE = THREE.TOUCH.ROTATE
    controls.touches.TWO = THREE.TOUCH.DOLLY_PAN
    const canvas = renderer.domElement
    canvas.addEventListener('keydown', keydown)
    canvas.addEventListener('pointerdown', pointerDown)
    canvas.addEventListener('pointermove', pointerMove)
    canvas.addEventListener('pointerup', pointerUp)
    canvas.addEventListener('pointercancel', pointerCancel)
    canvas.addEventListener('webglcontextlost', contextLost)
    observer = new ResizeObserver(resize)
    observer.observe(host.value)
    resize()
    const render = () => {
      if (!mounted || contextUnavailable || !renderer || !scene || !camera) return
      controls?.update()
      renderer.render(scene, camera)
      animationFrame = requestAnimationFrame(render)
    }
    render()
    void loadResult()
  } catch {
    contextUnavailable = true
    renderer?.dispose()
    fail('此设备暂时无法显示三维模型，请更换支持三维显示的浏览器。')
  }
})
watch(() => props.result, () => { if (mounted) void loadResult() })
watch(() => [props.selectedId, props.tolerance], updateColors)
watch(() => [props.displayMode, props.reducedMotion], syncDisplayMode)
watch(() => props.scanProgress, updateScanPosition)
onBeforeUnmount(() => {
  mounted = false
  requestToken++
  cancelAnimationFrame(animationFrame)
  observer?.disconnect()
  disposeModel()
  controls?.dispose()
  const canvas = renderer?.domElement
  if (canvas) {
    canvas.removeEventListener('keydown', keydown)
    canvas.removeEventListener('pointerdown', pointerDown)
    canvas.removeEventListener('pointermove', pointerMove)
    canvas.removeEventListener('pointerup', pointerUp)
    canvas.removeEventListener('pointercancel', pointerCancel)
    canvas.removeEventListener('webglcontextlost', contextLost)
    canvas.remove()
  }
  renderer?.dispose()
  renderer?.forceContextLoss()
  controls = null; renderer = null; camera = null; scene = null
})
</script>

<template>
  <div ref="host" class="operator-rebar-viewer" :aria-busy="loading">
    <div v-if="loading || errorMessage || !hasModel" class="viewer-message" :role="errorMessage ? 'alert' : 'status'" aria-live="polite">
      <span v-if="loading" class="loading-spinner" aria-hidden="true" />
      <strong>{{ loading ? '正在加载钢筋模型…' : errorMessage || '选择已完成的检测任务，查看钢筋位置。' }}</strong>
      <span v-if="errorMessage">可以继续查看检测列表中的记录。</span>
    </div>
  </div>
</template>

<style scoped>
.operator-rebar-viewer { position: relative; width: 100%; height: 100%; min-height: 340px; overflow: hidden; background: #eef3f9; border: 1px solid var(--el-border-color-light, #dce5f0); border-radius: 12px; }
.operator-rebar-viewer :deep(canvas) { display: block; width: 100%; height: 100%; touch-action: none; outline: none; }
.operator-rebar-viewer :deep(canvas:focus-visible) { outline: 3px solid var(--el-color-primary, #1674e8); outline-offset: -4px; }
.viewer-message { position: absolute; inset: 0; z-index: 1; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 12px; padding: 32px; text-align: center; background: #eef3f9; color: var(--el-text-color-regular, #536277); }
.viewer-message strong { font-size: 17px; line-height: 1.6; font-weight: 500; max-width: 32em; }
.viewer-message > span:not(.loading-spinner) { font-size: 14px; line-height: 1.6; }
.loading-spinner { width: 28px; height: 28px; border: 3px solid #d6e3f4; border-top-color: var(--el-color-primary, #1674e8); border-radius: 50%; animation: viewer-spin 1s linear infinite; }
@keyframes viewer-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .loading-spinner { animation: none; } }
</style>
