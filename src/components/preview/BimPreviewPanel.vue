<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { View } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { getAssetDetail, getBimGlbFile } from '@/api/backend-file'

type CameraPose = {
  camera: THREE.Vector3
  target: THREE.Vector3
}

type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'
type ClipAxis = 'x' | 'y' | 'z'
type ClipBoxOffsets = {
  xMin: number
  xMax: number
  yMin: number
  yMax: number
  zMin: number
  zMax: number
}
type ClipBoxState = {
  baseBox: THREE.Box3
  offsets: ClipBoxOffsets
}

const props = withDefaults(
  defineProps<{
    assetId: number | null
    displayName?: string
    minimal?: boolean
  }>(),
  {
    displayName: undefined,
    minimal: false,
  },
)

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('等待加载 BIM 模型')
const modelLoaded = ref(false)

const defaultBgColor = '#0b1020'
const dprCap = 1.25

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let modelRoot: THREE.Object3D | null = null
let animationId = 0
let resizeObserver: ResizeObserver | null = null
let isRendering = false
let needsRender = false
let isMountedReady = false
let loadToken = 0
let axesHelper: THREE.AxesHelper | null = null
let gridHelper: THREE.GridHelper | null = null
let wireframeEnabled = false
let showAxesEnabled = false
let showGridEnabled = false
let sectionEnabled = false
let sectionRatio = 50
let backgroundTheme: PreviewBackgroundTheme = 'deep'
let raycaster: THREE.Raycaster | null = null
let boundsBoxHelper: THREE.Box3Helper | null = null
let clipHandlesGroup: THREE.Group | null = null
let clipHandlePickers: THREE.Object3D[] = []
let clipPointerCaptureId: number | null = null
let clipDragState: null | {
  pointerId: number
  axis: ClipAxis
  invert: boolean
  dragPlane: THREE.Plane
  startPoint: THREE.Vector3
  startPosition: number
  min: number
  max: number
} = null
let clipBoxState: ClipBoxState | null = null
let clipAxis: ClipAxis = 'z'
let clipInvert = false

function getBackgroundColor(theme: PreviewBackgroundTheme) {
  switch (theme) {
    case 'light':
      return '#f7fbff'
    case 'black':
      return '#000000'
    case 'gradient':
      return '#17365f'
    case 'deep':
    default:
      return '#08111d'
  }
}

function getGridThemeColors(theme: PreviewBackgroundTheme) {
  switch (theme) {
    case 'light':
      return {
        center: '#2563eb',
        grid: '#94a3b8',
        opacity: 0.72,
      }
    case 'black':
      return {
        center: '#67e8f9',
        grid: '#334155',
        opacity: 0.62,
      }
    case 'gradient':
      return {
        center: '#38bdf8',
        grid: '#1d4ed8',
        opacity: 0.78,
      }
    case 'deep':
    default:
      return {
        center: '#5eead4',
        grid: '#334155',
        opacity: 0.55,
      }
  }
}

function emitCameraPose() {
  emit('camera-change', getCameraPose())
}

function disposeObject3D(obj: THREE.Object3D) {
  obj.traverse((child: any) => {
    child?.geometry?.dispose?.()
    const material = child?.material
    if (Array.isArray(material)) {
      material.forEach((item) => item?.dispose?.())
    } else {
      material?.dispose?.()
    }
  })
}

function fitCameraToObject(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  object: THREE.Object3D,
) {
  const box = new THREE.Box3().setFromObject(object)
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())

  object.position.sub(center)

  const maxDim = Math.max(size.x, size.y, size.z)
  if (maxDim <= 0) {
    return
  }

  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)
  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, maxDim * 0.18, distance * 2.1)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(5000, distance * 100)
  nextCamera.updateProjectionMatrix()
  nextCamera.updateMatrixWorld()
  nextControls.update()
}

function syncSceneBackground() {
  if (!scene) {
    return
  }

  const nextBackground = new THREE.Color(getBackgroundColor(backgroundTheme))
  scene.background = nextBackground
  renderer?.setClearColor(nextBackground, 1)
}

function syncHelperStyle() {
  const themeColors = getGridThemeColors(backgroundTheme)

  if (gridHelper) {
    ;(gridHelper as THREE.GridHelper & {
      setColors?: (center: THREE.ColorRepresentation, grid: THREE.ColorRepresentation) => void
    }).setColors?.(themeColors.center, themeColors.grid)
    ;(gridHelper.material as THREE.Material).transparent = true
    ;(gridHelper.material as THREE.Material).opacity = themeColors.opacity
    ;(gridHelper.material as THREE.Material).needsUpdate = true
  }
}

