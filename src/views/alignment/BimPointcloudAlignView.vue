<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft,
  Box,
  Delete,
  Location,
  RefreshLeft,
} from '@element-plus/icons-vue'
import * as THREE from 'three'
import {
  ClippingGroup,
  MeshBasicNodeMaterial,
  MeshLambertNodeMaterial,
  NodeMaterial,
  PointsNodeMaterial,
  WebGPURenderer,
} from 'three/webgpu'
import { color as tslColor, float, vertexColor as tslVertexColor } from 'three/tsl'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { TilesRenderer } from '3d-tiles-renderer'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import { createBimAlignment, getBimAlignment } from '@/api/backend-alignment'
import {
  getAssetDetail,
  getBimGlbFile,
  getBimMetadata,
  getPointcloudTilesAsset,
  getPointcloudTilesetUrl,
} from '@/api/backend-file'
import { normalizeBackendUrl } from '@/api/backend-http'
import { createUploadHeaders } from '@/config/upload-backend'
import wanggeIcon from '@/assets/images/wangge.png'
import toushiIcon from '@/assets/images/toushi.png'
import zhengjiaoIcon from '@/assets/images/zhengjiao.png'

type ProjectionMode = 'perspective' | 'orthographic'
type MaterialMode = 'original' | 'unlit' | 'lambert'
type ClipAxis = 'x' | 'y' | 'z'
type SelectedItemId = '' | 'bim' | 'pointcloud'
type TransformMode = 'translate' | 'rotate'
type ViewerTransformControls = TransformControls &
  THREE.Object3D & {
    visible: boolean
  }

const props = defineProps<{
  bimAssetId: number | null
  pointcloudAssetId: number | null
  bimDisplayName?: string
  pointcloudDisplayName?: string
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('准备就绪')
const showPanel = ref(true)
const loadingBim = ref(false)
const loadingPointcloud = ref(false)
const loadingAlignmentMatrix = ref(false)
const showAlignmentMatrixDialog = ref(false)
const alignmentMatrixDialogText = ref('[]')
const projectionMode = ref<ProjectionMode>('perspective')
const materialMode = ref<MaterialMode>('unlit')
const showGrid = ref(true)
const showBounds = ref(false)
const backgroundColor = ref('#0b1020')
const enableClipping = ref(false)
const clipAxis = ref<ClipAxis>('z')
const clipInvert = ref(false)
const clipPosition = ref(0)
const clipRange = ref({ min: -1, max: 1 })
const editMode = ref(false)
const selectedItemId = ref<SelectedItemId>('')
const transformMode = ref<TransformMode>('translate')
const positionOffsetX = ref(0)
const positionOffsetY = ref(0)
const positionOffsetZ = ref(0)
const orientationDegX = ref(0)
const orientationDegY = ref(0)
const orientationDegZ = ref(0)
const positionStepOptions = [0.001, 0.01, 0.1, 1] as const
const rotationStepOptions = [0.01, 0.1, 1, 5] as const
const positionAdjustStep = ref(0.01)
const rotationAdjustStep = ref(1)
const positionStepPreset = ref('0.01')
const rotationStepPreset = ref('1')
const tilesErrorTarget = ref(16)
const enableElementPicking = ref(true)
const pickedElement = ref<null | {
  label: string
  ifcId?: string
  stepId?: number | string
  type?: string
  sourceLabel?: string
}>(null)
const bimMetadata = ref<any | null>(null)
const bimLoaded = ref(false)
const pointcloudLoaded = ref(false)
const bimVisible = ref(true)
const pointcloudVisible = ref(true)
const activeView = ref('')

const hasModel = computed(() => bimLoaded.value)
const hasTileset = computed(() => pointcloudLoaded.value)
const bimVisibilityLabel = computed(() => (bimVisible.value ? '隐藏模型' : '显示模型'))
const pointcloudVisibilityLabel = computed(() =>
  pointcloudVisible.value ? '隐藏点云' : '显示点云',
)
const visibilityToggleAllLabel = computed(() =>
  !bimVisible.value && !pointcloudVisible.value ? '显示全部' : '清全部',
)
const selectedItemIsPointcloud = computed(() => selectedItemId.value === 'pointcloud')
const showOnlyVerticalAxis = computed(() => {
  return Boolean(selectedItemId.value) && transformMode.value === 'rotate'
})
const positionSliderRange = computed(() => {
  const maxAbs = Math.max(
    50,
    Math.abs(positionOffsetX.value),
    Math.abs(positionOffsetY.value),
    Math.abs(positionOffsetZ.value),
  )
  const padded = Math.ceil((maxAbs + 5) / 5) * 5
  return {
    min: -padded,
    max: padded,
  }
})
const loadedItemOptions = computed(() => {
  const list: Array<{ label: string; value: SelectedItemId }> = []
  if (hasModel.value) {
    list.push({
      label: props.bimDisplayName || `BIM-${props.bimAssetId ?? ''}`,
      value: 'bim',
    })
  }
  if (hasTileset.value) {
    list.push({
      label: props.pointcloudDisplayName || `点云-${props.pointcloudAssetId ?? ''}`,
      value: 'pointcloud',
    })
  }
  return list
})

const pickedElementTitle = computed(() => {
  if (!pickedElement.value) return ''
  return pickedElement.value.label || pickedElement.value.ifcId || '构件'
})

const webgpuSupported = computed(
  () => typeof navigator !== 'undefined' && 'gpu' in navigator,
)

const dprCap = 1.25
const clipStep = computed(() => {
  const span = (clipRange.value?.max ?? 0) - (clipRange.value?.min ?? 0)
  if (!Number.isFinite(span) || span <= 0) return 0.001
  return Math.max(span / 1000, 1e-6)
})
const originalMaterialStore = new WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>()
const pointcloudUnlitMaterialCache = new WeakMap<
  THREE.Material,
  THREE.Material | { single?: THREE.Material; multi?: THREE.Material }
>()
const pointcloudUnlitTSLMaterialCache = new WeakMap<
  THREE.Material,
  { single?: THREE.Material; multi?: THREE.Material }
>()

let scene: THREE.Scene | null = null
let renderer: WebGPURenderer | THREE.WebGLRenderer | null = null
let perspectiveCamera: THREE.PerspectiveCamera | null = null
let orthographicCamera: THREE.OrthographicCamera | null = null
let activeCamera: THREE.PerspectiveCamera | THREE.OrthographicCamera | null = null
let controls: OrbitControls | null = null
let animationId = 0
let resizeObserver: ResizeObserver | null = null
let contentGroup: THREE.Group | null = null
let clippingGroup: ClippingGroup | null = null
let gridHelper: THREE.GridHelper | null = null
let axesHelper: THREE.AxesHelper | null = null
let transformControls: ViewerTransformControls | null = null
let selectionHelper: THREE.BoxHelper | null = null
let pickedElementHelper: THREE.BoxHelper | null = null
let bimPivot: THREE.Group | null = null
let bimRoot: THREE.Object3D | null = null
let pointcloudWrapper: THREE.Group | null = null
let pointcloudGroup: THREE.Group | null = null
let tileset: TilesRenderer | null = null
let bimBoundsHelper: THREE.BoxHelper | null = null
let pointcloudBoundsHelper: THREE.BoxHelper | null = null
let clippingPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0)
let orthoViewSize = 10
let bimLoadToken = 0
let pointcloudLoadToken = 0
let lastAlignmentKey = ''
let raycaster: THREE.Raycaster | null = null
let pointcloudMaxDim = 1
let rendererMode: 'webgpu' | 'webgl' | null = null
let initPromise: Promise<void> | null = null
let clipUpdateScheduled = false
let highlightedElement:
  | {
      mesh: THREE.Mesh
      overlay: THREE.Mesh
      material: THREE.Material
    }
  | null = null

function isPerspectiveCamera(
  camera: THREE.PerspectiveCamera | THREE.OrthographicCamera,
): camera is THREE.PerspectiveCamera {
  return camera instanceof THREE.PerspectiveCamera
}

function isOrthographicCamera(
  camera: THREE.PerspectiveCamera | THREE.OrthographicCamera,
): camera is THREE.OrthographicCamera {
  return camera instanceof THREE.OrthographicCamera
}

function closePage() {
  if (window.opener) {
    window.close()
    return
  }

  window.location.href = window.location.origin
}

