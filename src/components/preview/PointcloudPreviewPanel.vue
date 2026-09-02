<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RefreshRight, View } from '@element-plus/icons-vue'
import * as THREE from 'three'
import {
  ClippingGroup,
  NodeMaterial,
  PointsNodeMaterial,
  WebGPURenderer,
} from 'three/webgpu'
import { color as tslColor, float, vertexColor as tslVertexColor } from 'three/tsl'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { TilesRenderer } from '3d-tiles-renderer'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import { createUploadHeaders } from '@/config/upload-backend'
import { normalizeBackendUrl } from '@/api/backend-http'
import {
  getAssetDetail,
  getPointcloudTilesAsset,
  getPointcloudTilesetUrl,
} from '@/api/backend-file'

type CameraPose = {
  camera: THREE.Vector3
  target: THREE.Vector3
}

type CameraRotation = {
  lon: number
  lat: number
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
    minimal?: boolean
  }>(),
  {
    minimal: false,
  },
)

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('等待加载点云')
const loaded = ref(false)
const pointColorOverride = ref<string | null>(null)

const defaultBgColor = '#0b1020'
const baseGridSize = 10000
const baseGridDivisions = 2000
const dprCap = 1.25
const tilesErrorTargetMin = 2
const tilesErrorTargetMax = 64
const tilesErrorTargetNear = 0.6
const tilesErrorTargetFar = 4

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: WebGPURenderer | THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let tileset: TilesRenderer | null = null
let tilesetWrapper: THREE.Group | null = null
let animationId = 0
let isRendering = false
let needsRender = false
let tilesLoadingCount = 0
let isPointcloudLoading = false
let resizeObserver: ResizeObserver | null = null
let pointcloudMaxDim = 1
let fixedViewSize: number | null = null
let lastTilesErrorTarget = -1
let rendererReady = false
let initPromise: Promise<void> | null = null
let rendererMode: 'webgpu' | 'webgl' | null = null
let isMountedReady = false
let loadToken = 0
let axesHelper: THREE.AxesHelper | null = null
let gridHelper: THREE.GridHelper | null = null
let showAxesEnabled = false
let showGridEnabled = true
let sectionEnabled = false
let sectionRatio = 50
let backgroundTheme: PreviewBackgroundTheme = 'deep'
let clippingGroup: ClippingGroup | null = null
let boundsBoxHelper: THREE.Box3Helper | null = null
let raycaster: THREE.Raycaster | null = null
let clipHandlesGroup: THREE.Group | null = null
let clipHandlePickers: THREE.Object3D[] = []
let clipBoxState: ClipBoxState | null = null
let clipAxis: ClipAxis = 'z'
let clipInvert = false
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
let clipPointerCaptureId: number | null = null
const originalMaterialStore = new WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>()
const pointcloudUnlitMaterialCache = new WeakMap<
  THREE.Material,
  THREE.Material | { single?: THREE.Material; multi?: THREE.Material }
>()
const pointcloudUnlitTSLMaterialCache = new WeakMap<
  THREE.Material,
  { single?: THREE.Material; multi?: THREE.Material }
>()
const materialStateCache = new WeakMap<any, { color?: THREE.Color | null; vertexColors?: boolean; colorNode?: any }>()

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value))

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

function getObjectBounds(object: THREE.Object3D | null) {
  if (!object) {
    return null
  }

  object.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(object)
  if (box.isEmpty()) {
    return null
  }

  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())

  return {
    box,
    size,
    center,
    maxDim: Math.max(size.x, size.y, size.z, 1),
  }
}

