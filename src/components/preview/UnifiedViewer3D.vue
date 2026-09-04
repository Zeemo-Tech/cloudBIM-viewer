<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { TilesRenderer } from '3d-tiles-renderer'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import { PointCloudEdlPipeline } from './edlPipeline'
import ViewerMeasurementBadge, {
  type ViewerMeasurementBadgeOverlay,
} from './ViewerMeasurementBadge.vue'
import { createUploadHeaders } from '@/config/upload-backend'
import { getAssetDetail, getBimGlbUrl, getPointcloudTilesetUrl } from '@/api/backend-file'
import { getC2MColoredPlyUrl, type C2MResult } from '@/api/backend-c2m'
import { backendRequest } from '@/api/backend-http'
import type { AnalysisArea, AnalysisDistance, AnalysisMode, AnalysisPoint } from './ViewerAnalysisOverlay.vue'

export type ViewerType = 'bim' | 'pointcloud' | 'c2m' | 'hybrid'
export type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'
export type StandardView = 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom'
export type PointcloudColorMode = 'rgb' | 'intensity'
export type PointcloudColorRamp = 'grayscale' | 'spectrum' | 'viridis'
export type PointcloudColorRange = { min: number; max: number }

export type CameraPose = {
  camera: THREE.Vector3
  target: THREE.Vector3
  up?: THREE.Vector3
}

export type CameraRotation = {
  lon: number
  lat: number
}

export type ClipAxis = 'x' | 'y' | 'z'
export type ClipBoxOffsets = {
  xMin: number
  xMax: number
  yMin: number
  yMax: number
  zMin: number
  zMax: number
}

export type ClipBoxState = {
  baseBox: THREE.Box3
  offsets: ClipBoxOffsets
}

export interface UnifiedViewerProps {
  type?: ViewerType
  assetId?: number | null
  scanAssetId?: number | null
  bimAssetId?: number | null
  pointcloudAssetId?: number | null
  displayName?: string
  minimal?: boolean
  calibration?: { modelMatrix: number[] } | null
  bimWorldPose?: {
    position: THREE.Vector3
    quaternion: THREE.Quaternion
    scale: THREE.Vector3
  } | null
  fusionMode?: boolean
  analysisMode?: AnalysisMode
  analysisPoints?: AnalysisPoint[]
  analysisDistances?: AnalysisDistance[]
  analysisAreas?: AnalysisArea[]
  c2mResult?: C2MResult | null
  edlEnabled?: boolean
  edlStrength?: number
  showEdlControl?: boolean
}

const props = withDefaults(defineProps<UnifiedViewerProps>(), {
  type: 'bim',
  assetId: null,
  scanAssetId: null,
  bimAssetId: null,
  pointcloudAssetId: null,
  displayName: undefined,
  minimal: false,
  calibration: null,
  bimWorldPose: null,
  fusionMode: false,
  analysisMode: 'none',
  analysisPoints: () => [],
  analysisDistances: () => [],
  analysisAreas: () => [],
  c2mResult: null,
  edlEnabled: true,
  edlStrength: 1.0,
  showEdlControl: true,
})

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
  (event: 'analysis-point', point: AnalysisPoint): void
  (event: 'analysis-distance', distance: AnalysisDistance): void
  (event: 'analysis-area', area: AnalysisArea): void
  (event: 'analysis-delete', payload: { kind: 'point' | 'distance' | 'area'; id: string }): void
  (event: 'analysis-mode-exit', mode: AnalysisMode): void
  (event: 'pointcloud-color-stats', payload: {
    histogram: number[]
    hasIntensity: boolean
    hasRgb: boolean
  }): void
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('')
const loadError = ref('')
const loaded = ref(false)

// EDL 配置与响应式状态
const EDL_STORAGE_KEY = 'cloudbim.viewer.edlEnabled'
const EDL_STRENGTH_STORAGE_KEY = 'cloudbim.viewer.edlStrength'

function readEdlPreference() {
  if (typeof window === 'undefined') return true
  try {
    const value = window.localStorage.getItem(EDL_STORAGE_KEY)
    return value === null ? true : value === '1'
  } catch {
    return true
  }
}

function readEdlStrengthPreference() {
  if (typeof window === 'undefined') return 1.0
  try {
    const value = Number(window.localStorage.getItem(EDL_STRENGTH_STORAGE_KEY))
    return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1.0
  } catch {
    return 1.0
  }
}

const localEdlEnabled = ref(readEdlPreference())
const localEdlStrength = ref(readEdlStrengthPreference())

// 基础三维变量
const defaultBgColor = '#0b1020'
const dprCap = 1.25

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let edlPipeline: PointCloudEdlPipeline | null = null
let resizeObserver: ResizeObserver | null = null

// 场景模型根节点
let bimRoot: THREE.Object3D | null = null
let pointcloudWrapper: THREE.Group | null = null
let tileset: TilesRenderer | null = null
let c2mMeshRoot: THREE.Object3D | null = null

let bimSourceMatrix = new THREE.Matrix4()
let bimSourceCenter = new THREE.Vector3()
let hasBimSourceCenter = false

let animationId = 0
let isMountedReady = false
let loadToken = 0
let tilesLoadingCount = 0

// 辅助对象
let axesHelper: THREE.AxesHelper | null = null
let gridHelper: THREE.GridHelper | null = null
let raycaster: THREE.Raycaster | null = null

let wireframeEnabled = false
let showAxesEnabled = false
let showGridEnabled = true
let gridColor = '#2a6f82'
let backgroundTheme: PreviewBackgroundTheme = 'deep'
let customBackgroundColor = ''
let pointColorOverride: string | null = '#86898D'
let pointSize = 2.5
let pointcloudColorMode: PointcloudColorMode = 'rgb'
let pointcloudColorRamp: PointcloudColorRamp = 'grayscale'
let pointcloudColorRange: PointcloudColorRange = { min: 0, max: 1 }
let pointcloudIntensityHistogram = Array.from({ length: 64 }, () => 0)
let pointcloudHasIntensity = false
let pointcloudHasRgb = false

// 剖切盒状态
let sectionEnabled = false
let sectionRatio = 50
let boundsBoxHelper: THREE.Box3Helper | null = null
let clipHandlesGroup: THREE.Group | null = null

let clipState: ClipBoxState = {
  baseBox: new THREE.Box3(new THREE.Vector3(-10, -10, -10), new THREE.Vector3(10, 10, 10)),
  offsets: { xMin: 0, xMax: 0, yMin: 0, yMax: 0, zMin: 0, zMax: 0 },
}

const clipPlanes = [
  new THREE.Plane(new THREE.Vector3(1, 0, 0), 0),
  new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0),
  new THREE.Plane(new THREE.Vector3(0, 1, 0), 0),
  new THREE.Plane(new THREE.Vector3(0, -1, 0), 0),
  new THREE.Plane(new THREE.Vector3(0, 0, 1), 0),
  new THREE.Plane(new THREE.Vector3(0, 0, -1), 0),
]

// 测量分析状态
let analysisAnchorPoint: THREE.Vector3 | null = null
let analysisAreaPoints: THREE.Vector3[] = []
let analysisVisualGroup: THREE.Group | null = null
let analysisAreaLine: THREE.Line | null = null
let analysisAreaFill: THREE.Mesh | null = null
let analysisAreaMarkers: THREE.Sprite[] = []
let analysisDistanceLine: Line2 | null = null
let analysisDistanceStartMarker: THREE.Sprite | null = null
let analysisDistanceEndMarker: THREE.Sprite | null = null
let analysisDistanceHoverMarker: THREE.Sprite | null = null
let analysisPointerDown: { x: number; y: number } | null = null
let measurementModelDiagonal = 10
const archivedAnalysisGroups: THREE.Group[] = []
const archivedAnalysisById = new Map<string, THREE.Group>()
const hiddenMeasurementIds = new Set<string>()
const measurementPanelOffsets = new Map<string, { x: number; y: number }>()
const measurementBadges = ref<Array<{
  id: string
  kind: 'point' | 'distance' | 'area'
  title: string
  mainLabel?: string
  mainValue?: string
  rows: Array<{ label: string; value: string }>
  overlay: ViewerMeasurementBadgeOverlay
}>>([])

// 材质存储
const originalMaterialStore = new WeakMap<THREE.Object3D, any>()
const originalPointColors = new WeakMap<THREE.BufferGeometry, THREE.BufferAttribute | null>()

// ---------------------------
// 基础渲染流程（平滑 60FPS 连续渲染环，对齐校准页机制）
// ---------------------------
function requestRender() {
  if (animationId) return

  const renderFrame = () => {
    animationId = requestAnimationFrame(renderFrame)

    if (!renderer || !scene || !camera) return

    if (tileset) {
      camera.updateMatrixWorld()
      tileset.setCamera(camera)
      tileset.setResolutionFromRenderer?.(camera, renderer)
      tileset.update()
    }
    syncMeasurementMarkerScales()

    // EDL 渲染管线判断：仅在开启 EDL 且含点云场景时应用
    const shouldRunEdl =
      localEdlEnabled.value && edlPipeline && (props.type === 'pointcloud' || props.type === 'hybrid')

    if (shouldRunEdl) {
      edlPipeline!.render(scene, camera)
    } else {
      renderer.render(scene, camera)
    }
  }

  renderFrame()
}

function stopRenderLoop() {
  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = 0
  }
}

function emitCameraPose() {
  if (!camera || !controls) return
  emit('camera-change', {
    camera: camera.position.clone(),
    target: controls.target.clone(),
    up: camera.up.clone(),
  })
}

// ---------------------------
// 背景与灯光同步（完全对齐校准页环境光与色调）
// ---------------------------
function getThemeColor(theme: PreviewBackgroundTheme): string {
  switch (theme) {
    case 'light':
      return '#f7fbff'
    case 'black':
      return '#000000'
    case 'gradient':
      return '#17365f'
    case 'deep':
    default:
      return '#0b1020'
  }
}