function parseColor(value: string) {
  const normalized = value.trim()
  if (/^#[0-9a-fA-F]{6}$/.test(normalized) || /^#[0-9a-fA-F]{3}$/.test(normalized)) {
    return normalized
  }
  return '#000000'
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function ensureTrailingSlash(value: string) {
  return value.endsWith('/') ? value : `${value}/`
}

function resolveTileResourceUrl(uri: string, baseUrl: string) {
  if (/^(?:https?:)?\/\//i.test(uri) || uri.startsWith('blob:') || uri.startsWith('data:')) {
    return uri
  }

  const normalizedBaseUrl = /^(?:https?:)?\/\//i.test(baseUrl)
    ? baseUrl
    : new URL(baseUrl, window.location.origin).toString()

  return new URL(uri, normalizedBaseUrl).toString()
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
    child?.geometry?.dispose?.()
    const material = child?.material
    if (Array.isArray(material)) {
      material.forEach((item) => item?.dispose?.())
      return
    }
    material?.dispose?.()
  })
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
  const maxBufferBytes = 256 * 1024 * 1024
  const softLimit = maxBufferBytes - 8 * 1024 * 1024

  root.traverse((obj: any) => {
    const geometry = obj?.geometry
    if (!geometry?.isBufferGeometry) return

    ensureWebGPUVertexAlignment(geometry)
    const bytes = estimateGeometryBytes(geometry)
    if (bytes > softLimit) {
      obj.visible = false
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

function updateRendererBackground() {
  const next = new THREE.Color(parseColor(backgroundColor.value))
  scene!.background = next
  renderer?.setClearColor(next, 1)
}

function onBackgroundColorChange() {
  if (!scene || !renderer) return
  updateRendererBackground()
}

function resetBackgroundColor() {
  backgroundColor.value = '#0b1020'
  onBackgroundColorChange()
}

function syncGridVisibility() {
  if (gridHelper) {
    gridHelper.visible = showGrid.value
  }
}

function updateGridPlacement() {
  if (!gridHelper || !contentGroup) return

  const placementTarget =
    pointcloudWrapper ?? (contentGroup.children.length ? contentGroup : null)

  if (!placementTarget) {
    gridHelper.position.set(0, -10.01, 0)
    return
  }

  placementTarget.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(placementTarget)
  if (box.isEmpty()) {
    gridHelper.position.set(0, -10.01, 0)
    return
  }

  const center = box.getCenter(new THREE.Vector3())
  const size = box.getSize(new THREE.Vector3())
  const offset = Math.max(10.5, size.y * 0.01)
  gridHelper.position.set(center.x, box.min.y - offset, center.z)
}

function updateSelectionHighlight() {
  if (!scene) return
  if (editMode.value) {
    if (selectionHelper) {
      selectionHelper.visible = false
    }
    return
  }

  const selected = getSelectedObject()
  if (!selected || !selected.visible) {
    if (selectionHelper) {
      selectionHelper.visible = false
    }
    return
  }

  if (!selectionHelper) {
    selectionHelper = new THREE.BoxHelper(selected, 0xf59e0b)
    scene.add(selectionHelper)
  }

  selectionHelper.setFromObject(selected)
  selectionHelper.visible = true
}

function disposeMaterial(material: THREE.Material | null | undefined) {
  material?.dispose?.()
}

function guessIfcId(userData: unknown): string | undefined {
  if (!userData || typeof userData !== 'object') return undefined

  const keys = [
    'expressID',
    'ExpressID',
    'expressId',
    'ifcId',
    'ifcID',
    'IfcId',
    'globalId',
    'GlobalId',
    'guid',
    'GUID',
    'IfcGUID',
    'ifcGuid',
  ] as const

  for (const key of keys) {
    const value = (userData as Record<string, unknown>)[key]
    if (value === null || value === undefined) continue
    const text = String(value).trim()
    if (text) return text
  }

  const nested =
    (userData as Record<string, unknown>).properties ??
    (userData as Record<string, unknown>).PropertySets ??
    (userData as Record<string, unknown>).ifc ??
    null

  if (nested && typeof nested === 'object') {
    for (const key of keys) {
      const value = (nested as Record<string, unknown>)[key]
      if (value === null || value === undefined) continue
      const text = String(value).trim()
      if (text) return text
    }
  }

  return undefined
}

function getElementIdFromObject(object: THREE.Object3D | null) {
  let current: THREE.Object3D | null = object

  while (current) {
    const name = String(current.name || '').trim()
    if (name) {
      if (/^[0-9A-Za-z_$]{22}$/.test(name)) {
        return name
      }

      const [baseName] = name.split('_')
      if (baseName?.trim()) {
        return baseName.trim()
      }
      return name
    }

    const ifcId = guessIfcId(current.userData)
    if (ifcId) return ifcId
    current = current.parent
  }

  return undefined
}

function findMetadataElementById(id: unknown) {
  if (!bimMetadata.value) return null

  const key = String(id ?? '').trim()
  if (!key) return null

  const elements = bimMetadata.value?.elements
  if (!elements || typeof elements !== 'object') return null

  if (elements[key]) {
    return {
      id: key,
      meta: elements[key] as Record<string, unknown>,
    }
  }

  for (const [elementId, meta] of Object.entries(elements as Record<string, unknown>)) {
    const current = meta as Record<string, unknown>
    if (
      current?.stepId === Number(key) ||
      current?.id === key ||
      current?.name === key
    ) {
      return {
        id: elementId,
        meta: current,
      }
    }
  }

  return null
}

function restoreHighlightedElement() {
  if (!highlightedElement) return

  highlightedElement.mesh.remove(highlightedElement.overlay)
  disposeMaterial(highlightedElement.material)
  highlightedElement = null
}

function clearPickedElement() {
  restoreHighlightedElement()
  pickedElement.value = null
  if (pickedElementHelper) pickedElementHelper.visible = false
}

function createHighlightOverlayMaterial(colorValue: THREE.ColorRepresentation) {
  const material =
    materialMode.value === 'lambert'
      ? new MeshLambertNodeMaterial()
      : new MeshBasicNodeMaterial()

  material.transparent = true
  material.opacity = 0.65
  material.depthTest = false
  material.depthWrite = false
  material.polygonOffset = true
  material.polygonOffsetFactor = -1
  material.polygonOffsetUnits = -1
  material.toneMapped = false
  material.colorNode = tslColor(new THREE.Color(colorValue))
  material.vertexColors = false
  material.needsUpdate = true

  return material
}

function highlightPickedElement(target: THREE.Object3D) {
  restoreHighlightedElement()

  if ((target as THREE.Mesh)?.isMesh) {
    const mesh = target as THREE.Mesh
    const material = createHighlightOverlayMaterial('#ffcf4a')
    const overlay = new THREE.Mesh(mesh.geometry, material)
    overlay.name = 'Pick Highlight Overlay'
    overlay.userData.__viewerPickIgnore = true
    overlay.frustumCulled = false
    overlay.matrixAutoUpdate = false
    overlay.renderOrder = 9998
    overlay.matrix.identity()
    mesh.add(overlay)
    highlightedElement = { mesh, overlay, material }
    if (pickedElementHelper) pickedElementHelper.visible = false
    return
  }

  if (!scene) return
  if (!pickedElementHelper) {
    pickedElementHelper = new THREE.BoxHelper(target, 0xffcf4a)
    scene.add(pickedElementHelper)
  }
  pickedElementHelper.setFromObject(target)
  pickedElementHelper.visible = true
}

function syncBoundsHelpers() {
  if (!scene) return

  if (!showBounds.value) {
    if (bimBoundsHelper) bimBoundsHelper.visible = false
    if (pointcloudBoundsHelper) pointcloudBoundsHelper.visible = false
    updateGridPlacement()
    updateSelectionHighlight()
    return
  }

  if (bimPivot) {
    if (!bimBoundsHelper) {
      bimBoundsHelper = new THREE.BoxHelper(bimPivot, 0x60a5fa)
      scene.add(bimBoundsHelper)
    }
    bimBoundsHelper.setFromObject(bimPivot)
    bimBoundsHelper.visible = true
  }

  if (pointcloudGroup) {
    if (!pointcloudBoundsHelper) {
      pointcloudBoundsHelper = new THREE.BoxHelper(pointcloudGroup, 0x22d3ee)
      scene.add(pointcloudBoundsHelper)
    }
    pointcloudBoundsHelper.setFromObject(pointcloudGroup)
    pointcloudBoundsHelper.visible = true
  }

  updateGridPlacement()
  updateSelectionHighlight()
}

function getVisibleContentBox() {
  const box = new THREE.Box3()
  let hasAny = false

  if (bimPivot?.visible) {
    box.expandByObject(bimPivot)
    hasAny = true
  }
  if (pointcloudGroup?.visible) {
    box.expandByObject(pointcloudGroup)
    hasAny = true
  }

  return hasAny ? box : null
}

function updateOrthographicFrustum() {
  if (!orthographicCamera || !viewportEl.value) return

  const rect = viewportEl.value.getBoundingClientRect()
  const aspect = Math.max(1, rect.width) / Math.max(1, rect.height)
  orthographicCamera.left = -orthoViewSize * aspect
  orthographicCamera.right = orthoViewSize * aspect
  orthographicCamera.top = orthoViewSize
  orthographicCamera.bottom = -orthoViewSize
  orthographicCamera.updateProjectionMatrix()
}

function syncRendererSize() {
  if (!renderer || !activeCamera || !viewportEl.value) return

  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))
  const dpr = Math.min(window.devicePixelRatio || 1, dprCap)

  renderer.setPixelRatio(dpr)
  renderer.setSize(width, height, false)

  if (isPerspectiveCamera(activeCamera)) {
    activeCamera.aspect = width / height
    activeCamera.updateProjectionMatrix()
  } else {
    updateOrthographicFrustum()
  }

  updateTilesetResolution()
}

function updateTilesetResolution() {
  if (!viewportEl.value || !tileset || !activeCamera || !renderer) {
    return false
  }

  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))

  const updatedBySize = tileset.setResolution?.(activeCamera, width, height) ?? false
  const updatedByRenderer =
    tileset.setResolutionFromRenderer?.(activeCamera, renderer as THREE.WebGLRenderer) ?? false
  return updatedBySize || updatedByRenderer
}

function applyTilesErrorTarget() {
  if (!tileset) return
  tileset.errorTarget = tilesErrorTarget.value
}

function onTilesErrorTargetInput() {
  applyTilesErrorTarget()
}

function requestRender() {
  if (animationId) return

  const renderFrame = () => {
    animationId = requestAnimationFrame(renderFrame)

    if (!renderer || !scene || !activeCamera) {
      return
    }

    if (tileset) {
      runWithSuppressedConsoleAssert(() => {
        applyTilesErrorTarget()
        updateTilesetResolution()
        tileset!.setCamera(activeCamera!)
        tileset!.setResolutionFromRenderer?.(activeCamera!, renderer! as THREE.WebGLRenderer)
        tileset!.update()
      })
    }

    syncBoundsHelpers()
    renderer.render(scene, activeCamera)
  }

  renderFrame()
}

function stopRenderLoop() {
  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = 0
  }
}

function mountControls(camera: THREE.PerspectiveCamera | THREE.OrthographicCamera) {
  if (!renderer) return

  const currentTarget = controls?.target.clone() ?? new THREE.Vector3(0, 0, 0)
  controls?.dispose()
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = false
  controls.target.copy(currentTarget)
  controls.addEventListener('change', () => {
    syncBoundsHelpers()
  })
  controls.update()

  if (scene && renderer) {
    if (transformControls) {
      scene.remove(transformControls.getHelper() as unknown as THREE.Object3D)
      transformControls.dispose()
    }

    transformControls = new TransformControls(
      camera,
      renderer.domElement,
    ) as ViewerTransformControls
    transformControls.visible = false
    transformControls.enabled = false
    transformControls.setMode(transformMode.value)
    transformControls.addEventListener('dragging-changed', (event: any) => {
      if (controls) {
        controls.enabled = !event.value
      }
    })
    transformControls.addEventListener('objectChange', () => {
      syncBoundsHelpers()
      scheduleClipRangeUpdate()
      applyClippingState()
    })
    transformControls.addEventListener('mouseUp', () => {
      const target = getSelectedObject()
      if (!target) return
      ensureTransformState(target)
      target.userData.__basePosition = target.position.clone()
      target.userData.__baseQuaternion = target.quaternion.clone()
      positionOffsetX.value = 0
      positionOffsetY.value = 0
      positionOffsetZ.value = 0
      orientationDegX.value = 0
      orientationDegY.value = 0
      orientationDegZ.value = 0
    })
    scene.add(transformControls.getHelper() as unknown as THREE.Object3D)
  }
}

