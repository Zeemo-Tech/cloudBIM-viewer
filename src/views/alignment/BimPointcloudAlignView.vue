<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Box, Hide, RefreshLeft, View } from '@element-plus/icons-vue'
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
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { TilesRenderer } from '3d-tiles-renderer'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import {
  createBimAlignment,
  computeFineAlignment,
  getBimAlignment,
  type FineAlignmentResult,
  type BimAlignmentResult,
} from '@/api/backend-alignment'
import {
  getAssetDetail,
  getBimGlbFile,
  getBimMetadata,
  getPointcloudTilesAsset,
  getPointcloudTilesetUrl,
  updateAssetAppearance,
} from '@/api/backend-file'
import { downloadRemeshResult, getMeshAlgorithms, getRemeshStatus, remeshBimAsset, type MeshAlgorithm, type RemeshStats, type RemeshStatus } from '@/api/backend-mesh'
import { backendRequest, normalizeBackendUrl } from '@/api/backend-http'
import { computeC2M, getLatestC2M, getC2MColoredPlyUrl, type C2MResult } from '@/api/backend-c2m'
import { createUploadHeaders } from '@/config/upload-backend'
import wanggeIcon from '@/assets/images/wangge.png'
import toushiIcon from '@/assets/images/toushi.png'
import zhengjiaoIcon from '@/assets/images/zhengjiao.png'
import GlobalAnalysisToolbar from '@/components/preview/GlobalAnalysisToolbar.vue'
import ViewerAnalysisOverlay, {
  type AnalysisDistance,
  type AnalysisMode,
  type AnalysisPoint,
} from '@/components/preview/ViewerAnalysisOverlay.vue'

type ProjectionMode = 'perspective' | 'orthographic'
type MaterialMode = 'original' | 'unlit' | 'lambert'
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

type RegistrationStage = 'coarse' | 'fine'
const registrationStage = ref<RegistrationStage>('coarse')
const fineAlignLoading = ref(false)
const fineAlignResult = ref<FineAlignmentResult | null>(null)
const fineApplyWhenRegressed = ref(false)
const fineRmseRegressRatio = ref(1.05)
const fineFitnessRegressRatio = ref(0.95)
const analysisMode = ref<AnalysisMode>('none')
const analysisPoint = ref<AnalysisPoint | null>(null)
const analysisDistance = ref<AnalysisDistance | null>(null)
const analysisToolbarCollapsed = ref(true)
const hasSavedAlignmentMatrix = ref(false)
const coarseAlignmentDirty = ref(false)
const latestAlignmentResult = ref<BimAlignmentResult | null>(null)
const loadingAlignmentMatrix = ref(false)
const showAlignmentMatrixDialog = ref(false)
const alignmentMatrixRows = computed(() => {
  const matrix = latestAlignmentResult.value?.modelMatrix
  if (!Array.isArray(matrix) || matrix.length !== 16) {
    return [] as string[][]
  }

  // three.js Matrix4 arrays are column-major; display them as conventional rows.
  return [0, 1, 2, 3].map((row) =>
    [matrix[row], matrix[row + 4], matrix[row + 8], matrix[row + 12]].map((value) =>
      formatMatrixCell(Number(value)),
    ),
  )
})
const alignmentRtRows = computed(() =>
  alignmentMatrixRows.value.slice(0, 3).map((row) => ({
    rotation: row.slice(0, 3),
    translation: row[3],
  })),
)
const alignmentMatrixRawText = computed(() =>
  JSON.stringify(latestAlignmentResult.value?.modelMatrix ?? [], null, 2),
)
const canRunFineAlignment = computed(() =>
  registrationStage.value === 'fine' && !!props.bimAssetId && !!props.pointcloudAssetId &&
  hasSavedAlignmentMatrix.value && !coarseAlignmentDirty.value && !fineAlignLoading.value,
)
const fineRunBlockedReason = computed(() => {
  if (registrationStage.value !== 'fine') return ''
  if (!props.bimAssetId || !props.pointcloudAssetId) return '缺少 BIM 或点云资产'
  if (!hasSavedAlignmentMatrix.value) return '请先保存粗配准矩阵'
  if (coarseAlignmentDirty.value) return '粗配准存在未保存的变换修改'
  if (fineAlignLoading.value) return '精细化配准计算中...'
  return ''
})
const canSaveCalibration = computed(() => !!bimLoaded.value && !!pointcloudLoaded.value &&
  (registrationStage.value === 'coarse' || !!fineAlignResult.value))
const canSaveFineAlignment = computed(() =>
  registrationStage.value === 'fine' && !!fineAlignResult.value && !fineAlignLoading.value,
)
const canSaveCoarseAlignment = computed(() =>
  !!bimLoaded.value && !!pointcloudLoaded.value && registrationStage.value === 'coarse' && !savingCalibration.value,
)
const coarseSaveHint = computed(() => {
  if (!bimLoaded.value || !pointcloudLoaded.value) return '等待 BIM 与点云加载完成'
  if (coarseAlignmentDirty.value) return '检测到未保存的变换修改'
  if (hasSavedAlignmentMatrix.value) return '当前粗配准矩阵已保存'
  return '调整模型位置后保存粗配准矩阵'
})

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('准备就绪')
const showPanel = ref(true)
const showAdvancedSettings = ref(false)
const loadingBim = ref(false)
const loadingPointcloud = ref(false)
const savingCalibration = ref(false)
const meshAlgorithms = ref<MeshAlgorithm[]>([])
const meshAlgorithm = ref('bim_preprocessor')
const meshTargetEdgeLength = ref(0.1)
const meshRunning = ref(false)
const meshStatus = ref<RemeshStatus | null>(null)
const meshStats = ref<RemeshStats | null>(null)
const meshError = ref('')
const c2mRunning = ref(false)
const c2mResult = ref<C2MResult | null>(null)
const c2mVoxelSize = ref(0.05)
const c2mError = ref('')
const c2mSceneLoaded = ref(false)
const c2mSceneLoading = ref(false)
const c2mApplied = ref(false)
let c2mSceneGroup: THREE.Group | null = null
const remeshLoading = ref(false)
const remeshMeshLoaded = ref(false)
const remeshRestoreAvailable = ref(false)
const remeshSolidHidden = ref(false)
const remeshWireHidden = ref(false)
const remeshWireAvailable = ref(true)
let remeshSceneGroup: THREE.Group | null = null
type RemeshSceneSnapshot = {
  objects: Array<{
    object: THREE.Object3D
    visible: boolean
    position: THREE.Vector3
    quaternion: THREE.Quaternion
    scale: THREE.Vector3
  }>
}
let remeshSceneSnapshot: RemeshSceneSnapshot | null = null
const REMESH_WIREFRAME_MAX_FACES = 2_700_000
let meshStatusPollingTimer: number | null = null

const meshReady = computed(() => meshStatus.value?.status === 'succeeded')
const meshTaskActive = computed(() =>
  meshStatus.value?.status === 'queued' || meshStatus.value?.status === 'processing',
)
const meshControlsDisabled = computed(() => meshRunning.value || meshTaskActive.value)
const canLoadRemesh = computed(() => meshReady.value && !remeshLoading.value && !!props.bimAssetId)
const canRunC2M = computed(() => Boolean(props.pointcloudAssetId && props.bimAssetId && hasSavedAlignmentMatrix.value && meshReady.value && !c2mRunning.value))

async function runC2M() {
  if (!canRunC2M.value || !props.pointcloudAssetId || !props.bimAssetId) return
  c2mRunning.value = true
  c2mError.value = ''
  try {
    const response = await computeC2M({ modelScanFileId: props.pointcloudAssetId, modelBimFileId: props.bimAssetId, voxelSize: c2mVoxelSize.value })
    c2mResult.value = response.data
    ElMessage.success('Scan vs BIM 计算完成')
  } catch (error) {
    c2mError.value = error instanceof Error ? error.message : 'Scan vs BIM 计算失败'
    ElMessage.error(c2mError.value)
  } finally {
    c2mRunning.value = false
  }
}