function syncSceneBackground() {
  if (!renderer || !scene) return
  const color = customBackgroundColor || getThemeColor(backgroundTheme)
  const threeColor = new THREE.Color(color)
  renderer.setClearColor(threeColor, 1)
  scene.background = threeColor
}

function setBackgroundTheme(theme: PreviewBackgroundTheme) {
  backgroundTheme = theme
  customBackgroundColor = ''
  syncSceneBackground()
}

function setBackgroundColor(color: string) {
  customBackgroundColor = color
  syncSceneBackground()
}

function initLights() {
  if (!scene) return
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.78)
  ambientLight.name = 'ambient-light'
  scene.add(ambientLight)

  const keyLight = new THREE.DirectionalLight(0xffffff, 0.92)
  keyLight.position.set(14, 18, 12)
  keyLight.name = 'key-light'
  scene.add(keyLight)

  const fillLight = new THREE.DirectionalLight(0x9cc3ff, 0.42)
  fillLight.position.set(-10, 8, -10)
  fillLight.name = 'fill-light'
  scene.add(fillLight)
}

// ---------------------------
// 剖切盒交互系统 (Section Box)
// ---------------------------
function updateClipPlanes() {
  const box = getEffectiveBox(clipState)
  clipPlanes[0].set(new THREE.Vector3(1, 0, 0), -box.min.x)
  clipPlanes[1].set(new THREE.Vector3(-1, 0, 0), box.max.x)
  clipPlanes[2].set(new THREE.Vector3(0, 1, 0), -box.min.y)
  clipPlanes[3].set(new THREE.Vector3(0, -1, 0), box.max.y)
  clipPlanes[4].set(new THREE.Vector3(0, 0, 1), -box.min.z)
  clipPlanes[5].set(new THREE.Vector3(0, 0, -1), box.max.z)

  if (boundsBoxHelper) {
    boundsBoxHelper.box.copy(box)
  }
  syncClippingToMaterials()
}

function getEffectiveBox(state: ClipBoxState): THREE.Box3 {
  const { baseBox, offsets } = state
  return new THREE.Box3(
    new THREE.Vector3(baseBox.min.x + offsets.xMin, baseBox.min.y + offsets.yMin, baseBox.min.z + offsets.zMin),
    new THREE.Vector3(baseBox.max.x + offsets.xMax, baseBox.max.y + offsets.yMax, baseBox.max.z + offsets.zMax),
  )
}

function syncClippingToMaterials() {
  if (!renderer) return
  renderer.clippingPlanes = sectionEnabled ? clipPlanes : []
  renderer.localClippingEnabled = sectionEnabled
}

function buildClipHandles() {
  if (!scene) return
  if (clipHandlesGroup) {
    scene.remove(clipHandlesGroup)
    clipHandlesGroup.traverse((c: any) => {
      c.geometry?.dispose?.()
      c.material?.dispose?.()
    })
  }

  clipHandlesGroup = new THREE.Group()
  clipHandlesGroup.name = 'clip-handles'

  boundsBoxHelper = new THREE.Box3Helper(getEffectiveBox(clipState), new THREE.Color(0x00ffff))
  ;(boundsBoxHelper.material as THREE.Material).clippingPlanes = null
  clipHandlesGroup.add(boundsBoxHelper)

  clipHandlesGroup.visible = sectionEnabled
  scene.add(clipHandlesGroup)
}

function setSectionState(state: { enabled?: boolean; ratio?: number; box?: THREE.Box3 }) {
  if (state.enabled !== undefined) {
    sectionEnabled = state.enabled
    if (clipHandlesGroup) clipHandlesGroup.visible = sectionEnabled
    syncClippingToMaterials()
  }
  if (state.box) {
    clipState.baseBox.copy(state.box)
    const diagonal = state.box.getSize(new THREE.Vector3()).length()
    if (Number.isFinite(diagonal) && diagonal > 0) measurementModelDiagonal = diagonal
    clipState.offsets = { xMin: 0, xMax: 0, yMin: 0, yMax: 0, zMin: 0, zMax: 0 }
    updateClipPlanes()
  }
  if (state.ratio !== undefined) {
    sectionRatio = Math.max(1, Math.min(100, state.ratio))
    const size = clipState.baseBox.getSize(new THREE.Vector3())
    const fraction = (100 - sectionRatio) / 100
    clipState.offsets.zMax = -size.z * fraction
    updateClipPlanes()
  }
}

// ---------------------------
// 空间测量系统 (Analysis Overlay)
// ---------------------------
function clearAnalysisVisuals() {
  cancelActiveAnalysis()
  archivedAnalysisGroups.forEach(disposeAnalysisGroup)
  archivedAnalysisGroups.splice(0)
  archivedAnalysisById.clear()
  hiddenMeasurementIds.clear()
  measurementPanelOffsets.clear()
  measurementBadges.value = []
}

function disposeAnalysisGroup(group: THREE.Group | null) {
  if (!group) return
  scene?.remove(group)
  group.traverse((child: any) => {
    child.geometry?.dispose?.()
    const material = child.material
    if (Array.isArray(material)) {
      material.forEach((item: any) => {
        item?.map?.dispose?.()
        item?.dispose?.()
      })
    } else {
      material?.map?.dispose?.()
      material?.dispose?.()
    }
  })
}

function createMeasurementPinSprite(color = '#ff4040', opacity = 1) {
  const canvas = document.createElement('canvas')
  canvas.width = 128
  canvas.height = 128
  const context = canvas.getContext('2d')
  if (!context) throw new Error('无法创建测量标记画布')
  context.shadowColor = 'rgba(255, 86, 86, .38)'
  context.shadowBlur = 18
  context.fillStyle = color
  context.beginPath()
  context.moveTo(64, 10)
  context.bezierCurveTo(33, 10, 18, 32, 18, 55)
  context.bezierCurveTo(18, 82, 39, 96, 64, 118)
  context.bezierCurveTo(89, 96, 110, 82, 110, 55)
  context.bezierCurveTo(110, 32, 95, 10, 64, 10)
  context.closePath()
  context.fill()
  context.shadowBlur = 0
  context.fillStyle = '#fff1f1'
  context.beginPath()
  context.arc(64, 52, 18, 0, Math.PI * 2)
  context.fill()
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const marker = new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    opacity,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
  }))
  marker.center.set(.5, .1)
  marker.renderOrder = 10002
  return marker
}

function getAdaptiveMeasurementMarkerPixels() {
  // Keep pins restrained on small assets while giving large-scale point clouds
  // enough visual weight. The screen-space clamp prevents oversized markers.
  const sizeFactor = Math.log10(Math.max(measurementModelDiagonal, 1))
  return THREE.MathUtils.clamp(10 + sizeFactor * 2, 10, 16)
}

function scaleMeasurementMarker(marker: THREE.Sprite, targetPixels?: number) {
  if (!camera || !viewportEl.value || !marker.visible) return

  const rect = viewportEl.value.getBoundingClientRect()
  const viewportHeight = Math.max(rect.height, 1)
  const distance = camera.position.distanceTo(marker.position)
  const fov = THREE.MathUtils.degToRad(camera.fov)

  // Sprites use world-unit dimensions. Convert the desired screen size to
  // world units so zooming the point cloud does not make the pin grow on screen.
  const worldUnitsPerPixel =
    (2 * distance * Math.tan(fov * 0.5)) / viewportHeight
  const pixelSize = targetPixels ?? getAdaptiveMeasurementMarkerPixels()
  const size = Math.max(worldUnitsPerPixel * pixelSize, Number.EPSILON)
  marker.scale.set(size, size, 1)
}

function syncMeasurementMarkerScales() {
  ;[...archivedAnalysisGroups, analysisVisualGroup].forEach((group) => {
    group?.traverse((child) => {
      if (child instanceof THREE.Sprite) scaleMeasurementMarker(child)
      if (child instanceof Line2 && renderer) {
        child.material.resolution.set(renderer.domElement.clientWidth || 1, renderer.domElement.clientHeight || 1)
      }
    })
  })
}

function ensureAnalysisGroup() {
  if (!scene) return null
  if (!analysisVisualGroup) {
    analysisVisualGroup = new THREE.Group()
    analysisVisualGroup.renderOrder = 10000
    scene.add(analysisVisualGroup)
  }
  return analysisVisualGroup
}