async function initScene() {
  if (!viewportEl.value || renderer) return
  if (initPromise) return initPromise

  initPromise = (async () => {
    if (!viewportEl.value) return

    scene = new THREE.Scene()
    contentGroup = new THREE.Group()
    raycaster = new THREE.Raycaster()

    const width = viewportEl.value.clientWidth || 1
    const height = viewportEl.value.clientHeight || 1

    perspectiveCamera = new THREE.PerspectiveCamera(55, width / height, 0.1, 100000)
    perspectiveCamera.position.set(0, 14, 24)

    orthographicCamera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 100000)
    orthographicCamera.position.copy(perspectiveCamera.position)

    activeCamera = perspectiveCamera

    const setupRendererCommon = () => {
      if (!renderer || !activeCamera || !scene || !viewportEl.value) return

      ;(renderer as any).localClippingEnabled = true
      renderer.domElement.addEventListener('pointerdown', handleViewportPointerDown)

      if (clippingGroup) {
        clippingGroup.remove(contentGroup!)
        scene.remove(clippingGroup)
        clippingGroup = null
      }

      if (rendererMode === 'webgpu') {
        clippingGroup = new ClippingGroup()
        scene.add(clippingGroup)
        clippingGroup.add(contentGroup!)
      } else {
        scene.add(contentGroup!)
      }

      mountControls(activeCamera)
      updateRendererBackground()

      scene.add(new THREE.AmbientLight(0xffffff, 0.78))
      const keyLight = new THREE.DirectionalLight(0xffffff, 0.92)
      keyLight.position.set(14, 18, 12)
      scene.add(keyLight)
      const fillLight = new THREE.DirectionalLight(0x9cc3ff, 0.42)
      fillLight.position.set(-10, 8, -10)
      scene.add(fillLight)

      axesHelper = new THREE.AxesHelper(18)
      axesHelper.setColors('#fb7185', '#86efac', '#67e8f9')
      ;(axesHelper.material as THREE.LineBasicMaterial).transparent = true
      ;(axesHelper.material as THREE.LineBasicMaterial).opacity = 0.95
      scene.add(axesHelper)

      gridHelper = new THREE.GridHelper(10000, 2000, 0x67e8f9, 0x2a6f82)
      ;(gridHelper.material as THREE.LineBasicMaterial).transparent = true
      ;(gridHelper.material as THREE.LineBasicMaterial).opacity = 0.62
      gridHelper.position.set(0, -10.01, 0)
      scene.add(gridHelper)
      syncGridVisibility()

      resizeObserver = new ResizeObserver(() => {
        syncRendererSize()
      })
      resizeObserver.observe(viewportEl.value)

      syncRendererSize()
      requestRender()
    }

    const buildWebGLRenderer = () => {
      rendererMode = 'webgl'
      const nextRenderer = new THREE.WebGLRenderer({
        antialias: true,
        powerPreference: 'high-performance',
      })
      nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
      nextRenderer.setSize(width, height)
      nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
      nextRenderer.toneMappingExposure = 1
      if ('outputColorSpace' in nextRenderer) {
        nextRenderer.outputColorSpace = THREE.SRGBColorSpace
      }
      viewportEl.value?.appendChild(nextRenderer.domElement)
      renderer = nextRenderer
      setupRendererCommon()
    }

    const buildWebGPURenderer = async () => {
      rendererMode = 'webgpu'
      const nextRenderer = new WebGPURenderer({ antialias: true })
      nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
      nextRenderer.setSize(width, height)
      nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
      nextRenderer.toneMappingExposure = 1
      viewportEl.value?.appendChild(nextRenderer.domElement)
      await nextRenderer.init?.()
      renderer = nextRenderer
      setupRendererCommon()
    }

    const supportsWebGPU = typeof navigator !== 'undefined' && 'gpu' in navigator
    if (supportsWebGPU) {
      try {
        await buildWebGPURenderer()
      } catch (error) {
        console.error('[BimPointcloudAlign] WebGPU 初始化失败:', error)
        renderer = null
        rendererMode = null
      }
    }

    if (!renderer) {
      buildWebGLRenderer()
    }
  })()

  await initPromise
}

function fitCameraToBox(box: THREE.Box3) {
  if (!activeCamera || !controls) return

  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z, 1)

  controls.target.copy(center)

  if (projectionMode.value === 'orthographic' && isOrthographicCamera(activeCamera)) {
    orthoViewSize = maxDim * 0.72
    updateOrthographicFrustum()
    activeCamera.position.set(center.x, center.y + maxDim * 1.4, center.z + maxDim * 1.6)
    activeCamera.near = 0.01
    activeCamera.far = Math.max(5000, maxDim * 200)
    activeCamera.updateProjectionMatrix()
  } else if (isPerspectiveCamera(activeCamera)) {
    const fov = THREE.MathUtils.degToRad(activeCamera.fov)
    const distance = maxDim / 2 / Math.tan(fov / 2)
    activeCamera.position.set(center.x, center.y + maxDim * 0.22, center.z + distance * 2.2)
    activeCamera.near = Math.max(0.01, distance / 100)
    activeCamera.far = Math.max(5000, distance * 200)
    activeCamera.updateProjectionMatrix()
  }

  activeCamera.lookAt(center)
  controls.update()
}

function fitCameraToContent() {
  const box = getVisibleContentBox()
  if (!box) return
  fitCameraToBox(box)
}