async function loadLatestC2M() {
  if (!props.pointcloudAssetId || !props.bimAssetId) return
  try {
    const response = await getLatestC2M(props.pointcloudAssetId, props.bimAssetId)
    c2mResult.value = response.data
  } catch {
    c2mResult.value = null
  }
}

async function loadC2MToScene() {
  if (!c2mResult.value?.coloredPlyAvailable || !props.pointcloudAssetId || !props.bimAssetId || !scene) return
  c2mSceneLoading.value = true
  try {
    const blob = await backendRequest<Blob>(getC2MColoredPlyUrl(props.pointcloudAssetId, props.bimAssetId), { method: 'GET', responseType: 'blob' })
    const objectUrl = URL.createObjectURL(blob)
    try {
      const geometry = await new PLYLoader().loadAsync(objectUrl)
      if (!geometry.attributes.position) throw new Error('C2M 着色 PLY 缺少顶点数据')
      if (!geometry.attributes.normal) geometry.computeVertexNormals()
      geometry.computeBoundingBox()
      const center = geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3()
      geometry.translate(-center.x, -center.y, -center.z)
      clearC2MScene()
      const group = new THREE.Group()
      group.name = 'c2m-colored-result'
      if (bimPivot) {
        bimPivot.updateMatrixWorld(true)
        bimPivot.getWorldPosition(group.position)
        bimPivot.getWorldQuaternion(group.quaternion)
        bimPivot.getWorldScale(group.scale)
      }
      const material = new THREE.MeshBasicMaterial({ vertexColors: Boolean(geometry.attributes.color), side: THREE.DoubleSide })
      group.add(new THREE.Mesh(geometry, material))
      scene.add(group)
      c2mSceneGroup = group
      c2mSceneLoaded.value = true
      requestRender()
      ElMessage.success('C2M 着色结果已加载到场景')
    } finally { URL.revokeObjectURL(objectUrl) }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载 C2M 结果失败')
  } finally {
    c2mSceneLoading.value = false
  }
}

function clearC2MScene() {
  if (c2mSceneGroup && scene) {
    scene.remove(c2mSceneGroup)
    disposeObject3D(c2mSceneGroup)
  }
  c2mSceneGroup = null
  c2mSceneLoaded.value = false
  c2mApplied.value = false
  requestRender()
}

function confirmC2MApply() {
  if (!c2mSceneLoaded.value) return
  c2mApplied.value = true
  ElMessage.success('C2M 结果已确认应用，当前着色结果将用于后续标注查看')
}
const meshStatusText = computed(() => {
  if (meshRunning.value) return '正在提交均匀化任务...'
  switch (meshStatus.value?.status) {
    case 'queued':
      return '均匀化任务已排队'
    case 'processing':
      return '均匀化任务正在处理中'
    case 'succeeded':
      return '已有可用的均匀化网格'
    case 'failed':
      return '上次均匀化失败，可重新发起'
    default:
      return '尚未生成均匀化网格'
  }
})
const meshActionText = computed(() => {
  if (meshRunning.value) return '正在提交...'
  if (meshStatus.value?.status === 'queued') return '已排队'
  if (meshStatus.value?.status === 'processing') return '处理中'
  if (meshReady.value) return '重新均匀化'
  if (meshStatus.value?.status === 'failed') return '重新均匀化'
  return '开始均匀化'
})

function clearMeshStatusPolling() {
  if (meshStatusPollingTimer !== null) {
    window.clearTimeout(meshStatusPollingTimer)
    meshStatusPollingTimer = null
  }
}

function scheduleMeshStatusPolling() {
  clearMeshStatusPolling()
  if (meshTaskActive.value) {
    meshStatusPollingTimer = window.setTimeout(() => {
      void refreshMeshStatus()
    }, 4000)
  }
}

async function refreshMeshStatus(showError = false) {
  if (!props.bimAssetId) {
    clearMeshStatusPolling()
    meshStatus.value = null
    return
  }
  try {
    const previousStatus = meshStatus.value?.status
    const response = await getRemeshStatus(props.bimAssetId)
    meshStatus.value = response.data
    meshStats.value = response.data.stats || null
    meshError.value = response.data.status === 'failed' ? response.data.lastError || '网格均匀化失败' : ''
    scheduleMeshStatusPolling()
    if (
      (previousStatus === 'queued' || previousStatus === 'processing') &&
      response.data.status === 'succeeded'
    ) {
      ElMessage.success('网格均匀化完成')
    }
  } catch (error) {
    clearMeshStatusPolling()
    meshError.value = error instanceof Error ? error.message : '获取网格均匀化状态失败'
    if (showError) {
      ElMessage.error(meshError.value)
    }
  }
}

async function loadMeshAlgorithms() {
  try {
    const response = await getMeshAlgorithms()
    meshAlgorithms.value = response.data || []
    if (meshAlgorithms.value.length && !meshAlgorithms.value.some((item) => item.name === meshAlgorithm.value)) {
      meshAlgorithm.value = meshAlgorithms.value[0].name
    }
  } catch {
    meshAlgorithms.value = []
  }
}

async function runMeshRemesh() {
  if (!props.bimAssetId || meshRunning.value) return
  await refreshMeshStatus(true)
  if (meshTaskActive.value) {
    ElMessage.info('网格均匀化任务正在排队或处理中')
    return
  }
  meshRunning.value = true
  meshError.value = ''
  remeshSceneSnapshot = null
  remeshRestoreAvailable.value = false
  clearLoadedRemeshMesh()
  try {
    const response = await remeshBimAsset(props.bimAssetId, {
      algorithm: meshAlgorithm.value,
      params: { target_edge_length: meshTargetEdgeLength.value },
      force: meshReady.value,
    })
    meshStats.value = null
    meshStatus.value = { supported: true, status: response.data.status }
    ElMessage.success('网格均匀化任务已进入后台队列')
    scheduleMeshStatusPolling()
  } catch (error) {
    meshError.value = error instanceof Error ? error.message : '网格均匀化失败'
    await refreshMeshStatus()
  } finally {
    meshRunning.value = false
  }
}

function clearLoadedRemeshMesh() {
  if (!remeshSceneGroup) {
    remeshMeshLoaded.value = false
    return
  }
  if (scene) scene.remove(remeshSceneGroup)
  disposeObject3D(remeshSceneGroup)
  remeshSceneGroup = null
  remeshMeshLoaded.value = false
  remeshSolidHidden.value = false
  remeshWireHidden.value = false
  remeshWireAvailable.value = true
}

function captureRemeshSceneSnapshot() {
  if (remeshSceneSnapshot) return
  const objects = [bimPivot, pointcloudWrapper, pointcloudGroup].filter(Boolean) as THREE.Object3D[]
  if (!objects.length) return
  remeshSceneSnapshot = {
    objects: objects.map((object) => ({
      object,
      visible: object.visible,
      position: object.position.clone(),
      quaternion: object.quaternion.clone(),
      scale: object.scale.clone(),
    })),
  }
}

function restoreRemeshScene() {
  if (!remeshSceneSnapshot) return
  clearLoadedRemeshMesh()
  remeshSceneSnapshot.objects.forEach(({ object, visible, position, quaternion, scale }) => {
    if (!object.parent) return
    object.visible = visible
    object.position.copy(position)
    object.quaternion.copy(quaternion)
    object.scale.copy(scale)
    object.updateMatrixWorld(true)
  })
  remeshSceneSnapshot = null
  remeshRestoreAvailable.value = false
  requestRender()
  ElMessage.success('已复原到加载均匀化结果之前的场景')
}