function createDistanceLine(start: THREE.Vector3, end: THREE.Vector3) {
  const line = new Line2(
    new LineGeometry(),
    new LineMaterial({
      color: '#d63d3d',
      dashed: true,
      dashSize: .9,
      gapSize: .48,
      transparent: true,
      opacity: .96,
      linewidth: 2.8,
      worldUnits: false,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  line.geometry.setPositions([start.x, start.y, start.z, end.x, end.y, end.z])
  line.computeLineDistances()
  line.renderOrder = 10001
  return line
}

function createArchivedDistanceGroup(record: AnalysisDistance) {
  const start = new THREE.Vector3(record.start.x, record.start.y, record.start.z)
  const end = new THREE.Vector3(record.end.x, record.end.y, record.end.z)
  const group = new THREE.Group()
  group.renderOrder = 10000
  group.add(createDistanceLine(start, end))

  const startMarker = createMeasurementPinSprite('#ff4040')
  startMarker.position.copy(start)
  group.add(startMarker)
  const endMarker = createMeasurementPinSprite('#ff5a5a', .96)
  endMarker.position.copy(end)
  group.add(endMarker)
  return group
}

function createArchivedPointGroup(point: AnalysisPoint) {
  const group = new THREE.Group()
  group.renderOrder = 10000
  const marker = createMeasurementPinSprite('#22d3ee')
  marker.position.set(point.x, point.y, point.z)
  group.add(marker)
  return group
}

function createArchivedAreaGroup(record: AnalysisArea) {
  const points = record.points.map((point) => new THREE.Vector3(point.x, point.y, point.z))
  const group = new THREE.Group()
  group.renderOrder = 10000
  if (!points.length) return group

  const outline = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(points.length > 2 ? [...points, points[0]] : points),
    new THREE.LineDashedMaterial({
      color: 0xff5a5a,
      dashSize: .9,
      gapSize: .48,
      transparent: true,
      opacity: .9,
      depthTest: false,
      depthWrite: false,
    }),
  )
  outline.computeLineDistances()
  outline.renderOrder = 10001
  group.add(outline)

  const metrics = createPolygonMetrics(points)
  if (metrics) {
    const triangles = THREE.ShapeUtils.triangulateShape(metrics.projected, [])
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute(
      'position',
      new THREE.Float32BufferAttribute(points.flatMap((point) => [point.x, point.y, point.z]), 3),
    )
    geometry.setIndex(triangles.flat())
    geometry.computeVertexNormals()
    const fill = new THREE.Mesh(
      geometry,
      new THREE.MeshBasicMaterial({
        color: 0xff5a5a,
        transparent: true,
        opacity: .16,
        depthTest: false,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    )
    fill.renderOrder = 10000
    group.add(fill)
  }

  points.forEach((point) => {
    const marker = createMeasurementPinSprite('#ff4040')
    marker.position.copy(point)
    group.add(marker)
  })
  return group
}

function archiveMeasurementGroup(kind: 'point' | 'distance' | 'area', id: string, group: THREE.Group) {
  group.userData.measurementKind = kind
  group.userData.measurementId = id
  scene?.add(group)
  archivedAnalysisGroups.push(group)
  archivedAnalysisById.set(`${kind}:${id}`, group)
}

function removeAnalysisVisual(kind: 'point' | 'distance' | 'area', id: string) {
  const key = `${kind}:${id}`
  const matchedGroups = archivedAnalysisGroups.filter((group) =>
    group.userData.measurementKind === kind && group.userData.measurementId === id,
  )
  const mappedGroup = archivedAnalysisById.get(key)
  if (mappedGroup && !matchedGroups.includes(mappedGroup)) matchedGroups.push(mappedGroup)

  matchedGroups.forEach((group) => {
    disposeAnalysisGroup(group)
    const index = archivedAnalysisGroups.indexOf(group)
    if (index >= 0) archivedAnalysisGroups.splice(index, 1)
  })
  archivedAnalysisById.delete(key)
  hiddenMeasurementIds.delete(id)
  measurementPanelOffsets.delete(id)
  syncMeasurementBadges()
}

function rebuildArchivedAnalysisVisuals() {
  if (!scene) return
  archivedAnalysisGroups.forEach(disposeAnalysisGroup)
  archivedAnalysisGroups.splice(0)
  archivedAnalysisById.clear()

  props.analysisPoints.forEach((point, index) => {
    const id = getMeasurementId('point', index, point.id)
    archiveMeasurementGroup('point', id, createArchivedPointGroup(point))
  })
  props.analysisDistances.forEach((record, index) => {
    const id = getMeasurementId('distance', index, record.id)
    archiveMeasurementGroup('distance', id, createArchivedDistanceGroup(record))
  })
  props.analysisAreas.forEach((record, index) => {
    const id = getMeasurementId('area', index, record.id)
    archiveMeasurementGroup('area', id, createArchivedAreaGroup(record))
  })

  syncMeasurementMarkerScales()
  syncMeasurementBadges()
}

function createPolygonMetrics(points: THREE.Vector3[]) {
  if (points.length < 3) return null
  const normal = new THREE.Vector3()
  points.forEach((point, index) => {
    const next = points[(index + 1) % points.length]
    normal.x += (point.y - next.y) * (point.z + next.z)
    normal.y += (point.z - next.z) * (point.x + next.x)
    normal.z += (point.x - next.x) * (point.y + next.y)
  })
  if (normal.lengthSq() < 1e-10) return null
  normal.normalize()
  const origin = points[0].clone()
  const axisU = points[1].clone().sub(origin)
  if (axisU.lengthSq() < 1e-10) return null
  axisU.normalize()
  const axisV = normal.clone().cross(axisU).normalize()
  const projected = points.map((point) => {
    const relative = point.clone().sub(origin)
    return new THREE.Vector2(relative.dot(axisU), relative.dot(axisV))
  })
  let twiceArea = 0
  let centroidX = 0
  let centroidY = 0
  projected.forEach((point, index) => {
    const next = projected[(index + 1) % projected.length]
    const cross = point.x * next.y - next.x * point.y
    twiceArea += cross
    centroidX += (point.x + next.x) * cross
    centroidY += (point.y + next.y) * cross
  })
  const area = Math.abs(twiceArea) * .5
  if (area <= 1e-8) return null
  let perimeter = 0
  points.forEach((point, index) => { perimeter += point.distanceTo(points[(index + 1) % points.length]) })
  const centroid = origin
    .clone()
    .addScaledVector(axisU, centroidX / (3 * twiceArea))
    .addScaledVector(axisV, centroidY / (3 * twiceArea))
  return { area, centroid, perimeter, projected }
}

function projectMeasurementPoint(point: THREE.Vector3) {
  if (!camera || !viewportEl.value) return null
  const rect = viewportEl.value.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  const projected = point.clone().project(camera)
  if (projected.z < -1 || projected.z > 1) return null
  return {
    x: ((projected.x + 1) * .5) * rect.width,
    y: ((-projected.y + 1) * .5) * rect.height,
  }
}

function formatMeasurementMeters(value: number, digits = 3) {
  return `${value.toFixed(digits)} m`
}

function getMeasurementId(kind: 'point' | 'distance' | 'area', index: number, id?: string) {
  return id || `${kind}-${index}`
}

function createMeasurementId() {
  return globalThis.crypto?.randomUUID?.() || `measurement-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function syncMeasurementBadges() {
  const next: typeof measurementBadges.value = []
  props.analysisPoints.forEach((point, index) => {
    const id = getMeasurementId('point', index, point.id)
    if (hiddenMeasurementIds.has(id)) return
    const screenPoint = projectMeasurementPoint(new THREE.Vector3(point.x, point.y, point.z))
    if (!screenPoint) return
    const offset = measurementPanelOffsets.get(id) ?? { x: 0, y: 0 }
    next.push({
      id,
      kind: 'point',
      title: `定位 #${index + 1}`,
      rows: [
        { label: 'X', value: formatMeasurementMeters(point.x) },
        { label: 'Y', value: formatMeasurementMeters(point.z) },
        { label: 'Z', value: formatMeasurementMeters(point.y) },
      ],
      overlay: { visible: true, x: screenPoint.x + 14 + offset.x, y: screenPoint.y - 18 + offset.y },
    })
  })
  props.analysisDistances.forEach((record, index) => {
    const id = getMeasurementId('distance', index, record.id)
    if (hiddenMeasurementIds.has(id)) return
    const start = new THREE.Vector3(record.start.x, record.start.y, record.start.z)
    const end = new THREE.Vector3(record.end.x, record.end.y, record.end.z)
    const screenPoint = projectMeasurementPoint(start.clone().lerp(end, .5))
    if (!screenPoint) return
    const dx = record.end.x - record.start.x
    const dy = record.end.y - record.start.y
    const dz = record.end.z - record.start.z
    const horizontal = Math.hypot(dx, dz)
    const slope = horizontal <= 1e-8 ? (Math.abs(dy) <= 1e-8 ? 0 : 90) : Math.atan2(Math.abs(dy), horizontal) * 180 / Math.PI
    const offset = measurementPanelOffsets.get(id) ?? { x: 0, y: 0 }
    next.push({
      id,
      kind: 'distance',
      title: `测距 #${index + 1}`,
      mainLabel: '直线距离',
      mainValue: formatMeasurementMeters(record.distance),
      rows: [
        { label: '水平距离', value: formatMeasurementMeters(horizontal) },
        { label: '垂直距离', value: formatMeasurementMeters(Math.abs(dy)) },
        { label: '坡度', value: `${slope.toFixed(2)}°` },
      ],
      overlay: { visible: true, x: screenPoint.x + 14 + offset.x, y: screenPoint.y - 18 + offset.y },
    })
  })
  props.analysisAreas.forEach((record, index) => {
    const id = getMeasurementId('area', index, record.id)
    if (hiddenMeasurementIds.has(id)) return
    const points = record.points.map((point) => new THREE.Vector3(point.x, point.y, point.z))
    const metrics = createPolygonMetrics(points)
    const screenPoint = projectMeasurementPoint(metrics?.centroid ?? points[0])
    if (!screenPoint) return
    const offset = measurementPanelOffsets.get(id) ?? { x: 0, y: 0 }
    next.push({
      id,
      kind: 'area',
      title: `面积 #${index + 1}`,
      mainLabel: '面积',
      mainValue: `${record.area.toFixed(2)} m²`,
      rows: [{ label: '周长', value: `${record.perimeter.toFixed(2)} m` }],
      overlay: { visible: true, x: screenPoint.x + 14 + offset.x, y: screenPoint.y - 18 + offset.y },
    })
  })
  measurementBadges.value = next
}

function hideMeasurementBadge(id: string) {
  hiddenMeasurementIds.add(id)
  syncMeasurementBadges()
}

function distanceToScreenSegment(
  point: { x: number; y: number },
  start: { x: number; y: number },
  end: { x: number; y: number },
) {
  const dx = end.x - start.x
  const dy = end.y - start.y
  const lengthSquared = dx * dx + dy * dy
  if (lengthSquared <= 1e-8) return Math.hypot(point.x - start.x, point.y - start.y)
  const t = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared))
  return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy))
}