function fitCameraToRadius(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  radius: number,
) {
  const safeRadius = Math.max(radius, 1)
  const maxDim = safeRadius * 2
  pointcloudMaxDim = maxDim
  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)

  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, maxDim * 0.15, distance * 2.2)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(100000, distance * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function setTopViewForPerspective(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  height: number,
) {
  const safeHeight = Number.isFinite(height) && height > 0 ? height : 10
  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, safeHeight, 0.1)
  nextCamera.lookAt(0, 0, 0)
  nextCamera.near = 0.01
  nextCamera.far = Math.max(5000, safeHeight * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function setPresetView(kind: 'front' | 'top' | 'side') {
  if (!activeCamera || !controls) return

  const box = getVisibleContentBox()
  if (!box) return

  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z, 1)
  const distance = maxDim * 1.45
  controls.target.copy(center)

  if (kind === 'front') {
    activeCamera.position.set(center.x, center.y, center.z + distance)
  } else if (kind === 'top') {
    activeCamera.position.set(center.x, center.y + distance, center.z + 0.1)
  } else {
    activeCamera.position.set(center.x + distance, center.y, center.z)
  }

  activeCamera.lookAt(center)
  activeCamera.updateProjectionMatrix()
  controls.update()
  activeView.value = kind
}

function setFrontView() {
  setPresetView('front')
}

function setTopView() {
  setPresetView('top')
}

function setSideView() {
  setPresetView('side')
}

function setProjectionMode(nextMode: ProjectionMode) {
  if (!perspectiveCamera || !orthographicCamera || !activeCamera) return
  if (projectionMode.value === nextMode) return

  const previousCamera = activeCamera
  const previousTarget = controls?.target.clone() ?? new THREE.Vector3()
  const nextCamera =
    nextMode === 'perspective' ? perspectiveCamera : orthographicCamera

  nextCamera.position.copy(previousCamera.position)
  nextCamera.quaternion.copy(previousCamera.quaternion)
  nextCamera.near = previousCamera.near
  nextCamera.far = previousCamera.far

  if (isOrthographicCamera(nextCamera)) {
    const box = getVisibleContentBox()
    if (box) {
      const size = box.getSize(new THREE.Vector3())
      orthoViewSize = Math.max(size.x, size.y, size.z, 1) * 0.72
    }
    updateOrthographicFrustum()
  } else {
    nextCamera.aspect = isPerspectiveCamera(previousCamera)
      ? previousCamera.aspect
      : (viewportEl.value?.clientWidth || 1) / (viewportEl.value?.clientHeight || 1)
    nextCamera.updateProjectionMatrix()
  }

  activeCamera = nextCamera
  projectionMode.value = nextMode
  mountControls(nextCamera)
  controls?.target.copy(previousTarget)
  controls?.update()

    if (tileset) {
      tileset.setCamera(nextCamera)
      tileset.setResolutionFromRenderer?.(nextCamera, renderer! as THREE.WebGLRenderer)
      applyTilesErrorTarget()
    }
  }

function getMaterialClone(
  source: THREE.Material,
  mode: MaterialMode,
  opts: { isPoints: boolean; vertexColors: boolean },
) {
  if (mode === 'original') {
    return source
  }

  if (opts.isPoints) {
    const next = new THREE.PointsMaterial({
      color: (source as any).color?.clone?.() ?? new THREE.Color(0xffffff),
      size: Math.max(0.2, (source as any).size ?? 1),
      sizeAttenuation: (source as any).sizeAttenuation ?? true,
      map: (source as any).map ?? null,
      alphaMap: (source as any).alphaMap ?? null,
      vertexColors: opts.vertexColors,
    })
    applySharedMaterialFlags(next, source)
    next.depthWrite = true
    next.depthTest = true
    next.fog = false
    next.toneMapped = mode === 'lambert'
    return next
  }

  if (mode === 'unlit') {
    const next = new THREE.MeshBasicMaterial({
      color: (source as any).color?.clone?.() ?? new THREE.Color(0xffffff),
      map: (source as any).map ?? null,
      alphaMap: (source as any).alphaMap ?? null,
      vertexColors: opts.vertexColors,
    })
    applySharedMaterialFlags(next, source)
    next.toneMapped = false
    return next
  }

  const next = new THREE.MeshLambertMaterial({
    color: (source as any).color?.clone?.() ?? new THREE.Color(0xffffff),
    map: (source as any).map ?? null,
    alphaMap: (source as any).alphaMap ?? null,
    vertexColors: opts.vertexColors,
  })
  applySharedMaterialFlags(next, source)
  next.toneMapped = true
  return next
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

function applyBimMaterialMode(root: THREE.Object3D | null) {
  if (!root) return

  root.traverse((obj: any) => {
    if (!obj?.material) return

    if (!originalMaterialStore.has(obj)) {
      originalMaterialStore.set(obj, obj.material)
    }

    if (materialMode.value === 'original') {
      obj.material = originalMaterialStore.get(obj)
      return
    }

    const opts = {
      isPoints: Boolean(obj.isPoints),
      vertexColors: !!obj.geometry?.attributes?.color,
    }
    const original = originalMaterialStore.get(obj)
    if (Array.isArray(original)) {
      obj.material = original.map((item) => getMaterialClone(item, materialMode.value, opts))
      return
    }

    if (original) {
      obj.material = getMaterialClone(original, materialMode.value, opts)
    }
  })

  applyClippingState()
}

function applyPointcloudMaterialMode(root: THREE.Object3D | null) {
  if (!root) return

  root.traverse((obj: any) => {
    if (!obj?.material) return

    if (!originalMaterialStore.has(obj)) {
      originalMaterialStore.set(obj, obj.material)
    }

    if (materialMode.value === 'original') {
      obj.material = originalMaterialStore.get(obj)
      return
    }

    if (materialMode.value !== 'unlit') {
      const opts = {
        isPoints: Boolean(obj.isPoints),
        vertexColors: !!obj.geometry?.attributes?.color,
      }
      const original = originalMaterialStore.get(obj)
      if (Array.isArray(original)) {
        obj.material = original.map((item) => getMaterialClone(item, materialMode.value, opts))
        return
      }

      if (original) {
        obj.material = getMaterialClone(original, materialMode.value, opts)
      }
      return
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

function updateClipRangeFromContent(
  opts: { resetPosition?: boolean; preserveT?: boolean } = {},
) {
  if (!contentGroup) return

  const prev = clipRange.value
  const prevSpan = (prev?.max ?? 0) - (prev?.min ?? 0)
  const t =
    opts.preserveT && Number.isFinite(prevSpan) && prevSpan > 0
      ? (clipPosition.value - prev.min) / prevSpan
      : 0

  const box = new THREE.Box3().setFromObject(contentGroup)
  if (!box || box.isEmpty()) {
    clipRange.value = { min: 0, max: 1 }
    clipPosition.value = 0
    return
  }

  const min = box.min[clipAxis.value]
  const max = box.max[clipAxis.value]
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    clipRange.value = {
      min: Number.isFinite(min) ? min : 0,
      max: (Number.isFinite(max) ? max : 0) + 1,
    }
  } else {
    clipRange.value = { min, max }
  }

  if (opts.resetPosition) {
    clipPosition.value = clipInvert.value ? clipRange.value.max : clipRange.value.min
    return
  }

  const nextSpan = clipRange.value.max - clipRange.value.min
  const nextPos =
    Number.isFinite(t) && nextSpan > 0
      ? clipRange.value.min + THREE.MathUtils.clamp(t, 0, 1) * nextSpan
      : clipPosition.value

  clipPosition.value = THREE.MathUtils.clamp(
    nextPos,
    clipRange.value.min,
    clipRange.value.max,
  )
}

function applyMaterialClipping(enabled: boolean) {
  if (!contentGroup) return

  contentGroup.traverse((obj: any) => {
    const material = obj?.material
    if (!material) return

    const applyToMaterial = (item: THREE.Material) => {
      item.clippingPlanes = enabled ? [clippingPlane] : null
      item.needsUpdate = true
    }

    if (Array.isArray(material)) {
      material.forEach((item) => applyToMaterial(item))
      return
    }

    applyToMaterial(material)
  })
}

function applyClippingState() {
  const enabled = !!enableClipping.value && !!hasModel.value && !!showBounds.value

  const axisVector =
    clipAxis.value === 'x'
      ? new THREE.Vector3(1, 0, 0)
      : clipAxis.value === 'y'
        ? new THREE.Vector3(0, 1, 0)
        : new THREE.Vector3(0, 0, 1)

  const normal = clipInvert.value ? axisVector.clone().negate() : axisVector
  clippingPlane = new THREE.Plane(
    normal,
    clipInvert.value ? clipPosition.value : -clipPosition.value,
  )

  if (rendererMode === 'webgpu' && clippingGroup) {
    clippingGroup.enabled = enabled
    clippingGroup.clippingPlanes.length = 0
    if (enabled) {
      clippingGroup.clippingPlanes.push(clippingPlane)
    }
    requestRender()
    return
  }

  applyMaterialClipping(enabled)
}

function scheduleClipRangeUpdate() {
  if (clipUpdateScheduled) return
  clipUpdateScheduled = true
  requestAnimationFrame(() => {
    clipUpdateScheduled = false
    updateClipRangeFromContent({ preserveT: true })
    applyClippingState()
  })
}

function onShowBoundsChange() {
  syncBoundsHelpers()
  if (!showBounds.value && enableClipping.value) {
    enableClipping.value = false
    applyClippingState()
  }
}

function onClippingEnabledChange() {
  if (!contentGroup) return
  if (!showBounds.value) {
    enableClipping.value = false
    ElMessage.warning('请先开启包围盒')
    return
  }

  updateClipRangeFromContent({ resetPosition: true })
  applyClippingState()
}

function onClippingParamsChange() {
  if (!contentGroup) return
  updateClipRangeFromContent({ preserveT: true })
  applyClippingState()
}

function ensureTransformState(obj: THREE.Object3D | null) {
  if (!obj) return

  obj.userData.__initialPosition = obj.userData.__initialPosition || obj.position.clone()
  obj.userData.__initialQuaternion = obj.userData.__initialQuaternion || obj.quaternion.clone()
  obj.userData.__basePosition = obj.userData.__basePosition || obj.position.clone()
  obj.userData.__baseQuaternion = obj.userData.__baseQuaternion || obj.quaternion.clone()
}

function getSelectedObject() {
  if (selectedItemId.value === 'bim') return bimPivot
  if (selectedItemId.value === 'pointcloud') return pointcloudWrapper
  return null
}

function selectSceneObject(
  next: SelectedItemId,
  options?: { focus?: boolean; enableEdit?: boolean },
) {
  if (!next) return

  selectedItemId.value = next

  if (options?.enableEdit) {
    editMode.value = true
  }

  refreshSelectedTransformUi(true)
  updateSelectionHighlight()

  if (options?.focus) {
    focusSelected()
  }
}

function resetOrientationFix() {
  orientationDegX.value = 0
  orientationDegY.value = 0
  orientationDegZ.value = 0
}

function resetPositionFix() {
  positionOffsetX.value = 0
  positionOffsetY.value = 0
  positionOffsetZ.value = 0
}

function normalizeDegrees(value: number) {
  let next = value
  while (next <= -180) next += 360
  while (next > 180) next -= 360
  return next
}

function roundToStep(value: number, step = 0.01) {
  return Math.round(value / step) * step
}

function getStepPrecision(step: number) {
  const normalized = Number(step)
  if (!Number.isFinite(normalized) || normalized <= 0) return 2

  const text = normalized.toString()
  if (text.includes('e-')) {
    const exponent = Number(text.split('e-')[1] || 0)
    return Math.min(3, Math.max(0, exponent))
  }

  const decimals = text.split('.')[1]?.length ?? 0
  return Math.min(3, Math.max(0, decimals))
}

function normalizeAdjustStep(
  value: unknown,
  { min, max, fallback }: { min: number; max: number; fallback: number },
) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return fallback
  return Math.min(max, Math.max(min, roundToStep(num, min)))
}

function getStepPresetValue(
  value: number,
  options: readonly number[],
  precision = 6,
) {
  const normalized = Number(value.toFixed(precision))
  const matched = options.find(
    (item) => Number(item.toFixed(precision)) === normalized,
  )
  return matched !== undefined ? String(matched) : 'custom'
}

function formatPositionStep(value: number) {
  const normalized = normalizeAdjustStep(value, {
    min: 0.001,
    max: 10,
    fallback: 0.01,
  })
  return normalized.toFixed(getStepPrecision(normalized))
}

function formatPositionStepLabel(value: number) {
  return `${formatPositionStep(value)} m`
}

function formatRotationStep(value: number) {
  const normalized = normalizeAdjustStep(value, {
    min: 0.01,
    max: 45,
    fallback: 1,
  })
  return normalized.toFixed(getStepPrecision(normalized))
}

function formatRotationStepLabel(value: number) {
  return `${formatRotationStep(value)} deg`
}

function formatPositionOffset(value: number) {
  const precision = Math.max(2, getStepPrecision(positionAdjustStep.value))
  return Number(value || 0).toFixed(precision)
}

function formatRotationOffset(value: number) {
  const precision = Math.max(2, getStepPrecision(rotationAdjustStep.value))
  return Number(value || 0).toFixed(precision)
}

function syncPositionStepPreset() {
  const presetValue = getStepPresetValue(positionAdjustStep.value, positionStepOptions)
  positionStepPreset.value =
    presetValue === 'custom' ? formatPositionStep(positionAdjustStep.value) : presetValue
}

function syncRotationStepPreset() {
  const presetValue = getStepPresetValue(rotationAdjustStep.value, rotationStepOptions)
  rotationStepPreset.value =
    presetValue === 'custom' ? formatRotationStep(rotationAdjustStep.value) : presetValue
}

function onPositionStepPresetChange(value: string) {
  positionAdjustStep.value = normalizeAdjustStep(value, {
    min: 0.001,
    max: 10,
    fallback: 0.01,
  })
  syncPositionStepPreset()
}

function onRotationStepPresetChange(value: string) {
  rotationAdjustStep.value = normalizeAdjustStep(value, {
    min: 0.01,
    max: 45,
    fallback: 1,
  })
  syncRotationStepPreset()
}

function syncTransformModeForSelection() {
  if (selectedItemIsPointcloud.value && transformMode.value === 'translate') {
    transformMode.value = 'rotate'
  }
}

function syncOrientationFixFromSelected() {
  const target = getSelectedObject()
  if (!target) return

  ensureTransformState(target)
  const base = target.userData.__baseQuaternion as THREE.Quaternion
  const offsetQuat = target.quaternion.clone().multiply(base.clone().invert())
  const offsetEuler = new THREE.Euler().setFromQuaternion(offsetQuat, 'YXZ')

  orientationDegX.value = 0
  orientationDegY.value = roundToStep(
    normalizeDegrees(THREE.MathUtils.radToDeg(offsetEuler.y)),
  )
  orientationDegZ.value = 0
}

function syncPositionFixFromSelected() {
  const target = getSelectedObject()
  if (!target) return

  ensureTransformState(target)
  const base = target.userData.__basePosition as THREE.Vector3
  const offset = target.position.clone().sub(base)
  positionOffsetX.value = roundToStep(offset.x)
  positionOffsetY.value = roundToStep(offset.z)
  positionOffsetZ.value = roundToStep(offset.y)
}

function syncTransformFixFromSelected() {
  if (transformMode.value === 'rotate') {
    syncOrientationFixFromSelected()
    return
  }

  syncPositionFixFromSelected()
}

function refreshSelectedTransformUi(rebaseBase = true) {
  const target = getSelectedObject()
  if (!target) {
    if (transformControls) {
      transformControls.detach()
      transformControls.visible = false
      transformControls.enabled = false
    }
    updateSelectionHighlight()
    return
  }

  syncTransformModeForSelection()
  ensureTransformState(target)
  target.matrixAutoUpdate = true
  target.updateMatrixWorld(true)
  updateSelectionHighlight()
  resetOrientationFix()
  resetPositionFix()

  if (rebaseBase) {
    target.userData.__basePosition = target.position.clone()
    target.userData.__baseQuaternion = target.quaternion.clone()
  } else {
    syncTransformFixFromSelected()
  }

  if (transformControls) {
    transformControls.setMode(transformMode.value)

    if (selectedItemIsPointcloud.value) {
      transformControls.showX = false
      transformControls.showY = false
      transformControls.showZ = false
      transformControls.detach()
      transformControls.visible = false
      transformControls.enabled = false
      return
    }

    if (transformMode.value === 'rotate') {
      transformControls.showX = false
      transformControls.showY = true
      transformControls.showZ = false
    } else {
      transformControls.showX = true
      transformControls.showY = true
      transformControls.showZ = true
    }

    transformControls.attach(target)
    transformControls.visible = editMode.value
    transformControls.enabled = editMode.value
  }
}

function setTransformMode(mode: TransformMode) {
  if (mode === 'translate' && selectedItemIsPointcloud.value) {
    transformMode.value = 'rotate'
    syncTransformFixFromSelected()
    return
  }

  transformMode.value = mode
  refreshSelectedTransformUi(false)
}

function onEditModeChange() {
  if (editMode.value && !selectedItemId.value) {
    selectedItemId.value = loadedItemOptions.value[0]?.value ?? ''
  }

  if (editMode.value && enableElementPicking.value) {
    enableElementPicking.value = false
  }

  syncTransformModeForSelection()

  if (!editMode.value) {
    if (transformControls) {
      transformControls.detach()
      transformControls.visible = false
      transformControls.enabled = false
    }
    resetOrientationFix()
    resetPositionFix()
    return
  }

  refreshSelectedTransformUi(false)
}

function onElementPickingChange() {
  if (enableElementPicking.value) {
    if (editMode.value) {
      editMode.value = false
    }
    return
  }

  clearPickedElement()
}

function onSelectedItemChange() {
  refreshSelectedTransformUi(false)
}

function resetTransformFixRealtime() {
  resetCurrentObjectTransform()
}

function resolveSelectionFromIntersection(object: THREE.Object3D | null) {
  let current: THREE.Object3D | null = object
  while (current) {
    if (current === bimPivot) {
      return 'bim' as const
    }
    if (current === pointcloudWrapper || current === pointcloudGroup) {
      return 'pointcloud' as const
    }
    current = current.parent
  }
  return '' as const
}

function getTopLevelSceneObjectFromIntersection(object: THREE.Object3D | null) {
  let current: THREE.Object3D | null = object

  while (current?.parent && current.parent !== contentGroup) {
    current = current.parent
  }

  return current
}

function handleViewportPointerDown(event: PointerEvent) {
  if (!viewportEl.value || !activeCamera || !raycaster || !contentGroup) return
  if (transformControls && !controls?.enabled) return

  const rect = viewportEl.value.getBoundingClientRect()
  const pointer = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  )

  raycaster.setFromCamera(pointer, activeCamera)
  const hits = raycaster.intersectObjects(contentGroup.children, true)
  if (!hits.length) return

  const pickedHit =
    hits.find((hit) => !(hit.object as any)?.userData?.__viewerPickIgnore) ?? hits[0]
  const topLevelObject = getTopLevelSceneObjectFromIntersection(pickedHit.object)
  const picked = resolveSelectionFromIntersection(topLevelObject ?? pickedHit.object)

  const topIsBim = picked === 'bim'
  const topIsPointcloud = picked === 'pointcloud'
  const wantElementPick =
    enableElementPicking.value && topIsBim && (!editMode.value || event.altKey)

  if (wantElementPick) {
    const mesh = pickedHit.object
    const ifcId = getElementIdFromObject(mesh)
    const metadataMatch = findMetadataElementById(ifcId)
    const metadata = metadataMatch?.meta
    pickedElement.value = {
      label:
        String(metadata?.name || '').trim() ||
        mesh?.name ||
        (mesh as any)?.userData?.name ||
        (mesh as any)?.userData?.label ||
        '构件',
      ifcId: metadataMatch?.id || ifcId,
      stepId:
        typeof metadata?.stepId === 'number' || typeof metadata?.stepId === 'string'
          ? metadata.stepId
          : undefined,
      type: String(metadata?.type || '').trim() || undefined,
      sourceLabel: props.bimDisplayName || 'BIM 模型',
    }
    highlightPickedElement(mesh)
    return
  }

  if (!editMode.value) return
  if (topIsPointcloud) return
  if (picked) {
    clearPickedElement()
    selectedItemId.value = picked
    refreshSelectedTransformUi(true)
    syncBoundsHelpers()
  }
}

function applyPositionFixRealtime() {
  const target = getSelectedObject()
  if (!target) return
  if (selectedItemIsPointcloud.value) {
    resetPositionFix()
    return
  }

  ensureTransformState(target)
  const base = target.userData.__basePosition as THREE.Vector3
  target.position.set(
    base.x + positionOffsetX.value,
    base.y + positionOffsetZ.value,
    base.z + positionOffsetY.value,
  )
  target.updateMatrixWorld(true)
  syncBoundsHelpers()
}

function applyOrientationFixRealtime() {
  const target = getSelectedObject()
  if (!target) return

  ensureTransformState(target)
  const base = target.userData.__baseQuaternion as THREE.Quaternion
  const delta = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(0, 1, 0),
    THREE.MathUtils.degToRad(orientationDegY.value),
  )
  orientationDegX.value = 0
  orientationDegZ.value = 0
  target.quaternion.copy(delta).multiply(base)
  target.updateMatrixWorld(true)
  syncBoundsHelpers()
}

function resetCurrentObjectTransform() {
  const target = getSelectedObject()
  if (!target) return

  ensureTransformState(target)
  target.position.copy(target.userData.__initialPosition as THREE.Vector3)
  target.quaternion.copy(target.userData.__initialQuaternion as THREE.Quaternion)
  refreshSelectedTransformUi(true)
}

function focusSelected() {
  const target = getSelectedObject()
  if (!target) return
  const box = new THREE.Box3().setFromObject(target)
  fitCameraToBox(box)
}

function clearPickedState() {
  editMode.value = false
  selectedItemId.value = ''
  if (transformControls) {
    transformControls.detach()
    transformControls.visible = false
    transformControls.enabled = false
  }
  updateSelectionHighlight()
}

function applySceneVisibility() {
  if (bimPivot) {
    bimPivot.visible = bimVisible.value
  }
  if (pointcloudWrapper) {
    pointcloudWrapper.visible = pointcloudVisible.value
  }

  const selectedTarget = getSelectedObject()
  if (selectedTarget && !selectedTarget.visible) {
    clearPickedState()
  } else {
    updateSelectionHighlight()
  }

  clearPickedElement()
  syncBoundsHelpers()
  scheduleClipRangeUpdate()
}

function toggleBimVisibility() {
  if (!bimPivot) return
  bimVisible.value = !bimVisible.value
  applySceneVisibility()
}

function togglePointcloudVisibility() {
  if (!pointcloudWrapper) return
  pointcloudVisible.value = !pointcloudVisible.value
  applySceneVisibility()
}

function toggleAllVisibility() {
  const shouldShowAll = !bimVisible.value && !pointcloudVisible.value

  if (bimPivot) {
    bimVisible.value = shouldShowAll
  }
  if (pointcloudWrapper) {
    pointcloudVisible.value = shouldShowAll
  }

  applySceneVisibility()
}

function resetView() {
  if (bimPivot) {
    bimVisible.value = true
  }
  if (pointcloudWrapper) {
    pointcloudVisible.value = true
  }
  applySceneVisibility()
  setTopView()
}

function collectCalibrationSnapshot() {
  if (!bimPivot || !pointcloudGroup || !props.bimAssetId || !props.pointcloudAssetId) {
    return null
  }

  bimPivot.updateMatrixWorld(true)
  pointcloudGroup.updateMatrixWorld(true)

  const relative = new THREE.Matrix4()
    .copy(bimPivot.matrixWorld)
    .invert()
    .multiply(pointcloudGroup.matrixWorld)

  const rigid = new THREE.Matrix4()
  const pos = new THREE.Vector3()
  const quat = new THREE.Quaternion()
  relative.decompose(pos, quat, new THREE.Vector3())
  rigid.compose(pos, quat, new THREE.Vector3(1, 1, 1))

  const modelPairs = [
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(1, 0, 0),
    new THREE.Vector3(0, 1, 0),
  ].map((scanPoint) => {
    const bimPoint = scanPoint.clone().applyMatrix4(rigid)
    return {
      modelScanX: scanPoint.x,
      modelScanY: scanPoint.y,
      modelScanZ: scanPoint.z,
      modelBimX: bimPoint.x,
      modelBimY: bimPoint.y,
      modelBimZ: bimPoint.z,
    }
  })

  return {
    modelScanFileId: props.pointcloudAssetId,
    modelBimFileId: props.bimAssetId,
    modelPairs,
  }
}

async function handleSaveAlignment() {
  const payload = collectCalibrationSnapshot()
  if (!payload) {
    ElMessage.warning('请先加载 BIM 与点云后再保存校准结果')
    return false
  }

  try {
    await createBimAlignment(payload)
    ElMessage.success('校准结果已保存')
    lastAlignmentKey = `${payload.modelScanFileId}:${payload.modelBimFileId}`
    return true
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存校准结果失败')
    return false
  }
}

async function handleCalibrationComplete() {
  const saved = await handleSaveAlignment()
  if (!saved) return
  closePage()
}

function buildAlignmentMatrix(alignment: {
  modelMatrix: number[]
  modelRotationQx: number
  modelRotationQy: number
  modelRotationQz: number
  modelRotationQw: number
  modelTranslationX: number
  modelTranslationY: number
  modelTranslationZ: number
}) {
  const raw = new THREE.Matrix4()
  if (Array.isArray(alignment.modelMatrix) && alignment.modelMatrix.length === 16) {
    raw.fromArray(alignment.modelMatrix)
  } else {
    raw.compose(
      new THREE.Vector3(
        alignment.modelTranslationX,
        alignment.modelTranslationY,
        alignment.modelTranslationZ,
      ),
      new THREE.Quaternion(
        alignment.modelRotationQx,
        alignment.modelRotationQy,
        alignment.modelRotationQz,
        alignment.modelRotationQw,
      ),
      new THREE.Vector3(1, 1, 1),
    )
  }

  const pos = new THREE.Vector3()
  const quat = new THREE.Quaternion()
  raw.decompose(pos, quat, new THREE.Vector3())
  return new THREE.Matrix4().compose(pos, quat, new THREE.Vector3(1, 1, 1))
}

function tryRestoreAlignment(alignment: {
  modelMatrix: number[]
  modelRotationQx: number
  modelRotationQy: number
  modelRotationQz: number
  modelRotationQw: number
  modelTranslationX: number
  modelTranslationY: number
  modelTranslationZ: number
}) {
  if (!bimPivot || !pointcloudWrapper || !pointcloudGroup) return false

  bimPivot.updateMatrixWorld(true)
  pointcloudWrapper.updateMatrixWorld(true)
  pointcloudGroup.updateMatrixWorld(true)

  const alignmentMatrix = buildAlignmentMatrix(alignment)
  const desiredGroupWorld = new THREE.Matrix4()
    .copy(bimPivot.matrixWorld)
    .multiply(alignmentMatrix)

  const wrapperInverse = new THREE.Matrix4()
    .copy(pointcloudWrapper.matrixWorld)
    .invert()
  const wrapperToGroup = new THREE.Matrix4().multiplyMatrices(
    wrapperInverse,
    pointcloudGroup.matrixWorld,
  )
  const wrapperWorld = new THREE.Matrix4()
    .copy(desiredGroupWorld)
    .multiply(wrapperToGroup.invert())

  pointcloudWrapper.parent?.updateMatrixWorld(true)
  const parentInverse = new THREE.Matrix4()
    .copy(pointcloudWrapper.parent?.matrixWorld ?? new THREE.Matrix4())
    .invert()
  const wrapperLocal = new THREE.Matrix4().multiplyMatrices(parentInverse, wrapperWorld)

  const pos = new THREE.Vector3()
  const quat = new THREE.Quaternion()
  const scale = new THREE.Vector3()
  wrapperLocal.decompose(pos, quat, scale)
  pointcloudWrapper.position.copy(pos)
  pointcloudWrapper.quaternion.copy(quat)
  pointcloudWrapper.scale.set(1, 1, 1)
  pointcloudWrapper.updateMatrixWorld(true)
  ensureTransformState(pointcloudWrapper)
  refreshSelectedTransformUi(selectedItemId.value === 'pointcloud')
  fitCameraToContent()
  return true
}

async function fetchAndRestoreAlignment() {
  if (!props.pointcloudAssetId || !props.bimAssetId || !bimPivot || !pointcloudWrapper) {
    return
  }

  const key = `${props.pointcloudAssetId}:${props.bimAssetId}`
  if (lastAlignmentKey === key) return

  try {
    const response = await getBimAlignment({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
    })
    if (response?.data && tryRestoreAlignment(response.data)) {
      lastAlignmentKey = key
      statusText.value = '已恢复后端校准结果'
    }
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 400 || status === 404) {
      return
    }
    console.error(error)
  }
}

async function handleFetchAlignmentMatrix() {
  if (!props.pointcloudAssetId || !props.bimAssetId) {
    ElMessage.warning('缺少 BIM 或点云资产 ID')
    return
  }

  loadingAlignmentMatrix.value = true
  try {
    const response = await getBimAlignment({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
    })
    const values = Array.isArray(response?.data?.modelMatrix) ? response.data.modelMatrix : null
    if (!values || values.length !== 16) {
      ElMessage.warning('后端暂未返回有效的 modelMatrix')
      return
    }

    alignmentMatrixDialogText.value = JSON.stringify(values, null, 2)
    showAlignmentMatrixDialog.value = true
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 400 || status === 404) {
      ElMessage.warning('当前组合暂无已保存的校准矩阵')
      return
    }

    console.error(error)
    ElMessage.error(error instanceof Error ? error.message : '读取校准矩阵失败')
  } finally {
    loadingAlignmentMatrix.value = false
  }
}