function syncHelperVisibility() {
  if (axesHelper) {
    axesHelper.visible = showAxesEnabled
  }

  if (gridHelper) {
    gridHelper.visible = showGridEnabled
  }
}

function cloneBox3(box: THREE.Box3) {
  return new THREE.Box3(box.min.clone(), box.max.clone())
}

function createDefaultClipOffsets(): ClipBoxOffsets {
  return {
    xMin: 0,
    xMax: 0,
    yMin: 0,
    yMax: 0,
    zMin: 0,
    zMax: 0,
  }
}

function resetClipBoxToContent() {
  clipAxis = 'z'
  clipInvert = false
  clipBoxState = null
  const state = getOrCreateClipState()
  if (!state) return
  state.offsets = createDefaultClipOffsets()
  clampClipOffsets(state)
}

function getContentWorldBox() {
  if (!modelRoot) return null
  modelRoot.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(modelRoot)
  return box.isEmpty() ? null : box
}

function getClipOffsetKey(axis: ClipAxis, invert: boolean) {
  return `${axis}${invert ? 'Max' : 'Min'}` as keyof ClipBoxOffsets
}

function clampClipOffsets(state: ClipBoxState) {
  ;(['x', 'y', 'z'] as ClipAxis[]).forEach((axis) => {
    const minKey = `${axis}Min` as keyof ClipBoxOffsets
    const maxKey = `${axis}Max` as keyof ClipBoxOffsets
    const span = Math.max(0, state.baseBox.max[axis] - state.baseBox.min[axis])
    state.offsets[minKey] = THREE.MathUtils.clamp(state.offsets[minKey], 0, span)
    state.offsets[maxKey] = THREE.MathUtils.clamp(state.offsets[maxKey], 0, span)
    if (state.offsets[minKey] + state.offsets[maxKey] > span) {
      state.offsets[maxKey] = Math.max(0, span - state.offsets[minKey])
    }
  })
}

function getOrCreateClipState() {
  const baseBox = getContentWorldBox()
  if (!baseBox) {
    clipBoxState = null
    return null
  }

  if (!clipBoxState) {
    clipBoxState = {
      baseBox: cloneBox3(baseBox),
      offsets: createDefaultClipOffsets(),
    }
    return clipBoxState
  }

  clipBoxState.baseBox.copy(baseBox)
  clampClipOffsets(clipBoxState)
  return clipBoxState
}

function getCurrentClipBox() {
  const state = getOrCreateClipState()
  if (!state) return null

  const box = cloneBox3(state.baseBox)
  box.min.x += state.offsets.xMin
  box.max.x -= state.offsets.xMax
  box.min.y += state.offsets.yMin
  box.max.y -= state.offsets.yMax
  box.min.z += state.offsets.zMin
  box.max.z -= state.offsets.zMax
  return box
}

function getClipFacePosition(axis: ClipAxis, invert: boolean) {
  const box = getCurrentClipBox()
  if (!box) return 0
  return invert ? box.max[axis] : box.min[axis]
}

function getClipFaceRange(axis: ClipAxis, invert: boolean) {
  const state = getOrCreateClipState()
  const box = getCurrentClipBox()
  if (!state || !box) return { min: 0, max: 1 }

  return invert
    ? { min: box.min[axis], max: state.baseBox.max[axis] }
    : { min: state.baseBox.min[axis], max: box.max[axis] }
}

function setClipFacePosition(axis: ClipAxis, invert: boolean, value: number) {
  const state = getOrCreateClipState()
  if (!state) return

  const currentBox = getCurrentClipBox()
  if (!currentBox) return

  const baseMin = state.baseBox.min[axis]
  const baseMax = state.baseBox.max[axis]
  const minLimit = invert ? currentBox.min[axis] : baseMin
  const maxLimit = invert ? baseMax : currentBox.max[axis]
  const clamped = THREE.MathUtils.clamp(value, minLimit, maxLimit)
  const key = getClipOffsetKey(axis, invert)

  if (invert) state.offsets[key] = baseMax - clamped
  else state.offsets[key] = clamped - baseMin

  clampClipOffsets(state)
}