function restoreMeasurementAt(event: MouseEvent) {
  if (!viewportEl.value || !hiddenMeasurementIds.size) return
  const rect = viewportEl.value.getBoundingClientRect()
  const cursor = { x: event.clientX - rect.left, y: event.clientY - rect.top }
  let bestId = ''
  let bestDistance = 16

  props.analysisDistances.forEach((record, index) => {
    const id = getMeasurementId('distance', index, record.id)
    if (!hiddenMeasurementIds.has(id)) return
    const start = projectMeasurementPoint(new THREE.Vector3(record.start.x, record.start.y, record.start.z))
    const end = projectMeasurementPoint(new THREE.Vector3(record.end.x, record.end.y, record.end.z))
    if (!start || !end) return
    const distance = distanceToScreenSegment(cursor, start, end)
    if (distance < bestDistance) {
      bestDistance = distance
      bestId = id
    }
  })

  props.analysisPoints.forEach((point, index) => {
    const id = getMeasurementId('point', index, point.id)
    if (!hiddenMeasurementIds.has(id)) return
    const projected = projectMeasurementPoint(new THREE.Vector3(point.x, point.y, point.z))
    if (!projected) return
    const distance = Math.hypot(cursor.x - projected.x, cursor.y - projected.y)
    if (distance < bestDistance) {
      bestDistance = distance
      bestId = id
    }
  })

  props.analysisAreas.forEach((record, index) => {
    const id = getMeasurementId('area', index, record.id)
    if (!hiddenMeasurementIds.has(id)) return
    const points = record.points
      .map((point) => projectMeasurementPoint(new THREE.Vector3(point.x, point.y, point.z)))
      .filter((point): point is { x: number; y: number } => point != null)
    for (let index = 0; index < points.length; index++) {
      const distance = distanceToScreenSegment(cursor, points[index], points[(index + 1) % points.length])
      if (distance < bestDistance) {
        bestDistance = distance
        bestId = id
      }
    }
  })

  if (bestId) {
    event.preventDefault()
    event.stopPropagation()
    hiddenMeasurementIds.delete(bestId)
    syncMeasurementBadges()
  }
}

function deleteMeasurementBadge(badge: (typeof measurementBadges.value)[number]) {
  emit('analysis-delete', { kind: badge.kind, id: badge.id })
}

function moveMeasurementBadge(id: string, delta: { x: number; y: number }) {
  const previous = measurementPanelOffsets.get(id) ?? { x: 0, y: 0 }
  measurementPanelOffsets.set(id, { x: previous.x + delta.x, y: previous.y + delta.y })
  syncMeasurementBadges()
}

function resetMeasurementBadge(id: string) {
  measurementPanelOffsets.delete(id)
  syncMeasurementBadges()
}

function updateAreaVisuals(points: THREE.Vector3[], previewPoint: THREE.Vector3 | null = null) {
  const group = ensureAnalysisGroup()
  if (!group) return
  const displayedPoints = previewPoint ? [...points, previewPoint] : points

  if (!analysisAreaLine) {
    analysisAreaLine = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineDashedMaterial({ color: 0xff5a5a, dashSize: .9, gapSize: .48, transparent: true, opacity: .9, depthTest: false, depthWrite: false }),
    )
    analysisAreaLine.renderOrder = 10001
    group.add(analysisAreaLine)
  }
  while (analysisAreaMarkers.length < points.length) {
    const marker = createMeasurementPinSprite('#ff4040')
    analysisAreaMarkers.push(marker)
    group.add(marker)
  }
  const outlinePoints = displayedPoints.length > 2 ? [...displayedPoints, displayedPoints[0]] : displayedPoints
  analysisAreaLine.geometry.setFromPoints(outlinePoints)
  analysisAreaLine.computeLineDistances()
  analysisAreaLine.visible = outlinePoints.length > 1
  if (displayedPoints.length >= 3) {
    if (!analysisAreaFill) {
      analysisAreaFill = new THREE.Mesh(
        new THREE.BufferGeometry(),
        new THREE.MeshBasicMaterial({
          color: 0xff5a5a,
          transparent: true,
          opacity: .16,
          depthTest: false,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      )
      analysisAreaFill.renderOrder = 10000
      group.add(analysisAreaFill)
    }
    const metrics = createPolygonMetrics(displayedPoints)
    const triangles = metrics ? THREE.ShapeUtils.triangulateShape(metrics.projected, []) : []
    const geometry = analysisAreaFill.geometry as THREE.BufferGeometry
    geometry.setAttribute(
      'position',
      new THREE.Float32BufferAttribute(displayedPoints.flatMap((point) => [point.x, point.y, point.z]), 3),
    )
    geometry.setIndex(triangles.flat())
    geometry.computeVertexNormals()
    geometry.computeBoundingSphere()
    analysisAreaFill.visible = triangles.length > 0
  } else if (analysisAreaFill) {
    analysisAreaFill.visible = false
  }

  analysisAreaMarkers.forEach((marker, index) => {
    marker.visible = index < points.length
    if (marker.visible) {
      marker.position.copy(points[index])
      scaleMeasurementMarker(marker)
    }
  })
  requestRender()
}

function completeAreaSelection() {
  const metrics = createPolygonMetrics(analysisAreaPoints)
  if (!metrics) return
  emit('analysis-area', { id: createMeasurementId(), points: analysisAreaPoints.map((point) => ({ x: point.x, y: point.y, z: point.z })), area: metrics.area, perimeter: metrics.perimeter })
  updateAreaVisuals(analysisAreaPoints)
  if (analysisVisualGroup) archivedAnalysisGroups.push(analysisVisualGroup)
  analysisVisualGroup = null
  analysisAreaPoints = []
  analysisAreaLine = null
  analysisAreaFill = null
  analysisAreaMarkers = []
}

function pickAnalysisPoint(event: PointerEvent) {
  if (!camera || !scene) return null
  const rect = viewportEl.value?.getBoundingClientRect()
  if (!rect) return null
  const mouse = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  )

  raycaster = raycaster || new THREE.Raycaster()
  raycaster.setFromCamera(mouse, camera)

  const targets: THREE.Object3D[] = []
  if (bimRoot) targets.push(bimRoot)
  if (tileset?.group) targets.push(tileset.group)
  if (c2mMeshRoot) targets.push(c2mMeshRoot)
  const intersects = raycaster.intersectObjects(targets, true)
  const picked = intersects[0]?.point.clone() ?? null
  if (!picked) return null

  const screenX = event.clientX - rect.left
  const screenY = event.clientY - rect.top
  const candidates: THREE.Vector3[] = []
  const addPoint = (point: AnalysisPoint | null | undefined) => {
    if (point) candidates.push(new THREE.Vector3(point.x, point.y, point.z))
  }
  props.analysisPoints.forEach(addPoint)
  props.analysisDistances.forEach((record) => {
    addPoint(record.start)
    addPoint(record.end)
  })
  props.analysisAreas.forEach((record) => record.points.forEach(addPoint))
  analysisAreaPoints.forEach((point) => candidates.push(point.clone()))
  if (analysisAnchorPoint) candidates.push(analysisAnchorPoint.clone())

  let snappedPoint: THREE.Vector3 | null = null
  let minDistance = 18
  candidates.forEach((candidate) => {
    const projected = candidate.clone().project(camera!)
    if (projected.z < -1 || projected.z > 1) return
    const candidateX = ((projected.x + 1) * .5) * rect.width
    const candidateY = ((-projected.y + 1) * .5) * rect.height
    const distance = Math.hypot(candidateX - screenX, candidateY - screenY)
    if (distance <= minDistance) {
      minDistance = distance
      snappedPoint = candidate
    }
  })
  return snappedPoint ?? picked
}

function updateDistanceVisuals(start: THREE.Vector3, end: THREE.Vector3 | null, preview = false) {
  const group = ensureAnalysisGroup()
  if (!group) return
  if (!analysisDistanceLine) {
    analysisDistanceLine = createDistanceLine(start, start)
    group.add(analysisDistanceLine)
  }
  if (!analysisDistanceStartMarker) {
    analysisDistanceStartMarker = createMeasurementPinSprite('#ff4040')
    group.add(analysisDistanceStartMarker)
  }
  analysisDistanceStartMarker.position.copy(start)
  analysisDistanceStartMarker.visible = true
  scaleMeasurementMarker(analysisDistanceStartMarker)
  if (!end) { analysisDistanceLine.visible = false; return }
  if (!analysisDistanceEndMarker) {
    analysisDistanceEndMarker = createMeasurementPinSprite('#ff5a5a', .96)
    group.add(analysisDistanceEndMarker)
  }
  analysisDistanceEndMarker.position.copy(end)
  analysisDistanceEndMarker.visible = true
  scaleMeasurementMarker(analysisDistanceEndMarker)
  analysisDistanceLine.geometry.setPositions([start.x, start.y, start.z, end.x, end.y, end.z])
  analysisDistanceLine.computeLineDistances()
  analysisDistanceLine.visible = true
  if (preview) {
    if (!analysisDistanceHoverMarker) {
      analysisDistanceHoverMarker = createMeasurementPinSprite('#ff7b7b', .74)
      group.add(analysisDistanceHoverMarker)
    }
    analysisDistanceHoverMarker.position.copy(end)
    analysisDistanceHoverMarker.visible = true
    scaleMeasurementMarker(analysisDistanceHoverMarker)
  } else if (analysisDistanceHoverMarker) analysisDistanceHoverMarker.visible = false
}

function completeDistanceSelection(end: THREE.Vector3) {
  if (!analysisAnchorPoint) return
  const start = analysisAnchorPoint.clone()
  const dx = end.x - start.x
  const dy = end.y - start.y
  const dz = end.z - start.z
  const horizontalDistance = Math.hypot(dx, dz)
  const verticalDistance = Math.abs(dy)
  const slopeDegrees = horizontalDistance <= 1e-8
    ? (verticalDistance <= 1e-8 ? 0 : 90)
    : Math.atan2(verticalDistance, horizontalDistance) * 180 / Math.PI
  updateDistanceVisuals(start, end)
  emit('analysis-distance', {
    id: createMeasurementId(),
    distance: start.distanceTo(end),
    start: { x: start.x, y: start.y, z: start.z },
    end: { x: end.x, y: end.y, z: end.z },
    heightDifference: verticalDistance,
    horizontalDistance,
    verticalDistance,
    slopeDegrees,
  })
  if (analysisVisualGroup) archivedAnalysisGroups.push(analysisVisualGroup)
  analysisVisualGroup = null
  analysisAnchorPoint = null
  analysisDistanceLine = null
  analysisDistanceStartMarker = null
  analysisDistanceEndMarker = null
  analysisDistanceHoverMarker = null
}