async function handleLoadBimFromApi(silent = false) {
  if (!props.bimAssetId) {
    if (!silent) {
      ElMessage.warning('缺少 BIM 资产 ID')
    }
    return
  }

  await initScene()
  if (!scene || !contentGroup) return
  const nextContentGroup = contentGroup

  const token = ++bimLoadToken
  loadingBim.value = true
  statusText.value = '加载 BIM 模型中...'
  bimMetadata.value = null

  try {
    const assetDetailResult = await getAssetDetail(props.bimAssetId)
    const assetDetail = assetDetailResult.data
    if (assetDetail.type !== 'bim' || assetDetail.status !== 'ready' || !assetDetail.glbUrl) {
      statusText.value = 'BIM 模型尚未就绪'
      if (!silent) {
        ElMessage.warning('BIM 模型尚未就绪，暂时无法加载')
      }
      return
    }

    const blob = await getBimGlbFile(assetDetail.glbUrl)
    if (token !== bimLoadToken) return

    if (assetDetail.metadataUrl) {
      try {
        const metadata = await getBimMetadata(assetDetail.metadataUrl)
        if (token === bimLoadToken) {
          bimMetadata.value = metadata
        }
      } catch (error) {
        console.error('[BimPointcloudAlign] 加载 BIM metadata 失败:', error)
      }
    }

    const objectUrl = URL.createObjectURL(blob)
    const loader = new GLTFLoader()
    const dracoLoader = new DRACOLoader()
    dracoLoader.setDecoderPath('https://www.gstatic.com/draco/v1/decoders/')
    loader.setDRACOLoader(dracoLoader)

    await new Promise<void>((resolve, reject) => {
      loader.load(
        objectUrl,
        (gltf: GLTF) => {
          URL.revokeObjectURL(objectUrl)
          dracoLoader.dispose()

          if (bimPivot) {
            nextContentGroup.remove(bimPivot)
            disposeObject3D(bimPivot)
          }

          const root = gltf.scene
          const box = new THREE.Box3().setFromObject(root)
          const center = box.getCenter(new THREE.Vector3())
          root.position.sub(center)

          const pivot = new THREE.Group()
          pivot.userData.__normalizationCenter = center.clone()
          pivot.add(root)
          nextContentGroup.add(pivot)

          bimRoot = root
          bimPivot = pivot
          bimLoaded.value = true
          bimVisible.value = true

          ensureTransformState(bimPivot)
          applySceneVisibility()
          applyBimMaterialMode(bimPivot)
          updateClipRangeFromContent({ preserveT: true })
          applyClippingState()
          syncBoundsHelpers()
          fitCameraToContent()
          statusText.value = `已加载 BIM：${props.bimDisplayName || assetDetail.sourceName}`
          resolve()
        },
        undefined,
        (error) => {
          URL.revokeObjectURL(objectUrl)
          dracoLoader.dispose()
          reject(error)
        },
      )
    })

    await fetchAndRestoreAlignment()
  } catch (error) {
    console.error(error)
    if (!silent) {
      ElMessage.error(error instanceof Error ? error.message : '加载 BIM 失败')
    }
    statusText.value = '加载 BIM 失败'
  } finally {
    loadingBim.value = false
  }
}