function clearClipHandles() {
  clipHandlePickers = []
  if (!scene || !clipHandlesGroup) {
    clipHandlesGroup = null
    return
  }

  scene.remove(clipHandlesGroup)
  clipHandlesGroup.traverse((child: any) => {
    child.geometry?.dispose?.()
    const material = child?.material
    if (Array.isArray(material)) {
      material.forEach((item: any) => item?.dispose?.())
    } else {
      material?.dispose?.()
    }
  })
  clipHandlesGroup = null
}

function styleBoundsBoxHelper(helper: THREE.Box3Helper) {
  const material = helper.material as THREE.LineBasicMaterial
  material.depthTest = false
  material.depthWrite = false
  material.transparent = true
  material.opacity = 0.95
  material.needsUpdate = true
  helper.renderOrder = 9999
}

function ensureClipHandlesGroup() {
  if (!scene) return null
  if (clipHandlesGroup) return clipHandlesGroup

  const activeColor = new THREE.Color('#ffd04b')
  const idleColor = new THREE.Color('#409eff')
  const baseAxis = new THREE.Vector3(0, 1, 0)
  const group = new THREE.Group()
  const faces: Array<{
    axis: ClipAxis
    invert: boolean
    normal: THREE.Vector3
    arrowDir: THREE.Vector3
  }> = [
    { axis: 'x', invert: false, normal: new THREE.Vector3(-1, 0, 0), arrowDir: new THREE.Vector3(-1, 0, 0) },
    { axis: 'x', invert: true, normal: new THREE.Vector3(1, 0, 0), arrowDir: new THREE.Vector3(1, 0, 0) },
    { axis: 'y', invert: false, normal: new THREE.Vector3(0, -1, 0), arrowDir: new THREE.Vector3(0, -1, 0) },
    { axis: 'y', invert: true, normal: new THREE.Vector3(0, 1, 0), arrowDir: new THREE.Vector3(0, 1, 0) },
    { axis: 'z', invert: false, normal: new THREE.Vector3(0, 0, -1), arrowDir: new THREE.Vector3(0, 0, -1) },
    { axis: 'z', invert: true, normal: new THREE.Vector3(0, 0, 1), arrowDir: new THREE.Vector3(0, 0, 1) },
  ]

  clipHandlePickers = []
  for (const face of faces) {
    const handle = new THREE.Group()
    const shaft = new THREE.Mesh(
      new THREE.CylinderGeometry(1, 1, 1, 12),
      new THREE.MeshBasicMaterial({
        color: idleColor,
        transparent: true,
        opacity: 0.82,
        depthTest: false,
        depthWrite: false,
      }),
    )
    const cone = new THREE.Mesh(
      new THREE.ConeGeometry(1, 1, 16),
      new THREE.MeshBasicMaterial({
        color: idleColor,
        transparent: true,
        opacity: 0.9,
        depthTest: false,
        depthWrite: false,
      }),
    )
    const hitArea = new THREE.Mesh(
      new THREE.CylinderGeometry(1, 1, 1, 10),
      new THREE.MeshBasicMaterial({
        transparent: true,
        opacity: 0,
        depthTest: false,
        depthWrite: false,
      }),
    )

    hitArea.userData = {
      __viewerClipHandle: true,
      axis: face.axis,
      invert: face.invert,
    }

    handle.userData = {
      axis: face.axis,
      invert: face.invert,
      normal: face.normal,
      arrowDir: face.arrowDir,
      shaft,
      cone,
      hitArea,
      idleColor,
      activeColor,
    }

    handle.add(shaft)
    handle.add(cone)
    handle.add(hitArea)
    handle.quaternion.setFromUnitVectors(baseAxis, face.arrowDir)
    handle.renderOrder = 10000
    handle.traverse((obj: any) => {
      obj.renderOrder = 10000
    })
    group.add(handle)
    clipHandlePickers.push(hitArea)
  }

  clipHandlesGroup = group
  scene.add(group)
  return group
}