function commitAnalysisPoint(hitPoint: THREE.Vector3) {
  if (props.analysisMode === 'locate') {
    const group = ensureAnalysisGroup()
    if (!group) return
    const marker = createMeasurementPinSprite('#22d3ee')
    marker.position.copy(hitPoint)
    marker.visible = true
    scaleMeasurementMarker(marker)
    group.add(marker)
    emit('analysis-point', { id: createMeasurementId(), x: hitPoint.x, y: hitPoint.y, z: hitPoint.z })
    archivedAnalysisGroups.push(group)
    analysisVisualGroup = null
    return
  }
  if (props.analysisMode === 'distance') {
    if (!analysisAnchorPoint) {
      analysisAnchorPoint = hitPoint.clone()
      updateDistanceVisuals(hitPoint, null)
    } else {
      completeDistanceSelection(hitPoint)
    }
    return
  }
  if (props.analysisMode === 'area') {
    const closeThreshold = Math.max(.15, (camera?.position.distanceTo(hitPoint) ?? 1) * .025)
    if (analysisAreaPoints.length >= 3 && hitPoint.distanceTo(analysisAreaPoints[0]) < closeThreshold) {
      completeAreaSelection()
    } else {
      analysisAreaPoints.push(hitPoint.clone())
      updateAreaVisuals(analysisAreaPoints)
    }
  }
}

function handleAnalysisPointerDown(event: PointerEvent) {
  if (props.analysisMode === 'none' || event.button !== 0) return
  analysisPointerDown = { x: event.clientX, y: event.clientY }
}

function handleAnalysisPointerMove(event: PointerEvent) {
  if (props.analysisMode === 'none' || event.buttons !== 0) return
  const hitPoint = pickAnalysisPoint(event)
  if (!hitPoint) return
  if (props.analysisMode === 'distance' && analysisAnchorPoint) updateDistanceVisuals(analysisAnchorPoint, hitPoint, true)
  if (props.analysisMode === 'area' && analysisAreaPoints.length) updateAreaVisuals(analysisAreaPoints, hitPoint)
}

function handleAnalysisPointerUp(event: PointerEvent) {
  const pointerDown = analysisPointerDown
  analysisPointerDown = null
  if (!pointerDown || props.analysisMode === 'none') return
  if (Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y) > 6) return
  const hitPoint = pickAnalysisPoint(event)
  if (hitPoint) commitAnalysisPoint(hitPoint)
}

function handleAnalysisKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && props.analysisMode === 'area') completeAreaSelection()
  if (event.key === 'Escape') {
    cancelActiveAnalysis()
    if (props.analysisMode !== 'none') emit('analysis-mode-exit', props.analysisMode)
  }
}

function cancelActiveAnalysis() {
  disposeAnalysisGroup(analysisVisualGroup)
  analysisVisualGroup = null
  analysisAnchorPoint = null
  analysisAreaPoints = []
  analysisAreaLine = null
  analysisAreaFill = null
  analysisAreaMarkers = []
  analysisDistanceLine = null
  analysisDistanceStartMarker = null
  analysisDistanceEndMarker = null
  analysisDistanceHoverMarker = null
  analysisPointerDown = null
}

// ---------------------------
// 资源加载：BIM / 点云 / C2M
// ---------------------------
function cleanCurrentSceneModels() {
  if (bimRoot && scene) {
    scene.remove(bimRoot)
    bimRoot.traverse((c: any) => {
      c.geometry?.dispose?.()
      if (Array.isArray(c.material)) c.material.forEach((m: any) => m?.dispose?.())
      else c.material?.dispose?.()
    })
    bimRoot = null
  }
  if (pointcloudWrapper && scene) {
    scene.remove(pointcloudWrapper)
    pointcloudWrapper = null
  }
  if (tileset) {
    tileset.dispose()
    tileset = null
  }
  if (c2mMeshRoot && scene) {
    scene.remove(c2mMeshRoot)
    c2mMeshRoot.traverse((c: any) => {
      c.geometry?.dispose?.()
      c.material?.dispose?.()
    })
    c2mMeshRoot = null
  }
  clearAnalysisVisuals()
}

async function loadBimModel(assetId: number) {
  loaded.value = false
  emit('loaded-change', false)

  const res = await getAssetDetail(assetId)
  const assetDetail = res.data
  if (!assetDetail?.glbUrl) throw new Error('BIM 模型资源尚未就绪')

  const loader = new GLTFLoader()
  const dracoLoader = new DRACOLoader()
  dracoLoader.setDecoderPath('/draco/')
  dracoLoader.preload()
  loader.setDRACOLoader(dracoLoader)
  loader.setRequestHeader(
    createUploadHeaders({ Accept: 'model/gltf-binary,application/octet-stream,*/*' }),
  )

  try {
    const gltf = await loader.loadAsync(getBimGlbUrl(assetDetail.glbUrl))

    const root = gltf.scene || gltf.scenes[0]
    root.name = 'bim-model-root'

    // 保存初始包围盒与中心点
    const sourceBox = new THREE.Box3().setFromObject(root)
    if (!sourceBox.isEmpty()) {
      bimSourceCenter.copy(sourceBox.getCenter(new THREE.Vector3()))
      root.worldToLocal(bimSourceCenter)
      hasBimSourceCenter = true
    } else {
      bimSourceCenter.set(0, 0, 0)
      hasBimSourceCenter = false
    }
    bimSourceMatrix.copy(root.matrix)

    // 应用材质与线框
    root.traverse((obj: any) => {
      if (obj.isMesh && obj.material) {
        if (!originalMaterialStore.has(obj)) {
          originalMaterialStore.set(obj, obj.material)
        }
        obj.material.wireframe = wireframeEnabled
        obj.material.clippingPlanes = sectionEnabled ? clipPlanes : []
        obj.material.clipShadows = true
      }
    })

    bimRoot = root
    scene?.add(bimRoot)

    const calibrated = applyCalibrationToBim(false)
    if (!calibrated) {
      // 若无校准矩阵且为独立 BIM 预览，按包围盒中心回退居中
      const center = sourceBox.getCenter(new THREE.Vector3())
      root.position.sub(center)
      root.updateMatrixWorld(true)
    }

    // 重设剖切盒底边界
    const modelBox = new THREE.Box3().setFromObject(root)
    setSectionState({ box: modelBox })

    // 聚焦镜头
    fitCameraToBox(modelBox)

    loaded.value = true
    statusText.value = ''
    loadError.value = ''
    emit('loaded-change', true)
  } catch (err: any) {
    loadError.value = `BIM 加载失败: ${err.message || err}`
    loaded.value = false
    emit('loaded-change', false)
  } finally {
    dracoLoader.dispose()
  }
}

async function loadPointcloudModel(assetId: number) {
  loaded.value = false
  emit('loaded-change', false)
  pointcloudIntensityHistogram = Array.from({ length: 64 }, () => 0)
  pointcloudHasIntensity = false
  pointcloudHasRgb = false

  const res = await getAssetDetail(assetId)
  const detail = res.data
  if (!detail?.tilesetUrl) throw new Error('点云切片尚未就绪')

  const url = getPointcloudTilesetUrl(detail.tilesetUrl)
  const nextTileset = new TilesRenderer(url)
  nextTileset.displayActiveTiles = true
  nextTileset.errorTarget = 32.0
  nextTileset.downloadQueue.maxJobs = 8
  nextTileset.parseQueue.maxJobs = 2
  nextTileset.fetchOptions = {
    headers: createUploadHeaders({ Accept: '*/*' }),
  }

  const dracoLoader = new DRACOLoader(nextTileset.manager)
  dracoLoader.setDecoderPath('/draco/')
  dracoLoader.preload()
  nextTileset.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader }))

  if (camera) nextTileset.setCamera(camera)
  if (renderer && camera) nextTileset.setResolutionFromRenderer?.(camera, renderer)

  const wrapper = new THREE.Group()
  wrapper.name = 'pointcloud-tileset-wrapper'
  wrapper.rotation.x = -Math.PI / 2
  wrapper.add(nextTileset.group)
  scene?.add(wrapper)

  tileset = nextTileset
  pointcloudWrapper = wrapper

  tileset.addEventListener('tiles-load-start', () => {
    tilesLoadingCount++
  })

  tileset.addEventListener('tiles-load-end', () => {
    tilesLoadingCount = Math.max(0, tilesLoadingCount - 1)
    if (tilesLoadingCount === 0 && !loaded.value) {
      loaded.value = true
      emit('loaded-change', true)
      statusText.value = ''
    }
  })

  tileset.addEventListener('load-model', ({ scene: tileScene }: any) => {
    if (!tileScene) return
    collectPointcloudColorStats(tileScene)
    applyPointcloudMaterial(tileScene)
  })

  // 完全对齐校准页的视錐与包围球聚焦定位
  tileset.addEventListener('load-root-tileset', () => {
    if (!camera || !controls || !nextTileset) return
    const sphere = new THREE.Sphere()
    if (nextTileset.getBoundingSphere?.(sphere)) {
      nextTileset.group.updateMatrixWorld(true)
      const worldSphere = sphere.clone()
      worldSphere.center.applyMatrix4(nextTileset.group.matrixWorld)
      fitCameraToRadius(worldSphere.radius, worldSphere.center)
      const box = new THREE.Box3().setFromCenterAndSize(
        worldSphere.center,
        new THREE.Vector3(worldSphere.radius * 2, worldSphere.radius * 2, worldSphere.radius * 2),
      )
      setSectionState({ box })
    }
  })
}

function getPointAttribute(geometry: THREE.BufferGeometry, names: string[]) {
  const attributes = geometry.attributes as Record<string, THREE.BufferAttribute>
  const entry = Object.entries(attributes).find(([name]) =>
    names.includes(name.toLowerCase()),
  )
  return entry?.[1] ?? null
}