async function loadRemeshResult() {
  if (!canLoadRemesh.value || !props.bimAssetId || !scene) return
  remeshLoading.value = true
  meshError.value = ''
  captureRemeshSceneSnapshot()
  clearLoadedRemeshMesh()
  try {
    ElMessage({ message: '正在加载均匀化结果…', type: 'info', duration: 0, grouping: true })
    const blob = await downloadRemeshResult(props.bimAssetId)
    const objectUrl = URL.createObjectURL(blob)
    try {
      const geometry = await new PLYLoader().loadAsync(objectUrl)
      if (!geometry.attributes.position) throw new Error('PLY 缺少顶点数据')
      if (!geometry.attributes.normal) geometry.computeVertexNormals()
      geometry.computeBoundingBox()
      const center = geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3()
      geometry.translate(-center.x, -center.y, -center.z)

      const group = new THREE.Group()
      group.name = 'remesh-result'
      const position = new THREE.Vector3()
      const quaternion = new THREE.Quaternion()
      if (bimPivot) {
        bimPivot.getWorldPosition(position)
        bimPivot.getWorldQuaternion(quaternion)
      }
      group.position.copy(position)
      group.quaternion.copy(quaternion)

      const solidMaterial =
        rendererMode === 'webgpu'
          ? new MeshLambertNodeMaterial({ color: 0xff7a18, side: THREE.DoubleSide })
          : new THREE.MeshLambertMaterial({ color: 0xff7a18, side: THREE.DoubleSide })
      const solid = new THREE.Mesh(geometry, solidMaterial)
      group.add(solid)

      const faceCount = geometry.index
        ? geometry.index.count / 3
        : geometry.attributes.position.count / 3
      let wire: THREE.LineSegments | null = null
      if (faceCount <= REMESH_WIREFRAME_MAX_FACES) {
        wire = new THREE.LineSegments(
          new THREE.WireframeGeometry(geometry),
          new THREE.LineBasicMaterial({ color: 0x00ff88, transparent: true, opacity: 0.9 }),
        )
        group.add(wire)
      } else {
        remeshWireAvailable.value = false
      }
      remeshSceneGroup = group
      scene.add(group)
      remeshMeshLoaded.value = true
      remeshRestoreAvailable.value = Boolean(remeshSceneSnapshot)
      remeshSolidHidden.value = false
      remeshWireHidden.value = false
      requestRender()
      ElMessage.closeAll()
      ElMessage.success(`均匀化结果已加载（${geometry.attributes.position.count.toLocaleString()} 顶点）`)
    } finally {
      URL.revokeObjectURL(objectUrl)
    }
  } catch (error) {
    ElMessage.closeAll()
    meshError.value = error instanceof Error ? error.message : '加载均匀化结果失败'
    ElMessage.error(meshError.value)
  } finally {
    remeshLoading.value = false
  }
}

function toggleRemeshSolid() {
  const solid = remeshSceneGroup?.children.find((child): child is THREE.Mesh => child instanceof THREE.Mesh)
  if (!solid) return
  remeshSolidHidden.value = !remeshSolidHidden.value
  solid.visible = !remeshSolidHidden.value
  requestRender()
}

function toggleRemeshWire() {
  const wire = remeshSceneGroup?.children.find((child): child is THREE.LineSegments => child instanceof THREE.LineSegments)
  if (!wire) return
  remeshWireHidden.value = !remeshWireHidden.value
  wire.visible = !remeshWireHidden.value
  requestRender()
}
const projectionMode = ref<ProjectionMode>('perspective')
const materialMode = ref<MaterialMode>('unlit')
const showGrid = ref(true)
const showMeshWireframe = ref(false)
const showBounds = ref(false)
const backgroundColor = ref('#0b1020')
const pointcloudColor = ref('#ffffff')
const persistedPointcloudColor = ref('#ffffff')
const pointcloudColorOverridden = ref(false)
const pointcloudColorSaving = ref(false)
let pointcloudColorSaveTimer: number | null = null
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
const originalWireframeStore = new WeakMap<THREE.Material, boolean>()
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
let clipHandlesGroup: THREE.Group | null = null
let clipHandlePickers: THREE.Object3D[] = []
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
let clipBoxState: ClipBoxState | null = null
let highlightedElement:
  | {
      mesh: THREE.Mesh
      overlay: THREE.Mesh
      material: THREE.Material
    }
  | null = null
let analysisStartPoint: THREE.Vector3 | null = null
let analysisGroup: THREE.Group | null = null
let analysisLine: THREE.Line | null = null
let analysisMarkers: THREE.Mesh[] = []
let analysisPointerDown: { x: number; y: number } | null = null

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
    target: window.location.origin,
  })
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

function normalizePointcloudColor(value: string, fallback = '#ffffff') {
  const normalized = value.trim()
  return /^#[0-9a-fA-F]{6}$/.test(normalized) ? normalized.toLowerCase() : fallback
}

function applyPointcloudColor() {
  if (!pointcloudGroup || !pointcloudColorOverridden.value) return
  const color = new THREE.Color(normalizePointcloudColor(pointcloudColor.value))
  pointcloudGroup.traverse((obj: any) => {
    const material = obj?.material
    if (!material) return
    const apply = (item: any) => {
      if (!item) return
      if (item?.color?.isColor) {
        item.color.copy(color)
      }
      if ('vertexColors' in item) {
        item.vertexColors = false
      }
      if ('colorNode' in item) {
        item.colorNode = tslColor(color)
      }
      item.needsUpdate = true
    }
    if (Array.isArray(material)) material.forEach(apply)
    else apply(material)
  })
  requestRender()
}

function clearPointcloudColorSaveTimer() {
  if (pointcloudColorSaveTimer !== null) {
    window.clearTimeout(pointcloudColorSaveTimer)
    pointcloudColorSaveTimer = null
  }
}

async function persistPointcloudColor() {
  if (!props.pointcloudAssetId || pointcloudColorSaving.value) return
  const normalized = pointcloudColorOverridden.value
    ? normalizePointcloudColor(pointcloudColor.value, '')
    : ''
  if (pointcloudColorOverridden.value && !normalized) return
  pointcloudColorSaving.value = true
  try {
    const response = await updateAssetAppearance(props.pointcloudAssetId, {
      pointcloudColor: normalized,
    })
    const saved = normalizePointcloudColor(response.data.pointcloudColor || '', '')
    if (saved) {
      pointcloudColor.value = saved
      persistedPointcloudColor.value = saved
      pointcloudColorOverridden.value = true
    } else {
      pointcloudColorOverridden.value = false
      persistedPointcloudColor.value = '#ffffff'
    }
  } catch (error) {
    pointcloudColorOverridden.value = true
    pointcloudColor.value = persistedPointcloudColor.value
    applyPointcloudColor()
    ElMessage.error(error instanceof Error ? error.message : '保存点云颜色失败')
  } finally {
    pointcloudColorSaving.value = false
    if (normalizePointcloudColor(pointcloudColor.value) !== persistedPointcloudColor.value) {
      schedulePointcloudColorSave()
    }
  }
}

function schedulePointcloudColorSave() {
  clearPointcloudColorSaveTimer()
  pointcloudColorSaveTimer = window.setTimeout(() => {
    pointcloudColorSaveTimer = null
    void persistPointcloudColor()
  }, 400)
}

function handlePointcloudColorInput() {
  const normalized = normalizePointcloudColor(pointcloudColor.value, '')
  if (!normalized) return
  pointcloudColor.value = normalized
  pointcloudColorOverridden.value = true
  applyPointcloudColor()
  schedulePointcloudColorSave()
}

function handlePointcloudColorChange() {
  const normalized = normalizePointcloudColor(pointcloudColor.value, '')
  if (!normalized) {
    pointcloudColor.value = persistedPointcloudColor.value
    ElMessage.warning('请输入有效的颜色值，例如 #ffffff')
    return
  }
  pointcloudColor.value = normalized
  pointcloudColorOverridden.value = true
  applyPointcloudColor()
  clearPointcloudColorSaveTimer()
  void persistPointcloudColor()
}

function resetPointcloudColor() {
  pointcloudColorOverridden.value = false
  applyPointcloudMaterialMode(pointcloudGroup)
  clearPointcloudColorSaveTimer()
  void persistPointcloudColor()
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
  if (selectionHelper) {
    selectionHelper.visible = false
  }
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
  updateSelectionHighlight()
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

  if (pickedElementHelper) pickedElementHelper.visible = false
}