async function handleLoadPointCloudFromApi(silent = false) {
  if (!props.pointcloudAssetId) {
    if (!silent) {
      ElMessage.warning('缺少点云资产 ID')
    }
    return
  }

  await initScene()
  if (!scene || !contentGroup || !activeCamera || !renderer) return
  const nextContentGroup = contentGroup

  const token = ++pointcloudLoadToken
  loadingPointcloud.value = true
  statusText.value = '加载点云中...'

  try {
    if (pointcloudWrapper) {
      nextContentGroup.remove(pointcloudWrapper)
      pointcloudWrapper = null
      pointcloudGroup = null
      pointcloudLoaded.value = false
    }
    if (tileset) {
      tileset.dispose?.()
      tileset = null
    }
    pointcloudMaxDim = 1

    const assetDetailResult = await getAssetDetail(props.pointcloudAssetId)
    const assetDetail = assetDetailResult.data
    if (
      assetDetail.type !== 'pointcloud' ||
      assetDetail.status !== 'ready' ||
      !assetDetail.tilesetUrl
    ) {
      throw new Error('点云资源尚未就绪，暂时无法加载')
    }

    const url = getPointcloudTilesetUrl(assetDetail.tilesetUrl)
    const resourceBasePath = getTileResourceBasePath(assetDetail)
    const resourceBaseUrl = normalizeBackendUrl(resourceBasePath)
    const nextTileset = new TilesRenderer(url)
    nextTileset.errorTarget = tilesErrorTarget.value
    nextTileset.fetchOptions = {
      headers: createUploadHeaders({ Accept: '*/*' }),
    }
    nextTileset.registerPlugin({
      fetchData: async (uri: any, options: any) => {
        const raw = typeof uri === 'string' ? uri : uri?.toString?.() || ''
        if (!raw) return null

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
    dracoLoader.setDecoderPath('https://www.gstatic.com/draco/v1/decoders/')
    dracoLoader.preload()
    nextTileset.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader }))
    nextTileset.setCamera(activeCamera)
    updateTilesetResolution()

    const wrapper = new THREE.Group()
    wrapper.rotation.x = -Math.PI / 2
    wrapper.add(nextTileset.group)
    nextContentGroup.add(wrapper)

    pointcloudWrapper = wrapper
    pointcloudGroup = nextTileset.group
    tileset = nextTileset
    pointcloudLoaded.value = true
    pointcloudVisible.value = true
    ensureTransformState(pointcloudWrapper)
    applySceneVisibility()
    updateClipRangeFromContent({ preserveT: true })
    applyClippingState()

    nextTileset.addEventListener('tiles-load-start', () => {
      if (token !== pointcloudLoadToken) return
      statusText.value = '点云加载中...'
    })
    nextTileset.addEventListener('tiles-load-end', () => {
      if (token !== pointcloudLoadToken) return
      statusText.value = `已加载点云：${props.pointcloudDisplayName || assetDetail.sourceName}`
    })
    nextTileset.addEventListener('load-root-tileset', () => {
      if (token !== pointcloudLoadToken) return
      if (!controls || !activeCamera) return

      if (rendererMode === 'webgpu') {
        sanitizeObjectForWebGPU(nextTileset.group)
      }
      applyPointcloudMaterialMode(nextTileset.group)
      const sphere = new THREE.Sphere()
      if (nextTileset.getBoundingSphere?.(sphere)) {
        nextTileset.group.position.copy(sphere.center).multiplyScalar(-1)
        if (isPerspectiveCamera(activeCamera)) {
          fitCameraToRadius(activeCamera, controls, sphere.radius)
          setTopViewForPerspective(activeCamera, controls, sphere.radius * 2.2)
          applyTilesErrorTarget()
        } else {
          fitCameraToContent()
        }
      }
      ensureTransformState(pointcloudWrapper)
      updateClipRangeFromContent({ preserveT: true })
      applyClippingState()
      syncBoundsHelpers()
      statusText.value = `已加载点云：${props.pointcloudDisplayName || assetDetail.sourceName}`
      void fetchAndRestoreAlignment()
    })
    nextTileset.addEventListener('load-model', ({ scene: tileScene }: any) => {
      if (token !== pointcloudLoadToken || !tileScene) return
      if (rendererMode === 'webgpu') {
        sanitizeObjectForWebGPU(tileScene)
      }
      applyPointcloudMaterialMode(tileScene)
    })
    nextTileset.addEventListener('load-error', (event: any) => {
      console.error(event)
    })

    statusText.value = `已加载点云：${props.pointcloudDisplayName || assetDetail.sourceName}`
  } catch (error) {
    console.error(error)
    if (!silent) {
      ElMessage.error(error instanceof Error ? error.message : '加载点云失败')
    }
    statusText.value = '加载点云失败'
  } finally {
    loadingPointcloud.value = false
  }
}

async function preloadFromRoute() {
  if (props.bimAssetId) {
    await handleLoadBimFromApi(true)
  }
  if (props.pointcloudAssetId) {
    await handleLoadPointCloudFromApi(true)
  }
}

watch(backgroundColor, () => {
  onBackgroundColorChange()
})

watch(showGrid, () => {
  syncGridVisibility()
})

watch(materialMode, () => {
  clearPickedElement()
  applyBimMaterialMode(bimPivot)
  if (tileset?.group) {
    applyPointcloudMaterialMode(tileset.group)
  }
})

watch(tilesErrorTarget, () => {
  onTilesErrorTargetInput()
})

watch(showBounds, () => {
  onShowBoundsChange()
})

watch(enableClipping, () => {
  onClippingEnabledChange()
})

watch([clipAxis, clipInvert], () => {
  onClippingParamsChange()
})

watch(clipPosition, () => {
  onClippingParamsChange()
})

watch(selectedItemId, () => {
  onSelectedItemChange()
})

watch(editMode, () => {
  onEditModeChange()
})

watch(enableElementPicking, () => {
  onElementPickingChange()
})

watch(transformMode, () => {
  setTransformMode(transformMode.value)
})

onMounted(async () => {
  syncPositionStepPreset()
  syncRotationStepPreset()
  await initScene()
  await preloadFromRoute()
})

onBeforeUnmount(() => {
  stopRenderLoop()
  resizeObserver?.disconnect()
  controls?.dispose()
  if (scene && transformControls) {
    scene.remove(transformControls.getHelper() as unknown as THREE.Object3D)
  }
  transformControls?.dispose()
  tileset?.dispose?.()
  renderer?.domElement?.removeEventListener?.('pointerdown', handleViewportPointerDown)
  renderer?.dispose()
  raycaster = null
  if (renderer?.domElement?.parentElement) {
    renderer.domElement.parentElement.removeChild(renderer.domElement)
  }
  if (bimPivot) {
    disposeObject3D(bimPivot)
  }
})
</script>