function getPointScalarSource(geometry: THREE.BufferGeometry) {
  const intensity = getPointAttribute(geometry, ['intensity', '_intensity', 'scalar_intensity'])
  if (intensity?.count) {
    return {
      count: intensity.count,
      valueAt: (index: number) => intensity.getX(index),
    }
  }

  const originalColor = originalPointColors.get(geometry) ??
    (geometry.getAttribute('color') as THREE.BufferAttribute | undefined)
  if (originalColor?.count) {
    return {
      count: originalColor.count,
      valueAt: (index: number) =>
        originalColor.getX(index) * 0.2126 +
        originalColor.getY(index) * 0.7152 +
        originalColor.getZ(index) * 0.0722,
    }
  }

  const position = geometry.getAttribute('position') as THREE.BufferAttribute | undefined
  if (!position?.count) return null
  return {
    count: position.count,
    valueAt: (index: number) => position.getY(index),
  }
}

function scalarBounds(source: { count: number; valueAt: (index: number) => number }, stride = 1) {
  let min = Infinity
  let max = -Infinity
  for (let index = 0; index < source.count; index += stride) {
    const value = source.valueAt(index)
    if (!Number.isFinite(value)) continue
    min = Math.min(min, value)
    max = Math.max(max, value)
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 }
  return { min, max }
}

function collectPointcloudColorStats(root: THREE.Object3D) {
  root.traverse((object) => {
    const points = object as THREE.Points
    if (!points.isPoints) return
    const geometry = points.geometry as THREE.BufferGeometry
    if (geometry.getAttribute('color')) pointcloudHasRgb = true
    const scalar = getPointScalarSource(geometry)
    if (!scalar) return
    pointcloudHasIntensity = true
    const stride = Math.max(1, Math.ceil(scalar.count / 50000))
    const { min, max } = scalarBounds(scalar, stride)
    const span = Math.max(1e-9, max - min)
    for (let i = 0; i < scalar.count; i += stride) {
      const value = scalar.valueAt(i)
      if (!Number.isFinite(value)) continue
      const bin = Math.min(63, Math.max(0, Math.floor(((value - min) / span) * 64)))
      pointcloudIntensityHistogram[bin] += 1
    }
  })
  emit('pointcloud-color-stats', {
    histogram: [...pointcloudIntensityHistogram],
    hasIntensity: pointcloudHasIntensity,
    hasRgb: pointcloudHasRgb,
  })
}

function samplePointcloudRamp(value: number): [number, number, number] {
  const t = THREE.MathUtils.clamp(value, 0, 1)
  if (pointcloudColorRamp === 'grayscale') return [t, t, t]
  if (pointcloudColorRamp === 'viridis') {
    const stops = [
      [0, 0.267, 0.005, 0.329],
      [0.25, 0.283, 0.141, 0.458],
      [0.5, 0.128, 0.567, 0.551],
      [0.75, 0.37, 0.789, 0.383],
      [1, 0.993, 0.906, 0.144],
    ]
    const upper = stops.findIndex((stop) => t <= stop[0])
    const b = stops[Math.max(1, upper < 0 ? stops.length - 1 : upper)]
    const a = stops[Math.max(0, (upper < 0 ? stops.length - 1 : upper) - 1)]
    const mix = (t - a[0]) / Math.max(1e-6, b[0] - a[0])
    return [
      THREE.MathUtils.lerp(a[1], b[1], mix),
      THREE.MathUtils.lerp(a[2], b[2], mix),
      THREE.MathUtils.lerp(a[3], b[3], mix),
    ]
  }
  const hue = (1 - t) * 240
  const color = new THREE.Color().setHSL(hue / 360, 1, 0.5)
  return [color.r, color.g, color.b]
}

function applyPointcloudColoring(obj: THREE.Points, material: THREE.PointsMaterial) {
  const geometry = obj.geometry as THREE.BufferGeometry
  if (!originalPointColors.has(geometry)) {
    const original = geometry.getAttribute('color') as THREE.BufferAttribute | undefined
    originalPointColors.set(geometry, original?.clone() ?? null)
  }

  if (pointColorOverride) {
    material.color.set(pointColorOverride)
    material.vertexColors = false
    return
  }

  if (pointcloudColorMode === 'intensity') {
    const scalar = getPointScalarSource(geometry)
    if (scalar) {
      const { min, max } = scalarBounds(scalar)
      const span = Math.max(1e-9, max - min)
      const displaySpan = Math.max(0.01, pointcloudColorRange.max - pointcloudColorRange.min)
      const colors = new Float32Array(scalar.count * 3)
      for (let i = 0; i < scalar.count; i++) {
        const normalized = (scalar.valueAt(i) - min) / span
        const displayed = (normalized - pointcloudColorRange.min) / displaySpan
        const [r, g, b] = samplePointcloudRamp(displayed)
        colors[i * 3] = r
        colors[i * 3 + 1] = g
        colors[i * 3 + 2] = b
      }
      geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
      material.color.set(0xffffff)
      material.vertexColors = true
      material.needsUpdate = true
      return
    }
  }

  const original = originalPointColors.get(geometry)
  if (original) geometry.setAttribute('color', original)
  else geometry.deleteAttribute('color')
  material.color.set(0xffffff)
  material.vertexColors = Boolean(original)
  material.needsUpdate = true
}

// 核心：100% 对齐校准页点云材质（sizeAttenuation=false, 2.5px圆点，禁用色调映射）
function applyPointcloudMaterial(root: THREE.Object3D) {
  root.traverse((obj: any) => {
    if (obj.isPoints && obj.material) {
      if (!originalMaterialStore.has(obj)) {
        originalMaterialStore.set(obj, obj.material)
      }
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material]
      mats.forEach((mat: any) => {
        if (!mat) return
        mat.fog = false
        mat.toneMapped = false
        mat.depthWrite = true
        mat.depthTest = true
        mat.sizeAttenuation = false
        mat.size = pointSize

        applyPointcloudColoring(obj as THREE.Points, mat as THREE.PointsMaterial)
        mat.clippingPlanes = sectionEnabled ? clipPlanes : []

        if (!mat.userData?.roundPointsHooked) {
          mat.userData = mat.userData || {}
          mat.userData.roundPointsHooked = true
          mat.onBeforeCompile = (shader: any) => {
            shader.fragmentShader = shader.fragmentShader.replace(
              '#include <clipping_planes_fragment>',
              `#include <clipping_planes_fragment>
              if (length(gl_PointCoord - vec2(0.5)) > 0.5) discard;`
            )
          }
        }
        mat.needsUpdate = true
      })
    }
  })
}

async function loadC2MModel() {
  loaded.value = false
  emit('loaded-change', false)

  let plyUrl = ''
  if (props.scanAssetId && props.bimAssetId) {
    plyUrl = getC2MColoredPlyUrl(props.scanAssetId, props.bimAssetId)
  }

  if (!plyUrl) {
    loadError.value = '缺少 C2M 资产 ID'
    return
  }

  try {
    const blob = await backendRequest<Blob>(plyUrl, { method: 'GET', responseType: 'blob' })
    const objectUrl = URL.createObjectURL(blob)
    const loader = new PLYLoader()
    const geometry = await loader.loadAsync(objectUrl)
    URL.revokeObjectURL(objectUrl)

    if (!geometry.attributes.normal) geometry.computeVertexNormals()

    geometry.computeBoundingBox()
    const center = geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3()
    geometry.translate(-center.x, -center.y, -center.z)

    const material = new THREE.MeshStandardMaterial({
      vertexColors: !!geometry.attributes.color,
      roughness: 0.45,
      metalness: 0.1,
      side: THREE.DoubleSide,
      clippingPlanes: sectionEnabled ? clipPlanes : [],
    })

    const group = new THREE.Group()
    group.name = 'c2m-result-mesh'
    group.add(new THREE.Mesh(geometry, material))

    c2mMeshRoot = group
    scene?.add(group)

    applyBimWorldPose()

    const box = new THREE.Box3().setFromObject(group)
    fitCameraToBox(box)
    setSectionState({ box })

    loaded.value = true
    statusText.value = ''
    loadError.value = ''
    emit('loaded-change', true)
  } catch (err: any) {
    loadError.value = `C2M 结果加载失败: ${err.message || err}`
    loaded.value = false
  }
}

function getCalibrationWorldMatrix(): THREE.Matrix4 | null {
  const values = props.calibration?.modelMatrix
  if (!Array.isArray(values) || values.length !== 16) return null
  const modelMatrix = new THREE.Matrix4().fromArray(values)
  if (!modelMatrix.elements.every(Number.isFinite)) return null
  return new THREE.Matrix4()
    .makeRotationX(-Math.PI / 2)
    .multiply(modelMatrix.invert())
}

function applyCalibrationToBim(refitCamera = false): boolean {
  if (!bimRoot) return false
  const desired = getCalibrationWorldMatrix()
  if (!desired) {
    bimRoot.matrixAutoUpdate = true
    bimRoot.matrix.copy(bimSourceMatrix)
    bimSourceMatrix.decompose(bimRoot.position, bimRoot.quaternion, bimRoot.scale)
    bimRoot.updateMatrixWorld(true)
    return false
  }

  bimRoot.matrixAutoUpdate = false
  bimRoot.matrix.copy(
    props.fusionMode ? desired : desired.clone().multiply(bimSourceMatrix),
  )
  bimRoot.matrixWorldNeedsUpdate = true
  bimRoot.updateMatrixWorld(true)

  if (refitCamera && camera && controls) {
    const box = new THREE.Box3().setFromObject(bimRoot)
    fitCameraToBox(box)
  }
  return true
}

function fitCameraToBox(box: THREE.Box3) {
  if (!camera || !controls || box.isEmpty()) return
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z)
  const fov = THREE.MathUtils.degToRad(camera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)

  controls.target.copy(center)
  camera.position.set(center.x, center.y + maxDim * 0.15, center.z + distance * 2.2)
  camera.near = Math.max(0.01, distance / 100)
  camera.far = Math.max(5000, distance * 200)
  camera.updateProjectionMatrix()
  controls.update()
  emitCameraPose()
}