function syncBoundsHelpers() {
  if (!scene) return

  if (!showBounds.value) {
    if (boundsBoxHelper) boundsBoxHelper.visible = false
    clearClipHandles()
    updateGridPlacement()
    updateSelectionHighlight()
    return
  }

  const boundsBox =
    enableClipping.value && showBounds.value
      ? getCurrentClipBox()
      : getContentWorldBox()

  if (boundsBox && !boundsBox.isEmpty()) {
    if (!boundsBoxHelper) {
      boundsBoxHelper = new THREE.Box3Helper(boundsBox.clone(), 0x67e8f9)
      scene.add(boundsBoxHelper)
    }
    boundsBoxHelper.box.copy(boundsBox)
    boundsBoxHelper.visible = true
    boundsBoxHelper.updateMatrixWorld(true)
    if (enableClipping.value) {
      updateClipHandles(boundsBox)
    } else {
      clearClipHandles()
    }
  } else if (boundsBoxHelper) {
    boundsBoxHelper.visible = false
    clearClipHandles()
  }

  updateGridPlacement()
  updateSelectionHighlight()
}

function disposeObjectMaterial(target: any) {
  if (Array.isArray(target?.material)) {
    target.material.forEach((item: any) => item?.dispose?.())
  } else {
    target?.material?.dispose?.()
  }
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
    disposeObjectMaterial(child)
  })
  clipHandlesGroup = null
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

function getContentWorldBox() {
  if (!contentGroup) return null
  contentGroup.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(contentGroup)
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

function syncClipUiFromFace() {
  const range = getClipFaceRange(clipAxis.value, clipInvert.value)
  clipRange.value = range
  clipPosition.value = getClipFacePosition(clipAxis.value, clipInvert.value)
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
    {
      axis: 'x',
      invert: false,
      normal: new THREE.Vector3(-1, 0, 0),
      arrowDir: new THREE.Vector3(-1, 0, 0),
    },
    {
      axis: 'x',
      invert: true,
      normal: new THREE.Vector3(1, 0, 0),
      arrowDir: new THREE.Vector3(1, 0, 0),
    },
    {
      axis: 'y',
      invert: false,
      normal: new THREE.Vector3(0, -1, 0),
      arrowDir: new THREE.Vector3(0, -1, 0),
    },
    {
      axis: 'y',
      invert: true,
      normal: new THREE.Vector3(0, 1, 0),
      arrowDir: new THREE.Vector3(0, 1, 0),
    },
    {
      axis: 'z',
      invert: false,
      normal: new THREE.Vector3(0, 0, -1),
      arrowDir: new THREE.Vector3(0, 0, -1),
    },
    {
      axis: 'z',
      invert: true,
      normal: new THREE.Vector3(0, 0, 1),
      arrowDir: new THREE.Vector3(0, 0, 1),
    },
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
    const isActiveFace = axis === clipAxis.value && invert === clipInvert.value
    const color = isActiveFace ? activeColor : idleColor

    const anchor =
      axis === 'x'
        ? new THREE.Vector3(invert ? box.max.x : box.min.x, center.y, center.z)
        : axis === 'y'
          ? new THREE.Vector3(center.x, invert ? box.max.y : box.min.y, center.z)
          : new THREE.Vector3(center.x, center.y, invert ? box.max.z : box.min.z)

    shaft.geometry.dispose?.()
    shaft.geometry = new THREE.CylinderGeometry(
      shaftRadius,
      shaftRadius,
      shaftLength,
      12,
    )
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

function getPointerNdc(ev: PointerEvent) {
  const rect = renderer?.domElement?.getBoundingClientRect?.()
  if (!rect) return null
  return new THREE.Vector2(
    ((ev.clientX - rect.left) / rect.width) * 2 - 1,
    -((ev.clientY - rect.top) / rect.height) * 2 + 1,
  )
}

function clearAnalysis() {
  analysisStartPoint = null
  analysisPoint.value = null
  analysisDistance.value = null
  if (analysisGroup && scene) scene.remove(analysisGroup)
  analysisGroup?.traverse((child: any) => { child.geometry?.dispose?.(); child.material?.dispose?.() })
  analysisGroup = null
  analysisLine = null
  analysisMarkers = []
}

function pickAnalysisPoint(event: PointerEvent) {
  if (!raycaster || !activeCamera || !contentGroup) return null
  const pointer = getPointerNdc(event)
  if (!pointer) return null
  raycaster.setFromCamera(pointer, activeCamera)
  return raycaster.intersectObjects(contentGroup.children, true)[0]?.point?.clone() ?? null
}

function renderAnalysisPoint(start: THREE.Vector3, end?: THREE.Vector3) {
  if (!scene) return
  if (!analysisGroup) { analysisGroup = new THREE.Group(); analysisGroup.renderOrder = 10001; scene.add(analysisGroup) }
  const points = end ? [start, end] : [start]
  if (!analysisLine) {
    analysisLine = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xff5252, depthTest: false, depthWrite: false }))
    analysisGroup.add(analysisLine)
  }
  analysisLine.geometry.setFromPoints(points)
  analysisLine.visible = Boolean(end)
  while (analysisMarkers.length < points.length) {
    const marker = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 8), new THREE.MeshBasicMaterial({ color: 0xff5252, depthTest: false, depthWrite: false }))
    analysisMarkers.push(marker); analysisGroup.add(marker)
  }
  analysisMarkers.forEach((marker, index) => { marker.visible = index < points.length; if (marker.visible) marker.position.copy(points[index]) })
  requestRender()
}

function selectAnalysisMode(mode: AnalysisMode) {
  clearAnalysis()
  analysisMode.value = mode
}

