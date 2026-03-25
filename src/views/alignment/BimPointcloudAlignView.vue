<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
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
import {
  createBimAlignment,
  getBimAlignment,
  getScanCalibration,
  type BimAlignmentResult,
} from '@/api/backend-alignment'
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

const router = useRouter()
const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('准备就绪')
const showPanel = ref(true)
const loadingBim = ref(false)
const loadingPointcloud = ref(false)
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
const hasClippableContent = computed(() => hasModel.value || hasTileset.value)
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
const bimUnlitMaterialCache = new WeakMap<THREE.Material, { v0?: THREE.Material; v1?: THREE.Material }>()
const bimLambertMaterialCache = new WeakMap<THREE.Material, { v0?: THREE.Material; v1?: THREE.Material }>()
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
let transformHelper: THREE.Object3D | null = null
let selectionHelper: THREE.BoxHelper | null = null
let pickedElementHelper: THREE.BoxHelper | null = null
let bimPivot: THREE.Group | null = null
let bimRoot: THREE.Object3D | null = null
let pointcloudWrapper: THREE.Group | null = null
let pointcloudGroup: THREE.Group | null = null
let tileset: TilesRenderer | null = null
let boundsBoxHelper: THREE.Box3Helper | null = null
let clippingPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0)
let orthoViewSize = 10
let bimLoadToken = 0
let pointcloudLoadToken = 0
let pointcloudRootReady = false
let loggedSavedAlignmentKey = ''
let restoredSavedAlignmentKey = ''
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
  console.info('[BimPointcloudAlign] closePage start', {
    hasOpener: !!window.opener,
    historyLength: window.history.length,
    href: window.location.href,
  })

  if (window.opener) {
    window.close()
    window.setTimeout(() => {
      console.warn('[BimPointcloudAlign] window.close attempted', {
        hasOpener: !!window.opener,
        closed: window.closed,
        href: window.location.href,
      })
    }, 150)
    return
  }

  console.info('[BimPointcloudAlign] closePage fallback redirect', {
    target: '/upload',
  })
  void router.push('/upload')
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

function vectorToPlainObject(vector: THREE.Vector3) {
  return {
    x: vector.x,
    y: vector.y,
    z: vector.z,
  }
}

function quaternionToPlainObject(quaternion: THREE.Quaternion) {
  return {
    x: quaternion.x,
    y: quaternion.y,
    z: quaternion.z,
    w: quaternion.w,
  }
}

function findFirstRenderableDescendant(root: THREE.Object3D | null): THREE.Object3D | null {
  if (!root) return null

  let found: THREE.Object3D | null = null
  root.traverse((obj) => {
    if (found) return
    if ((obj as any)?.isMesh || (obj as any)?.isPoints || (obj as any)?.isBatchedMesh) {
      found = obj
    }
  })
  return found
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
    if (boundsBoxHelper) boundsBoxHelper.visible = false
    updateGridPlacement()
    updateSelectionHighlight()
    return
  }

  const boundsBox = new THREE.Box3()
  let hasAny = false

  if (bimPivot) {
    boundsBox.expandByObject(bimPivot)
    hasAny = true
  }

  if (pointcloudWrapper) {
    boundsBox.expandByObject(pointcloudWrapper)
    hasAny = true
  }

  if (hasAny && !boundsBox.isEmpty()) {
    if (!boundsBoxHelper) {
      boundsBoxHelper = new THREE.Box3Helper(boundsBox.clone(), 0x67e8f9)
      scene.add(boundsBoxHelper)
    }
    boundsBoxHelper.box.copy(boundsBox)
    boundsBoxHelper.visible = true
    boundsBoxHelper.updateMatrixWorld(true)
  } else if (boundsBoxHelper) {
    boundsBoxHelper.visible = false
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
      if (transformHelper) {
        scene.remove(transformHelper)
      }
      transformControls.dispose()
    }

    transformControls = new TransformControls(
      camera,
      renderer.domElement,
    ) as ViewerTransformControls
    transformControls.visible = false
    transformControls.enabled = false
    transformControls.setSize?.(1.5)
    transformControls.setSpace?.('world')
    transformControls.setMode(transformMode.value)
    transformHelper = transformControls.getHelper() as unknown as THREE.Object3D
    transformHelper.visible = false
    transformHelper.frustumCulled = false
    transformHelper.traverse?.((obj: any) => {
      obj.frustumCulled = false
      if (!obj.material) return
      if (Array.isArray(obj.material)) {
        obj.material.forEach((item: any) => {
          if (item) item.depthTest = false
        })
        return
      }
      obj.material.depthTest = false
    })
    transformControls.addEventListener('dragging-changed', (event: any) => {
      const dragging = !!event?.value
      if (controls) {
        controls.enabled = !dragging
      }

      if (!dragging) {
        syncTransformFixFromSelected()
      }

      if (!dragging && enableClipping.value) {
        updateClipRangeFromContent({ preserveT: true })
        applyClippingState()
      }
    })
    transformControls.addEventListener('change', () => {
      syncTransformFixFromSelected()
      syncBoundsHelpers()
      requestRender()
    })
    scene.add(transformHelper)
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

    perspectiveCamera = new THREE.PerspectiveCamera(50, width / height, 0.01, 5000)
    perspectiveCamera.position.set(0, 1.5, 4)

    orthographicCamera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.01, 5000)
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