<template>
  <section class="BimPointcloudAlign-container">
    <header class="topbar">
      <div class="topbar-left">
        <h1 class="brand-title">
          BIM 与点云校准 - {{ bimDisplayName || 'BIM 模型' }}
        </h1>
        <div class="topbar-center">
          <el-tag round effect="light">步骤 1/2</el-tag>
        </div>
      </div>

      <div class="topbar-right">
        <el-button :icon="ArrowLeft" @click="closePage">返回</el-button>
        <el-button :loading="loadingAlignmentMatrix" @click="handleFetchAlignmentMatrix">
          校准矩阵
        </el-button>
        <el-button type="success" @click="handleSaveAlignment">保存</el-button>
        <el-button type="primary" :disabled="!hasModel || !hasTileset" @click="handleCalibrationComplete">
          校准完成
        </el-button>
        <el-button @click="showPanel = !showPanel">
          {{ showPanel ? '收起' : '展开' }}
        </el-button>
      </div>
    </header>

    <div class="main-content">
      <aside class="left-toolbar">
        <el-tooltip content="重置视角" placement="right">
          <div class="tool-item">
            <el-button class="tool-btn" circle text :icon="RefreshLeft" :disabled="!hasModel" @click="resetView" />
            <span class="tool-label">重置</span>
          </div>
        </el-tooltip>

        <el-tooltip content="透视" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--img"
              :class="{ 'is-on': projectionMode === 'perspective' }"
              circle
              text
              @click="setProjectionMode('perspective')"
            >
              <img class="tool-btn__img1" :src="toushiIcon" alt="透视" />
            </el-button>
            <span class="tool-label">透视</span>
          </div>
        </el-tooltip>

        <el-tooltip content="正交" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--img"
              :class="{ 'is-on': projectionMode === 'orthographic' }"
              circle
              text
              @click="setProjectionMode('orthographic')"
            >
              <img class="tool-btn__img1" :src="zhengjiaoIcon" alt="正交" />
            </el-button>
            <span class="tool-label">正交</span>
          </div>
        </el-tooltip>

        <el-tooltip content="网格" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--img"
              :class="{ 'is-on': showGrid }"
              circle
              text
              @click="showGrid = !showGrid"
            >
              <img class="tool-btn__img" :src="wanggeIcon" alt="网格" />
            </el-button>
            <span class="tool-label">网格</span>
          </div>
        </el-tooltip>

        <el-tooltip content="包围盒" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn"
              :class="{ 'is-on': showBounds }"
              circle
              text
              :icon="Box"
              :disabled="!hasModel"
              @click="showBounds = !showBounds"
            />
            <span class="tool-label">包围盒</span>
          </div>
        </el-tooltip>

        <el-divider />

        <el-tooltip content="前视图" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--text"
              :class="{ 'is-on': hasModel && activeView === 'front' }"
              circle
              text
              :disabled="!hasModel"
              @click="setFrontView"
            >
              前
            </el-button>
          </div>
        </el-tooltip>

        <el-tooltip content="俯视图" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--text"
              :class="{ 'is-on': hasModel && activeView === 'top' }"
              circle
              text
              :disabled="!hasModel"
              @click="setTopView"
            >
              俯
            </el-button>
          </div>
        </el-tooltip>

        <el-tooltip content="侧视图" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--text"
              :class="{ 'is-on': hasModel && activeView === 'side' }"
              circle
              text
              :disabled="!hasModel"
              @click="setSideView"
            >
              侧
            </el-button>
          </div>
        </el-tooltip>

        <el-divider />

        <el-tooltip :content="bimVisibilityLabel" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn"
              :class="{ 'is-on': hasModel && bimVisible }"
              circle
              text
              :icon="Box"
              :disabled="!hasModel"
              @click="toggleBimVisibility"
            />
            <span class="tool-label">{{ bimVisibilityLabel }}</span>
          </div>
        </el-tooltip>

        <el-tooltip :content="pointcloudVisibilityLabel" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn"
              :class="{ 'is-on': hasTileset && pointcloudVisible }"
              circle
              text
              :icon="Location"
              :disabled="!hasTileset"
              @click="togglePointcloudVisibility"
            />
            <span class="tool-label">{{ pointcloudVisibilityLabel }}</span>
          </div>
        </el-tooltip>

        <el-tooltip :content="visibilityToggleAllLabel" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn"
              :class="{ 'is-on': (hasModel && bimVisible) || (hasTileset && pointcloudVisible) }"
              circle
              text
              :icon="Delete"
              :disabled="!hasModel && !hasTileset"
              @click="toggleAllVisibility"
            />
            <span class="tool-label">{{ visibilityToggleAllLabel }}</span>
          </div>
        </el-tooltip>
      </aside>

      <div ref="viewportEl" class="viewport"></div>

      <aside v-if="showPanel" class="right-panel">
        <div class="panel-section">
          <div class="section-title">编辑</div>
          <div class="control-row">
            <el-switch v-model="editMode" @change="onEditModeChange" />
            <span class="label">编辑模式</span>
          </div>
          <div class="control-row" :class="{ disabled: !editMode || !loadedItemOptions.length }">
            <span class="label">对象</span>
            <el-select
              v-model="selectedItemId"
              popper-class="bpa-right-popper"
              filterable
              :disabled="!editMode || !loadedItemOptions.length"
              @change="onSelectedItemChange"
            >
              <el-option
                v-for="option in loadedItemOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-button size="small" :disabled="!editMode || !selectedItemId" @click="focusSelected">
              定位
            </el-button>
          </div>
          <div class="control-row" :class="{ disabled: !editMode }">
            <span class="label">变换</span>
            <el-radio-group
              v-model="transformMode"
              :disabled="!editMode"
              @change="setTransformMode"
            >
              <el-radio-button label="translate" :disabled="!editMode || selectedItemIsPointcloud">移动</el-radio-button>
              <el-radio-button label="rotate">旋转</el-radio-button>
            </el-radio-group>
          </div>

          <div
            class="control-row control-row--orientation"
            :class="{ disabled: !editMode || !selectedItemId }"
          >
            <span class="label">
              {{ transformMode === 'rotate' ? '方向修正 (deg)' : '位置修正' }}
            </span>
            <div v-if="transformMode === 'rotate'" class="orientation-sliders">
              <div
                class="control-row control-row--compact"
                :class="{ disabled: !editMode || !selectedItemId }"
              >
                <div class="step-control-group">
                  <el-select
                    v-model="rotationStepPreset"
                    class="step-select"
                    popper-class="bpa-right-popper"
                    filterable
                    allow-create
                    default-first-option
                    :disabled="!editMode || !selectedItemId"
                    @change="onRotationStepPresetChange"
                  >
                    <el-option
                      v-for="stepOption in rotationStepOptions"
                      :key="`rotate-${stepOption}`"
                      :label="formatRotationStepLabel(stepOption)"
                      :value="String(stepOption)"
                    />
                  </el-select>
                </div>
              </div>
              <template v-if="showOnlyVerticalAxis">
                <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                  <span class="axis">y</span>
                  <input
                    v-model.number="orientationDegY"
                    type="range"
                    min="-180"
                    max="180"
                    :step="rotationAdjustStep"
                    :disabled="!editMode || !selectedItemId"
                    @input="applyOrientationFixRealtime"
                  />
                  <span class="val wide">{{ formatRotationOffset(orientationDegY) }}</span>
                </label>
              </template>
              <template v-else>
                <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                  <span class="axis">X</span>
                  <input
                    v-model.number="orientationDegX"
                    type="range"
                    min="-10"
                    max="10"
                    :step="rotationAdjustStep"
                    :disabled="!editMode || !selectedItemId"
                    @input="applyOrientationFixRealtime"
                  />
                  <span class="val wide">{{ formatRotationOffset(orientationDegX) }}</span>
                </label>
                <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                  <span class="axis">Y</span>
                  <input
                    v-model.number="orientationDegY"
                    type="range"
                    min="-10"
                    max="10"
                    :step="rotationAdjustStep"
                    :disabled="!editMode || !selectedItemId"
                    @input="applyOrientationFixRealtime"
                  />
                  <span class="val wide">{{ formatRotationOffset(orientationDegY) }}</span>
                </label>
                <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                  <span class="axis">Z</span>
                  <input
                    v-model.number="orientationDegZ"
                    type="range"
                    min="-10"
                    max="10"
                    :step="rotationAdjustStep"
                    :disabled="!editMode || !selectedItemId"
                    @input="applyOrientationFixRealtime"
                  />
                  <span class="val wide">{{ formatRotationOffset(orientationDegZ) }}</span>
                </label>
              </template>
            </div>
            <div v-else class="orientation-sliders">
              <div
                class="control-row control-row--compact"
                :class="{ disabled: !editMode || !selectedItemId || selectedItemIsPointcloud }"
              >
                <div class="step-control-group">
                  <el-select
                    v-model="positionStepPreset"
                    class="step-select"
                    popper-class="bpa-right-popper"
                    filterable
                    allow-create
                    default-first-option
                    :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud"
                    @change="onPositionStepPresetChange"
                  >
                    <el-option
                      v-for="stepOption in positionStepOptions"
                      :key="`position-${stepOption}`"
                      :label="formatPositionStepLabel(stepOption)"
                      :value="String(stepOption)"
                    />
                  </el-select>
                </div>
              </div>
              <label class="slider" :class="{ disabled: !editMode || !selectedItemId || selectedItemIsPointcloud }">
                <span class="axis">X</span>
                <input
                  v-model.number="positionOffsetX"
                  type="range"
                  :min="positionSliderRange.min"
                  :max="positionSliderRange.max"
                  :step="positionAdjustStep"
                  :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud"
                  @input="applyPositionFixRealtime"
                />
                <span class="val wide">{{ formatPositionOffset(positionOffsetX) }}</span>
              </label>
              <label class="slider" :class="{ disabled: !editMode || !selectedItemId || selectedItemIsPointcloud }">
                <span class="axis">Y</span>
                <input
                  v-model.number="positionOffsetY"
                  type="range"
                  :min="positionSliderRange.min"
                  :max="positionSliderRange.max"
                  :step="positionAdjustStep"
                  :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud"
                  @input="applyPositionFixRealtime"
                />
                <span class="val wide">{{ formatPositionOffset(positionOffsetY) }}</span>
              </label>
              <label class="slider" :class="{ disabled: !editMode || !selectedItemId || selectedItemIsPointcloud }">
                <span class="axis">Z</span>
                <input
                  v-model.number="positionOffsetZ"
                  type="range"
                  :min="positionSliderRange.min"
                  :max="positionSliderRange.max"
                  :step="positionAdjustStep"
                  :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud"
                  @input="applyPositionFixRealtime"
                />
                <span class="val wide">{{ formatPositionOffset(positionOffsetZ) }}</span>
              </label>
            </div>
            <div class="orientation-actions">
              <el-button
                size="small"
                :disabled="!editMode || !selectedItemId || (transformMode === 'translate' && selectedItemIsPointcloud)"
                @click="resetTransformFixRealtime"
              >
                重置当前对象到初始
              </el-button>
            </div>
          </div>
        </div>

        <div class="panel-section">
          <div class="section-title">渲染</div>
          <div class="control-row">
            <span class="label">SSE</span>
            <el-slider
              v-model="tilesErrorTarget"
              :min="2"
              :max="64"
              :disabled="!hasTileset"
              @input="onTilesErrorTargetInput"
            />
            <span class="value">{{ tilesErrorTarget }}</span>
          </div>
          <div class="control-row">
            <span class="label">材质</span>
            <el-select v-model="materialMode" popper-class="bpa-right-popper" :disabled="!hasModel">
              <el-option label="原始材质" value="original" />
              <el-option label="无光照" value="unlit" />
              <el-option label="漫反射" value="lambert" />
            </el-select>
          </div>
          <div class="control-row">
            <span class="label">背景</span>
            <div class="color-row">
              <input v-model="backgroundColor" class="color-picker" type="color" @input="onBackgroundColorChange" />
              <input v-model="backgroundColor" class="color-hex" type="text" @change="onBackgroundColorChange" />
              <el-button size="small" @click="resetBackgroundColor">重置</el-button>
            </div>
          </div>
        </div>

        <div class="panel-section">
          <div class="section-title">剖切</div>
          <div class="control-row">
            <span class="label">包围盒</span>
            <el-switch v-model="showBounds" :disabled="!hasModel && !hasTileset" />
          </div>
          <div class="control-row">
            <span class="label">剖切</span>
            <el-switch v-model="enableClipping" :disabled="!hasModel || !showBounds" />
          </div>
          <div class="control-row" :class="{ disabled: !enableClipping || !showBounds || !hasModel }">
            <span class="label">剖切轴</span>
            <el-select v-model="clipAxis" :disabled="!enableClipping || !showBounds || !hasModel">
              <el-option label="X" value="x" />
              <el-option label="Y" value="y" />
              <el-option label="Z" value="z" />
            </el-select>
            <el-switch
              v-model="clipInvert"
              inline-prompt
              active-text="反"
              inactive-text="正"
              :disabled="!enableClipping || !showBounds || !hasModel"
            />
          </div>
          <div class="control-row" :class="{ disabled: !enableClipping || !showBounds || !hasModel }">
            <span class="label">位置</span>
            <el-slider
              v-model="clipPosition"
              :min="clipRange.min"
              :max="clipRange.max"
              :step="clipStep"
              :disabled="!enableClipping || !showBounds || !hasModel"
            />
            <span class="value">{{ clipPosition.toFixed(2) }}</span>
          </div>
        </div>

        <div class="panel-section">
          <div class="section-title">构件</div>
          <div class="control-row">
            <el-switch v-model="enableElementPicking" />
            <span class="label">点击高亮</span>
          </div>
          <div v-if="pickedElement" class="picked-element-card" :class="{ disabled: !enableElementPicking || !hasModel }">
            <div class="picked-element-card__head">
              <span class="picked-element-card__label">已选构件</span>
              <el-button
                size="small"
                :disabled="!enableElementPicking || !pickedElement"
                @click="clearPickedElement"
              >
                清除
              </el-button>
            </div>
            <div class="picked-element-card__title">
              {{ pickedElementTitle }}
            </div>
          </div>
          <div
            v-else
            class="picked-element-empty"
            :class="{ disabled: !enableElementPicking || !hasModel }"
          >
            未选择构件
          </div>
        </div>

      </aside>
    </div>

    <footer class="status-bar">
      <el-tag v-if="!webgpuSupported" type="warning" size="small">WebGPU 不支持</el-tag>
      <span class="status-text">{{ statusText }}</span>
    </footer>

    <el-dialog
      v-model="showAlignmentMatrixDialog"
      title="校准矩阵"
      width="720px"
      append-to-body
    >
      <div class="matrix-dialog">
        <div class="matrix-dialog__meta">
          <span>bimAssetId: {{ bimAssetId }}</span>
          <span>pointcloudAssetId: {{ pointcloudAssetId }}</span>
        </div>
        <pre class="matrix-dialog__content">{{ alignmentMatrixDialogText }}</pre>
      </div>
    </el-dialog>
  </section>