function updateClipHandles(box: THREE.Box3) {
  const group = ensureClipHandlesGroup()
  if (!group) return

  const center = box.getCenter(new THREE.Vector3())
  const size = box.getSize(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z, 1)
  const offset = Math.max(maxDim * 0.06, 0.12)
  const handleLength = Math.max(maxDim * 0.12, 0.22)
  const shaftLength = handleLength * 0.62
  const coneHeight = handleLength - shaftLength
  const shaftRadius = Math.max(maxDim * 0.006, 0.012)
  const coneRadius = shaftRadius * 2.2
  const hitRadius = Math.max(shaftRadius * 5, 0.06)

  group.visible = true
  group.children.forEach((child) => {
    const handle = child as THREE.Group
    const { axis, invert, normal, shaft, cone, hitArea, idleColor, activeColor } =
      handle.userData as any
    const isActiveFace = axis === clipAxis && invert === clipInvert
    const color = isActiveFace ? activeColor : idleColor

    const anchor =
      axis === 'x'
        ? new THREE.Vector3(invert ? box.max.x : box.min.x, center.y, center.z)
        : axis === 'y'
          ? new THREE.Vector3(center.x, invert ? box.max.y : box.min.y, center.z)
          : new THREE.Vector3(center.x, center.y, invert ? box.max.z : box.min.z)

    shaft.geometry.dispose?.()
    shaft.geometry = new THREE.CylinderGeometry(shaftRadius, shaftRadius, shaftLength, 12)
    shaft.position.y = shaftLength * 0.5
    shaft.material.color.copy(color)
    shaft.material.opacity = isActiveFace ? 0.95 : 0.82

    cone.geometry.dispose?.()
    cone.geometry = new THREE.ConeGeometry(coneRadius, coneHeight, 16)
    cone.position.y = shaftLength + coneHeight * 0.5
    cone.material.color.copy(color)
    cone.material.opacity = isActiveFace ? 1 : 0.9

    hitArea.geometry.dispose?.()
    hitArea.geometry = new THREE.CylinderGeometry(hitRadius, hitRadius, handleLength, 10)
    hitArea.position.y = handleLength * 0.5

    handle.position.copy(anchor).add((normal as THREE.Vector3).clone().multiplyScalar(offset))
  })
}

function syncBoundsHelpers() {
  if (!scene) return

  if (!sectionEnabled) {
    if (boundsBoxHelper) boundsBoxHelper.visible = false
    clearClipHandles()
    return
  }

  const boundsBox = getCurrentClipBox()
  if (boundsBox && !boundsBox.isEmpty()) {
    if (!boundsBoxHelper) {
      boundsBoxHelper = new THREE.Box3Helper(boundsBox.clone(), 0x67e8f9)
      styleBoundsBoxHelper(boundsBoxHelper)
      scene.add(boundsBoxHelper)
    }
    boundsBoxHelper.box.copy(boundsBox)
    boundsBoxHelper.visible = true
    boundsBoxHelper.updateMatrixWorld(true)
    updateClipHandles(boundsBox)
  } else if (boundsBoxHelper) {
    boundsBoxHelper.visible = false
    clearClipHandles()
  }
}

function getPointerNdc(ev: PointerEvent) {
  const rect = renderer?.domElement?.getBoundingClientRect?.()
  if (!rect) return null
  return new THREE.Vector2(
    ((ev.clientX - rect.left) / rect.width) * 2 - 1,
    -((ev.clientY - rect.top) / rect.height) * 2 + 1,
  )
}

function buildClipDragPlane(axisKey: ClipAxis, anchor: THREE.Vector3) {
  if (!camera) return null
  const axis =
    axisKey === 'x'
      ? new THREE.Vector3(1, 0, 0)
      : axisKey === 'y'
        ? new THREE.Vector3(0, 1, 0)
        : new THREE.Vector3(0, 0, 1)
  const cameraDir = new THREE.Vector3()
  camera.getWorldDirection(cameraDir)
  let normal = cameraDir.sub(axis.clone().multiplyScalar(cameraDir.dot(axis)))
  if (normal.lengthSq() < 1e-6) {
    normal = new THREE.Vector3(0, 1, 0).cross(axis)
  }
  if (normal.lengthSq() < 1e-6) {
    normal = new THREE.Vector3(0, 0, 1).cross(axis)
  }
  normal.normalize()
  return new THREE.Plane().setFromNormalAndCoplanarPoint(normal, anchor)
}