// 对齐校准页的全局包围球视点定位算法
function fitCameraToRadius(radius: number, center = new THREE.Vector3()) {
  if (!camera || !controls) return
  const safeRadius = Math.max(radius, 1)
  const maxDim = safeRadius * 2
  const fov = THREE.MathUtils.degToRad(camera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)

  controls.target.copy(center)
  camera.position.set(center.x, center.y + maxDim * 0.15, center.z + distance * 2.2)
  camera.near = Math.max(0.01, distance / 100)
  camera.far = Math.max(5000, distance * 200)
  camera.updateProjectionMatrix()
  controls.update()
  emitCameraPose()
}

// ---------------------------
// 统一组件生命周期管理
// ---------------------------
async function reload() {
  cleanCurrentSceneModels()
  loadError.value = ''
  const currentToken = ++loadToken

  try {
    if (props.type === 'bim' && props.assetId) {
      await loadBimModel(props.assetId)
    } else if (props.type === 'pointcloud' && (props.assetId || props.pointcloudAssetId)) {
      await loadPointcloudModel((props.assetId || props.pointcloudAssetId)!)
    } else if (props.type === 'c2m') {
      await loadC2MModel()
    } else if (props.type === 'hybrid') {
      if (props.bimAssetId) await loadBimModel(props.bimAssetId)
      const pcId = props.scanAssetId || props.pointcloudAssetId || props.assetId
      if (pcId) {
        await loadPointcloudModel(pcId)
      }
    }
  } catch (err: any) {
    if (currentToken === loadToken) {
      loadError.value = err.message || '加载模型失败'
    }
  } finally {
    if (currentToken === loadToken) rebuildArchivedAnalysisVisuals()
  }
}