function getClipTargetObject() {
  return tilesetWrapper ?? tileset?.group ?? null
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
  const bounds = getObjectBounds(getClipTargetObject())
  if (!bounds) {
    clipBoxState = null
    return null
  }

  if (!clipBoxState) {
    clipBoxState = {
      baseBox: cloneBox3(bounds.box),
      offsets: createDefaultClipOffsets(),
    }
    return clipBoxState
  }

  clipBoxState.baseBox.copy(bounds.box)
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
  applyPointcloudClipping()
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
  applyPointcloudClipping()
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

function resetGridPlacement() {
  if (!gridHelper) {
    return
  }

  gridHelper.scale.setScalar(1)
  gridHelper.position.set(0, -10.01, 0)
}

function syncGridToPointcloud(object: THREE.Object3D | null) {
  if (!gridHelper) {
    return
  }

  const bounds = getObjectBounds(object)
  if (!bounds) {
    resetGridPlacement()
    return
  }

  const planarSize = Math.max(bounds.size.x, bounds.size.z, 40)
  const scale = clamp((planarSize * 1.2) / baseGridSize, 1, 500)
  const offset = Math.max(10.5, bounds.size.y * 0.01)

  gridHelper.scale.setScalar(scale)
  gridHelper.position.set(bounds.center.x, bounds.box.min.y - offset, bounds.center.z)
}

function ensureTrailingSlash(value: string) {
  return value.endsWith('/') ? value : `${value}/`
}

function resolveTileResourceUrl(uri: string, baseUrl: string) {
  if (/^(?:https?:)?\/\//i.test(uri) || uri.startsWith('blob:') || uri.startsWith('data:')) {
    return uri
  }

  return new URL(uri, baseUrl).toString()
}

function getTileResourceBasePath(assetDetail: {
  tilesBaseUrl?: string
  tilesetUrl?: string
}) {
  if (assetDetail.tilesBaseUrl) {
    return ensureTrailingSlash(assetDetail.tilesBaseUrl)
  }

  if (!assetDetail.tilesetUrl) {
    return '/'
  }

  return ensureTrailingSlash(assetDetail.tilesetUrl.replace(/\/[^/]*$/, ''))
}

function extractTileAssetPath(resourceUrl: string, baseUrl: string) {
  const normalizedBaseUrl = ensureTrailingSlash(baseUrl)

  if (!resourceUrl.startsWith(normalizedBaseUrl)) {
    return null
  }

  return resourceUrl.slice(normalizedBaseUrl.length)
}

function runWithSuppressedConsoleAssert<T>(task: () => T) {
  const originalAssert = console.assert
  console.assert = () => {}

  try {
    return task()
  } finally {
    console.assert = originalAssert
  }
}

function disposeObject3D(obj: THREE.Object3D) {
  obj.traverse((child: any) => {
    if (child?.geometry) child.geometry.dispose?.()
    const material = child?.material
    if (Array.isArray(material)) {
      material.forEach((item) => item?.dispose?.())
    } else {
      material?.dispose?.()
    }
  })
}

function applySharedMaterialFlags(mat: any, src: any) {
  const alphaTest = src?.alphaTest ?? 0
  const opacity = src?.opacity ?? 1
  mat.alphaTest = alphaTest
  mat.opacity = opacity
  mat.transparent = alphaTest > 0 ? false : !!src?.transparent || opacity < 1
  mat.side = src?.side ?? THREE.FrontSide
}

function applyPointcloudMaterialAppearance(
  material: any,
  source: THREE.Material,
  opts: { isPoints: boolean; vertexColors: boolean },
) {
  if (!material) return

  if (material?.color?.isColor) {
    material.color.copy((source as any)?.color?.clone?.() ?? new THREE.Color(0xffffff))
  }

  if ('vertexColors' in material) {
    material.vertexColors = opts.vertexColors
  }

  if ('colorNode' in material) {
    material.colorNode = opts.vertexColors
      ? tslVertexColor()
      : tslColor(((source as any)?.color?.getHex?.() ?? 0xffffff) as number)
  }

  if (opts.isPoints && 'size' in material && typeof material.size === 'number') {
    material.size = Math.max(0.2, material.size)
  }

  if (opts.isPoints && 'sizeNode' in material) {
    material.sizeNode = float(Math.max(0.2, material.size ?? 1))
  }

  material.needsUpdate = true
}

function getOrCreatePointcloudUnlitMaterial(
  source: THREE.Material,
  opts: { isPoints: boolean; vertexColors: boolean },
  cacheKey = 'single',
) {
  const cachedEntry = pointcloudUnlitMaterialCache.get(source)
  const cached =
    cachedEntry instanceof THREE.Material
      ? cachedEntry
      : cachedEntry?.[cacheKey as 'single' | 'multi']
  if (cached) {
    applyPointcloudMaterialAppearance(cached, source, opts)
    return cached
  }

  let material: THREE.Material
  if (opts.isPoints) {
    const next = new THREE.PointsMaterial({
      size: (source as any)?.size ?? 1,
      sizeAttenuation: (source as any)?.sizeAttenuation ?? true,
      color: (source as any)?.color?.clone?.() ?? new THREE.Color(0xffffff),
      vertexColors: opts.vertexColors,
    })
    if ((source as any)?.map) next.map = (source as any).map
    if ((source as any)?.alphaMap) next.alphaMap = (source as any).alphaMap
    applySharedMaterialFlags(next, source)
    next.fog = false
    next.toneMapped = false
    material = next
  } else {
    const next = new THREE.MeshBasicMaterial({
      color: (source as any)?.color?.clone?.() ?? new THREE.Color(0xffffff),
      vertexColors: opts.vertexColors,
    })
    if ((source as any)?.map) next.map = (source as any).map
    if ((source as any)?.alphaMap) next.alphaMap = (source as any).alphaMap
    applySharedMaterialFlags(next, source)
    next.toneMapped = false
    material = next
  }

  applyPointcloudMaterialAppearance(material, source, opts)

  if (cacheKey === 'single') {
    pointcloudUnlitMaterialCache.set(source, material)
  } else {
    const nextEntry =
      cachedEntry instanceof THREE.Material ? {} : (cachedEntry ?? {})
    nextEntry[cacheKey as 'single' | 'multi'] = material
    pointcloudUnlitMaterialCache.set(source, nextEntry)
  }

  return material
}

function getOrCreatePointcloudUnlitTSLMaterial(
  source: THREE.Material,
  opts: { isPoints: boolean; vertexColors: boolean },
  cacheKey: 'single' | 'multi' = 'single',
) {
  const cachedEntry = pointcloudUnlitTSLMaterialCache.get(source) ?? {}
  const cached = cachedEntry[cacheKey]
  if (cached) {
    applyPointcloudMaterialAppearance(cached, source, opts)
    return cached
  }

  const material = opts.isPoints ? new PointsNodeMaterial() : new NodeMaterial()
  material.name = (source as any)?.name
    ? `${(source as any).name} (Pointcloud Unlit)`
    : 'Pointcloud Unlit'
  material.fog = false
  material.lights = false
  applySharedMaterialFlags(material, source)
  material.toneMapped = false
  material.colorNode = opts.vertexColors
    ? tslVertexColor()
    : tslColor(((source as any)?.color?.getHex?.() ?? 0xffffff) as number)
  material.vertexColors = opts.vertexColors
  if ('sizeNode' in material) {
    material.sizeNode = float(Math.max(0.2, (source as any)?.size ?? 1))
  }
  ;(material as any).__viewerOriginalMaterial = source

  cachedEntry[cacheKey] = material
  pointcloudUnlitTSLMaterialCache.set(source, cachedEntry)
  applyPointcloudMaterialAppearance(material, source, opts)
  return material
}

function applyPointcloudMaterialMode(root: THREE.Object3D | null) {
  if (!root) return

  root.traverse((obj: any) => {
    if (!obj?.material) return

    if (!originalMaterialStore.has(obj)) {
      originalMaterialStore.set(obj, obj.material)
    }

    const opts = {
      isPoints: Boolean(obj.isPoints),
      vertexColors: !!obj.geometry?.attributes?.color,
    }
    const original = originalMaterialStore.get(obj)
    const useWebGPU = rendererMode === 'webgpu'
    if (Array.isArray(original)) {
      obj.material = original.map((item) =>
        useWebGPU
          ? getOrCreatePointcloudUnlitTSLMaterial(item, opts, 'multi')
          : getOrCreatePointcloudUnlitMaterial(item, opts, 'multi'),
      )
      return
    }

    if (original) {
      obj.material = useWebGPU
        ? getOrCreatePointcloudUnlitTSLMaterial(original, opts)
        : getOrCreatePointcloudUnlitMaterial(original, opts)
    }
  })
}

function ensureMaterialState(material: any) {
  const cached = materialStateCache.get(material)
  if (cached) {
    return cached
  }

  const nextState = {
    color: material?.color?.isColor ? material.color.clone() : null,
    vertexColors: 'vertexColors' in material ? material.vertexColors : undefined,
    colorNode: 'colorNode' in material ? material.colorNode : undefined,
  }
  materialStateCache.set(material, nextState)
  return nextState
}

function applyColorOverrideToMaterial(material: any, color: string | null) {
  if (!material) {
    return
  }

  const original = ensureMaterialState(material)
  if (!color) {
    if (material?.color?.isColor && original.color) {
      material.color.copy(original.color)
    }
    if ('vertexColors' in material && original.vertexColors !== undefined) {
      material.vertexColors = original.vertexColors
    }
    if ('colorNode' in material) {
      material.colorNode = original.colorNode
    }
    material.needsUpdate = true
    return
  }

  if (material?.color?.isColor) {
    material.color.set(color)
  }
  if ('vertexColors' in material) {
    material.vertexColors = false
  }
  if ('colorNode' in material) {
    material.colorNode = tslColor(color)
  }
  material.needsUpdate = true
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

function applyClippingToMaterial(material: any, clippingPlanes: THREE.Plane[] | null) {
  if (!material) {
    return
  }

  material.clippingPlanes = clippingPlanes
  material.needsUpdate = true
}

function applyPointcloudColorOverride(root: THREE.Object3D | null, color: string | null) {
  if (!root) {
    return
  }

  const clippingPlanes = buildClippingPlanes()
  root.traverse((obj: any) => {
    const material = obj?.material
    if (!material) {
      return
    }

    if (Array.isArray(material)) {
      material.forEach((item) => {
        applyColorOverrideToMaterial(item, color)
        applyClippingToMaterial(item, clippingPlanes)
      })
      return
    }

    applyColorOverrideToMaterial(material, color)
    applyClippingToMaterial(material, clippingPlanes)
  })
}

function applyPointcloudClipping() {
  const target = tileset?.group ?? null
  applyPointcloudColorOverride(target, pointColorOverride.value)

  if (rendererMode === 'webgpu' && clippingGroup) {
    const clippingPlanes = buildClippingPlanes()
    clippingGroup.enabled = !!clippingPlanes?.length
    clippingGroup.clippingPlanes.length = 0
    if (clippingPlanes?.length) {
      clippingGroup.clippingPlanes.push(...clippingPlanes)
    }
  }
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

function clampRotationLatitude(value: number) {
  return Math.max(-85, Math.min(85, value))
}

function rotationToDirection(rotation: CameraRotation) {
  const lon = THREE.MathUtils.degToRad(rotation.lon)
  const lat = THREE.MathUtils.degToRad(clampRotationLatitude(rotation.lat))
  const cosLat = Math.cos(lat)
  return new THREE.Vector3(
    cosLat * Math.cos(lon),
    Math.sin(lat),
    cosLat * Math.sin(lon),
  ).normalize()
}

function directionToRotation(direction: THREE.Vector3): CameraRotation | null {
  if (direction.lengthSq() <= 1e-12) return null
  const normalized = direction.clone().normalize()
  const horizontalLength = Math.hypot(normalized.x, normalized.z)
  return {
    lon: THREE.MathUtils.radToDeg(Math.atan2(normalized.z, normalized.x)),
    lat: THREE.MathUtils.radToDeg(Math.atan2(normalized.y, horizontalLength)),
  }
}

function getCameraOrientation(): CameraRotation | null {
  if (!camera || !controls) return null
  return directionToRotation(
    new THREE.Vector3().subVectors(controls.target, camera.position),
  )
}

function getCameraDistance(): number | null {
  if (!camera || !controls) return null
  const distance = camera.position.distanceTo(controls.target)
  return Number.isFinite(distance) && distance > 0 ? distance : null
}

function syncFromRotation(rotation: CameraRotation | null) {
  if (!camera || !controls || !rotation) return
  if (!Number.isFinite(rotation.lon) || !Number.isFinite(rotation.lat)) return

  const lookDistance = Math.max(camera.position.distanceTo(controls.target), 0.5)
  const target = camera.position
    .clone()
    .addScaledVector(rotationToDirection(rotation), lookDistance)
  camera.up.set(0, 1, 0)
  controls.target.copy(target)
  camera.lookAt(target)
  camera.updateMatrixWorld()
  controls.update()
  requestRender()
}

function syncFromCameraDistance(distance: number | null) {
  if (!camera || !controls || !distance) return
  if (!Number.isFinite(distance) || distance <= 0) return

  const offset = camera.position.clone().sub(controls.target)
  if (offset.lengthSq() <= 1e-12) return
  camera.position.copy(
    controls.target.clone().add(offset.normalize().multiplyScalar(Math.max(distance, 0.01))),
  )
  camera.lookAt(controls.target)
  camera.updateMatrixWorld()
  controls.update()
  requestRender()
}

function emitCameraPose() {
  emit('camera-change', getCameraPose())
}

function estimateGeometryBytes(geometry: any) {
  let bytes = 0
  const index = geometry?.index
  if (index?.array?.byteLength) bytes += index.array.byteLength

  const attrs = geometry?.attributes ?? {}
  for (const attr of Object.values(attrs) as any[]) {
    const array = attr?.isInterleavedBufferAttribute ? attr.data?.array : attr?.array
    if (array?.byteLength) bytes += array.byteLength
  }

  return bytes
}

function ensureWebGPUVertexAlignment(geometry: any) {
  if (!geometry?.attributes) return 0

  let fixed = 0
  for (const [name, attr] of Object.entries(geometry.attributes)) {
    const source = attr as any
    if (!source || source.isInterleavedBufferAttribute) continue

    const array = source.array
    const bytesPerElement = array?.BYTES_PER_ELEMENT ?? 0
    const itemSize = source.itemSize ?? 0
    const stride = bytesPerElement * itemSize

    if (!bytesPerElement || !itemSize || stride % 4 === 0) continue
    if (itemSize > 4) continue

    const count = source.count ?? 0
    const paddedSize = 4
    const paddedArray = new array.constructor(count * paddedSize)

    for (let index = 0; index < count; index++) {
      const srcIndex = index * itemSize
      const dstIndex = index * paddedSize
      for (let component = 0; component < itemSize; component++) {
        paddedArray[dstIndex + component] = array[srcIndex + component]
      }

      if (name === 'color' && bytesPerElement === 1 && source.normalized) {
        paddedArray[dstIndex + 3] = 255
      } else {
        paddedArray[dstIndex + 3] = 1
      }
    }

    const interleaved = new THREE.InterleavedBuffer(paddedArray, paddedSize)
    interleaved.usage = source.usage ?? THREE.StaticDrawUsage

    const nextAttr = new THREE.InterleavedBufferAttribute(
      interleaved,
      itemSize,
      0,
      source.normalized,
    )
    ;(nextAttr as any).gpuType = source.gpuType
    geometry.setAttribute(name, nextAttr)
    fixed++
  }

  return fixed
}

function sanitizeObjectForWebGPU(root: any) {
  let fixedAttributes = 0
  let oversizedGeometries = 0

  const maxBufferBytes = 256 * 1024 * 1024
  const softLimit = maxBufferBytes - 8 * 1024 * 1024

  root.traverse((obj: any) => {
    const geometry = obj?.geometry
    if (!geometry?.isBufferGeometry) return

    fixedAttributes += ensureWebGPUVertexAlignment(geometry)
    const bytes = estimateGeometryBytes(geometry)
    if (bytes > softLimit) {
      oversizedGeometries++
      obj.visible = false
    }
  })

  return { fixedAttributes, oversizedGeometries }
}

function fitCameraToObject(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  object: THREE.Object3D,
) {
  const bounds = getObjectBounds(object)
  if (!bounds) {
    return
  }

  const { center, maxDim } = bounds
  pointcloudMaxDim = maxDim

  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)
  nextControls.target.copy(center)
  nextCamera.position.set(center.x, center.y + maxDim * 0.15, center.z + distance * 2.2)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(100000, distance * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function fitCameraToRadius(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  radius: number,
  center = new THREE.Vector3(),
) {
  const safeRadius = Math.max(radius, 1)
  const maxDim = safeRadius * 2
  pointcloudMaxDim = maxDim
  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)

  nextControls.target.copy(center)
  nextCamera.position.set(center.x, center.y + maxDim * 0.15, center.z + distance * 2.2)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(100000, distance * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function setTopView(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  height: number,
  target = new THREE.Vector3(),
) {
  const safeHeight = Number.isFinite(height) && height > 0 ? height : 10
  nextControls.target.copy(target)
  nextCamera.position.set(target.x, target.y + safeHeight, target.z + 0.1)
  nextCamera.lookAt(target)
  nextCamera.near = 0.01
  nextCamera.far = Math.max(5000, safeHeight * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function getTilesErrorTarget() {
  if (!camera || !controls) return tilesErrorTargetMin
  const base = (fixedViewSize ?? pointcloudMaxDim ?? 1) || 1
  const distance = camera.position.distanceTo(controls.target)
  const ratio = distance / Math.max(base, 1)
  const t = clamp(
    (ratio - tilesErrorTargetNear) / (tilesErrorTargetFar - tilesErrorTargetNear),
    0,
    1,
  )

  return tilesErrorTargetMin + t * (tilesErrorTargetMax - tilesErrorTargetMin)
}

function applyTilesErrorTarget() {
  if (!tileset) return
  const next = Math.round(getTilesErrorTarget() * 10) / 10
  if (Math.abs(next - lastTilesErrorTarget) < 0.1) return

  lastTilesErrorTarget = next
  tileset.errorTarget = next
}

function updateTilesetResolution() {
  if (!viewportEl.value || !tileset || !camera || !renderer) {
    return false
  }

  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))
  if (import.meta.env.DEV && (width <= 1 || height <= 1)) {
    console.warn('[PointcloudPreview] viewport size is too small:', { width, height })
  }
  const updatedBySize = tileset.setResolution?.(camera, width, height) ?? false
  const updatedByRenderer =
    tileset.setResolutionFromRenderer?.(camera, renderer as THREE.WebGLRenderer) ?? false

  return updatedBySize || updatedByRenderer
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
  updateTilesetResolution()
  return true
}

async function initViewer() {
  if (!viewportEl.value) return
  if (renderer && rendererReady) return
  if (initPromise) return initPromise

  initPromise = (async () => {
    if (!viewportEl.value) return

    const supportsWebGPU = typeof navigator !== 'undefined' && 'gpu' in navigator
    const width = viewportEl.value.clientWidth || 1
    const height = viewportEl.value.clientHeight || 1

    scene = new THREE.Scene()
    raycaster = new THREE.Raycaster()
    syncSceneBackground()
    camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 100000)
    camera.position.set(0, 10, 20)

    const setupRendererCommon = () => {
      if (!renderer || !camera) return

      ;(renderer as any).localClippingEnabled = true
      renderer.domElement.addEventListener('pointerdown', handleViewportPointerDown)
      renderer.domElement.addEventListener('pointermove', onViewportPointerMove)
      renderer.domElement.addEventListener('pointerup', onViewportPointerUp)
      renderer.domElement.addEventListener('pointercancel', onViewportPointerCancel)

      if (clippingGroup) {
        if (tileset?.group) {
          clippingGroup.remove(tileset.group)
        }
        scene?.remove(clippingGroup)
        clippingGroup = null
      }

      if (rendererMode === 'webgpu') {
        clippingGroup = new ClippingGroup()
        scene?.add(clippingGroup)
      }

      controls = new OrbitControls(camera, renderer.domElement)
      controls.enableDamping = false
      controls.addEventListener('change', () => {
        requestRender()
        emitCameraPose()
      })

      scene?.add(new THREE.AmbientLight(0xffffff, 0.7))
      const keyLight = new THREE.DirectionalLight(0xffffff, 0.9)
      keyLight.position.set(10, 10, 10)
      scene?.add(keyLight)
      const fillLight = new THREE.DirectionalLight(0x99bbff, 0.45)
      fillLight.position.set(-8, 6, -6)
      scene?.add(fillLight)

      axesHelper = new THREE.AxesHelper(24)
      axesHelper.visible = showAxesEnabled
      scene?.add(axesHelper)

      gridHelper = new THREE.GridHelper(baseGridSize, baseGridDivisions, 0x67e8f9, 0x2a6f82)
      gridHelper.visible = showGridEnabled
      syncHelperStyle()
      scene?.add(gridHelper)

      if (!resizeObserver && typeof ResizeObserver !== 'undefined' && viewportEl.value) {
        resizeObserver = new ResizeObserver(() => {
          if (syncRendererSize()) {
            requestRender()
          }
        })
        resizeObserver.observe(viewportEl.value)
      }
    }

    const buildWebGLRenderer = () => {
      rendererMode = 'webgl'
      const nextRenderer = new THREE.WebGLRenderer({
        antialias: true,
        preserveDrawingBuffer: true,
      })
      nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
      nextRenderer.setSize(width, height)
      nextRenderer.setClearColor(new THREE.Color(defaultBgColor), 1)
      nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
      nextRenderer.toneMappingExposure = 1
      if ('outputColorSpace' in nextRenderer) {
        nextRenderer.outputColorSpace = THREE.SRGBColorSpace
      }
      viewportEl.value?.appendChild(nextRenderer.domElement)
      renderer = nextRenderer
      rendererReady = true
      setupRendererCommon()
    }

    const buildWebGPURenderer = async () => {
      rendererMode = 'webgpu'
      const nextRenderer = new WebGPURenderer({ antialias: true })
      nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
      nextRenderer.setSize(width, height)
      nextRenderer.setClearColor(new THREE.Color(defaultBgColor), 1)
      nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
      nextRenderer.toneMappingExposure = 1
      viewportEl.value?.appendChild(nextRenderer.domElement)
      await nextRenderer.init?.()
      renderer = nextRenderer
      rendererReady = true
      setupRendererCommon()
    }

    rendererReady = false
    if (supportsWebGPU) {
      try {
        await buildWebGPURenderer()
      } catch (error) {
        console.error('[PointcloudPreview] WebGPU 初始化失败:', error)
        rendererReady = false
      }
    }

    if (!rendererReady) {
      buildWebGLRenderer()
    }

    if (!rendererReady) return
    requestRender()
  })()

  await initPromise
}

function requestRender() {
  needsRender = true
  if (isRendering) return
  isRendering = true
  animationId = requestAnimationFrame(renderPointcloud)
}

function bumpTilesLoading(delta: number) {
  tilesLoadingCount = Math.max(0, tilesLoadingCount + delta)
  requestRender()
}

function getPlaceholderText() {
  if (statusText.value.trim()) {
    return statusText.value
  }

  if (props.assetId) {
    return '自动加载点云中...'
  }

  return '暂无可预览点云文件'
}

function syncFromExternalPose(pose: CameraPose | null) {
  if (!camera || !controls || !pose) {
    return
  }

  camera.position.copy(pose.camera)
  controls.target.copy(pose.target)
  camera.lookAt(controls.target)
  camera.updateProjectionMatrix()
  camera.updateMatrixWorld()
  controls.update()
  requestRender()
}

function resetPointcloudView() {
  if (!camera || !controls) {
    return
  }

  if (fixedViewSize) {
    setTopView(camera, controls, fixedViewSize * 1.2)
    requestRender()
    return
  }

  if (tileset) {
    const targetObject = tilesetWrapper ?? tileset.group
    syncGridToPointcloud(targetObject)
    const bounds = getObjectBounds(targetObject)

    if (bounds) {
      pointcloudMaxDim = bounds.maxDim
      setTopView(
        camera,
        controls,
        Math.max(bounds.maxDim * 1.2, 10),
        bounds.center,
      )
      requestRender()
      return
    }

    const sphere = new THREE.Sphere()
    if (tileset.getBoundingSphere?.(sphere)) {
      fitCameraToRadius(camera, controls, sphere.radius)
      setTopView(camera, controls, sphere.radius * 2.2)
      requestRender()
      return
    }

    fitCameraToObject(camera, controls, targetObject)
    requestRender()
    return
  }

  controls.target.set(0, 0, 0)
  camera.position.set(0, 10, 20)
  camera.updateProjectionMatrix()
  requestRender()
}

function renderPointcloud() {
  if (!renderer || !scene || !camera || !rendererReady) {
    isRendering = false
    animationId = 0
    return
  }

  try {
    const currentRenderer = renderer
    const currentScene = scene
    const currentCamera = camera

    if (syncRendererSize()) {
      needsRender = true
    }

    const didUpdate = controls?.update() ?? false
    currentCamera.updateMatrixWorld()

    if (tileset) {
      const currentTileset = tileset
      applyTilesErrorTarget()
      updateTilesetResolution()
      try {
        currentTileset.setCamera(currentCamera)
        runWithSuppressedConsoleAssert(() => {
          currentTileset.update()
        })
      } catch {
        // ignore renderer update errors
      }
    }

    const isActiveLoading = tilesLoadingCount > 0 || isPointcloudLoading
    const hasActiveTileset = Boolean(tileset)
    if (needsRender || didUpdate || isActiveLoading) {
      runWithSuppressedConsoleAssert(() => {
        currentRenderer.render(currentScene, currentCamera)
      })
      needsRender = false
    }

    if (needsRender || didUpdate || isActiveLoading || hasActiveTileset) {
      animationId = requestAnimationFrame(renderPointcloud)
    } else {
      isRendering = false
      animationId = 0
    }
  } catch {
    isRendering = false
    animationId = 0
  }
}

async function loadTileset(assetId: number) {
  const activeToken = ++loadToken
  await initViewer()
  if (!scene || !camera || !renderer || !controls || !rendererReady) {
    return
  }

  loaded.value = false
  statusText.value = '加载点云中...'
  emit('loaded-change', false)
  tilesLoadingCount = 0
  isPointcloudLoading = true
  lastTilesErrorTarget = -1
  pointcloudMaxDim = 1
  fixedViewSize = null
  resetGridPlacement()
  requestRender()

  if (tileset) {
    clearClipHandles()
    if (boundsBoxHelper && scene) {
      scene.remove(boundsBoxHelper)
      boundsBoxHelper = null
    }
    clipBoxState = null
    if (tilesetWrapper) {
      scene.remove(tilesetWrapper)
      tilesetWrapper = null
    } else {
      scene.remove(tileset.group)
    }
    tileset.dispose?.()
    tileset = null
  }

  const assetDetailResult = await getAssetDetail(assetId)
  const assetDetail = assetDetailResult.data

  if (assetDetail.type !== 'pointcloud') {
    throw new Error('当前资产不是点云资源')
  }

  if (assetDetail.status !== 'ready' || !assetDetail.tilesetUrl) {
    throw new Error('点云资源尚未就绪，暂时无法预览')
  }

  const url = getPointcloudTilesetUrl(assetDetail.tilesetUrl)
  const nextTileset = new TilesRenderer(url)
  nextTileset.errorTarget = getTilesErrorTarget()
  const resourceBasePath = getTileResourceBasePath(assetDetail)
  const resourceBaseUrl = normalizeBackendUrl(
    resourceBasePath,
  )

  nextTileset.fetchOptions = {
    headers: createUploadHeaders({
      Accept: '*/*',
    }),
  }

  nextTileset.registerPlugin({
    fetchData: async (uri: any, options: any) => {
      const raw = typeof uri === 'string' ? uri : uri?.toString?.() || ''
      if (!raw) {
        return null
      }

      let resolvedUrl = ''
      try {
        resolvedUrl = resolveTileResourceUrl(raw, resourceBaseUrl)
      } catch {
        resolvedUrl = raw
      }

      const assetPath = extractTileAssetPath(resolvedUrl, resourceBaseUrl)

      if (!assetPath) {
        return fetch(resolvedUrl, options)
      }

      const extension = assetPath.split('.').pop()?.toLowerCase()
      if (extension === 'json') {
        return getPointcloudTilesAsset(`${resourceBasePath}${assetPath}`, 'json')
      }

      return getPointcloudTilesAsset(`${resourceBasePath}${assetPath}`, 'arraybuffer')
    },
  } as any)

  const dracoLoader = new DRACOLoader(nextTileset.manager)
  dracoLoader.setDecoderPath('/draco/')
  dracoLoader.preload()
  nextTileset.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader }))

  nextTileset.setCamera(camera)
  updateTilesetResolution()

  const wrapper = new THREE.Group()
  wrapper.rotation.x = -Math.PI / 2
  wrapper.add(nextTileset.group)
  if (clippingGroup) {
    clippingGroup.add(wrapper)
  } else {
    scene.add(wrapper)
  }
  tilesetWrapper = wrapper

  nextTileset.addEventListener('tiles-load-start', () => {
    if (activeToken !== loadToken) return
    statusText.value = '点云加载中...'
    loaded.value = false
    emit('loaded-change', false)
    bumpTilesLoading(1)
    isPointcloudLoading = true
  })
  nextTileset.addEventListener('tiles-load-end', () => {
    if (activeToken !== loadToken) return
    statusText.value = '点云加载完成'
    loaded.value = true
    emit('loaded-change', true)
    bumpTilesLoading(-1)
    if (tilesLoadingCount === 0) {
      isPointcloudLoading = false
    }
  })
  nextTileset.addEventListener('load-model', ({ scene: tileScene }: any) => {
    if (activeToken !== loadToken) return
    if (!tileScene) return

    if (rendererMode === 'webgpu') {
      sanitizeObjectForWebGPU(tileScene)
    }
    applyPointcloudMaterialMode(tileScene)
    applyPointcloudClipping()
    const placementTarget = tilesetWrapper ?? nextTileset.group
    syncGridToPointcloud(placementTarget)
    syncBoundsHelpers()
    if (!loaded.value) {
      loaded.value = true
      emit('loaded-change', true)
    }
    statusText.value = tilesLoadingCount > 0 ? '点云加载中...' : '点云加载完成'
    requestRender()
  })
  nextTileset.addEventListener('load-error', (event: any) => {
    if (activeToken !== loadToken) return
    console.error(event)
    const message =
      event?.error?.message ||
      event?.message ||
      event?.target?.error?.message ||
      '点云加载失败'
    statusText.value = message
    loaded.value = false
    emit('loaded-change', false)
    tilesLoadingCount = 0
    isPointcloudLoading = false
    requestRender()
  })
  nextTileset.addEventListener('load-root-tileset', () => {
    if (activeToken !== loadToken) return
    if (!controls || !camera) {
      return
    }

    if (rendererMode === 'webgpu') {
      sanitizeObjectForWebGPU(nextTileset.group)
    }
    applyPointcloudMaterialMode(nextTileset.group)
    if (sectionEnabled) {
      resetClipBoxToContent()
    }
    applyPointcloudClipping()

    const sphere = new THREE.Sphere()
    if (nextTileset.getBoundingSphere?.(sphere)) {
      nextTileset.group.position.copy(sphere.center).multiplyScalar(-1)
      nextTileset.group.updateMatrixWorld(true)
      syncGridToPointcloud(tilesetWrapper ?? nextTileset.group)
      syncBoundsHelpers()
      fitCameraToRadius(camera, controls, sphere.radius)
      setTopView(camera, controls, sphere.radius * 2.2)
      emitCameraPose()
      requestRender()
      return
    }

    const placementTarget = tilesetWrapper ?? nextTileset.group
    syncGridToPointcloud(placementTarget)
    syncBoundsHelpers()
    fitCameraToObject(camera, controls, placementTarget)
    emitCameraPose()
    requestRender()
  })

  tileset = nextTileset
  requestRender()
}

function cleanup() {
  loadToken += 1

  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = 0
  }
  isRendering = false
  needsRender = false
  tilesLoadingCount = 0
  isPointcloudLoading = false

  resizeObserver?.disconnect()
  resizeObserver = null

  if (tileset) {
    if (tilesetWrapper && scene) {
      if (clippingGroup) {
        clippingGroup.remove(tilesetWrapper)
      } else {
        scene.remove(tilesetWrapper)
      }
      tilesetWrapper = null
    }
    disposeObject3D(tileset.group)
    tileset.dispose?.()
    tileset = null
  }

  if (boundsBoxHelper && scene) {
    scene.remove(boundsBoxHelper)
    boundsBoxHelper = null
  }

  clearClipHandles()
  endClipDrag()

  controls?.dispose()
  renderer?.domElement?.removeEventListener?.('pointerdown', handleViewportPointerDown)
  renderer?.domElement?.removeEventListener?.('pointermove', onViewportPointerMove)
  renderer?.domElement?.removeEventListener?.('pointerup', onViewportPointerUp)
  renderer?.domElement?.removeEventListener?.('pointercancel', onViewportPointerCancel)
  renderer?.dispose()
  if (renderer?.domElement?.parentElement) {
    renderer.domElement.parentElement.removeChild(renderer.domElement)
  }

  scene = null
  camera = null
  renderer = null
  controls = null
  pointcloudMaxDim = 1
  fixedViewSize = null
  lastTilesErrorTarget = -1
  rendererMode = null
  rendererReady = false
  initPromise = null
  axesHelper = null
  gridHelper = null
  clippingGroup = null
  raycaster = null
  clipBoxState = null
}