function beginClipDrag(ev: PointerEvent, options: { axis: ClipAxis; invert: boolean }) {
  if (!raycaster || !camera || !renderer) return

  clipAxis = options.axis
  clipInvert = options.invert
  syncModelPresentation()
  syncBoundsHelpers()

  const ndc = getPointerNdc(ev)
  if (!ndc) return
  raycaster.setFromCamera(ndc, camera)

  const box = getCurrentClipBox()
  if (!box) return

  const center = box.getCenter(new THREE.Vector3())
  const anchor =
    options.axis === 'x'
      ? new THREE.Vector3(options.invert ? box.max.x : box.min.x, center.y, center.z)
      : options.axis === 'y'
        ? new THREE.Vector3(center.x, options.invert ? box.max.y : box.min.y, center.z)
        : new THREE.Vector3(center.x, center.y, options.invert ? box.max.z : box.min.z)

  const dragPlane = buildClipDragPlane(options.axis, anchor)
  if (!dragPlane) return

  const startPoint = new THREE.Vector3()
  if (!raycaster.ray.intersectPlane(dragPlane, startPoint)) return

  const range = getClipFaceRange(options.axis, options.invert)
  clipDragState = {
    pointerId: ev.pointerId,
    axis: options.axis,
    invert: options.invert,
    dragPlane,
    startPoint,
    startPosition: getClipFacePosition(options.axis, options.invert),
    min: range.min,
    max: range.max,
  }
  renderer.domElement.setPointerCapture?.(ev.pointerId)
  clipPointerCaptureId = ev.pointerId
  if (controls) controls.enabled = false
}

function onClipDragMove(ev: PointerEvent) {
  if (!clipDragState || !raycaster || !camera) return
  const ndc = getPointerNdc(ev)
  if (!ndc) return
  raycaster.setFromCamera(ndc, camera)
  const point = new THREE.Vector3()
  if (!raycaster.ray.intersectPlane(clipDragState.dragPlane, point)) return

  const axisVec =
    clipDragState.axis === 'x'
      ? new THREE.Vector3(1, 0, 0)
      : clipDragState.axis === 'y'
        ? new THREE.Vector3(0, 1, 0)
        : new THREE.Vector3(0, 0, 1)
  const delta = point.clone().sub(clipDragState.startPoint).dot(axisVec)
  const nextPosition = THREE.MathUtils.clamp(
    clipDragState.startPosition + delta,
    clipDragState.min,
    clipDragState.max,
  )

  setClipFacePosition(clipDragState.axis, clipDragState.invert, nextPosition)
  syncModelPresentation()
  syncBoundsHelpers()
  requestRender()
}

function endClipDrag(ev?: PointerEvent) {
  if ((clipDragState || clipPointerCaptureId !== null) && renderer?.domElement && ev) {
    const captureId = clipPointerCaptureId ?? clipDragState?.pointerId
    try {
      if (captureId !== null && captureId !== undefined) {
        renderer.domElement.releasePointerCapture?.(captureId)
      }
    } catch {
      // ignore pointer capture release errors
    }
  }
  clipDragState = null
  clipPointerCaptureId = null
  if (controls) controls.enabled = true
  syncBoundsHelpers()
  requestRender()
}

function handleViewportPointerDown(event: PointerEvent) {
  if (!camera || !raycaster || !sectionEnabled || !clipHandlePickers.length) return

  const pointer = getPointerNdc(event)
  if (!pointer) return

  raycaster.setFromCamera(pointer, camera)
  const handleHits = raycaster.intersectObjects(clipHandlePickers, true)
  const handleHit = handleHits[0] as any
  if (!handleHit?.object?.userData?.__viewerClipHandle) return

  beginClipDrag(event, {
    axis: handleHit.object.userData.axis,
    invert: !!handleHit.object.userData.invert,
  })
}

function onViewportPointerMove(event: PointerEvent) {
  if (!clipDragState) return
  onClipDragMove(event)
}

function onViewportPointerUp(event: PointerEvent) {
  if (!clipDragState && clipPointerCaptureId === null) return
  endClipDrag(event)
}

function onViewportPointerCancel(event: PointerEvent) {
  if (!clipDragState && clipPointerCaptureId === null) return
  endClipDrag(event)
}

function buildClippingPlanes() {
  const clipBox = sectionEnabled ? getCurrentClipBox() : null
  if (!clipBox || clipBox.isEmpty()) {
    return null
  }

  return [
    new THREE.Plane(new THREE.Vector3(1, 0, 0), -clipBox.min.x),
    new THREE.Plane(new THREE.Vector3(-1, 0, 0), clipBox.max.x),
    new THREE.Plane(new THREE.Vector3(0, 1, 0), -clipBox.min.y),
    new THREE.Plane(new THREE.Vector3(0, -1, 0), clipBox.max.y),
    new THREE.Plane(new THREE.Vector3(0, 0, 1), -clipBox.min.z),
    new THREE.Plane(new THREE.Vector3(0, 0, -1), clipBox.max.z),
  ]
}