function initViewer() {
  if (!viewportEl.value) return
  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 800))
  const height = Math.max(1, Math.floor(rect.height || 600))

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(50, width / height, 0.01, 5000)
  camera.position.set(0, 1.5, 4)

  // 严格对齐校准页 WebGL 渲染管线配置
  renderer = new THREE.WebGLRenderer({
    antialias: true,
    preserveDrawingBuffer: true,
    alpha: true,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
  renderer.setSize(width, height)
  renderer.localClippingEnabled = true
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.0
  if ('outputColorSpace' in renderer) {
    renderer.outputColorSpace = THREE.SRGBColorSpace
  }

  viewportEl.value.appendChild(renderer.domElement)

  // 初始化纯 WebGL 的 EDL 渲染管线
  edlPipeline = new PointCloudEdlPipeline(renderer, {
    enabled: localEdlEnabled.value,
    strength: localEdlStrength.value,
    radius: 1.0,
  })

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = false
  controls.addEventListener('change', () => {
    emitCameraPose()
    syncMeasurementBadges()
  })

  // 交互事件监听
  renderer.domElement.addEventListener('pointerdown', handleAnalysisPointerDown)
  renderer.domElement.addEventListener('pointermove', handleAnalysisPointerMove)
  renderer.domElement.addEventListener('pointerup', handleAnalysisPointerUp)
  renderer.domElement.addEventListener('contextmenu', restoreMeasurementAt)
  window.addEventListener('keydown', handleAnalysisKeydown)

  initLights()
  syncSceneBackground()

  axesHelper = new THREE.AxesHelper(15)
  axesHelper.visible = showAxesEnabled
  scene.add(axesHelper)

  // 完全对齐校准页网格参数
  gridHelper = new THREE.GridHelper(10000, 2000, gridColor, gridColor)
  ;(gridHelper.material as THREE.LineBasicMaterial).transparent = true
  ;(gridHelper.material as THREE.LineBasicMaterial).opacity = 0.62
  gridHelper.position.set(0, -10.01, 0)
  gridHelper.visible = showGridEnabled
  scene.add(gridHelper)

  buildClipHandles()

  // 视口监听
  resizeObserver = new ResizeObserver(() => {
    if (!renderer || !camera || !viewportEl.value) return
    const nextRect = viewportEl.value.getBoundingClientRect()
    const nw = Math.max(1, Math.floor(nextRect.width || 1))
    const nh = Math.max(1, Math.floor(nextRect.height || 1))
    const dpr = Math.min(window.devicePixelRatio || 1, dprCap)
    renderer.setPixelRatio(dpr)
    renderer.setSize(nw, nh, false)
    camera.aspect = nw / nh
    camera.updateProjectionMatrix()
    edlPipeline?.setSize(nw, nh)
    if (tileset && camera) tileset.setResolutionFromRenderer?.(camera, renderer)
    syncMeasurementMarkerScales()
    syncMeasurementBadges()
  })
  resizeObserver.observe(viewportEl.value)

  requestRender()
}

function cleanup() {
  stopRenderLoop()
  resizeObserver?.disconnect()
  cleanCurrentSceneModels()

  edlPipeline?.dispose()
  edlPipeline = null

  if (renderer) {
    renderer.domElement.removeEventListener('pointerdown', handleAnalysisPointerDown)
    renderer.domElement.removeEventListener('pointermove', handleAnalysisPointerMove)
    renderer.domElement.removeEventListener('pointerup', handleAnalysisPointerUp)
    renderer.domElement.removeEventListener('contextmenu', restoreMeasurementAt)
    renderer.dispose()
    renderer.domElement.remove()
    renderer = null
  }
  window.removeEventListener('keydown', handleAnalysisKeydown)
  scene = null
  camera = null
  controls = null
}

// ---------------------------
// 外部调用 API (defineExpose)
// ---------------------------
function setEdlEnabled(enabled: boolean) {
  localEdlEnabled.value = enabled
  edlPipeline?.setEnabled(enabled)
  try {
    window.localStorage.setItem(EDL_STORAGE_KEY, enabled ? '1' : '0')
  } catch {}
}

function setEdlStrength(strength: number) {
  const clamped = Math.min(1, Math.max(0, strength))
  localEdlStrength.value = clamped
  edlPipeline?.setStrength(clamped)
  try {
    window.localStorage.setItem(EDL_STRENGTH_STORAGE_KEY, String(clamped))
  } catch {}
}

function setShowAxes(show: boolean) {
  showAxesEnabled = show
  if (axesHelper) axesHelper.visible = show
}

function setShowGrid(show: boolean) {
  showGridEnabled = show
  if (gridHelper) gridHelper.visible = show
}

function setGridColor(color: string) {
  gridColor = color
  if (!gridHelper) return

  const rgb = new THREE.Color(color)
  const colors = gridHelper.geometry.getAttribute('color')
  if (colors) {
    for (let index = 0; index < colors.count; index += 1) {
      colors.setXYZ(index, rgb.r, rgb.g, rgb.b)
    }
    colors.needsUpdate = true
  }
}

function setWireframe(wireframe: boolean) {
  wireframeEnabled = wireframe
  if (bimRoot) {
    bimRoot.traverse((c: any) => {
      if (c.isMesh && c.material) {
        c.material.wireframe = wireframe
      }
    })
  }
}

function setPointColor(color: string | null) {
  pointColorOverride = color
  if (tileset?.group) {
    applyPointcloudMaterial(tileset.group)
  }
}

function setPointcloudColorDisplay(
  mode: PointcloudColorMode,
  ramp: PointcloudColorRamp,
  range: PointcloudColorRange,
) {
  pointColorOverride = null
  pointcloudColorMode = mode
  pointcloudColorRamp = ramp
  pointcloudColorRange = {
    min: THREE.MathUtils.clamp(range.min, 0, 1),
    max: THREE.MathUtils.clamp(range.max, 0, 1),
  }
  if (tileset?.group) applyPointcloudMaterial(tileset.group)
}

function setPointSize(size: number) {
  pointSize = Math.min(5, Math.max(1, size))
  if (tileset?.group) {
    applyPointcloudMaterial(tileset.group)
  }
}

function setStandardView(view: StandardView) {
  if (!camera || !controls) return

  const distance = Math.max(camera.position.distanceTo(controls.target), 1)
  const directions: Record<StandardView, THREE.Vector3> = {
    front: new THREE.Vector3(0, 0, 1),
    back: new THREE.Vector3(0, 0, -1),
    left: new THREE.Vector3(-1, 0, 0),
    right: new THREE.Vector3(1, 0, 0),
    top: new THREE.Vector3(0, 1, 0),
    bottom: new THREE.Vector3(0, -1, 0),
  }

  camera.up.set(0, view === 'top' ? 0 : view === 'bottom' ? 0 : 1, view === 'top' ? -1 : view === 'bottom' ? 1 : 0)
  camera.position.copy(controls.target).addScaledVector(directions[view], distance)
  camera.lookAt(controls.target)
  controls.update()
  emitCameraPose()
}

function setViewDirection(direction: [number, number, number]) {
  if (!camera || !controls) return
  const next = new THREE.Vector3(...direction)
  if (next.lengthSq() < 1e-8) return
  const distance = Math.max(camera.position.distanceTo(controls.target), 1)
  next.normalize()
  camera.up.set(0, Math.abs(next.y) > 0.98 ? 0 : 1, next.y > 0.98 ? -1 : next.y < -0.98 ? 1 : 0)
  camera.position.copy(controls.target).addScaledVector(next, distance)
  camera.lookAt(controls.target)
  controls.update()
  emitCameraPose()
}

function rollView(direction: -1 | 1) {
  if (!camera || !controls) return
  const axis = camera.position.clone().sub(controls.target).normalize()
  camera.up.applyQuaternion(
    new THREE.Quaternion().setFromAxisAngle(axis, direction * Math.PI / 2),
  ).normalize()
  camera.lookAt(controls.target)
  controls.update()
  emitCameraPose()
}

function resetView() {
  if (tileset) {
    const sphere = new THREE.Sphere()
    if (tileset.getBoundingSphere?.(sphere)) {
      tileset.group.updateMatrixWorld(true)
      const worldSphere = sphere.clone()
      worldSphere.center.applyMatrix4(tileset.group.matrixWorld)
      fitCameraToRadius(worldSphere.radius, worldSphere.center)
      return
    }
  }
  let targetBox = new THREE.Box3()
  if (bimRoot) targetBox.union(new THREE.Box3().setFromObject(bimRoot))
  if (pointcloudWrapper) targetBox.union(new THREE.Box3().setFromObject(pointcloudWrapper))
  if (c2mMeshRoot) targetBox.union(new THREE.Box3().setFromObject(c2mMeshRoot))
  if (!targetBox.isEmpty()) {
    fitCameraToBox(targetBox)
  }
}

function getCameraPose(): CameraPose | null {
  if (!camera || !controls) return null
  return {
    camera: camera.position.clone(),
    target: controls.target.clone(),
  }
}

function setCameraPose(pose: CameraPose | null) {
  if (!camera || !controls || !pose) return
  camera.position.copy(pose.camera)
  controls.target.copy(pose.target)
  camera.lookAt(controls.target)
  controls.update()
}

function syncFromExternalPose(pose: CameraPose | null) {
  setCameraPose(pose)
}

function getCameraDistance(): number {
  if (!camera || !controls) return 1
  return camera.position.distanceTo(controls.target)
}

function getCameraOrientation(): CameraRotation {
  if (!camera || !controls) return { lon: 0, lat: 0 }
  const offset = camera.position.clone().sub(controls.target)
  const radius = offset.length()
  const lat = 90 - THREE.MathUtils.radToDeg(Math.acos(Math.max(-1, Math.min(1, offset.y / radius))))
  const lon = THREE.MathUtils.radToDeg(Math.atan2(offset.x, offset.z))
  return { lon, lat }
}

function syncFromRotation(deltaLon: number, deltaLat: number) {
  if (!camera || !controls) return
  const offset = camera.position.clone().sub(controls.target)
  const radius = offset.length()
  let phi = Math.acos(Math.max(-1, Math.min(1, offset.y / radius))) - THREE.MathUtils.degToRad(deltaLat)
  let theta = Math.atan2(offset.x, offset.z) + THREE.MathUtils.degToRad(deltaLon)
  phi = Math.max(0.01, Math.min(Math.PI - 0.01, phi))
  offset.x = radius * Math.sin(phi) * Math.sin(theta)
  offset.y = radius * Math.cos(phi)
  offset.z = radius * Math.sin(phi) * Math.cos(theta)
  camera.position.copy(controls.target).add(offset)
  camera.lookAt(controls.target)
  controls.update()
}

function syncFromCameraDistance(scale: number) {
  if (!camera || !controls || scale <= 0) return
  const offset = camera.position.clone().sub(controls.target)
  offset.multiplyScalar(scale)
  camera.position.copy(controls.target).add(offset)
  camera.lookAt(controls.target)
  controls.update()
}

function getModelWorldPose() {
  if (!bimRoot) return null
  bimRoot.updateMatrixWorld(true)
  const position = hasBimSourceCenter
    ? bimSourceCenter.clone().applyMatrix4(bimRoot.matrixWorld)
    : bimRoot.getWorldPosition(new THREE.Vector3())
  return {
    position,
    quaternion: bimRoot.getWorldQuaternion(new THREE.Quaternion()),
    scale: bimRoot.getWorldScale(new THREE.Vector3()),
  }
}

function applyBimWorldPose(
  pose?: { position: THREE.Vector3; quaternion: THREE.Quaternion; scale: THREE.Vector3 } | null,
) {
  const targetPose = pose || props.bimWorldPose
  if (!c2mMeshRoot) return
  if (targetPose) {
    c2mMeshRoot.matrixAutoUpdate = true
    c2mMeshRoot.position.copy(targetPose.position)
    c2mMeshRoot.quaternion.copy(targetPose.quaternion)
    c2mMeshRoot.scale.set(1, 1, 1)
    c2mMeshRoot.updateMatrixWorld(true)
  } else {
    c2mMeshRoot.position.set(0, 0, 0)
    c2mMeshRoot.rotation.set(-Math.PI / 2, 0, 0)
    c2mMeshRoot.scale.set(1, 1, 1)
    c2mMeshRoot.updateMatrixWorld(true)
  }
}

defineExpose({
  reload,
  resetView,
  resetPointcloudView: resetView,
  getCameraPose,
  setCameraPose,
  syncFromExternalPose,
  syncInitialViewFromExternalPose: syncFromExternalPose,
  getCameraDistance,
  getCameraOrientation,
  syncFromRotation,
  syncFromCameraDistance,
  setBackgroundTheme,
  setBackgroundColor,
  setShowAxes,
  setShowGrid,
  setGridColor,
  setWireframe,
  setPointColor,
  setPointcloudColorDisplay,
  setPointSize,
  setStandardView,
  setViewDirection,
  rollView,
  setSectionState,
  setEdlEnabled,
  setEdlStrength,
  getModelWorldPose,
  applyBimWorldPose,
  cancelAnalysis: cancelActiveAnalysis,
  removeAnalysisVisual,
  clearAnalysis: clearAnalysisVisuals,
})

// 监听 Prop 变化
watch(
  () => [props.assetId, props.bimAssetId, props.scanAssetId, props.pointcloudAssetId, props.type] as const,
  () => {
    if (isMountedReady) reload()
  },
)

watch(
  () => props.calibration,
  () => {
    if (props.type === 'bim' || props.type === 'hybrid') {
      applyCalibrationToBim(false)
    }
  },
  { deep: true },
)

watch(
  () => props.bimWorldPose,
  (pose) => {
    if (props.type === 'c2m') {
      applyBimWorldPose(pose)
    }
  },
  { deep: true },
)

watch(
  () => props.edlEnabled,
  (val) => {
    if (val !== undefined) setEdlEnabled(val)
  },
)

watch(
  () => props.edlStrength,
  (val) => {
    if (val !== undefined) setEdlStrength(val)
  },
)

watch(
  () => [props.analysisPoints, props.analysisDistances, props.analysisAreas],
  () => {
    if (isMountedReady) rebuildArchivedAnalysisVisuals()
  },
  { deep: true },
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
  <div class="unified-viewer-3d">
    <div ref="viewportEl" class="unified-viewer-viewport" />

    <ViewerMeasurementBadge
      v-for="badge in measurementBadges"
      :key="badge.id"
      :overlay="badge.overlay"
      :title="badge.title"
      :main-label="badge.mainLabel"
      :main-value="badge.mainValue"
      :rows="badge.rows"
      closable
      deletable
      :resettable="Boolean(measurementPanelOffsets.get(badge.id))"
      @close="hideMeasurementBadge(badge.id)"
      @delete="deleteMeasurementBadge(badge)"
      @drag-by="moveMeasurementBadge(badge.id, $event)"
      @reset-position="resetMeasurementBadge(badge.id)"
    />

    <div v-if="loadError" class="unified-viewer-placeholder unified-viewer-error">
      <div class="placeholder-text error-text">{{ loadError }}</div>
    </div>

    <!-- 点云 EDL 快捷增强浮层（可选是否打开，并支持实时调节明暗强度） -->
    <div
      v-if="showEdlControl && (type === 'pointcloud' || type === 'hybrid')"
      class="edl-floating-panel"
    >
      <el-tooltip
        :content="localEdlEnabled ? '点击关闭 EDL 深度边缘增强' : '点击开启 EDL 深度边缘增强'"
        placement="top"
      >
        <button
          type="button"
          class="edl-toggle-button"
          :class="{ 'is-active': localEdlEnabled }"
          @click="setEdlEnabled(!localEdlEnabled)"
        >
          <span class="edl-label">EDL</span>
          <span class="edl-status-dot" />
        </button>
      </el-tooltip>

      <div v-if="localEdlEnabled" class="edl-slider-container">
        <span class="edl-slider-label">强度</span>
        <input
          type="range"
          min="0.1"
          max="1.0"
          step="0.05"
          :value="localEdlStrength"
          class="edl-range-input"
          @input="setEdlStrength(Number(($event.target as HTMLInputElement).value))"
        />
        <span class="edl-value-text">{{ (localEdlStrength * 100).toFixed(0) }}%</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.unified-viewer-3d {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background-color: #0b1020;
  user-select: none;
}

.unified-viewer-viewport {
  width: 100%;
  height: 100%;
  display: block;
}

.unified-viewer-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(8, 17, 29, 0.7);
  backdrop-filter: blur(4px);
  pointer-events: none;
  z-index: 10;
}

.placeholder-text {
  color: #94a3b8;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.placeholder-text.error-text {
  color: #f87171;
}

/* EDL 悬浮快捷控制器 */
.edl-floating-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  background: rgba(15, 23, 42, 0.75);
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 6px;
  backdrop-filter: blur(8px);
  z-index: 20;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.edl-toggle-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  background: rgba(30, 41, 59, 0.7);
  border: 1px solid rgba(100, 116, 139, 0.4);
  border-radius: 4px;
  color: #94a3b8;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.edl-toggle-button:hover {
  background: rgba(51, 65, 85, 0.9);
  color: #f1f5f9;
}

.edl-toggle-button.is-active {
  background: rgba(14, 165, 233, 0.25);
  border-color: #38bdf8;
  color: #38bdf8;
}

.edl-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #64748b;
  transition: background 0.2s ease;
}

.edl-toggle-button.is-active .edl-status-dot {
  background: #38bdf8;
  box-shadow: 0 0 6px #38bdf8;
}

.edl-slider-container {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding-left: 4px;
  border-left: 1px solid rgba(148, 163, 184, 0.2);
}

.edl-slider-label {
  font-size: 11px;
  color: #94a3b8;
}

.edl-range-input {
  width: 60px;
  height: 4px;
  accent-color: #38bdf8;
  cursor: pointer;
}

.edl-value-text {
  font-size: 11px;
  color: #cbd5e1;
  min-width: 30px;
  font-family: monospace;
}
</style>