function fitCameraToObject(object: THREE.Object3D | null) {
  if (!object || !activeCamera || !controls) return

  const box = new THREE.Box3().setFromObject(object)
  const size = box.getSize(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z, 1)

  controls.target.set(0, 0, 0)

  if (isOrthographicCamera(activeCamera)) {
    orthoViewSize = maxDim * 0.5 * 1.2
    updateOrthographicFrustum()
    activeCamera.position.set(0, maxDim * 0.15, maxDim * 2.2)
    activeCamera.near = Math.max(0.01, maxDim / 100)
    activeCamera.far = Math.max(5000, maxDim * 200)
    activeCamera.updateProjectionMatrix()
  } else {
    const fov = THREE.MathUtils.degToRad(activeCamera.fov)
    const distance = maxDim / 2 / Math.tan(fov / 2)
    activeCamera.position.set(0, maxDim * 0.15, distance * 2.2)
    activeCamera.near = Math.max(0.01, distance / 100)
    activeCamera.far = Math.max(5000, distance * 100)
    activeCamera.updateProjectionMatrix()
  }

  controls.update()
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

  if (rendererMode === 'webgpu') {
    const cache = mode === 'lambert' ? bimLambertMaterialCache : bimUnlitMaterialCache
    const key = opts.vertexColors ? 'v1' : 'v0'
    const cached = cache.get(source)?.[key]
    if (cached) {
      return cached
    }

    const next =
      mode === 'lambert' ? new MeshLambertNodeMaterial() : new MeshBasicNodeMaterial()

    next.name = (source as any)?.name
      ? `${(source as any).name} (${mode === 'lambert' ? 'TSL Lambert' : 'TSL Unlit'})`
      : mode === 'lambert'
        ? 'TSL Lambert'
        : 'TSL Unlit'
    next.fog = false
    next.lights = mode === 'lambert'
    applySharedMaterialFlags(next, source)
    next.toneMapped = mode === 'lambert'
    if ('map' in next) {
      ;(next as any).map = (source as any)?.map ?? null
    }
    if ('alphaMap' in next) {
      ;(next as any).alphaMap = (source as any)?.alphaMap ?? null
    }

    if (opts.vertexColors || (source as any)?.vertexColors) {
      next.colorNode = tslVertexColor()
      next.vertexColors = true
    } else {
      const colorValue =
        (source as any)?.color?.clone?.() ?? (source as any)?.color ?? new THREE.Color(0xffffff)
      next.colorNode = tslColor(colorValue)
      next.vertexColors = false
    }

    ;(next as any).__viewerOriginalMaterial = source
    const entry = cache.get(source) ?? {}
    entry[key] = next
    cache.set(source, entry)
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
  const enabled = !!enableClipping.value && !!hasClippableContent.value && !!showBounds.value

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

  // Always push the plane down to materials as well.
  // In the current viewer stack this is needed to keep BIM and point cloud
  // clipping behavior consistent across renderer/material combinations.
  applyMaterialClipping(enabled)

  if (rendererMode === 'webgpu' && clippingGroup) {
    clippingGroup.enabled = enabled
    clippingGroup.clippingPlanes.length = 0
    if (enabled) {
      clippingGroup.clippingPlanes.push(clippingPlane)
    }
    requestRender()
    return
  }
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

  if (!enableClipping.value) {
    showBounds.value = false
    applyClippingState()
    return
  }

  if (enableClipping.value && !showBounds.value) {
    showBounds.value = true
  }

  updateClipRangeFromContent({ resetPosition: true })
  applyClippingState()
}

function onClippingParamsChange() {
  if (!contentGroup) return
  updateClipRangeFromContent({ preserveT: true })
  applyClippingState()
}

function ensureOrientationBase(obj: THREE.Object3D | null) {
  if (!obj?.quaternion) return null
  obj.userData = obj.userData ?? {}
  if (!obj.userData.__orientationBaseQuat) {
    obj.userData.__orientationBaseQuat = obj.quaternion.clone()
  }
  return obj.userData.__orientationBaseQuat as THREE.Quaternion
}

function ensurePositionBase(obj: THREE.Object3D | null) {
  if (!obj?.position) return null
  obj.userData = obj.userData ?? {}
  if (!obj.userData.__positionBaseVec3) {
    obj.userData.__positionBaseVec3 = obj.position.clone()
  }
  return obj.userData.__positionBaseVec3 as THREE.Vector3
}

function ensureInitialOrientation(obj: THREE.Object3D | null) {
  if (!obj?.quaternion) return null
  obj.userData = obj.userData ?? {}
  if (!obj.userData.__initialOrientationQuat) {
    obj.userData.__initialOrientationQuat = obj.quaternion.clone()
  }
  return obj.userData.__initialOrientationQuat as THREE.Quaternion
}

function ensureInitialPosition(obj: THREE.Object3D | null) {
  if (!obj?.position) return null
  obj.userData = obj.userData ?? {}
  if (!obj.userData.__initialPositionVec3) {
    obj.userData.__initialPositionVec3 = obj.position.clone()
  }
  return obj.userData.__initialPositionVec3 as THREE.Vector3
}

function ensureInitialTransformState(obj: THREE.Object3D | null) {
  ensureInitialOrientation(obj)
  ensureInitialPosition(obj)
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

  const base = ensureOrientationBase(target)
  if (!base) return
  const offsetQuat = target.quaternion.clone().multiply(base.clone().invert())
  if (transformMode.value === 'rotate') {
    const offsetEuler = new THREE.Euler().setFromQuaternion(offsetQuat, 'YXZ')
    orientationDegX.value = 0
    orientationDegY.value = roundToStep(
      normalizeDegrees(THREE.MathUtils.radToDeg(offsetEuler.y)),
    )
    orientationDegZ.value = 0
    return
  }

  const offsetEuler = new THREE.Euler().setFromQuaternion(offsetQuat, 'XYZ')
  orientationDegX.value = roundToStep(
    normalizeDegrees(THREE.MathUtils.radToDeg(offsetEuler.x)),
  )
  orientationDegY.value = roundToStep(
    normalizeDegrees(THREE.MathUtils.radToDeg(offsetEuler.y)),
  )
  orientationDegZ.value = roundToStep(
    normalizeDegrees(THREE.MathUtils.radToDeg(offsetEuler.z)),
  )
}

function syncPositionFixFromSelected() {
  const target = getSelectedObject()
  if (!target) return

  const base = ensurePositionBase(target)
  if (!base) return
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

function syncAllTransformFixValuesFromSelected() {
  syncOrientationFixFromSelected()
  syncPositionFixFromSelected()
}

function applyTransformSelection() {
  const target = getSelectedObject()
  if (transformControls) {
    if (!editMode.value || !selectedItemId.value || !target) {
      transformControls.detach()
      transformControls.visible = false
      transformControls.enabled = false
      if (transformHelper) transformHelper.visible = false
      requestRender()
      return
    }

    target.matrixAutoUpdate = true
    target.updateMatrixWorld(true)
    ensureInitialTransformState(target)
    syncTransformModeForSelection()

    transformControls.setSpace?.('world')
    transformControls.setMode(transformMode.value)

    if (selectedItemIsPointcloud.value) {
      transformControls.showX = false
      transformControls.showY = false
      transformControls.showZ = false
      transformControls.detach()
      transformControls.visible = false
      transformControls.enabled = false
      if (transformHelper) transformHelper.visible = false
      resetOrientationFix()
      ensureOrientationBase(target)
      resetPositionFix()
      ensurePositionBase(target)
      requestRender()
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
    transformControls.visible = true
    transformControls.enabled = true
    if (transformHelper) {
      transformHelper.visible = true
      transformHelper.updateMatrixWorld?.(true)
    }
    resetOrientationFix()
    ensureOrientationBase(target)
    resetPositionFix()
    ensurePositionBase(target)
    requestRender()
  }

  updateSelectionHighlight()
}

function refreshSelectedTransformUi(rebaseBase = true) {
  syncTransformModeForSelection()
  applyTransformSelection()
  syncBoundsHelpers()
  resetOrientationFix()
  resetPositionFix()

  const target = getSelectedObject()
  ensureInitialTransformState(target)
  if (rebaseBase) {
    if (target?.quaternion) {
      target.userData = target.userData ?? {}
      target.userData.__orientationBaseQuat = target.quaternion.clone()
    }
    if (target?.position) {
      target.userData = target.userData ?? {}
      target.userData.__positionBaseVec3 = target.position.clone()
    }
    return
  }

  syncAllTransformFixValuesFromSelected()
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
    selectedItemId.value = hasModel.value ? 'bim' : ''
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
  logBimRelativeTransform()
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

  const base = ensurePositionBase(target)
  if (!base) return

  target.position.copy(base).add(
    new THREE.Vector3(
      positionOffsetX.value,
      positionOffsetZ.value,
      positionOffsetY.value,
    ),
  )
  target.updateMatrixWorld(true)
  transformHelper?.updateMatrixWorld?.(true)
  syncBoundsHelpers()
  requestRender()
}

function applyOrientationFixRealtime() {
  const target = getSelectedObject()
  if (!target) return

  const base = ensureOrientationBase(target)
  if (!base) return
  const delta = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(0, 1, 0),
    THREE.MathUtils.degToRad(orientationDegY.value),
  )
  target.quaternion.copy(delta).multiply(base)
  target.updateMatrixWorld(true)
  transformHelper?.updateMatrixWorld?.(true)
  syncBoundsHelpers()
  requestRender()
}

function resetCurrentObjectTransform() {
  const target = getSelectedObject()
  if (!target) return

  target.userData = target.userData ?? {}
  const currentPosition = target.position?.clone() ?? null
  const currentQuaternion = target.quaternion?.clone() ?? null

  const initialQuat = ensureInitialOrientation(target)
  if (initialQuat) {
    target.quaternion.copy(initialQuat)
    target.userData.__orientationBaseQuat = initialQuat.clone()
  }

  const initialPos = ensureInitialPosition(target)
  if (initialPos) {
    target.position.copy(initialPos)
    target.userData.__positionBaseVec3 = initialPos.clone()
  }

  console.info('[BimPointcloudAlign] resetCurrentObjectTransform', {
    selectedItemId: selectedItemId.value,
    currentPosition: currentPosition ? vectorToPlainObject(currentPosition) : null,
    currentQuaternion: currentQuaternion ? quaternionToPlainObject(currentQuaternion) : null,
    initialPosition: initialPos ? vectorToPlainObject(initialPos.clone()) : null,
    initialQuaternion: initialQuat ? quaternionToPlainObject(initialQuat.clone()) : null,
    basePosition: target.userData.__positionBaseVec3
      ? vectorToPlainObject((target.userData.__positionBaseVec3 as THREE.Vector3).clone())
      : null,
    baseQuaternion: target.userData.__orientationBaseQuat
      ? quaternionToPlainObject((target.userData.__orientationBaseQuat as THREE.Quaternion).clone())
      : null,
  })

  resetOrientationFix()
  resetPositionFix()
  target.updateMatrixWorld(true)
  transformHelper?.updateMatrixWorld?.(true)
  syncBoundsHelpers()
  logBimRelativeTransform()
  requestRender()
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

function recenterLoadedContentAsWhole() {
  if (!contentGroup) return
  if (!contentGroup.children?.length) {
    contentGroup.position.set(0, 0, 0)
    contentGroup.updateMatrixWorld(true)
    return
  }

  const previousPosition = contentGroup.position.clone()
  contentGroup.position.set(0, 0, 0)
  contentGroup.updateMatrixWorld(true)

  const box = new THREE.Box3().setFromObject(contentGroup)
  if (box.isEmpty()) {
    contentGroup.position.copy(previousPosition)
    contentGroup.updateMatrixWorld(true)
    return
  }

  const center = box.getCenter(new THREE.Vector3())
  contentGroup.position.copy(previousPosition).sub(center)
  contentGroup.updateMatrixWorld(true)
}

function recordNormalizationOffset(
  target: THREE.Object3D | null,
  center: THREE.Vector3 | null,
  mode: 'self' | 'child',
) {
  if (!target || !center) return

  target.userData = target.userData ?? {}
  target.userData.__viewerNormalizationCenter = center.clone()
  target.userData.__viewerNormalizationTranslation = center.clone().multiplyScalar(-1)
  target.userData.__viewerNormalizationMode = mode
}

function flattenStaticMeshesToRoot(root: THREE.Object3D) {
  root.updateMatrixWorld(true)

  const rootInverse = new THREE.Matrix4().copy(root.matrixWorld).invert()
  const meshes: THREE.Mesh[] = []

  root.traverse((obj: any) => {
    if (!obj?.isMesh) return
    if (obj === root) return
    if (obj?.isSkinnedMesh) return
    if (obj?.isInstancedMesh) return
    if (!obj?.geometry?.isBufferGeometry) return
    meshes.push(obj as THREE.Mesh)
  })

  let flattenedCount = 0

  meshes.forEach((mesh) => {
    const bakedMatrix = new THREE.Matrix4().multiplyMatrices(
      rootInverse,
      mesh.matrixWorld,
    )
    const nextGeometry = mesh.geometry.clone()
    nextGeometry.applyMatrix4(bakedMatrix)
    nextGeometry.computeBoundingBox?.()
    nextGeometry.computeBoundingSphere?.()

    const parent = mesh.parent
    if (parent && parent !== root) {
      parent.remove(mesh)
      root.add(mesh)
    }

    mesh.geometry.dispose?.()
    mesh.geometry = nextGeometry
    mesh.position.set(0, 0, 0)
    mesh.quaternion.identity()
    mesh.scale.set(1, 1, 1)
    mesh.updateMatrix()
    mesh.updateMatrixWorld(true)
    flattenedCount += 1
  })

  root.updateMatrixWorld(true)

  console.info('[BimPointcloudAlign] flattenStaticMeshesToRoot', {
    flattenedCount,
  })
}

function createCenteredPivot(root: THREE.Object3D) {
  const box = new THREE.Box3().setFromObject(root)
  const center = box.getCenter(new THREE.Vector3())
  const pivot = new THREE.Group()

  root.position.sub(center)
  recordNormalizationOffset(pivot, center, 'child')
  pivot.add(root)
  pivot.updateMatrixWorld(true)

  return pivot
}

function getRawMatrixWorldForCalibration(obj: THREE.Object3D | null) {
  const matrix = new THREE.Matrix4().copy(obj?.matrixWorld ?? new THREE.Matrix4())
  const center = obj?.userData?.__viewerNormalizationCenter as
    | THREE.Vector3
    | undefined
  const mode = obj?.userData?.__viewerNormalizationMode as 'self' | 'child' | undefined

  if (!center || mode !== 'child') {
    return matrix
  }

  matrix.multiply(
    new THREE.Matrix4().makeTranslation(
      -(center.x ?? 0),
      -(center.y ?? 0),
      -(center.z ?? 0),
    ),
  )

  return matrix
}

function logBimRelativeTransform() {
  if (!bimPivot || !pointcloudGroup) {
    return
  }

  contentGroup?.updateMatrixWorld(true)
  bimPivot.updateMatrixWorld(true)
  pointcloudWrapper?.updateMatrixWorld(true)
  pointcloudGroup.updateMatrixWorld(true)

  const relativeMatrix = new THREE.Matrix4()
    .copy(getRawMatrixWorldForCalibration(pointcloudGroup))
    .invert()
    .multiply(getRawMatrixWorldForCalibration(bimPivot))

  const position = new THREE.Vector3()
  const quaternion = new THREE.Quaternion()
  relativeMatrix.decompose(position, quaternion, new THREE.Vector3())

  console.info('[BimPointcloudAlign] BIM相对点云变换', {
    bimRelativePositionToPointcloud: vectorToPlainObject(position),
    bimRelativeQuaternionToPointcloud: quaternionToPlainObject(quaternion),
  })
}

function matrixToPlainArray(matrix: THREE.Matrix4) {
  return matrix.toArray().map((value) => Number(value))
}

function logCalibrationDiagnostics() {
  if (!bimPivot || !pointcloudWrapper || !pointcloudGroup) {
    return
  }

  contentGroup?.updateMatrixWorld(true)
  bimPivot.updateMatrixWorld(true)
  pointcloudWrapper.updateMatrixWorld(true)
  pointcloudGroup.updateMatrixWorld(true)

  const bimRawMatrixWorld = getRawMatrixWorldForCalibration(bimPivot)
  const pointcloudRawMatrixWorld = getRawMatrixWorldForCalibration(pointcloudGroup)
  const relativeRaw = new THREE.Matrix4()
    .copy(bimRawMatrixWorld)
    .invert()
    .multiply(pointcloudRawMatrixWorld)

  const relativeRigid = new THREE.Matrix4()
  const relativePosition = new THREE.Vector3()
  const relativeQuaternion = new THREE.Quaternion()
  relativeRaw.decompose(relativePosition, relativeQuaternion, new THREE.Vector3())
  relativeRigid.compose(
    relativePosition,
    relativeQuaternion,
    new THREE.Vector3(1, 1, 1),
  )

  const p0 = new THREE.Vector3(0, 0, 0).applyMatrix4(relativeRigid)
  const p1 = new THREE.Vector3(1, 0, 0).applyMatrix4(relativeRigid)
  const p2 = new THREE.Vector3(0, 1, 0).applyMatrix4(relativeRigid)
  const basisX = p1.clone().sub(p0)
  const basisY = p2.clone().sub(p0)

  console.info('[BimPointcloudAlign] calibration diagnostics', {
    bimPivotPosition: vectorToPlainObject(bimPivot.position.clone()),
    bimPivotQuaternion: quaternionToPlainObject(bimPivot.quaternion.clone()),
    bimRawMatrixWorld: matrixToPlainArray(bimRawMatrixWorld),
    pointcloudRawMatrixWorld: matrixToPlainArray(pointcloudRawMatrixWorld),
    relativeMatrixScanToBim: matrixToPlainArray(relativeRigid),
    basisFromScanXToBim: vectorToPlainObject(basisX),
    basisFromScanYToBim: vectorToPlainObject(basisY),
    samplePoints: {
      p0: vectorToPlainObject(p0),
      p1: vectorToPlainObject(p1),
      p2: vectorToPlainObject(p2),
    },
  })
}

function logScenePoseDiagnostics(stage: string) {
  const cameraPosition =
    activeCamera?.position ? vectorToPlainObject(activeCamera.position.clone()) : null
  const cameraQuaternion =
    activeCamera?.quaternion
      ? quaternionToPlainObject(activeCamera.quaternion.clone())
      : null
  const controlTarget = controls?.target ? vectorToPlainObject(controls.target.clone()) : null
  const bimPivotBox = bimPivot ? new THREE.Box3().setFromObject(bimPivot) : null
  const bimRootBox = bimRoot ? new THREE.Box3().setFromObject(bimRoot) : null
  const pointcloudFirstRenderable = findFirstRenderableDescendant(pointcloudGroup) as
    | THREE.Object3D
    | null
  const bimFirstRenderable = findFirstRenderableDescendant(bimRoot) as
    | THREE.Object3D
    | null

  console.info(`[BimPointcloudAlign] scene pose diagnostics ${stage}`, {
    stage,
    bimPivotPosition: bimPivot?.position
      ? vectorToPlainObject(bimPivot.position.clone())
      : null,
    bimPivotQuaternion: bimPivot?.quaternion
      ? quaternionToPlainObject(bimPivot.quaternion.clone())
      : null,
    bimRootQuaternion: bimRoot?.quaternion
      ? quaternionToPlainObject(bimRoot.quaternion.clone())
      : null,
    bimPivotBoxCenter:
      bimPivotBox && !bimPivotBox.isEmpty()
        ? vectorToPlainObject(bimPivotBox.getCenter(new THREE.Vector3()))
        : null,
    bimPivotBoxSize:
      bimPivotBox && !bimPivotBox.isEmpty()
        ? vectorToPlainObject(bimPivotBox.getSize(new THREE.Vector3()))
        : null,
    bimRootBoxCenter:
      bimRootBox && !bimRootBox.isEmpty()
        ? vectorToPlainObject(bimRootBox.getCenter(new THREE.Vector3()))
        : null,
    bimRootBoxSize:
      bimRootBox && !bimRootBox.isEmpty()
        ? vectorToPlainObject(bimRootBox.getSize(new THREE.Vector3()))
        : null,
    pointcloudWrapperPosition: pointcloudWrapper?.position
      ? vectorToPlainObject(pointcloudWrapper.position.clone())
      : null,
    pointcloudWrapperQuaternion: pointcloudWrapper?.quaternion
      ? quaternionToPlainObject(pointcloudWrapper.quaternion.clone())
      : null,
    pointcloudGroupQuaternion: pointcloudGroup?.quaternion
      ? quaternionToPlainObject(pointcloudGroup.quaternion.clone())
      : null,
    pointcloudFirstRenderablePosition: pointcloudFirstRenderable?.position
      ? vectorToPlainObject(pointcloudFirstRenderable.position.clone())
      : null,
    pointcloudFirstRenderableQuaternion: pointcloudFirstRenderable?.quaternion
      ? quaternionToPlainObject(pointcloudFirstRenderable.quaternion.clone())
      : null,
    bimFirstRenderablePosition: bimFirstRenderable?.position
      ? vectorToPlainObject(bimFirstRenderable.position.clone())
      : null,
    bimFirstRenderableQuaternion: bimFirstRenderable?.quaternion
      ? quaternionToPlainObject(bimFirstRenderable.quaternion.clone())
      : null,
    cameraPosition,
    cameraQuaternion,
    controlTarget,
  })
}

function logSavedAlignmentMatrix(alignment: BimAlignmentResult) {
  console.info('[BimPointcloudAlign] 后端已保存校准矩阵', {
    modelScanFileId: alignment.modelScanFileId,
    modelBimFileId: alignment.modelBimFileId,
    modelMatrix: Array.isArray(alignment.modelMatrix) ? alignment.modelMatrix : [],
    modelTranslation: {
      x: alignment.modelTranslationX,
      y: alignment.modelTranslationY,
      z: alignment.modelTranslationZ,
    },
    modelQuaternion: {
      x: alignment.modelRotationQx,
      y: alignment.modelRotationQy,
      z: alignment.modelRotationQz,
      w: alignment.modelRotationQw,
    },
    modelPairCount: alignment.modelPairCount,
    modelInlierCount: alignment.modelInlierCount,
    modelRmse: alignment.modelRmse,
    modelMaxError: alignment.modelMaxError,
  })
}

function buildAlignmentMatrix(alignment: BimAlignmentResult) {
  const rawMatrix = new THREE.Matrix4()
  if (Array.isArray(alignment.modelMatrix) && alignment.modelMatrix.length === 16) {
    rawMatrix.fromArray(alignment.modelMatrix)
  } else {
    rawMatrix.compose(
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

  const position = new THREE.Vector3()
  const quaternion = new THREE.Quaternion()
  rawMatrix.decompose(position, quaternion, new THREE.Vector3())
  return new THREE.Matrix4().compose(position, quaternion, new THREE.Vector3(1, 1, 1))
}

function tryRestoreSavedAlignment(alignment: BimAlignmentResult) {
  if (!bimPivot || !pointcloudWrapper || !pointcloudGroup || !pointcloudRootReady) {
    return false
  }

  const restoreKey = `${alignment.modelScanFileId}:${alignment.modelBimFileId}:${alignment.modelId}`
  if (restoredSavedAlignmentKey === restoreKey) {
    return true
  }

  const bimCenter = bimPivot.userData?.__viewerNormalizationCenter as THREE.Vector3 | undefined
  if (!bimCenter) {
    return false
  }

  const preservedBaseQuat = ensureOrientationBase(bimPivot)?.clone() ?? null
  const preservedBasePos = ensurePositionBase(bimPivot)?.clone() ?? null

  contentGroup?.updateMatrixWorld(true)
  pointcloudWrapper.updateMatrixWorld(true)
  pointcloudGroup.updateMatrixWorld(true)
  bimPivot.updateMatrixWorld(true)

  const alignmentMatrix = buildAlignmentMatrix(alignment)
  const pointcloudRawMatrixWorld = getRawMatrixWorldForCalibration(pointcloudGroup)
  const desiredBimWorld = new THREE.Matrix4()
    .copy(pointcloudRawMatrixWorld)
    .multiply(alignmentMatrix.clone().invert())
    .multiply(new THREE.Matrix4().makeTranslation(bimCenter.x ?? 0, bimCenter.y ?? 0, bimCenter.z ?? 0))

  const parentInverse = new THREE.Matrix4()
    .copy(bimPivot.parent?.matrixWorld ?? new THREE.Matrix4())
    .invert()
  const localMatrix = new THREE.Matrix4().multiplyMatrices(parentInverse, desiredBimWorld)

  const position = new THREE.Vector3()
  const quaternion = new THREE.Quaternion()
  const scale = new THREE.Vector3()
  localMatrix.decompose(position, quaternion, scale)

  bimPivot.position.copy(position)
  bimPivot.quaternion.copy(quaternion)
  bimPivot.scale.set(1, 1, 1)
  bimPivot.userData = bimPivot.userData ?? {}
  if (preservedBaseQuat) {
    bimPivot.userData.__orientationBaseQuat = preservedBaseQuat
  }
  if (preservedBasePos) {
    bimPivot.userData.__positionBaseVec3 = preservedBasePos
  }
  bimPivot.updateMatrixWorld(true)
  recenterLoadedContentAsWhole()
  editMode.value = true
  transformMode.value = 'translate'
  selectedItemId.value = 'bim'
  refreshSelectedTransformUi(false)
  void nextTick(() => {
    applyTransformSelection()
    syncAllTransformFixValuesFromSelected()
    requestRender()
  })
  syncBoundsHelpers()
  updateClipRangeFromContent({ preserveT: true })
  applyClippingState()
  restoredSavedAlignmentKey = restoreKey

  console.info('[BimPointcloudAlign] 已恢复后端校准矩阵到场景', {
    restoreKey,
    bimPivotPosition: vectorToPlainObject(bimPivot.position.clone()),
    bimPivotQuaternion: quaternionToPlainObject(bimPivot.quaternion.clone()),
  })
  logBimRelativeTransform()
  return true
}

async function fetchAndLogSavedAlignmentIfExists() {
  if (
    !props.pointcloudAssetId ||
    !props.bimAssetId ||
    !bimPivot ||
    !pointcloudGroup ||
    !pointcloudRootReady
  ) {
    return
  }

  const logKey = `${props.pointcloudAssetId}:${props.bimAssetId}`
  if (loggedSavedAlignmentKey === logKey) {
    return
  }

  try {
    const calibration = await getScanCalibration(props.pointcloudAssetId)
    const currentBimId = calibration?.data?.bimFileId ?? null

    if (!calibration?.data?.hasBimAlignment || currentBimId !== props.bimAssetId) {
      console.info('[BimPointcloudAlign] 当前组合暂无后端已保存校准矩阵', {
        pointcloudAssetId: props.pointcloudAssetId,
        bimAssetId: props.bimAssetId,
      })
      loggedSavedAlignmentKey = logKey
      return
    }

    const response = await getBimAlignment({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
    })

    if (!response?.data) {
      return
    }

    logSavedAlignmentMatrix(response.data)
    tryRestoreSavedAlignment(response.data)
    logBimRelativeTransform()
    loggedSavedAlignmentKey = logKey
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 400 || status === 404) {
      console.info('[BimPointcloudAlign] 当前组合暂无后端已保存校准矩阵', {
        pointcloudAssetId: props.pointcloudAssetId,
        bimAssetId: props.bimAssetId,
      })
      loggedSavedAlignmentKey = logKey
      return
    }

    console.error('[BimPointcloudAlign] 获取后端校准矩阵失败', error)
  }
}

function collectCalibrationSnapshot(options?: { warnOnMissing?: boolean }) {
  const warnOnMissing = options?.warnOnMissing ?? false

  if (!bimPivot || !pointcloudWrapper || !pointcloudGroup || !props.bimAssetId || !props.pointcloudAssetId) {
    if (warnOnMissing) {
      ElMessage.warning('缺少 BIM 或点云，无法生成校准点对')
    }
    return null
  }

  contentGroup?.updateMatrixWorld(true)
  bimPivot.updateMatrixWorld(true)
  pointcloudWrapper.updateMatrixWorld(true)
  pointcloudGroup.updateMatrixWorld(true)

  const relative = new THREE.Matrix4()
    .copy(getRawMatrixWorldForCalibration(bimPivot))
    .invert()
    .multiply(getRawMatrixWorldForCalibration(pointcloudGroup))

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
  const payload = collectCalibrationSnapshot({ warnOnMissing: true })
  if (!payload) {
    return false
  }

  try {
    logCalibrationDiagnostics()
    console.info('[BimPointcloudAlign] save alignment payload', payload)
    logBimRelativeTransform()
    await createBimAlignment(payload)
    ElMessage.success('校准结果已保存')
    console.info('[BimPointcloudAlign] save alignment success')
    return true
  } catch (error) {
    console.error('[BimPointcloudAlign] save alignment failed', error)
    ElMessage.error(error instanceof Error ? error.message : '保存校准结果失败')
    return false
  }
}

async function handleSaveAndContinue() {
  console.info('[BimPointcloudAlign] handleSaveAndContinue start')
  const saved = await handleSaveAlignment()
  console.info('[BimPointcloudAlign] handleSaveAndContinue result', { saved })
  if (!saved) return
  ElMessage.success('校准已保存，当前保留页面用于排查')
}

function handleCalibrationComplete() {
  const snapshot = collectCalibrationSnapshot({ warnOnMissing: true })
  if (!snapshot) return
  logBimRelativeTransform()
  ElMessage.success('已生成 3 组校准点对')
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
          flattenStaticMeshesToRoot(root)
          const pivot = createCenteredPivot(root)
          nextContentGroup.add(pivot)

          bimRoot = root
          bimPivot = pivot
          bimLoaded.value = true
          bimVisible.value = true

          ensureInitialTransformState(bimPivot)
          recenterLoadedContentAsWhole()
          applySceneVisibility()
          applyBimMaterialMode(bimPivot)
          updateClipRangeFromContent({ preserveT: true })
          applyClippingState()
          syncBoundsHelpers()
          fitCameraToObject(bimPivot)
          statusText.value = `已加载 BIM：${props.bimDisplayName || assetDetail.sourceName}`
          logScenePoseDiagnostics('after-bim-load')
          if (pointcloudRootReady) {
            void fetchAndLogSavedAlignmentIfExists()
          }
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
    pointcloudRootReady = false
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
    ensureInitialTransformState(pointcloudWrapper)
    recenterLoadedContentAsWhole()
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
      pointcloudRootReady = true
      recenterLoadedContentAsWhole()
      pointcloudWrapper?.updateMatrixWorld(true)
      nextTileset.group.updateMatrixWorld(true)
      scheduleClipRangeUpdate()
      ensureInitialTransformState(pointcloudWrapper)
      updateClipRangeFromContent({ preserveT: true })
      applyClippingState()
      syncBoundsHelpers()
      statusText.value = `已加载点云：${props.pointcloudDisplayName || assetDetail.sourceName}`
      logScenePoseDiagnostics('after-pointcloud-root-load')
      void fetchAndLogSavedAlignmentIfExists()
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
    pointcloudRootReady = false
    if (!silent) {
      ElMessage.error(error instanceof Error ? error.message : '加载点云失败')
    }
    statusText.value = '加载点云失败'
  } finally {
    loadingPointcloud.value = false
  }
}

async function preloadFromRoute() {
  const tasks: Promise<unknown>[] = []

  if (props.bimAssetId) {
    tasks.push(handleLoadBimFromApi(true))
  }
  if (props.pointcloudAssetId) {
    tasks.push(handleLoadPointCloudFromApi(true))
  }

  if (!tasks.length) return

  await Promise.allSettled(tasks)
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
  pointcloudRootReady = false
  loggedSavedAlignmentKey = ''
  stopRenderLoop()
  resizeObserver?.disconnect()
  controls?.dispose()
  if (scene && transformHelper) {
    scene.remove(transformHelper)
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
        <el-button type="success" @click="handleSaveAndContinue">保存并继续</el-button>
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
              :disabled="!hasClippableContent"
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
            <span class="label">剖切</span>
            <el-switch v-model="enableClipping" :disabled="!hasClippableContent" />
          </div>
          <div class="control-row" :class="{ disabled: !enableClipping || !showBounds || !hasClippableContent }">
            <span class="label">剖切轴</span>
            <el-select v-model="clipAxis" :disabled="!enableClipping || !showBounds || !hasClippableContent">
              <el-option label="X" value="x" />
              <el-option label="Y" value="y" />
              <el-option label="Z" value="z" />
            </el-select>
            <el-switch
              v-model="clipInvert"
              inline-prompt
              active-text="反"
              inactive-text="正"
              :disabled="!enableClipping || !showBounds || !hasClippableContent"
            />
          </div>
          <div class="control-row" :class="{ disabled: !enableClipping || !showBounds || !hasClippableContent }">
            <span class="label">位置</span>
            <el-slider
              v-model="clipPosition"
              :min="clipRange.min"
              :max="clipRange.max"
              :step="clipStep"
              :disabled="!enableClipping || !showBounds || !hasClippableContent"
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
  </section>
</template>

<style lang="scss" scoped>
@use './index.scss';
</style>