function applyMaterialState(material: THREE.Material, clippingPlanes: THREE.Plane[] | null) {
  if ('wireframe' in material) {
    ;(material as THREE.Material & { wireframe: boolean }).wireframe = wireframeEnabled
  }

  material.clippingPlanes = clippingPlanes
  material.needsUpdate = true
}

function syncModelPresentation() {
  if (!modelRoot) {
    return
  }

  const clippingPlanes = buildClippingPlanes()
  modelRoot.traverse((child: any) => {
    const material = child?.material
    if (!material) {
      return
    }

    if (Array.isArray(material)) {
      material.forEach((item) => item && applyMaterialState(item, clippingPlanes))
      return
    }

    applyMaterialState(material, clippingPlanes)
  })
}

function syncRendererSize() {
  if (!renderer || !camera || !viewportEl.value) {
    return false
  }

  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))
  const dpr = Math.min(window.devicePixelRatio || 1, dprCap)
  const canvasWidth = Math.floor(width * dpr)
  const canvasHeight = Math.floor(height * dpr)

  if (
    renderer.domElement.width === canvasWidth &&
    renderer.domElement.height === canvasHeight
  ) {
    return false
  }

  renderer.setPixelRatio(dpr)
  renderer.setSize(width, height)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  return true
}

function requestRender() {
  needsRender = true
  if (isRendering) return
  isRendering = true
  animationId = requestAnimationFrame(renderFrame)
}

function renderFrame() {
  if (!renderer || !scene || !camera) {
    animationId = 0
    isRendering = false
    return
  }

  const resized = syncRendererSize()
  if (!needsRender && !resized) {
    animationId = 0
    isRendering = false
    return
  }

  renderer.render(scene, camera)
  needsRender = false
  animationId = 0
  isRendering = false
}