</template>

<style lang="scss" scoped>
.BimPointcloudAlign-container {
  position: relative;
  width: 100%;
  height: 100vh;
  background: radial-gradient(1000px 700px at 20% 10%, #1b2a5a 0%, #0b1020 55%, #070914 100%);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px;
  background: rgba(10, 14, 30, 0.95);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
  z-index: 20;
}

.topbar-left,
.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.topbar-center {
  display: flex;
  align-items: center;
}

.brand-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.92);
  letter-spacing: 0.4px;
}

.main-content {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.left-toolbar {
  flex-shrink: 0;
  width: 72px;
  padding: 10px 8px;
  background: rgba(20, 30, 60, 0.95);
  border-right: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.tool-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.tool-btn {
  width: 40px;
  height: 40px;
  padding: 0;
  font-size: 18px;
  color: rgba(255, 255, 255, 0.92);
  border: 1px solid transparent;
  background: transparent;
}

.tool-btn:hover,
.tool-btn:focus-visible {
  color: #0f172a;
  background: rgba(255, 255, 255, 0.92);
  border-color: rgba(255, 255, 255, 0.92);
}

.tool-btn.is-on {
  color: #ffffff;
  background: rgba(64, 158, 255, 0.9);
  border-color: rgba(64, 158, 255, 0.9);
}

.tool-btn--text {
  font-size: 14px;
  font-weight: 700;
}

.tool-btn--img {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.tool-btn__img {
  width: 18px;
  height: 18px;
  object-fit: contain;
  filter: brightness(0) invert(1);
}

.tool-btn__img1 {
  width: 15px;
  height: 15px;
  object-fit: contain;
  filter: brightness(0) invert(1);
}

.tool-btn:hover .tool-btn__img,
.tool-btn:focus-visible .tool-btn__img,
.tool-btn:hover .tool-btn__img1,
.tool-btn:focus-visible .tool-btn__img1 {
  filter: none;
}

.tool-label {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.72);
  line-height: 1;
  text-align: center;
}

.left-toolbar :deep(.el-divider) {
  margin: 8px 0;
  border-color: rgba(255, 255, 255, 0.1);
}

.viewport {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
  background: #000;
}

.right-panel {
  flex-shrink: 0;
  width: 320px;
  padding: 16px;
  overflow-y: auto;
  background: linear-gradient(180deg, rgba(20, 30, 60, 0.96) 0%, rgba(14, 20, 44, 0.96) 100%);
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
}

.panel-section {
  margin-bottom: 14px;
  padding: 14px 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.18);
}

.panel-section:last-child {
  margin-bottom: 0;
}

.section-title {
  margin-bottom: 10px;
  font-size: 12px;
  font-weight: 700;
  color: rgba(125, 194, 255, 0.95);
  letter-spacing: 0.6px;
}

.control-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
  color: rgba(255, 255, 255, 0.9);
}

.control-row:last-child {
  margin-bottom: 0;
}

.control-row--compact {
  margin-bottom: 0;
}

.control-row.disabled {
  opacity: 0.48;
}

.label {
  width: 52px;
  flex: 0 0 auto;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.68);
}

.value {
  width: 52px;
  text-align: right;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.82);
}

.hint2 {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.56);
}

.matrix-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.matrix-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.matrix-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.matrix-cell {
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.92);
  font-size: 12px;
  text-align: center;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.matrix-meta {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.58);
}

.color-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.color-picker {
  width: 40px;
  height: 32px;
  padding: 0;
  border: 0;
  background: transparent;
}

.color-hex {
  width: 140px;
  height: 32px;
  padding: 0 10px;
  color: rgba(255, 255, 255, 0.92);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  outline: none;
}

.control-row--orientation {
  align-items: stretch;
  flex-direction: column;
}

.control-row--orientation .label {
  width: auto;
}

.orientation-sliders {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.step-control-group {
    display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.step-label {
  flex: 0 0 auto;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.68);
}

.step-select {
  width: 128px !important;
  :deep(.el-select__wrapper) {
  min-height: 25px !important;}
}

.picked-element-card,
.picked-element-empty {
  margin-top: 8px;
  padding: 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.picked-element-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.picked-element-card__label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
}

.picked-element-card__title {
  margin-top: 10px;
  color: rgba(255, 255, 255, 0.96);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  word-break: break-word;
}

.picked-element-card__meta {
  margin-top: 10px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.picked-element-pill {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.74);
  font-size: 12px;
}

.picked-element-empty {
  color: rgba(255, 255, 255, 0.56);
  font-size: 13px;
}

.slider {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.86);
  font-size: 12px;
  font-weight: 600;
  user-select: none;
  cursor: pointer;
}

.slider input[type='range'] {
  width: 60px;
  accent-color: #409eff;
}

.slider .val {
  min-width: 36px;
  text-align: right;
  color: rgba(255, 255, 255, 0.92);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.slider .val.wide {
  min-width: 48px;
}

.slider .axis {
  width: 16px;
  color: rgba(255, 255, 255, 0.55);
  text-align: center;
  font-size: 11px;
}

.slider.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.control-row--orientation .slider {
  width: 100%;
  display: grid;
  grid-template-columns: 20px 1fr 56px;
  gap: 10px;
  padding: 8px 10px;
}

.control-row--orientation .slider input[type='range'] {
  width: 100%;
  min-width: 0;
}

.orientation-actions {
  align-self: flex-end;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.status-bar {
  flex-shrink: 0;
  min-height: 36px;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(5, 8, 18, 0.94);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.status-text {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.74);
}

.matrix-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.matrix-dialog__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
  color: #7fb3ff;
  font-size: 13px;
}

.matrix-dialog__content {
  margin: 0;
  max-height: 420px;
  overflow: auto;
  padding: 16px;
  border-radius: 8px;
  background: #0b1020;
  color: #dbe8ff;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.right-panel :deep(.el-input),
.right-panel :deep(.el-select),
.right-panel :deep(.el-slider),
.right-panel :deep(.el-input-number) {
  flex: 1;
}

.right-panel :deep(.el-input__wrapper),
.right-panel :deep(.el-select__wrapper),
.right-panel :deep(.el-input-number__wrapper) {
  background: rgba(255, 255, 255, 0.08);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
}

.right-panel :deep(.el-input__inner),
.right-panel :deep(.el-select__placeholder),
.right-panel :deep(.el-select__selected-item) {
  color: rgba(255, 255, 255, 0.92);
}

.right-panel :deep(.el-button + .el-button) {
  margin-left: 0;
}

.right-panel :deep(.el-switch__label),
.right-panel :deep(.el-radio-button__inner) {
  color: rgba(255, 255, 255, 0.86);
}

.right-panel :deep(.el-radio-button__inner) {
  background: #ffffff;
  border-color: rgba(255, 255, 255, 0.92);
  color: #0f172a;
  box-shadow: none;
}

.right-panel :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: #409eff;
  border-color: #409eff;
  color: #ffffff;
  box-shadow: -1px 0 0 0 #409eff;
}

.right-panel :deep(.el-radio-button.is-disabled .el-radio-button__inner) {
  background: rgba(255, 255, 255, 0.72);
  border-color: rgba(255, 255, 255, 0.72);
  color: rgba(15, 23, 42, 0.46);
}

@media (max-width: 1280px) {
  .right-panel {
    width: 300px;
  }
}

@media (max-width: 1080px) {
  .topbar {
    flex-direction: column;
    align-items: flex-start;
  }

  .topbar-right {
    flex-wrap: wrap;
  }

  .right-panel {
    width: 280px;
  }
}
</style>
<style>
.el-popper.bpa-right-popper {
  background: rgba(6, 12, 28, 0.55) !important;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid rgba(64, 158, 255, 0.35);
  box-shadow:
    0 0 0 1px rgba(64, 158, 255, 0.15),
    0 12px 32px rgba(0, 0, 0, 0.6),
    0 0 24px rgba(64, 158, 255, 0.25);
}

.el-popper.bpa-right-popper .el-select-dropdown {
  background: transparent;
}

.el-popper.bpa-right-popper .el-select-dropdown__item {
  color: rgba(120, 190, 255, 0.85);
}

.el-popper.bpa-right-popper .el-select-dropdown__item:hover {
  background: linear-gradient(
    90deg,
    rgba(64, 158, 255, 0.05),
    rgba(64, 158, 255, 0.18),
    rgba(64, 158, 255, 0.05)
  );
}

.el-popper.bpa-right-popper .el-select-dropdown__item.selected {
  background: rgba(64, 158, 255, 0.28);
  color: #fff;
}
</style>