function buildClipDragPlane(axisKey: ClipAxis, anchor: THREE.Vector3) {
  if (!activeCamera) return null
  const axis =
    axisKey === 'x'
      ? new THREE.Vector3(1, 0, 0)
      : axisKey === 'y'
        ? new THREE.Vector3(0, 1, 0)
        : new THREE.Vector3(0, 0, 1)
  const cameraDir = new THREE.Vector3()
  activeCamera.getWorldDirection(cameraDir)
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

function beginClipDrag(
  ev: PointerEvent,
  options: { axis: ClipAxis; invert: boolean },
) {
  if (!raycaster || !activeCamera || !renderer) return

  clipAxis.value = options.axis
  clipInvert.value = options.invert
  syncClipUiFromFace()
  applyClippingState()
  syncBoundsHelpers()

  const ndc = getPointerNdc(ev)
  if (!ndc) return
  raycaster.setFromCamera(ndc, activeCamera)

  const box = getCurrentClipBox()
  if (!box) return

  const center = box.getCenter(new THREE.Vector3())
  const anchor =
    options.axis === 'x'
      ? new THREE.Vector3(
          options.invert ? box.max.x : box.min.x,
          center.y,
          center.z,
        )
      : options.axis === 'y'
        ? new THREE.Vector3(
            center.x,
            options.invert ? box.max.y : box.min.y,
            center.z,
          )
        : new THREE.Vector3(
            center.x,
            center.y,
            options.invert ? box.max.z : box.min.z,
          )

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
  if (!clipDragState || !raycaster || !activeCamera) return
  const ndc = getPointerNdc(ev)
  if (!ndc) return
  raycaster.setFromCamera(ndc, activeCamera)
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
  syncClipUiFromFace()
  applyClippingState()
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
      if (registrationStage.value === 'coarse') coarseAlignmentDirty.value = true
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
      renderer.domElement.addEventListener('pointermove', onViewportPointerMove)
      renderer.domElement.addEventListener('pointerup', onViewportPointerUp)
      renderer.domElement.addEventListener('pointercancel', onViewportPointerCancel)

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
  applyPointcloudColor()
}

function updateClipRangeFromContent(
  opts: { resetPosition?: boolean; preserveT?: boolean } = {},
) {
  void opts.preserveT
  if (!contentGroup) return

  const state = getOrCreateClipState()
  if (!state) {
    clipRange.value = { min: 0, max: 1 }
    clipPosition.value = 0
    return
  }

  if (opts.resetPosition) {
    state.offsets = createDefaultClipOffsets()
  }

  clampClipOffsets(state)
  syncClipUiFromFace()
}

function applyMaterialClipping(planes: THREE.Plane[]) {
  if (!contentGroup) return

  contentGroup.traverse((obj: any) => {
    const material = obj?.material
    if (!material) return

    const applyToMaterial = (item: THREE.Material) => {
      item.clippingPlanes = planes.length ? planes : null
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
  const clipBox = enabled ? getCurrentClipBox() : null
  const planes =
    clipBox && !clipBox.isEmpty()
      ? [
          new THREE.Plane(new THREE.Vector3(1, 0, 0), -clipBox.min.x),
          new THREE.Plane(new THREE.Vector3(-1, 0, 0), clipBox.max.x),
          new THREE.Plane(new THREE.Vector3(0, 1, 0), -clipBox.min.y),
          new THREE.Plane(new THREE.Vector3(0, -1, 0), clipBox.max.y),
          new THREE.Plane(new THREE.Vector3(0, 0, 1), -clipBox.min.z),
          new THREE.Plane(new THREE.Vector3(0, 0, -1), clipBox.max.z),
        ]
      : []

  applyMaterialClipping(planes)

  if (rendererMode === 'webgpu' && clippingGroup) {
    clippingGroup.enabled = planes.length > 0
    clippingGroup.clippingPlanes.length = 0
    if (planes.length) {
      clippingGroup.clippingPlanes.push(...planes)
    }
  }

  requestRender()
}

function scheduleClipRangeUpdate() {
  if (clipUpdateScheduled) return
  clipUpdateScheduled = true
  requestAnimationFrame(() => {
    clipUpdateScheduled = false
    updateClipRangeFromContent({ preserveT: true })
    applyClippingState()
    syncBoundsHelpers()
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
    if (!editMode.value) {
      editMode.value = true
    }
    showBounds.value = false
    syncBoundsHelpers()
    applyClippingState()
    return
  }

  if (editMode.value) {
    editMode.value = false
  }

  if (enableClipping.value && !showBounds.value) {
    showBounds.value = true
  }

  updateClipRangeFromContent({ resetPosition: true })
  syncBoundsHelpers()
  applyClippingState()
}

function onClippingParamsChange() {
  if (!contentGroup) return
  setClipFacePosition(clipAxis.value, clipInvert.value, clipPosition.value)
  syncClipUiFromFace()
  syncBoundsHelpers()
  applyClippingState()
}

function onClippingFaceChange() {
  if (!contentGroup) return
  updateClipRangeFromContent({ preserveT: true })
  syncBoundsHelpers()
  applyClippingState()
}

function resetClippingState() {
  clipAxis.value = 'z'
  clipInvert.value = false
  clipBoxState = null

  if (contentGroup) {
    updateClipRangeFromContent({ resetPosition: true })
  } else {
    clipRange.value = { min: 0, max: 1 }
    clipPosition.value = 0
  }

  syncBoundsHelpers()
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

function markFineAlignmentDirty() {
  fineAlignResult.value = null
}

function normalizeFineThreshold(value: unknown, min: number, max: number, fallback: number) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.min(max, Math.max(min, Number(numeric.toFixed(2))))
}

function onFineRmseRegressRatioChange(value: number | undefined) {
  fineRmseRegressRatio.value = normalizeFineThreshold(value, 1, 2, 1.05)
}

function onFineFitnessRegressRatioChange(value: number | undefined) {
  fineFitnessRegressRatio.value = normalizeFineThreshold(value, 0.5, 1, 0.95)
}

function resetFineThresholdDefaults() {
  fineRmseRegressRatio.value = 1.05
  fineFitnessRegressRatio.value = 0.95
  markFineAlignmentDirty()
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
  if (analysisMode.value !== 'none') {
    analysisPointerDown = { x: event.clientX, y: event.clientY }
    if (controls) controls.enabled = false
    return
  }
  if (transformControls && !controls?.enabled) return

  const pointer = getPointerNdc(event)
  if (!pointer) return

  raycaster.setFromCamera(pointer, activeCamera)
  if (enableClipping.value && showBounds.value && clipHandlePickers.length) {
    const handleHits = raycaster.intersectObjects(clipHandlePickers, true)
    const handleHit = handleHits[0] as any
    if (handleHit?.object?.userData?.__viewerClipHandle) {
      beginClipDrag(event, {
        axis: handleHit.object.userData.axis,
        invert: !!handleHit.object.userData.invert,
      })
      return
    }
  }

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

function onViewportPointerMove(event: PointerEvent) {
  if (!clipDragState) return
  onClipDragMove(event)
}

function onViewportPointerUp(event: PointerEvent) {
  if (analysisMode.value !== 'none' && analysisPointerDown) {
    const down = analysisPointerDown
    analysisPointerDown = null
    if (controls) controls.enabled = true
    if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) return
    const point = pickAnalysisPoint(event)
    if (!point) return
    const toPoint = (value: THREE.Vector3): AnalysisPoint => ({ x: value.x, y: value.y, z: value.z })
    if (analysisMode.value === 'locate') {
      clearAnalysis(); analysisMode.value = 'locate'; renderAnalysisPoint(point); analysisPoint.value = toPoint(point)
    } else if (!analysisStartPoint) {
      analysisStartPoint = point; renderAnalysisPoint(point)
    } else {
      const start = analysisStartPoint; renderAnalysisPoint(start, point)
      analysisDistance.value = { start: toPoint(start), end: toPoint(point), distance: start.distanceTo(point), heightDifference: Math.abs(start.y - point.y) }
    }
    return
  }
  if (!clipDragState && clipPointerCaptureId === null) return
  endClipDrag(event)
}

function onViewportPointerCancel(event: PointerEvent) {
  if (analysisMode.value !== 'none' && analysisPointerDown) {
    analysisPointerDown = null
    if (controls) controls.enabled = true
    return
  }
  if (!clipDragState && clipPointerCaptureId === null) return
  endClipDrag(event)
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
  if (registrationStage.value === 'coarse') coarseAlignmentDirty.value = true
  transformHelper?.updateMatrixWorld?.(true)
  syncBoundsHelpers()
  requestRender()
}

function setPositionOffsetAxis(axis: 'x' | 'y' | 'z', value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return
  const range = positionSliderRange.value
  const next = clamp(numeric, range.min, range.max)
  if (axis === 'x') positionOffsetX.value = next
  if (axis === 'y') positionOffsetY.value = next
  if (axis === 'z') positionOffsetZ.value = next
  applyPositionFixRealtime()
}

function onPositionNumberInput(axis: 'x' | 'y' | 'z', event: Event) {
  setPositionOffsetAxis(axis, (event.target as HTMLInputElement).value)
}

function onPositionNumberBlur(axis: 'x' | 'y' | 'z', event: Event) {
  const input = event.target as HTMLInputElement
  setPositionOffsetAxis(axis, input.value)
  input.value = String(
    axis === 'x' ? positionOffsetX.value : axis === 'y' ? positionOffsetY.value : positionOffsetZ.value,
  )
}

function onPositionNumberKeydown(event: KeyboardEvent, axis: 'x' | 'y' | 'z') {
  if (event.key === 'Enter') {
    onPositionNumberBlur(axis, event)
    ;(event.target as HTMLInputElement).blur()
  }
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
  if (registrationStage.value === 'coarse') coarseAlignmentDirty.value = true
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

function toggleMeshWireframe() {
  showMeshWireframe.value = !showMeshWireframe.value
  if (!bimRoot) return

  bimRoot.traverse((obj: any) => {
    const material = obj?.material as THREE.Material | THREE.Material[] | undefined
    if (!material) return
    const materials = Array.isArray(material) ? material : [material]
    materials.forEach((item) => {
      const wireframeMaterial = item as THREE.Material & { wireframe?: boolean }
      if (!originalWireframeStore.has(item)) {
        originalWireframeStore.set(item, Boolean(wireframeMaterial.wireframe))
      }
      if ('wireframe' in wireframeMaterial) {
        wireframeMaterial.wireframe = showMeshWireframe.value
        wireframeMaterial.needsUpdate = true
      }
    })
  })
  requestRender()
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
    const response = await getBimAlignment({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
    })

    if (!response?.data) {
      return
    }

    latestAlignmentResult.value = response.data
    logSavedAlignmentMatrix(response.data)
    hasSavedAlignmentMatrix.value = true
    coarseAlignmentDirty.value = false
    const restored = tryRestoreSavedAlignment(response.data)
    logBimRelativeTransform()
    if (restored) {
      loggedSavedAlignmentKey = logKey
    } else {
      window.setTimeout(() => {
        void fetchAndLogSavedAlignmentIfExists()
      }, 250)
    }
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
    const response = await createBimAlignment(payload)
    latestAlignmentResult.value = response.data
    hasSavedAlignmentMatrix.value = true
    coarseAlignmentDirty.value = false
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

async function saveCoarseAlignmentMatrix() {
  if (registrationStage.value !== 'coarse') return
  if (savingCalibration.value) return
  savingCalibration.value = true
  try {
    await handleSaveAlignment()
  } finally {
    savingCalibration.value = false
  }
}

async function saveFineAlignmentMatrix() {
  if (savingCalibration.value || !canSaveFineAlignment.value) return
  savingCalibration.value = true
  try {
    const saved = await handleSaveAlignment()
    if (saved) fineAlignResult.value = null
  } finally {
    savingCalibration.value = false
  }
}

function formatMatrixCell(value: number) {
  if (!Number.isFinite(value)) return '0.000000'
  const absoluteValue = Math.abs(value)
  if (absoluteValue >= 1000 || (absoluteValue > 0 && absoluteValue < 0.0001)) {
    return value.toExponential(6)
  }
  return value.toFixed(6)
}

async function handleShowAlignmentMatrix() {
  if (!props.bimAssetId || !props.pointcloudAssetId) {
    ElMessage.warning('缺少 BIM 或点云文件 ID，无法获取校准矩阵')
    return
  }
  if (loadingAlignmentMatrix.value) return

  loadingAlignmentMatrix.value = true
  try {
    let alignment = latestAlignmentResult.value
    if (!alignment) {
      const response = await getBimAlignment({
        modelScanFileId: props.pointcloudAssetId,
        modelBimFileId: props.bimAssetId,
      })
      alignment = response.data
    }

    if (!alignment) {
      ElMessage.warning('未获取到校准矩阵')
      return
    }

    latestAlignmentResult.value = alignment
    showAlignmentMatrixDialog.value = true
  } catch (error: any) {
    console.error('[BimPointcloudAlign] 获取校准矩阵失败', error)
    ElMessage.error(error?.message || '获取校准矩阵失败')
  } finally {
    loadingAlignmentMatrix.value = false
  }
}

async function handleCalibrationComplete() {
  if (fineAlignLoading.value) return
  const saved = await handleSaveAlignment()
  if (!saved || !props.bimAssetId || !props.pointcloudAssetId) return

  hasSavedAlignmentMatrix.value = true
  coarseAlignmentDirty.value = false
  ElMessage.success('校准矩阵已保存，可通过步骤条进入实模对比')
  // Reload the shell so the WebGL canvas is fully disposed and the upload
  // page recalculates its calibrated preview options from the backend.
  window.location.assign(`${window.location.origin}/upload`)
}

function activateCoarseRegistration() {
  registrationStage.value = 'coarse'
  fineAlignResult.value = null
}

function activateFineRegistration() {
  registrationStage.value = 'fine'
  fineAlignResult.value = null
  if (!hasSavedAlignmentMatrix.value || coarseAlignmentDirty.value) {
    ElMessage.warning('请先保存当前粗配准矩阵，再进行精细化配准')
  }
}

async function runFineAlignment() {
  if (!canRunFineAlignment.value || !props.bimAssetId || !props.pointcloudAssetId) return
  fineAlignLoading.value = true
  try {
    const response = await computeFineAlignment({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
      rmseRegressRatio: fineRmseRegressRatio.value,
      fitnessRegressRatio: fineFitnessRegressRatio.value,
      applyWhenRegressed: fineApplyWhenRegressed.value,
    })
    fineAlignResult.value = response.data
    const result = response.data
    const current = latestAlignmentResult.value
    const preview: BimAlignmentResult = {
      modelId: current?.modelId ?? 0,
      modelScanFileId: result.modelScanFileId,
      modelBimFileId: result.modelBimFileId,
      modelRotationQx: result.modelRotationQx,
      modelRotationQy: result.modelRotationQy,
      modelRotationQz: result.modelRotationQz,
      modelRotationQw: result.modelRotationQw,
      modelTranslationX: result.modelTranslationX,
      modelTranslationY: result.modelTranslationY,
      modelTranslationZ: result.modelTranslationZ,
      modelMatrix: result.modelMatrix,
      modelRmse: result.metrics?.fineRmse ?? 0,
      modelMaxError: current?.modelMaxError ?? 0,
      modelPairCount: current?.modelPairCount ?? 0,
      modelInlierCount: current?.modelInlierCount ?? 0,
    }
    latestAlignmentResult.value = preview
    restoredSavedAlignmentKey = ''
    tryRestoreSavedAlignment(preview)
    const metrics = result.metrics
    ElMessage.success(`精细化配准完成：RMSE ${Number(metrics?.fineRmse ?? 0).toFixed(4)} m`)
  } catch (error: any) {
    ElMessage.error(error?.message || '精细化配准失败')
  } finally {
    fineAlignLoading.value = false
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
    dracoLoader.setDecoderPath('/draco/')
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

    const savedPointcloudColor = normalizePointcloudColor(assetDetail.pointcloudColor || '', '')
    pointcloudColorOverridden.value = Boolean(savedPointcloudColor)
    pointcloudColor.value = savedPointcloudColor || '#ffffff'
    persistedPointcloudColor.value = savedPointcloudColor || '#ffffff'

    const url = getPointcloudTilesetUrl(assetDetail.tilesetUrl)
    const resourceBasePath = getTileResourceBasePath(assetDetail)
    const resourceBaseUrl = normalizeBackendUrl(resourceBasePath)
    const nextTileset = new TilesRenderer(url)
    // Keep the parent tile visible while finer children load so zooming never
    // causes a temporary drop in point density.
    nextTileset.displayActiveTiles = true
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
    dracoLoader.setDecoderPath('/draco/')
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
  onClippingFaceChange()
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
  await Promise.all([loadMeshAlgorithms(), refreshMeshStatus()])
  await initScene()
  await preloadFromRoute()
  void loadLatestC2M()
  // Tile loading and GLTF loading finish independently. Make one final
  // restore attempt after both route preload tasks have settled.
  void fetchAndLogSavedAlignmentIfExists()
})

onBeforeUnmount(() => {
  clearAnalysis()
  clearMeshStatusPolling()
  clearPointcloudColorSaveTimer()
  clearLoadedRemeshMesh()
  clearC2MScene()
  pointcloudRootReady = false
  loggedSavedAlignmentKey = ''
  endClipDrag()
  stopRenderLoop()
  resizeObserver?.disconnect()
  controls?.dispose()
  if (scene && transformHelper) {
    scene.remove(transformHelper)
  }
  transformControls?.dispose()
  tileset?.dispose?.()
  renderer?.domElement?.removeEventListener?.('pointerdown', handleViewportPointerDown)
  renderer?.domElement?.removeEventListener?.('pointermove', onViewportPointerMove)
  renderer?.domElement?.removeEventListener?.('pointerup', onViewportPointerUp)
  renderer?.domElement?.removeEventListener?.('pointercancel', onViewportPointerCancel)
  renderer?.dispose()
  raycaster = null
  clearClipHandles()
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
    <GlobalAnalysisToolbar
      v-model:collapsed="analysisToolbarCollapsed"
      :mode="analysisMode"
      :disabled="!hasModel"
      @update:mode="selectAnalysisMode"
      @clear="clearAnalysis"
    />
    <ViewerAnalysisOverlay
      :mode="analysisMode"
      :point="analysisPoint"
      :distance="analysisDistance"
      @clear="clearAnalysis"
    />
    <header class="topbar">
      <div class="topbar-left">
        <h1 class="brand-title">
          BIM 与点云校准 - {{ bimDisplayName || 'BIM 模型' }}
        </h1>
        <div class="topbar-center">
        </div>
      </div>

      <div class="topbar-right">
        <el-button :icon="ArrowLeft" @click="closePage">返回</el-button>
        <el-button :loading="loadingAlignmentMatrix" :disabled="!bimAssetId || !pointcloudAssetId" @click="handleShowAlignmentMatrix">校准矩阵</el-button>
        <el-button type="primary" :disabled="!canSaveCalibration" @click="handleCalibrationComplete">
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
          </div>
        </el-tooltip>

        <el-tooltip :content="showMeshWireframe ? '关闭线框' : '显示线框'" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--svg"
              :class="{ 'is-on': showMeshWireframe }"
              circle
              text
              :disabled="!hasModel"
              @click="toggleMeshWireframe"
            >
              <svg class="tool-btn__svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="3" y="9" width="12" height="12" />
                <rect x="9" y="3" width="12" height="12" />
                <line x1="3" y1="9" x2="9" y2="3" />
                <line x1="15" y1="9" x2="21" y2="3" />
                <line x1="15" y1="21" x2="21" y2="15" />
                <line x1="3" y1="21" x2="9" y2="15" />
              </svg>
            </el-button>
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
              :icon="bimVisible ? Hide : View"
              :disabled="!hasModel"
              @click="toggleBimVisibility"
            />
            <span class="tool-label">{{ bimVisibilityLabel }}</span>
          </div>
        </el-tooltip>

        <el-tooltip :content="pointcloudVisibilityLabel" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--svg"
              :class="{ 'is-on': hasTileset && pointcloudVisible }"
              circle
              text
              :disabled="!hasTileset"
              @click="togglePointcloudVisibility"
            >
              <svg class="tool-btn__svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <circle cx="5" cy="17" r="1.3" />
                <circle cx="9" cy="8" r="1.5" />
                <circle cx="14" cy="14" r="1.1" />
                <circle cx="18" cy="6" r="1.4" />
                <circle cx="7" cy="13" r="0.9" />
                <circle cx="16" cy="10" r="1.1" />
                <circle cx="12" cy="19" r="1.2" />
                <circle cx="20" cy="15" r="1" />
                <circle cx="4" cy="10" r="0.8" />
                <circle cx="11" cy="5" r="0.9" />
                <circle cx="19" cy="19" r="0.8" />
                <circle cx="3" cy="5" r="1" />
              </svg>
            </el-button>
          </div>
        </el-tooltip>

      </aside>

      <div ref="viewportEl" class="viewport"></div>

      <aside v-if="showPanel" class="right-panel">
         <div class="panel-section registration-edit-panel">
          <div class="section-title">配准</div>
          <div class="control-row registration-mode-row">
            <el-switch v-model="editMode" @change="onEditModeChange" />
            <span class="label">粗配准（手动）</span>
          </div>
          <div class="edit-target-row">
            <button class="edit-target-btn" :class="{ 'is-active': registrationStage === 'coarse' }" :disabled="!hasModel && !hasTileset" @click="activateCoarseRegistration">粗配准</button>
            <button class="edit-target-btn" :class="{ 'is-active': registrationStage === 'fine' }" :disabled="!hasSavedAlignmentMatrix" @click="activateFineRegistration">精细化配准</button>
          </div>
          <template v-if="registrationStage === 'fine'">
            <div class="fine-params">
              <div class="fine-param-row">
                <span class="fine-param-label">负优化策略</span>
                <el-switch v-model="fineApplyWhenRegressed" :disabled="fineAlignLoading" active-text="告警但应用精调" inactive-text="仅告警不应用" inline-prompt @change="markFineAlignmentDirty" />
              </div>
              <div class="fine-threshold-grid">
                <div class="fine-threshold-item">
                  <span class="fine-threshold-label">RMSE 阈值</span>
                  <el-input-number v-model="fineRmseRegressRatio" :disabled="fineAlignLoading" :min="1" :max="2" :step="0.01" :precision="2" controls-position="right" @change="onFineRmseRegressRatioChange" />
                </div>
                <div class="fine-threshold-item">
                  <span class="fine-threshold-label">Fitness 阈值</span>
                  <el-input-number v-model="fineFitnessRegressRatio" :disabled="fineAlignLoading" :min="0.5" :max="1" :step="0.01" :precision="2" controls-position="right" @change="onFineFitnessRegressRatioChange" />
                </div>
              </div>
              <button class="fine-reset-link" :disabled="fineAlignLoading" @click="resetFineThresholdDefaults">恢复默认阈值</button>
            </div>
            <div class="fine-actions">
              <el-button type="primary" :loading="fineAlignLoading" :disabled="!canRunFineAlignment" style="width: 100%" @click="runFineAlignment">开始计算</el-button>
              <el-button :loading="savingCalibration" :disabled="!canSaveFineAlignment" style="width: 100%; margin-left: 0" @click="saveFineAlignmentMatrix">保存配准结果</el-button>
            </div>
            <div v-if="fineRunBlockedReason" class="fine-actions__hint">{{ fineRunBlockedReason }}</div>
            <div v-if="fineAlignResult" class="fine-result" :class="{ 'fine-result--warning': fineAlignResult.regressed }">
              <div class="fine-result__title">{{ fineAlignResult.regressed ? '精调结果出现退化告警' : '精调结果' }}</div>
              <div class="fine-result__grid">
                <span>RMSE <strong>{{ Number(fineAlignResult.metrics?.fineRmse ?? 0).toFixed(4) }} m</strong></span>
                <span>Fitness <strong>{{ Number(fineAlignResult.metrics?.fineFitness ?? 0).toFixed(4) }}</strong></span>
                <span>位移变化 <strong>{{ Number(fineAlignResult.metrics?.deltaTranslationM ?? 0).toFixed(3) }} m</strong></span>
                <span>旋转变化 <strong>{{ Number(fineAlignResult.metrics?.deltaRotationDeg ?? 0).toFixed(3) }} deg</strong></span>
                <span>耗时 <strong>{{ Number(fineAlignResult.metrics?.elapsedS ?? 0).toFixed(1) }} s</strong></span>
                <span>点数 <strong>{{ fineAlignResult.metrics?.sourceTotalPoints ?? 0 }} / {{ fineAlignResult.metrics?.targetPoints ?? 0 }}</strong></span>
              </div>
            </div>
          </template>
          <template v-else>
            <div class="coarse-actions">
              <el-button
                type="primary"
                size="small"
                :loading="savingCalibration"
                :disabled="!canSaveCoarseAlignment"
                @click="saveCoarseAlignmentMatrix"
              >
                保存粗配准矩阵
              </el-button>
              <div class="coarse-actions__hint">{{ coarseSaveHint }}</div>
            </div>
          </template>
          <div class="section-divider" aria-hidden="true"></div>
          <div class="edit-target-row" :class="{ disabled: !editMode }">
            <button class="edit-target-btn" :class="{ 'is-active': selectedItemId === 'bim' }" :disabled="!editMode || !hasModel" @click="selectSceneObject('bim')">模型调整</button>
            <button class="edit-target-btn" :class="{ 'is-active': selectedItemId === 'pointcloud' }" :disabled="!editMode || !hasTileset" @click="selectSceneObject('pointcloud')">点云调整</button>
            <el-button size="small" :disabled="!editMode || !selectedItemId" @click="focusSelected">定位</el-button>
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
                <div class="slider__controls">
                  <input class="axis-number-input" :value="positionOffsetX" type="number" inputmode="decimal" :step="positionAdjustStep" :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud" @input="onPositionNumberInput('x', $event)" @blur="onPositionNumberBlur('x', $event)" @keydown="onPositionNumberKeydown($event, 'x')" />
                  <span class="slider__hint">m</span>
                </div>
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
                <div class="slider__controls">
                  <input class="axis-number-input" :value="positionOffsetY" type="number" inputmode="decimal" :step="positionAdjustStep" :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud" @input="onPositionNumberInput('y', $event)" @blur="onPositionNumberBlur('y', $event)" @keydown="onPositionNumberKeydown($event, 'y')" />
                  <span class="slider__hint">m</span>
                </div>
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
                <div class="slider__controls">
                  <input class="axis-number-input" :value="positionOffsetZ" type="number" inputmode="decimal" :step="positionAdjustStep" :disabled="!editMode || !selectedItemId || selectedItemIsPointcloud" @input="onPositionNumberInput('z', $event)" @blur="onPositionNumberBlur('z', $event)" @keydown="onPositionNumberKeydown($event, 'z')" />
                  <span class="slider__hint">m</span>
                </div>
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
        <div class="panel-section mesh-remesh-panel">
          <div class="section-title">网格均匀化</div>
          <div class="mesh-remesh-status" :class="`mesh-remesh-status--${meshStatus?.status || 'idle'}`">
            {{ meshStatusText }}
          </div>
          <div class="control-row">
            <span class="label">算法</span>
            <el-select
              v-model="meshAlgorithm"
              size="small"
              popper-class="bpa-right-popper"
              :disabled="meshControlsDisabled || !meshAlgorithms.length"
            >
              <el-option v-for="algorithm in meshAlgorithms" :key="algorithm.name" :label="algorithm.label" :value="algorithm.name" />
            </el-select>
          </div>
          <div class="control-row">
            <span class="label">目标边长 (m)</span>
            <el-input-number v-model="meshTargetEdgeLength" :min="0.05" :max="5" :step="0.05" :precision="3" size="small" :disabled="meshControlsDisabled" />
          </div>
          <el-button type="primary" size="small" style="width: 100%" :loading="meshRunning" :disabled="!bimAssetId || !meshAlgorithms.length || meshTaskActive" @click="runMeshRemesh">
            {{ meshActionText }}
          </el-button>
          <el-button
            v-if="meshReady"
            size="small"
            style="width: 100%; margin-top: 8px"
            :loading="remeshLoading"
            :disabled="!canLoadRemesh"
            @click="loadRemeshResult"
          >
            {{ remeshMeshLoaded ? '重新加载结果' : '加载结果到场景' }}
          </el-button>
          <div v-if="remeshMeshLoaded" class="mesh-remesh-visual-controls">
            <el-button size="small" @click="toggleRemeshSolid">
              {{ remeshSolidHidden ? '显示实体' : '隐藏实体' }}
            </el-button>
            <el-button v-if="remeshWireAvailable" size="small" @click="toggleRemeshWire">
              {{ remeshWireHidden ? '显示线框' : '隐藏线框' }}
            </el-button>
            <el-button v-else size="small" disabled>面数过多，跳过线框</el-button>
            <el-button
              v-if="remeshRestoreAvailable"
              size="small"
              type="warning"
              @click="restoreRemeshScene"
            >
              复原场景
            </el-button>
          </div>
          <div v-if="meshStats" class="mesh-remesh-stats">
            顶点 {{ meshStats.vertexBefore.toLocaleString() }} → {{ meshStats.vertexAfter.toLocaleString() }}<br />
            面数 {{ meshStats.faceBefore.toLocaleString() }} → {{ meshStats.faceAfter.toLocaleString() }}
          </div>
          <div v-if="meshError" class="mesh-remesh-error">{{ meshError }}</div>
        </div>
        <div class="panel-section c2m-panel">
          <div class="section-title">Scan vs BIM 计算</div>
          <div class="control-row">
            <span class="label">降采样 (m)</span>
            <el-input-number v-model="c2mVoxelSize" :min="0.01" :max="1" :step="0.01" :precision="3" size="small" :disabled="!canRunC2M" />
          </div>
          <el-button type="primary" size="small" style="width: 100%" :loading="c2mRunning" :disabled="!canRunC2M" @click="runC2M">开始计算</el-button>
          <div class="c2m-actions">
            <el-button size="small" :disabled="!c2mResult?.coloredPlyAvailable || c2mRunning || c2mSceneLoading" :loading="c2mSceneLoading" @click="loadC2MToScene">加载到场景</el-button>
            <el-button size="small" :disabled="!c2mSceneLoaded" @click="clearC2MScene">清空场景</el-button>
            <el-button size="small" type="success" :disabled="!c2mSceneLoaded" @click="confirmC2MApply">{{ c2mApplied ? '已确认应用' : '确认应用' }}</el-button>
          </div>
          <div v-if="c2mError" class="mesh-remesh-error">{{ c2mError }}</div>
          <div v-if="c2mResult" class="c2m-result-summary">
            <div>点云降采样：{{ c2mResult.pointsBefore.toLocaleString() }} → {{ c2mResult.pointsAfter.toLocaleString() }}</div>
            <div>Min / Max：{{ c2mResult.stats.min.toFixed(4) }} m / {{ c2mResult.stats.max.toFixed(4) }} m</div>
            <div>Mean / P95：{{ c2mResult.stats.mean.toFixed(4) }} m / {{ c2mResult.stats.p95.toFixed(4) }} m</div>
            <div v-if="c2mResult.diagnostics?.bboxOverlapIoU !== undefined">BBox 重叠度：{{ (c2mResult.diagnostics.bboxOverlapIoU * 100).toFixed(1) }}%</div>
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
          <div class="control-row">
            <span class="label">点云颜色</span>
            <div class="color-row">
              <input
                v-model="pointcloudColor"
                class="color-picker"
                type="color"
                :disabled="!hasTileset"
                @input="handlePointcloudColorInput"
              />
              <input
                v-model="pointcloudColor"
                class="color-hex"
                type="text"
                :disabled="!hasTileset"
                @change="handlePointcloudColorChange"
              />
              <el-button
                size="small"
                :disabled="!hasTileset || pointcloudColorSaving"
                @click="resetPointcloudColor"
              >
                重置
              </el-button>
            </div>
          </div>
        </div>

        <div class="panel-section">
          <div class="section-title section-title--with-action">
            <span>剖切</span>
            <el-tooltip content="恢复原始状态" placement="top">
              <el-button
                class="section-icon-btn"
                circle
                text
                size="small"
                :icon="RefreshLeft"
                :disabled="!hasClippableContent"
                @click="resetClippingState"
              />
            </el-tooltip>
          </div>
          <div class="control-row">
            <span class="label">剖切</span>
            <el-switch v-model="enableClipping" :disabled="!hasClippableContent" />
          </div>
          <div class="control-row" :class="{ disabled: !enableClipping || !showBounds || !hasClippableContent }">
            <span class="label">剖切轴</span>
            <el-select
              v-model="clipAxis"
              popper-class="bpa-right-popper"
              :disabled="!enableClipping || !showBounds || !hasClippableContent"
            >
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

    <el-dialog
      v-model="showAlignmentMatrixDialog"
      title="BIM 与点云校准矩阵"
      width="min(720px, 92vw)"
      append-to-body
    >
      <div class="matrix-dialog">
        <div class="matrix-dialog__meta">
          <span>点云文件 ID: {{ pointcloudAssetId }}</span>
          <span>BIM 文件 ID: {{ bimAssetId }}</span>
        </div>
        <div v-if="alignmentMatrixRows.length === 4" class="matrix-dialog__matrix">
          <div class="matrix-dialog__label">T = [ R | t ]</div>
          <div class="matrix-dialog__lines">
            <p
              v-for="(row, rowIndex) in alignmentRtRows"
              :key="`matrix-row-${rowIndex}`"
              class="matrix-dialog__line"
            >
              [ {{ row.rotation.join('    ') }} | {{ row.translation }} ]
            </p>
            <p class="matrix-dialog__line matrix-dialog__line--bottom">
              [ {{ alignmentMatrixRows[3].join('    ') }} ]
            </p>
          </div>
        </div>
        <el-empty v-else description="暂无有效矩阵数据" :image-size="64" />
        <details class="matrix-dialog__raw">
          <summary>查看原始矩阵数据（列主序）</summary>
          <pre class="matrix-dialog__content">{{ alignmentMatrixRawText }}</pre>
        </details>
      </div>
    </el-dialog>
  </section>
</template>

<style lang="scss" scoped>
@use './index.scss';
</style>