function initViewer() {
  if (!viewportEl.value || renderer) {
    return
  }

  scene = new THREE.Scene()
  syncSceneBackground()

  const width = viewportEl.value.clientWidth || 1
  const height = viewportEl.value.clientHeight || 1

  camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 5000)
  camera.position.set(3, 2, 5)

  renderer = new THREE.WebGLRenderer({
    antialias: true,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
  renderer.setSize(width, height)
  renderer.setClearColor(new THREE.Color(defaultBgColor), 1)
  renderer.localClippingEnabled = true
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1
  if ('outputColorSpace' in renderer) {
    renderer.outputColorSpace = THREE.SRGBColorSpace
  }
  viewportEl.value.appendChild(renderer.domElement)
  renderer.domElement.addEventListener('pointerdown', handleViewportPointerDown)
  renderer.domElement.addEventListener('pointermove', onViewportPointerMove)
  renderer.domElement.addEventListener('pointerup', onViewportPointerUp)
  renderer.domElement.addEventListener('pointercancel', onViewportPointerCancel)
  raycaster = new THREE.Raycaster()

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = false
  controls.addEventListener('change', () => {
    requestRender()
    emitCameraPose()
  })

  scene.add(new THREE.AmbientLight(0xffffff, 0.7))
  const keyLight = new THREE.DirectionalLight(0xffffff, 0.9)
  keyLight.position.set(10, 10, 10)
  scene.add(keyLight)
  const fillLight = new THREE.DirectionalLight(0x99bbff, 0.45)
  fillLight.position.set(-8, 6, -6)
  scene.add(fillLight)

  axesHelper = new THREE.AxesHelper(24)
  axesHelper.visible = showAxesEnabled
  scene.add(axesHelper)

  gridHelper = new THREE.GridHelper(280, 56, 0x5eead4, 0x334155)
  gridHelper.visible = showGridEnabled
  syncHelperStyle()
  scene.add(gridHelper)

  resizeObserver = new ResizeObserver(() => {
    if (syncRendererSize()) {
      requestRender()
    }
  })
  resizeObserver.observe(viewportEl.value)
  requestRender()
}

async function loadByAssetId(assetId: number, displayName: string) {
  const activeToken = ++loadToken
  initViewer()
  if (!scene || !camera || !controls) {
    return
  }

  statusText.value = '加载 BIM 模型中...'
  modelLoaded.value = false
  emit('loaded-change', false)
  requestRender()

  const assetDetailResult = await getAssetDetail(assetId)
  const assetDetail = assetDetailResult.data

  if (assetDetail.type !== 'bim') {
    throw new Error('当前资产不是 BIM 模型')
  }

  if (assetDetail.status !== 'ready' || !assetDetail.glbUrl) {
    throw new Error('BIM 模型尚未就绪，暂时无法预览')
  }

  const blob = await getBimGlbFile(assetDetail.glbUrl)
  if (activeToken !== loadToken) {
    return
  }

  const objectUrl = URL.createObjectURL(blob)
  const loader = new GLTFLoader()
  const dracoLoader = new DRACOLoader()
  dracoLoader.setDecoderPath('https://www.gstatic.com/draco/v1/decoders/')
  loader.setDRACOLoader(dracoLoader)

  loader.load(
    objectUrl,
    (gltf: GLTF) => {
      URL.revokeObjectURL(objectUrl)
      dracoLoader.dispose()

      if (activeToken !== loadToken || !scene || !camera || !controls) {
        return
      }

      if (modelRoot) {
        scene.remove(modelRoot)
        disposeObject3D(modelRoot)
      }

      modelRoot = gltf.scene
      scene.add(modelRoot)
      modelRoot.updateMatrixWorld(true)
      fitCameraToObject(camera, controls, modelRoot)
      modelRoot.updateMatrixWorld(true)
      resetClipBoxToContent()
      if (sectionEnabled) {
        resetClipBoxToContent()
      }
      syncModelPresentation()
      syncBoundsHelpers()
      modelLoaded.value = true
      statusText.value = `已加载：${displayName}`
      emit('loaded-change', true)
      emitCameraPose()
      requestRender()
    },
    undefined,
    (error: unknown) => {
      URL.revokeObjectURL(objectUrl)
      dracoLoader.dispose()
      if (activeToken !== loadToken) {
        return
      }

      console.error(error)
      modelLoaded.value = false
      statusText.value = 'BIM 模型加载失败'
      emit('loaded-change', false)
      requestRender()
    },
  )
}

function getCameraPose(): CameraPose | null {
  if (!camera || !controls) {
    return null
  }

  return {
    camera: camera.position.clone(),
    target: controls.target.clone(),
  }
}

function setCameraPose(pose: CameraPose | null) {
  if (!camera || !controls || !pose) {
    return
  }

  camera.position.copy(pose.camera)
  controls.target.copy(pose.target)
  camera.lookAt(controls.target)
  camera.updateProjectionMatrix()
  camera.updateMatrixWorld()
  controls.update()
  emitCameraPose()
  requestRender()
}

function resetView() {
  if (!camera || !controls) {
    return
  }

  if (modelRoot) {
    fitCameraToObject(camera, controls, modelRoot)
  } else {
    controls.target.set(0, 0, 0)
    camera.position.set(3, 2, 5)
    camera.lookAt(controls.target)
    camera.updateProjectionMatrix()
    camera.updateMatrixWorld()
    controls.update()
  }

  emitCameraPose()
  requestRender()
}

function setBackgroundTheme(theme: PreviewBackgroundTheme) {
  backgroundTheme = theme
  syncSceneBackground()
  syncHelperStyle()
  requestRender()
}

function setShowAxes(visible: boolean) {
  showAxesEnabled = visible
  syncHelperVisibility()
  requestRender()
}

function setShowGrid(visible: boolean) {
  showGridEnabled = visible
  syncHelperVisibility()
  requestRender()
}

function setWireframe(enabled: boolean) {
  wireframeEnabled = enabled
  syncModelPresentation()
  requestRender()
}

function setSectionState(enabled: boolean, ratio = sectionRatio) {
  sectionRatio = ratio
  sectionEnabled = enabled
  if (sectionEnabled) {
    resetClipBoxToContent()
  } else {
    clipDragState = null
    clipPointerCaptureId = null
    if (controls) controls.enabled = true
  }
  syncModelPresentation()
  syncBoundsHelpers()
  requestRender()
}

function reload() {
  if (!props.assetId) {
    statusText.value = '暂无可预览 BIM 文件'
    modelLoaded.value = false
    emit('loaded-change', false)
    requestRender()
    return
  }

  void loadByAssetId(props.assetId, props.displayName || `BIM-${props.assetId}`).catch(
    (error) => {
      console.error(error)
      modelLoaded.value = false
      statusText.value =
        error instanceof Error ? error.message : 'BIM 模型加载失败'
      emit('loaded-change', false)
      requestRender()
    },
  )
}

function cleanup() {
  loadToken += 1

  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = 0
  }
  isRendering = false
  needsRender = false

  resizeObserver?.disconnect()
  resizeObserver = null

  if (modelRoot && scene) {
    scene.remove(modelRoot)
    disposeObject3D(modelRoot)
    modelRoot = null
  }

  if (boundsBoxHelper && scene) {
    scene.remove(boundsBoxHelper)
    boundsBoxHelper = null
  }

  clearClipHandles()
  endClipDrag()

  controls?.dispose()
  renderer?.dispose()

  renderer?.domElement?.removeEventListener?.('pointerdown', handleViewportPointerDown)
  renderer?.domElement?.removeEventListener?.('pointermove', onViewportPointerMove)
  renderer?.domElement?.removeEventListener?.('pointerup', onViewportPointerUp)
  renderer?.domElement?.removeEventListener?.('pointercancel', onViewportPointerCancel)

  if (renderer?.domElement?.parentElement) {
    renderer.domElement.parentElement.removeChild(renderer.domElement)
  }

  scene = null
  camera = null
  renderer = null
  controls = null
  axesHelper = null
  gridHelper = null
  raycaster = null
  clipBoxState = null
}

