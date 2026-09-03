<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { TilesRenderer } from '3d-tiles-renderer'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import { PointCloudEdlPipeline } from './edlPipeline'
import { createUploadHeaders } from '@/config/upload-backend'
import { getAssetDetail, getBimGlbFile, getPointcloudTilesetUrl } from '@/api/backend-file'
import { getC2MColoredPlyUrl, type C2MResult } from '@/api/backend-c2m'
import { backendRequest } from '@/api/backend-http'
import type { AnalysisDistance, AnalysisMode, AnalysisPoint } from './ViewerAnalysisOverlay.vue'

export type ViewerType = 'bim' | 'pointcloud' | 'c2m' | 'hybrid'
export type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'

export type CameraPose = {
  camera: THREE.Vector3
  target: THREE.Vector3
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
let backgroundTheme: PreviewBackgroundTheme = 'deep'
let customBackgroundColor = ''
let pointColorOverride: string | null = '#86898D'

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
let analysisVisualGroup: THREE.Group | null = null

// 材质存储
const originalMaterialStore = new WeakMap<THREE.Object3D, any>()

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
  if (analysisVisualGroup && scene) {
    scene.remove(analysisVisualGroup)
    analysisVisualGroup.traverse((c: any) => {
      c.geometry?.dispose?.()
      c.material?.dispose?.()
    })
    analysisVisualGroup = null
  }
  analysisAnchorPoint = null
}

function handleAnalysisPointerDown(event: PointerEvent) {
  if (!props.analysisMode || props.analysisMode === 'none' || !camera || !scene) return
  if (event.button !== 0) return

  const rect = viewportEl.value?.getBoundingClientRect()
  if (!rect) return
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
  if (!intersects.length) return

  const hit = intersects[0]
  const hitPoint = hit.point

  if (props.analysisMode === 'locate') {
    clearAnalysisVisuals()
    analysisVisualGroup = new THREE.Group()
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.12, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0xff3366, depthTest: false }),
    )
    marker.position.copy(hitPoint)
    analysisVisualGroup.add(marker)
    scene.add(analysisVisualGroup)

    emit('analysis-point', {
      x: hitPoint.x,
      y: hitPoint.y,
      z: hitPoint.z,
    })
    return
  }

  if (props.analysisMode === 'distance') {
    if (!analysisAnchorPoint) {
      clearAnalysisVisuals()
      analysisAnchorPoint = hitPoint.clone()
      analysisVisualGroup = new THREE.Group()
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(0.12, 16, 16),
        new THREE.MeshBasicMaterial({ color: 0x00ffff, depthTest: false }),
      )
      marker.position.copy(hitPoint)
      analysisVisualGroup.add(marker)
      scene.add(analysisVisualGroup)
    } else {
      const p1 = analysisAnchorPoint.clone()
      const p2 = hitPoint.clone()
      const dist = p1.distanceTo(p2)
      const heightDiff = Math.abs(p2.y - p1.y)

      const marker2 = new THREE.Mesh(
        new THREE.SphereGeometry(0.12, 16, 16),
        new THREE.MeshBasicMaterial({ color: 0x00ffff, depthTest: false }),
      )
      marker2.position.copy(p2)
      analysisVisualGroup?.add(marker2)

      const lineGeom = new THREE.BufferGeometry().setFromPoints([p1, p2])
      const line = new THREE.Line(
        lineGeom,
        new THREE.LineBasicMaterial({ color: 0x00ffff, linewidth: 2, depthTest: false }),
      )
      analysisVisualGroup?.add(line)

      emit('analysis-distance', {
        distance: dist,
        start: { x: p1.x, y: p1.y, z: p1.z },
        end: { x: p2.x, y: p2.y, z: p2.z },
        heightDifference: heightDiff,
      })
      analysisAnchorPoint = null
    }
  }
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

  const glbBlob = await getBimGlbFile(assetDetail.glbUrl)
  const objectUrl = URL.createObjectURL(glbBlob)

  const loader = new GLTFLoader()
  const dracoLoader = new DRACOLoader()
  dracoLoader.setDecoderPath('/draco/')
  dracoLoader.preload()
  loader.setDRACOLoader(dracoLoader)

  try {
    const gltf = await loader.loadAsync(objectUrl)
    URL.revokeObjectURL(objectUrl)
    dracoLoader.dispose()

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
  }
}

async function loadPointcloudModel(assetId: number) {
  loaded.value = false
  emit('loaded-change', false)

  const res = await getAssetDetail(assetId)
  const detail = res.data
  if (!detail?.tilesetUrl) throw new Error('点云切片尚未就绪')

  const url = getPointcloudTilesetUrl(detail.tilesetUrl)
  const nextTileset = new TilesRenderer(url)
  nextTileset.displayActiveTiles = true
  // 完全对齐校准页的最佳画质与性能均衡阈值 16.0
  nextTileset.errorTarget = 16.0
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
        mat.size = 2.5

        if (pointColorOverride) {
          mat.color = new THREE.Color(pointColorOverride)
          mat.vertexColors = false
        } else {
          mat.color = new THREE.Color(0xffffff)
          mat.vertexColors = !!obj.geometry?.attributes?.color
        }
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
  })

  // 交互事件监听
  renderer.domElement.addEventListener('pointerdown', handleAnalysisPointerDown)

  initLights()
  syncSceneBackground()

  axesHelper = new THREE.AxesHelper(15)
  axesHelper.visible = showAxesEnabled
  scene.add(axesHelper)

  // 完全对齐校准页网格参数
  gridHelper = new THREE.GridHelper(10000, 2000, 0x67e8f9, 0x2a6f82)
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
    renderer.dispose()
    renderer.domElement.remove()
    renderer = null
  }
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
  setWireframe,
  setPointColor,
  setSectionState,
  setEdlEnabled,
  setEdlStrength,
  getModelWorldPose,
  applyBimWorldPose,
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