function reload() {
  if (!props.assetId) {
    loaded.value = false
    resetGridPlacement()
    emit('loaded-change', false)
    requestRender()
    return
  }

  void loadTileset(props.assetId).catch((error) => {
    console.error(error)
    loaded.value = false
    emit('loaded-change', false)
    requestRender()
  })
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
  applyPointcloudClipping()
  syncBoundsHelpers()
  requestRender()
}

function setPointColor(color: string | null) {
  pointColorOverride.value = color
}

defineExpose({
  reload,
  resetPointcloudView,
  getCameraPose,
  getCameraOrientation,
  getCameraDistance,
  syncFromRotation,
  syncFromCameraDistance,
  syncFromExternalPose,
  setBackgroundTheme,
  setShowAxes,
  setShowGrid,
  setSectionState,
  setPointColor,
})

watch(
  () => [props.assetId] as const,
  () => {
    if (!isMountedReady) {
      return
    }

    reload()
  },
)

watch(pointColorOverride, () => {
  applyPointcloudClipping()
  requestRender()
})

onMounted(() => {
  isMountedReady = true
  void initViewer()
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
      <span class="panel-title">点云预览</span>
      <div class="panel-actions">
        <span class="panel-status">{{ statusText }}</span>
        <button
          class="panel-refresh-btn"
          type="button"
          aria-label="重置视角"
          @click="resetPointcloudView"
        >
          <el-icon><RefreshRight /></el-icon>
        </button>
      </div>
    </div>



    <div v-if="!loaded" class="empty-state" :class="{ 'is-minimal': minimal }">
      <el-icon><View /></el-icon>
      <p>{{ getPlaceholderText() }}</p>
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

.panel-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.panel-status {
  font-size: 0.84rem;
  color: #94a3b8;
}

.color-control {
  min-width: 220px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border-radius: 999px;
  color: #dbe6f5;
  background: rgba(15, 23, 42, 0.7);
  box-shadow: inset 0 0 0 1px rgba(71, 85, 105, 0.35);
}

.color-label {
  font-size: 12px;
  color: #94a3b8;
  white-space: nowrap;
}

.color-value {
  min-width: 40px;
  font-size: 12px;
  text-align: right;
  color: #d9fbff;
}

.color-input {
  width: 42px;
  height: 28px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
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

.panel-refresh-btn {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #e2e8f0;
  background: rgba(30, 41, 59, 0.9);
  cursor: pointer;
  transition: background-color 0.2s ease, transform 0.2s ease;
}

.panel-refresh-btn:hover {
  background: rgba(51, 65, 85, 0.95);
}

.panel-refresh-btn:active {
  transform: scale(0.96);
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
  min-height: 750px;
}

@media (max-width: 900px) {
  .panel-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .panel-actions {
    width: 100%;
    justify-content: space-between;
  }

  .color-control {
    min-width: 0;
    width: 100%;
  }
}
</style>