defineExpose({
  reload,
  getCameraPose,
  setCameraPose,
  resetView,
  setBackgroundTheme,
  setShowAxes,
  setShowGrid,
  setWireframe,
  setSectionState,
})

watch(
  () => [props.assetId, props.displayName] as const,
  () => {
    if (!isMountedReady) {
      return
    }

    reload()
  },
)

onMounted(() => {
  isMountedReady = true
  initViewer()
  reload()
})

onBeforeUnmount(() => {
  isMountedReady = false
  cleanup()
})
</script>

<template>
  <div class="preview-panel" :class="{ 'is-minimal': minimal }">
    <div v-if="!minimal" class="panel-header">
      <span class="panel-title">BIM 预览</span>
      <span class="panel-status">{{ statusText }}</span>
    </div>

    <div v-if="minimal" class="panel-chip" :class="{ 'is-loaded': modelLoaded }">
      {{ statusText }}
    </div>

    <div
      v-if="!modelLoaded"
      class="empty-state"
      :class="{ 'is-minimal': minimal }"
    >
      <el-icon><View /></el-icon>
      <p>{{ statusText }}</p>
    </div>
    <div ref="viewportEl" class="viewport" />
  </div>
</template>

<style scoped>
.preview-panel {
  height: 100%;
  min-height: 520px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 20px;
  overflow: hidden;
  background:
    radial-gradient(circle at top, rgba(56, 189, 248, 0.08), transparent 28%),
    #0b1020;
  position: relative;
}

.preview-panel.is-minimal {
  border: 0;
  border-radius: 28px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),
    0 18px 48px rgba(2, 6, 23, 0.42);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(9, 14, 30, 0.92);
}

.panel-title {
  font-weight: 700;
  color: #e2e8f0;
}

.panel-status {
  font-size: 0.84rem;
  color: #94a3b8;
}

.panel-chip {
  position: absolute;
  left: 18px;
  bottom: 18px;
  z-index: 2;
  max-width: calc(100% - 36px);
  padding: 8px 14px;
  border-radius: 999px;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1;
  backdrop-filter: blur(18px);
  background: rgba(8, 15, 30, 0.62);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.16),
    0 0 24px rgba(56, 189, 248, 0.1);
}

.panel-chip.is-loaded {
  color: #d9fbff;
  box-shadow:
    inset 0 0 0 1px rgba(34, 211, 238, 0.2),
    0 0 28px rgba(34, 211, 238, 0.16);
}

.empty-state {
  position: absolute;
  inset: 54px 0 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #cbd5e1;
  background: linear-gradient(180deg, rgba(11, 16, 32, 0.82), rgba(11, 16, 32, 0.72));
}

.empty-state.is-minimal {
  inset: 0;
}

.viewport {
  flex: 1;
  min-height: 460px;
}
</style>
