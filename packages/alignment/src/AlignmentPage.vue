<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Aim,
  Brush,
  Check,
  CircleCheck,
  Close,
  DArrowLeft,
  DArrowRight,
  Delete,
  Document,
  DocumentChecked,
  Download,
  EditPen,
  Edit,
  FullScreen,
  Grid,
  Hide,
  Histogram,
  Promotion,
  RefreshLeft,
  Moon,
  Sunny,
  View,
  ZoomIn,
  ZoomOut,
} from '@element-plus/icons-vue'
import * as THREE from 'three'
// InfiniteGroundGrid 随 core 发布（dev-hong 侧新增，已归入 viewer-core）。
import { InfiniteGroundGrid, getTilesetWorldBounds } from '@cloudbim/viewer-core'
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
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { TilesRenderer } from '3d-tiles-renderer'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import {
  AnalysisMeshSession,
  C2MHistogramLegend,
  DEFAULT_REBAR_SWEEP_PARAMS,
  MeasurementToolbar,
  PointCloudEdlPipeline,
  PointcloudAxesTriad,
  PointcloudColorRangeBar,
  PointcloudViewCube,
  REBAR_SWEEP_ALGORITHM,
  ViewerAnalysisOverlay,
  ViewerMeasurementBadge,
  ViewportToolGlyph,
  applyC2MVertexColors,
  backendRequest,
  computeC2M,
  computeDenoise,
  computeFineAlignment,
  computePointcloudPreprocess,
  createBimAlignment,
  createMeasurement,
  createUploadHeaders,
  deleteMeasurement,
  denoiseArtifactUrl,
  downloadRemeshResult,
  getAssetDetail,
  getAssetRepresentations,
  getBimAlignment,
  getBimGlbUrl,
  getBimMetadata,
  getC2MColoredPlyUrl,
  getC2MDistancesUrl,
  getLatestC2M,
  getLatestDenoise,
  getPointcloudPreprocess,
  getPointcloudTilesetUrl,
  getRemeshStatus,
  histogramFromC2MDistances,
  isC2MResultFresh,
  listMeasurements,
  downloadC2MReportJSON,
  parseAnalysisC2MManifest,
  parseAnalysisMeshDistances,
  parseC2MDistances,
  recolorC2M,
  remeshBimAsset,
  resolveAnalysisArtifactURL,
  resolveC2MRangeMm,
  sampleC2MDeviationAtPick,
  selectPointcloudRepresentation,
  summarizeC2MRange,
  updateAssetAppearance,
  verifyPayloadHash,
  verifyPositionStreamHash,
  type AnalysisArea,
  type AnalysisC2MStats,
  type AnalysisDistance,
  type AnalysisMode,
  type AnalysisPoint,
  type BimAlignmentResult,
  type C2MColorMode,
  type C2MRangeMode,
  type C2MResult,
  type C2MVisualization,
  type CameraPose,
  type DenoiseResult,
  type FineAlignmentResult,
  type MeasurementKind,
  type PointcloudColorRamp,
  type PointcloudColorRange,
  type PointcloudPreprocessResult,
  type RebarComparisonBar,
  type RemeshStats,
  type RemeshStatus,
  type TileRendererEvents,
  type ViewerAssetPairProps,
  type ViewerMeasurementBadgeOverlay,
  type ViewerPageEmits,
  type ViewerStepProps,
} from '@cloudbim/viewer-core'
import { bindTransformChangeEvents } from './transformChangeEvents'
// 配准矩阵换算（保存 / 恢复共用同一套数学与测试）：
// packages/viewer-core/src/features/alignment/matrix.ts
import {
  alignmentMatrixFromResult,
  desiredBimWorldMatrix,
  modelPairsFromRigidTransform,
  rawWorldMatrixForAlignment,
  recordNormalizationOffset,
  scanToBimRigidTransform,
  toLocalMatrix,
} from '@cloudbim/viewer-core'
// 报告封面品牌标记随包发布（构建时内联为 data URI）。
import reportBrandMark from './assets/brand-mark.ico'
// 去噪能力由 @cloudbim/denoise 提供：本页只负责把它接进四步流程。
import {
  DenoisePanel,
  applyDenoisePreviewAppearance,
  parseDenoisePreview,
  type DenoiseColorMode,
} from '@cloudbim/denoise'
import { dimComparisonGeometry, rememberComparisonGeometryColors, filterComparisonGeometry, comparisonBarsAtTolerance, isRebarInspectionAbnormal, rebarStatusLabel, rebarReportCSV } from './rebarComparison'
// dev-hong 侧新增的钢筋巡检、调试与报告组件。
import RebarDebugPanel from './RebarDebugPanel.vue'
import { debugInventoryBars, debugGeometryBounds, debugNormalArrows, debugFaceNormalArrows, debugMeshCounts, observedRadialNormal, type RebarDebugInventory } from './rebarDebug'
import RebarDeviationDetail from './RebarDeviationDetail.vue'
import RebarInspectionSummary from './RebarInspectionSummary.vue'
import RebarReportHistory from './RebarReportHistory.vue'
// dev-hong 侧的视口工具按钮图标：随包发布（构建时按 assetsInlineLimit 内联）。
import toushiIcon from './assets/images/toushi.png'
import zhengjiaoIcon from './assets/images/zhengjiao.png'

// ---------------------------------------------------------------------------
// URL 查询参数垫片
// 包不依赖 vue-router（契约是 props 进、事件出），但 dev-hong 侧的钢筋调试面板需要
// 可分享的 URL 状态（?debug=rebar&debugBar=<id>）。这里只做「读写当前地址栏 query」
// 这一件事；步骤流转仍走 props/emit，宿主路由（若有）照常工作。
// ---------------------------------------------------------------------------
function readLocationQuery(): Record<string, string> {
  if (typeof window === 'undefined') return {}
  return Object.fromEntries(new URLSearchParams(window.location.search))
}

const route = reactive({
  path: typeof window === 'undefined' ? '' : window.location.pathname,
  query: readLocationQuery(),
})

const router = {
  replace: (payload: { query?: Record<string, unknown> }) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(payload?.query ?? {})) {
      if (value === null || value === undefined || value === '') continue
      search.set(key, String(value))
    }
    const url = `${window.location.pathname}${search.size ? `?${search.toString()}` : ''}`
    window.history.replaceState(window.history.state, '', url)
    route.path = window.location.pathname
    route.query = readLocationQuery()
  },
  resolve: (payload: { path?: string; query?: Record<string, unknown> }) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(payload?.query ?? {})) {
      if (value === null || value === undefined || value === '') continue
      search.set(key, String(value))
    }
    return {
      href: `${payload?.path ?? route.path}${search.size ? `?${search.toString()}` : ''}`,
    }
  },
}

/** 包内只需要 query 里的项目名，避免依赖宿主的 navigation helper。 */
function readNavigationRouteState(_path: string, query: Record<string, unknown>) {
  const projectName = typeof query?.projectName === 'string' ? query.projectName : ''
  return { projectName }
}

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
type SelectedItemId = '' | 'bim'
type TransformMode = 'translate' | 'rotate'
type ViewerTransformControls = TransformControls &
  THREE.Object3D & {
    visible: boolean
  }

const props = defineProps<ViewerAssetPairProps & ViewerStepProps>()
const emit = defineEmits<ViewerPageEmits>()

type RegistrationStage = 'coarse' | 'fine'
type WorkflowStepId = 1 | 2 | 3 | 4
const workflowSteps = [
  { id: 1 as const, title: '点云与工程坐标配准', subtitle: '调整 BIM 与点云位置' },
  { id: 2 as const, title: '点云分类与去噪', subtitle: '基于设计模型清理扫描点云' },
  { id: 3 as const, title: '偏差对比', subtitle: '查看 Scan vs BIM 偏差' },
  { id: 4 as const, title: '出报告', subtitle: '生成分析成果报告' },
]
const ALL_WORKFLOW_STEP_IDS: WorkflowStepId[] = [1, 2, 3, 4]
/** 宿主可用 `steps` 只开放部分步骤（例如 `:steps="[1, 2]"` 只要配准 + 去噪）。 */
const allowedWorkflowStepIds = computed<WorkflowStepId[]>(() => {
  const requested = (props.steps ?? [])
    .map((step) => Number(step))
    .filter((step): step is WorkflowStepId => ALL_WORKFLOW_STEP_IDS.includes(step as WorkflowStepId))
  if (!requested.length) return ALL_WORKFLOW_STEP_IDS
  return ALL_WORKFLOW_STEP_IDS.filter((step) => requested.includes(step))
})
const visibleWorkflowSteps = computed(() =>
  workflowSteps.filter((step) => allowedWorkflowStepIds.value.includes(step.id)),
)
const activeWorkflowStep = ref<WorkflowStepId>(1)
const workflowRouteReady = ref(false)
const reportEditing = ref(false)
const reportToolbarCollapsed = ref(false)
const reportZoom = ref(70)
const reportWorkspaceEl = ref<HTMLElement | null>(null)
const reportFullscreen = ref(false)
const reportTitle = ref('BIM 与点云校准报告')
const reportProjectName = ref(readNavigationRouteState(route.path, route.query).projectName || '未填写')
const reportOrganization = ref('未填写')
const reportInspectors = ref('未填写')
const reportReviewer = ref('未填写')
const reportDate = ref(new Date().toLocaleDateString('zh-CN'))
const reportFormat = ref<'pdf' | 'docx' | 'xls' | 'dxf' | 'json'>('pdf')
const reportContents = ref([
  { id: 'summary', title: '偏差对比摘要', enabled: true, locked: true, group: '基础信息' },
  { id: 'statistics', title: '偏差统计与分布', enabled: true, locked: false, group: '偏差分析' },
  { id: 'histogram', title: '偏差直方图', enabled: true, locked: false, group: '偏差分析' },
  { id: 'conclusion', title: '结论与建议', enabled: true, locked: false, group: '结论' },
])
const reportEnabledCount = computed(() => reportContents.value.filter((item) => item.enabled).length)
const registrationStage = ref<RegistrationStage>('coarse')
const fineAlignLoading = ref(false)
const fineAlignResult = ref<FineAlignmentResult | null>(null)
const fineApplyWhenRegressed = ref(false)
const fineRmseRegressRatio = ref(1.05)
const fineFitnessRegressRatio = ref(0.95)
const analysisMode = ref<AnalysisMode>('none')
const analysisPoint = ref<AnalysisPoint | null>(null)
const analysisDistance = ref<AnalysisDistance | null>(null)
const analysisPoints = ref<AnalysisPoint[]>([])
const analysisDistances = ref<AnalysisDistance[]>([])
const analysisAreas = ref<AnalysisArea[]>([])
const analysisToolbarCollapsed = ref(true)
const pointcloudCameraPose = ref<CameraPose | null>(null)
const pointcloudColorMode = ref<'rgb' | 'intensity'>('rgb')
const pointcloudColorRamp = ref<PointcloudColorRamp>('grayscale')
const pointcloudColorRange = ref<PointcloudColorRange>({ min: 0, max: 1 })
const pointcloudIntensityHistogram = ref<number[]>([])
const pointcloudPointSize = ref(2.5)
const pointcloudShowAxes = ref(true)
const hasSavedAlignmentMatrix = ref(false)
const sceneAlignmentReady = ref(false)
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
  if (!hasSavedAlignmentMatrix.value) return '请先完成粗配准保存'
  if (coarseAlignmentDirty.value) return '粗配准存在未保存的变换修改'
  if (fineAlignLoading.value) return '精细化配准计算中...'
  return ''
})
const canSaveCalibration = computed(() => !!bimLoaded.value && !!pointcloudLoaded.value &&
  !savingCalibration.value && !fineAlignLoading.value &&
  (registrationStage.value === 'coarse' || !!fineAlignResult.value))
const canSaveFineAlignment = computed(() =>
  registrationStage.value === 'fine' && !!fineAlignResult.value && !fineAlignLoading.value,
)
const canSaveCoarseAlignment = computed(() =>
  !!bimLoaded.value && !!pointcloudLoaded.value && registrationStage.value === 'coarse' && !savingCalibration.value,
)
const canOpenDenoiseStep = computed(() =>
  Boolean(props.bimAssetId && props.pointcloudAssetId && pointcloudLoaded.value && !pointcloudPreprocessRequired.value && hasSavedAlignmentMatrix.value && sceneAlignmentReady.value && !coarseAlignmentDirty.value),
)

const denoiseResult = ref<DenoiseResult | null>(null)
const denoiseRunning = ref(false)
const denoiseLoading = ref(false)
const denoiseError = ref('')
const denoiseView = ref<'source' | 'classes' | 'cleaned'>('source')
const denoisePreviewLoading = ref(false)
const denoiseColorMode = ref<DenoiseColorMode>('cleaned')
const denoiseVisibleClasses = ref<number[]>([3])
const denoiseVisiblePointCount = ref(0)
const pointcloudPreprocessResult = ref<PointcloudPreprocessResult | null>(null)
const pointcloudPreprocessRequired = ref(false)
const pointcloudPreprocessRunning = ref(false)
const pointcloudPreprocessError = ref('')
const canOpenDeviationStep = computed(() => canOpenDenoiseStep.value && denoiseResult.value?.fresh === true && denoiseResult.value.result.instanceContract === 'rebar-instance-map-v1' && !denoiseRunning.value && !denoiseLoading.value)
let denoiseRequestId = 0
let denoisePreviewRequestId = 0
let denoisePreviewPromise: Promise<void> | null = null
let denoiseRequestedView: 'source' | DenoiseColorMode = 'source'
let comparisonInventoryPromise: Promise<void> | null = null
let denoisePreview: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial> | null = null
const reportGeometryRevision = ref(0)

function workflowStepDisabled(step: WorkflowStepId): boolean {
  if (denoiseRunning.value || c2mRunning.value) return step !== activeWorkflowStep.value
  if (step === 1) return false
  if (step === 2) return !canOpenDenoiseStep.value
  return !canOpenDeviationStep.value
}

function openWorkflowStep(step: WorkflowStepId) {
  showPointcloudSettings.value = false
  showAdvancedSettings.value = false
  if (workflowStepDisabled(step)) {
    ElMessage.warning(step >= 3 ? '请先完成当前配准下的点云去噪' : '请先完成并保存点云与工程坐标配准')
    return
  }
  if (step === activeWorkflowStep.value) return
  reportEditing.value = false
  reportFullscreen.value = false
  activeWorkflowStep.value = step
  showPanel.value = true
  if (step === 1) editMode.value = true
  if (step >= 2) {
    editMode.value = false
    if (step !== 4 && denoiseResult.value?.fresh) void showDenoisePreview(denoiseColorMode.value)
    if (step === 3) void prepareRebarComparisonScene()
  } else {
    void showDenoisePreview('source')
  }
  if (step < 3) restoreBimVisibilityAfterC2M()
  else if (c2mSceneLoaded.value) hideBimWhileC2MIsLoaded()
  applySceneVisibility()
  syncWireframeStateFromCurrentMesh()
}

function clearDenoisePreview() {
  resetRebarInspection()
  denoisePreviewRequestId++
  denoisePreviewPromise = null
  denoiseRequestedView = 'source'
  comparisonInventoryPromise = null
  denoisePreview?.removeFromParent()
  denoisePreview?.geometry.dispose()
  denoisePreview?.material.dispose()
  denoisePreview = null
  comparisonInventory.value = null
  selectedComparisonBarId.value = ''
  denoiseView.value = 'source'
  denoisePreviewLoading.value = false
  denoiseVisibleClasses.value = [3]
  denoiseVisiblePointCount.value = 0
  denoiseColorMode.value = 'cleaned'
  applySceneVisibility()
  requestRender()
}

async function loadLatestDenoise() {
  const id = ++denoiseRequestId
  denoiseLoading.value = true
  denoiseError.value = ''
  try {
    const response = await getLatestDenoise(props.pointcloudAssetId!, props.bimAssetId!)
    if (id !== denoiseRequestId) return
    if (response.data?.version !== denoiseResult.value?.version || !response.data?.fresh) clearDenoisePreview()
    denoiseResult.value = response.data
    if (response.data?.fresh && activeWorkflowStep.value >= 2 && !denoisePreview) await showDenoisePreview('cleaned')
  } catch (error) {
    if (id !== denoiseRequestId) return
    denoiseResult.value = null
    clearDenoisePreview()
    denoiseError.value = error instanceof Error ? error.message : '读取去噪结果失败'
  } finally {
    if (id === denoiseRequestId) denoiseLoading.value = false
  }
}

async function runDenoise() {
  if (!canOpenDenoiseStep.value || denoiseRunning.value) return
  const id = ++denoiseRequestId
  denoiseRunning.value = true
  denoiseError.value = ''
  clearDenoisePreview()
  try {
    const response = await computeDenoise(props.pointcloudAssetId!, props.bimAssetId!)
    if (id !== denoiseRequestId) return
    denoiseResult.value = response.data
    invalidateC2MResult('点云去噪结果已更新，请重新计算偏差')
    if (response.data.fresh) await showDenoisePreview('cleaned')
    ElMessage.success('点云分类与去噪完成')
  } catch (error) {
    if (id === denoiseRequestId) denoiseError.value = error instanceof Error ? error.message : '点云去噪失败'
  } finally {
    if (id === denoiseRequestId) denoiseRunning.value = false
  }
}

async function showDenoisePreview(mode: 'source' | 'classes' | 'cleaned') {
  denoiseRequestedView = mode
  if (mode === 'source') {
    denoiseView.value = 'source'
    applySceneVisibility()
    requestRender()
    return
  }
  // A result-only debug view needs no scan payload or instance palette.
  // Retain the requested mode so showing the scan can load it on demand.
  if (rebarDebugActive.value && !rebarDebugScan.value) return
  if (!denoiseResult.value?.fresh || !scene || !pointcloudGroup) return
  if (denoisePreview) {
    try {
      denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(denoisePreview.geometry, mode, denoiseVisibleClasses.value)
      denoiseColorMode.value = mode
      denoiseView.value = mode
      applySceneVisibility()
      requestRender()
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : '配色切换失败')
    }
    return
  }
  if (denoisePreviewPromise) return denoisePreviewPromise
  const result = denoiseResult.value
  const id = ++denoisePreviewRequestId
  const promise = loadDenoisePreview(result, id)
  denoisePreviewPromise = promise
  try { await promise } finally {
    if (denoisePreviewPromise === promise) denoisePreviewPromise = null
  }
}

async function loadDenoisePreview(result: DenoiseResult, id: number) {
  denoisePreviewLoading.value = true
  try {
    const buffer = await backendRequest<ArrayBuffer>(denoiseArtifactUrl(props.pointcloudAssetId!, props.bimAssetId!, result.version, 'preview.ply'), { responseType: 'arraybuffer' })
    if (id !== denoisePreviewRequestId || result.version !== denoiseResult.value?.version) return
    if (!scene || !pointcloudGroup) return
    const mode = denoiseRequestedView === 'source' ? denoiseColorMode.value : denoiseRequestedView
    const geometry = parseDenoisePreview(buffer, mode)
    denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(geometry, mode, denoiseVisibleClasses.value)
    const material = new THREE.PointsMaterial({ size: pointcloudPointSize.value, sizeAttenuation: false, vertexColors: true })
    denoisePreview = new THREE.Points(geometry, material)
    denoisePreview.matrixAutoUpdate = false
    scene.add(denoisePreview)
    // chen 侧的报告几何版本号 + dev-hong 侧的预览变换同步都需要保留。
    reportGeometryRevision.value += 1
    syncDenoisePreviewTransform()
    denoiseView.value = activeWorkflowStep.value === 1 ? 'source' : denoiseRequestedView
    denoiseColorMode.value = mode
    applySceneVisibility()
    requestRender()
  } catch (error) {
    if (id === denoisePreviewRequestId) ElMessage.error(error instanceof Error ? error.message : '去噪预览加载失败')
  } finally {
    if (id === denoisePreviewRequestId) denoisePreviewLoading.value = false
  }
}

function syncDenoisePreviewTransform() {
  if (!denoisePreview || !pointcloudGroup || !denoiseResult.value) return
  // Preview vertices are in scan coordinates, offset by previewOrigin. The
  // source can move after this preview loads (root tiles / saved alignment).
  pointcloudGroup.updateWorldMatrix(true, false)
  denoisePreview.parent?.updateWorldMatrix(true, false)
  denoisePreview.matrix.copy(denoisePreview.parent?.matrixWorld ?? new THREE.Matrix4()).invert()
    .multiply(getRawMatrixWorldForCalibration(pointcloudGroup))
    .multiply(new THREE.Matrix4().makeTranslation(...denoiseResult.value.result.previewOrigin))
  denoisePreview.updateMatrixWorld(true)
}

function setDenoiseVisibleClasses(classes: number[]) {
  if (!denoisePreview || denoiseView.value === 'source') return
  denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(denoisePreview.geometry, denoiseColorMode.value, classes)
  denoiseVisibleClasses.value = classes
  requestRender()
}

async function downloadDenoise() {
  const result = denoiseResult.value
  if (!result?.fresh) return
  try {
    const download = (version: string) => backendRequest<Blob>(
      denoiseArtifactUrl(props.pointcloudAssetId!, props.bimAssetId!, version, 'cleaned.las'),
      { responseType: 'blob' },
    )
    let blob: Blob
    try {
      blob = await download(result.version)
    } catch (error) {
      // A background recompute can replace the immutable artifact between
      // rendering the panel and clicking download. Refresh once and retry
      // with the version the backend now considers current.
      if ((error as any)?.response?.status !== 409) throw error
      const latest = (await getLatestDenoise(props.pointcloudAssetId!, props.bimAssetId!)).data
      if (!latest?.fresh) throw error
      denoiseResult.value = latest
      blob = await download(latest.version)
    }
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'denoised-steel.las'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '下载去噪点云失败') }
}

function enterReportEditor() {
  activeWorkflowStep.value = 4
  reportEditing.value = true
  showPanel.value = false
}

function leaveReportEditor() {
  reportEditing.value = false
  showPanel.value = true
}

function reportAction(action: 'export' | 'save' | 'publish') {
  if (action === 'export') {
    exportReport()
    return
  }
  const labels = {
    export: '导出报告',
    save: '保存报告草稿',
    publish: '发布报告',
  }
  ElMessage.info(`${labels[action]}功能将在报告内容接入后开放`)
}

async function exportReport() {
  if (!canUseC2MResult.value) { ElMessage.warning('请先完成当前输入下的逐钢筋对比'); return }
  if (reportFormat.value === 'xls') { downloadRebarReport(); return }
  if (reportFormat.value === 'json') { await downloadInspectionJSON(); return }
  if (reportFormat.value !== 'pdf') { ElMessage.info('当前支持 PDF 和 CSV 导出'); return }
  document.body.classList.add('is-printing-alignment-report')
  await nextTick()
  window.setTimeout(() => {
    window.print()
    window.setTimeout(() => document.body.classList.remove('is-printing-alignment-report'), 300)
  }, 0)
}

function fitReportPage() {
  const workspace = reportWorkspaceEl.value
  if (!workspace) return
  const availableWidth = Math.max(1, workspace.clientWidth - 48)
  const availableHeight = Math.max(1, workspace.clientHeight - 88)
  reportZoom.value = Math.max(10, Math.min(140, Math.floor(Math.min(availableWidth / 794, availableHeight / 1123) * 100)))
  workspace.scrollTo({ top: 0, left: 0 })
}

function changeReportZoom(delta: number) {
  reportZoom.value = Math.min(140, Math.max(10, reportZoom.value + delta))
}

function toggleReportFullscreen() {
  reportFullscreen.value = !reportFullscreen.value
}

function saveReportEdits() {
  reportEditing.value = false
  showPanel.value = true
  ElMessage.info('已完成本次编辑；内容仅保留在当前页面，导出后可保存到本地')
}

function updateReportField(field: 'title' | 'project' | 'organization' | 'inspectors' | 'reviewer' | 'date', event: FocusEvent) {
  if (!reportEditing.value) return
  const value = (event.target as HTMLElement).innerText.trim()
  if (!value) return
  if (field === 'title') reportTitle.value = value
  else if (field === 'project') reportProjectName.value = value
  else if (field === 'organization') reportOrganization.value = value
  else if (field === 'inspectors') reportInspectors.value = value
  else if (field === 'reviewer') reportReviewer.value = value
  else reportDate.value = value
}
const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('准备就绪')
const showPanel = ref(true)
const showAdvancedSettings = ref(false)
const showPointcloudSettings = ref(false)
const loadingBim = ref(false)
const loadingPointcloud = ref(false)
const savingCalibration = ref(false)
const meshCrossSectionSides = ref<number>(DEFAULT_REBAR_SWEEP_PARAMS.cross_section_sides)
const meshAxialSpacing = ref<number>(DEFAULT_REBAR_SWEEP_PARAMS.axial_spacing)
const meshMaxChordError = ref<number>(DEFAULT_REBAR_SWEEP_PARAMS.max_chord_error)
const meshRunning = ref(false)
const meshStatus = ref<RemeshStatus | null>(null)
const meshStats = ref<RemeshStats | null>(null)
const meshError = ref('')
const c2mRunning = ref(false)
const c2mResult = ref<C2MResult | null>(null)
const c2mVoxelSize = ref(0.002)
const c2mNormalConstraintEnabled = ref(true)
const c2mNormalMaxAngleDeg = ref(30)
const c2mMaxSearchDistanceMm = ref(200)
const c2mCalculationSettingsDirty = computed(() => {
  const effective = c2mResult.value?.diagnostics?.rebarComparison?.effective
  if (!effective) return false
  return effective.normalConstraintEnabled !== c2mNormalConstraintEnabled.value ||
    (c2mNormalConstraintEnabled.value && effective.normalMaxAngleDeg !== c2mNormalMaxAngleDeg.value) ||
    Math.abs((effective.maxSearchDistance ?? 0.2) * 1000 - c2mMaxSearchDistanceMm.value) > 1e-7
})
const c2mError = ref('')
const selectedComparisonBarId = ref('')
const comparisonInventory = ref<RebarDebugInventory | null>(null)
// Keep these guards above all comparison-derived computed values. Vue may
// evaluate those values during setup (for example through an eager watcher).
const c2mResultIsFresh = computed(() => isC2MResultFresh(c2mResult.value))
const canUseC2MResult = computed(() => Boolean(c2mResult.value && c2mResultIsFresh.value))
const comparison = computed(() => canUseC2MResult.value ? c2mResult.value?.diagnostics?.rebarComparison : undefined)
const comparisonBars = computed(() => comparisonBarsAtTolerance(comparison.value?.bars ?? [], c2mDistances.value, c2mToleranceMm.value / 1000))
const comparisonReportToleranceMm = computed(() => c2mDistances.value ? c2mToleranceMm.value : (c2mResult.value?.visualization?.toleranceLimit ?? 0.01) * 1000)
// dev-hong report pagination: 14 bars per report page, configurable detail page size.
const comparisonReportPages = computed(() => Array.from({ length: Math.ceil(comparisonBars.value.length / 14) }, (_, page) => comparisonBars.value.slice(page * 14, (page + 1) * 14)))
const reportBarsPerPage = ref(2)
const comparisonDetailPages = computed(() => Array.from({ length: Math.ceil(comparisonBars.value.length / reportBarsPerPage.value) }, (_, page) => comparisonBars.value.slice(page * reportBarsPerPage.value, (page + 1) * reportBarsPerPage.value)))
const comparisonMeasurementLabel = computed(() => {
  if (!comparison.value?.effective?.normalConstraintEnabled) return '设计钢筋顶点到对应实例的有符号最近点距离'
  if (comparison.value.inspection?.method === 'control-net-real-point-radial-correspondence-v1') return '控制网辅助的同侧表面法向偏差'
  return comparison.value.measurement?.method === 'observed-axis-same-side-normal-v3'
    ? `同侧表面的法向偏差（朝向容差 ${comparison.value.effective.normalMaxAngleDeg}°）`
    : `法向约束的垂直轴向偏差（双向 ${comparison.value.effective.normalMaxAngleDeg}°）`
})
// chen side report paging state stays available for the existing template.
const comparisonReportTotalPages = computed(() => 1 + comparisonReportPages.value.length)
const comparisonReportPageIndex = ref(0)
const comparisonReportPageCount = computed(() => comparisonReportPages.value.length)
function changeComparisonReportPage(delta: number) {
  comparisonReportPageIndex.value = Math.min(
    Math.max(comparisonReportPageIndex.value + delta, 0),
    Math.max(comparisonReportPageCount.value - 1, 0),
  )
}
const selectedComparisonBar = computed(() => comparisonBars.value.find(bar => bar.ifcGlobalId === selectedComparisonBarId.value))
const rebarInspectionActive = ref(false)
const rebarInspectionManualSelect = ref(false)
const rebarInspectionAbnormalOnly = ref(false)
const rebarInspectionBars = computed(() => rebarInspectionAbnormalOnly.value
  ? comparisonBars.value.filter(bar => isRebarInspectionAbnormal(bar, c2mToleranceMm.value / 1000))
  : comparisonBars.value)
const rebarInspectionIndex = computed(() => rebarInspectionBars.value.findIndex(bar => bar.ifcGlobalId === selectedComparisonBarId.value))
const comparisonBimVisibility = new WeakMap<THREE.Object3D, boolean>()
let rebarCameraAnimationFrame: number | null = null
let rebarCameraControlsWasEnabled: boolean | null = null
const rebarInspectionMaterialState = new WeakMap<THREE.Material, { color?: THREE.Color; opacity: number; transparent: boolean; depthWrite: boolean }>()
const c2mOriginalVertexColors = new WeakMap<THREE.BufferGeometry, Float32Array>()

const rebarDebugEnabled = ref(route.query.debug === 'rebar' || import.meta.env.MODE === 'rebar-debug')
const rebarDebugActive = computed(() => rebarDebugEnabled.value && activeWorkflowStep.value === 3)
const rebarDebugBars = computed(() => comparison.value ? comparisonBars.value : debugInventoryBars(comparisonInventory.value))
const rebarDebugBar = computed(() => rebarDebugBars.value.find(bar => bar.ifcGlobalId === selectedComparisonBarId.value))
const rebarDebugSurface = ref('result')
// Loading the directory first must not overwrite the desired result view.
const rebarDebugDisplaySurface = computed(() => !comparison.value && (rebarDebugSurface.value === 'result' || rebarDebugSurface.value === 'mesh') ? 'source' : rebarDebugSurface.value)
const rebarDebugScan = ref(false)
const rebarDebugCluster = ref('matched')
const rebarDebugNormals = ref(false)
const rebarDebugNormalMode = ref<'vertex' | 'face'>('vertex')
const rebarDebugAllNormals = ref(true)
const rebarDebugMeshCounts = ref({ vertices: 0, faces: 0 })
const rebarDebugScanNormals = ref(false)
const rebarDebugLengthMm = ref(10)
const rebarDebugLimit = ref(200)
const rebarDebugNormalCount = ref(0)
const rebarDebugScanNormalCount = ref(0)
let rebarDebugOverlay: THREE.Group | null = null
let rebarDebugFocusPending = false
const rebarDebugMaterials = new Map<THREE.MeshBasicMaterial, { vertexColors: boolean; color: THREE.Color }>()

function clearRebarDebugOverlay() {
  if (rebarDebugOverlay) { rebarDebugOverlay.removeFromParent(); disposeObject3D(rebarDebugOverlay) }
  rebarDebugOverlay = null
  rebarDebugNormalCount.value = 0
  rebarDebugMeshCounts.value = { vertices: 0, faces: 0 }
  rebarDebugScanNormalCount.value = 0
}

function comparisonMeshMatches(object: THREE.Object3D, ids: Set<string>) {
  let current: THREE.Object3D | null = object
  while (current && current !== bimPivot) {
    const candidates = [current.name, String(current.userData.ifcGlobalId ?? ''), guessIfcId(current.userData)]
    if (candidates.some(id => id && (ids.has(id) || ids.has(findMetadataElementById(id)?.id ?? '')))) return true
    current = current.parent
  }
  return false
}

function rebarDebugDesignObjects(): THREE.Mesh[] {
  const root = rebarDebugDisplaySurface.value === 'source' ? bimPivot : rebarDebugDisplaySurface.value === 'hidden' ? null : c2mSceneGroup
  const meshes: THREE.Mesh[] = []
  if (root?.visible) root.traverseVisible(object => { if (object instanceof THREE.Mesh) meshes.push(object) })
  return meshes
}

function rebarDebugBounds() {
  if (!rebarDebugActive.value || !rebarDebugBar.value) return null
  const box = new THREE.Box3()
  for (const mesh of rebarDebugDesignObjects()) box.union(debugGeometryBounds(mesh))
  if (denoisePreview?.visible) box.union(debugGeometryBounds(denoisePreview))
  return box.isEmpty() ? null : box.expandByScalar(.01)
}

function focusRebarDebug() {
  const box = rebarDebugBounds()
  if (!box) return
  fitCameraToBox(box)
  requestRender()
}

function selectRebarDebugBar(id: string) {
  rebarDebugFocusPending = true
  selectedComparisonBarId.value = id
  if (route.query.debug === 'rebar') void router.replace({ query: { ...route.query, debugBar: id } })
}

function moveRebarDebugBar(delta: number) {
  const index = rebarDebugBars.value.findIndex(bar => bar.ifcGlobalId === selectedComparisonBarId.value)
  const next = rebarDebugBars.value[Math.max(0, Math.min(rebarDebugBars.value.length - 1, index + delta))]
  if (next) selectRebarDebugBar(next.ifcGlobalId)
}

function selectRebarDebugResult() {
  rebarDebugSurface.value = 'result'
  // Dense scan points can cover the very surface carrying the deviation colors.
  rebarDebugScan.value = false
  rebarDebugFocusPending = true
}

async function showRebarDebugResult() {
  if (!canUseC2MResult.value) return
  selectRebarDebugResult()
  if (!c2mSceneLoaded.value && !c2mSceneLoading.value) await loadC2MToScene()
  applySceneVisibility()
}

async function showRebarDebugPair() {
  rebarDebugSurface.value = comparison.value ? 'mesh' : 'source'
  rebarDebugScan.value = true
  rebarDebugFocusPending = true
  applySceneVisibility()
  if (canUseC2MResult.value && !c2mSceneLoaded.value && !c2mSceneLoading.value) await loadC2MToScene()
}

function toggleRebarDebug() {
  rebarDebugEnabled.value = !rebarDebugEnabled.value
  const query = { ...route.query }
  if (rebarDebugEnabled.value) query.debug = 'rebar'
  else { delete query.debug; delete query.debugBar }
  void router.replace({ query })
}

function openRebarDebugWindow() {
  const url = router.resolve({ path: route.path, query: { ...route.query, step: '3', debug: 'rebar', debugBar: selectedComparisonBarId.value } })
  const target = new URL(url.href, window.location.origin)
  if (import.meta.env.DEV) target.port = '5174'
  window.open(target.href, '_blank', 'noopener,noreferrer')
}

function exportRebarDebug() {
  const bar = rebarDebugBar.value
  if (!bar) return
  const payload = {
    schema: 'rebar-viewer-debug-v1', exportedAt: new Date().toISOString(),
    scanAssetId: props.pointcloudAssetId, bimAssetId: props.bimAssetId,
    resultVersion: comparison.value ? c2mResult.value?.resultVersion : null,
    denoiseVersion: denoiseResult.value?.version, instanceMapHash: comparison.value?.instanceMapHash,
    algorithmVersion: c2mResult.value?.algorithmVersion, effective: comparison.value?.effective,
    measurement: comparison.value?.measurement, bar,
    instances: comparisonInventory.value?.instances.filter(row => row.designBarId === bar.designBarId),
    preview: { surface: rebarDebugSurface.value, cluster: rebarDebugCluster.value, visiblePointCount: denoiseVisiblePointCount.value,
      normalArrowLengthMm: rebarDebugLengthMm.value, sampleLimit: rebarDebugLimit.value,
      designNormalMode: rebarDebugNormalMode.value, designMeshCounts: rebarDebugMeshCounts.value,
      designNormalSampling: rebarDebugAllNormals.value ? 'all' : 'sampled',
      designNormalArrowCount: rebarDebugNormalCount.value,
      designMeshSource: rebarDebugDisplaySurface.value === 'source' ? 'original-display-model' : rebarDebugDisplaySurface.value === 'hidden' ? 'hidden' : 'comparison-result',
      scanNormalSource: 'saved-section radial reconstruction on preview samples; not solver correspondences' },
  }
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = `rebar-debug-${bar.ifcGlobalId}.json`; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function updateRebarDebugOverlay() {
  clearRebarDebugOverlay()
  if (!scene || !rebarDebugActive.value || !rebarDebugBar.value) return
  const group = new THREE.Group()
  group.name = 'rebar-debug-normals'
  const meshes = rebarDebugDesignObjects()
  for (const mesh of meshes) {
    const counts = debugMeshCounts(mesh.geometry)
    rebarDebugMeshCounts.value.vertices += counts.vertices
    rebarDebugMeshCounts.value.faces += counts.faces
  }
  if (rebarDebugNormals.value) for (const mesh of meshes) {
    const makeArrows = rebarDebugNormalMode.value === 'face' ? debugFaceNormalArrows : debugNormalArrows
    const limit = rebarDebugAllNormals.value ? Infinity : Math.max(1, Math.floor(rebarDebugLimit.value / meshes.length))
    const arrows = makeArrows(mesh, rebarDebugLengthMm.value / 1000, limit, 0x22d3ee)
    rebarDebugNormalCount.value += arrows.userData.arrowCount
    group.add(arrows)
  }
  if (rebarDebugScanNormals.value && denoisePreview?.visible && bimPivot && comparison.value?.measurement?.coordinateFrame === 'model') {
    const profile = rebarDebugBar.value.measurement?.longitudinalProfile ?? []
    const rowsByUnit = new Map<string, typeof profile>()
    for (const row of profile) { const rows = rowsByUnit.get(row.designUnitId) ?? []; rows.push(row); rowsByUnit.set(row.designUnitId, rows) }
    const instanceUnits = new Map((comparisonInventory.value?.instances ?? []).filter(row => row.reviewStatus === 'matched' && row.designBarId === rebarDebugBar.value!.designBarId).map(row => [row.id, row.designUnitId]))
    bimPivot.updateWorldMatrix(true, false)
    denoisePreview.updateWorldMatrix(true, false)
    const modelToWorld = getRawMatrixWorldForCalibration(bimPivot)
    const scanToModel = modelToWorld.clone().invert().multiply(denoisePreview.matrixWorld)
    const normalMatrix = new THREE.Matrix3().getNormalMatrix(modelToWorld)
    const instances = denoisePreview.geometry.getAttribute('instance')
    const arrows = debugNormalArrows(denoisePreview, rebarDebugLengthMm.value / 1000, rebarDebugLimit.value, 0xfb923c, (id, point) => {
      const unit = instanceUnits.get(instances?.getX(id))
      const rows = unit ? rowsByUnit.get(unit) : undefined
      if (!rows) return null
      return observedRadialNormal(point.clone().applyMatrix4(scanToModel), rows)?.applyMatrix3(normalMatrix).normalize() ?? null
    })
    rebarDebugScanNormalCount.value = arrows.userData.arrowCount
    group.add(arrows)
  }
  scene.add(group)
  rebarDebugOverlay = group
}

async function prepareRebarComparisonScene() {
  const result = denoiseResult.value
  if (!result?.fresh) return
  if (!comparisonInventory.value) {
    if (!comparisonInventoryPromise) {
      const promise = loadComparisonInventory(result, denoisePreviewRequestId)
      comparisonInventoryPromise = promise
      void promise.finally(() => {
        if (comparisonInventoryPromise === promise) comparisonInventoryPromise = null
      })
    }
    await comparisonInventoryPromise
  }
  if (result.version !== denoiseResult.value?.version || activeWorkflowStep.value !== 3) return
  applySceneVisibility()
  if (canUseC2MResult.value && !c2mSceneLoaded.value && !c2mSceneLoading.value) await loadC2MToScene()
}

async function loadComparisonInventory(result: DenoiseResult, requestId: number) {
  try {
    const inventory = await backendRequest<NonNullable<typeof comparisonInventory.value>>(
      denoiseArtifactUrl(props.pointcloudAssetId!, props.bimAssetId!, result.version, 'instance-map.json'),
    )
    if (requestId !== denoisePreviewRequestId || result.version !== denoiseResult.value?.version) return
    comparisonInventory.value = inventory
  } catch (error) {
    c2mError.value = error instanceof Error ? error.message : '钢筋对应关系加载失败'
  }
}

function applyComparisonSelection() {
  const debugging = rebarDebugActive.value
  const bar = debugging ? rebarDebugBar.value : selectedComparisonBar.value
  const resultBar = comparison.value?.bars.find(row => row.ifcGlobalId === bar?.ifcGlobalId)
  if (denoisePreview && activeWorkflowStep.value >= 3) {
    const ids = debugging ? !bar ? [] : rebarDebugCluster.value === 'review' ? bar.reviewInstanceIds ?? []
      : rebarDebugCluster.value === 'all' ? [...bar.instanceIds, ...(bar.reviewInstanceIds ?? [])] : bar.instanceIds : bar?.instanceIds
    denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(denoisePreview.geometry, 'classes', [3], ids)
  }
  if (c2mSceneGroup && comparison.value) c2mSceneGroup.traverse(object => {
    if (!(object instanceof THREE.Mesh)) return
    filterComparisonGeometry(object.geometry, resultBar)
    if (debugging && !resultBar) object.geometry.setIndex([])
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (!(material instanceof THREE.MeshBasicMaterial)) continue
      if (debugging && rebarDebugSurface.value === 'mesh') {
        if (!rebarDebugMaterials.has(material)) rebarDebugMaterials.set(material, { vertexColors: material.vertexColors, color: material.color.clone() })
        material.vertexColors = false; material.color.set('#cbd5e1'); material.needsUpdate = true
      } else {
        const saved = rebarDebugMaterials.get(material)
        if (saved) { material.vertexColors = saved.vertexColors; material.color.copy(saved.color); material.needsUpdate = true; rebarDebugMaterials.delete(material) }
      }
    }
  })
  updateRebarDebugOverlay()
  if (rebarDebugFocusPending && rebarDebugBounds()) { rebarDebugFocusPending = false; focusRebarDebug() }
  requestRender()
}

function downloadRebarReport(selectedOnly = false) {
  const result = c2mResult.value
  if (!canUseC2MResult.value || !result || !comparison.value) return
  const bars = selectedOnly && selectedComparisonBar.value ? [selectedComparisonBar.value] : comparisonBars.value
  const csv = rebarReportCSV(result, bars, comparisonReportToleranceMm.value)
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = selectedOnly ? '钢筋比对明细.csv' : '逐钢筋比对明细.csv'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const inspectionDownloading = ref(false)
async function downloadInspectionJSON() {
  const version = c2mResult.value?.resultVersion
  if (!canUseC2MResult.value || !version || inspectionDownloading.value) return
  const scanId = props.pointcloudAssetId, bimId = props.bimAssetId
  inspectionDownloading.value = true
  try {
    const blob = await downloadC2MReportJSON(version)
    if (scanId !== props.pointcloudAssetId || bimId !== props.bimAssetId || version !== c2mResult.value?.resultVersion) return
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `钢筋检测-${version}.json`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '检测 JSON 下载失败')
  } finally { inspectionDownloading.value = false }
}

watch(selectedComparisonBarId, applySceneVisibility)

function stopRebarCameraAnimation() {
  if (rebarCameraAnimationFrame !== null) {
    window.cancelAnimationFrame(rebarCameraAnimationFrame)
    rebarCameraAnimationFrame = null
  }
  if (controls && rebarCameraControlsWasEnabled !== null) {
    controls.enabled = rebarCameraControlsWasEnabled
    rebarCameraControlsWasEnabled = null
  }
}

function objectMatchesComparisonBar(object: THREE.Object3D, ifcGlobalId: string) {
  let current: THREE.Object3D | null = object
  while (current && current !== bimPivot) {
    const ids = [current.name, String(current.userData.ifcGlobalId ?? ''), guessIfcId(current.userData)]
    if (ids.some(id => id && (id === ifcGlobalId || findMetadataElementById(id)?.id === ifcGlobalId))) return true
    current = current.parent
  }
  return false
}

function comparisonBarBounds(bar: RebarComparisonBar) {
  const analysisBounds = c2mAnalysisSession?.queryBounds(bar.ifcGlobalId)
  if (analysisBounds && !analysisBounds.isEmpty()) return analysisBounds

  if (!c2mAnalysisSession && c2mSceneGroup) {
    const bounds = new THREE.Box3()
    const point = new THREE.Vector3()
    let found = false
    c2mSceneGroup.updateMatrixWorld(true)
    c2mSceneGroup.traverse(object => {
      if (!(object instanceof THREE.Mesh)) return
      const positions = object.geometry.getAttribute('position')
      const start = Math.max(0, bar.vertexStart)
      const end = Math.min(positions.count, start + bar.vertexCount)
      for (let index = start; index < end; index += 1) {
        point.fromBufferAttribute(positions, index).applyMatrix4(object.matrixWorld)
        bounds.expandByPoint(point)
        found = true
      }
    })
    if (found && !bounds.isEmpty()) return bounds
  }

  if (bimPivot) {
    const bounds = new THREE.Box3()
    let found = false
    bimPivot.updateMatrixWorld(true)
    bimPivot.traverse(object => {
      if (!(object instanceof THREE.Mesh) || !objectMatchesComparisonBar(object, bar.ifcGlobalId)) return
      bounds.expandByObject(object)
      found = true
    })
    if (found && !bounds.isEmpty()) return bounds
  }
  return null
}

function focusComparisonBar(bar: RebarComparisonBar, options: { manual?: boolean } = {}) {
  if (!activeCamera || !controls) return
  const bounds = comparisonBarBounds(bar)
  if (!bounds) return
  stopRebarCameraAnimation()

  const startPosition = activeCamera.position.clone()
  const startTarget = controls.target.clone()
  const startUp = activeCamera.up.clone().normalize()
  const endTarget = bounds.getCenter(new THREE.Vector3())
  const size = bounds.getSize(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z, 0.1)
  const isManualFocus = options.manual === true
  // Sequence navigation should show the whole member with breathing room;
  // a direct mouse pick can remain a tighter close-up for detail inspection.
  const framingMargin = isManualFocus ? 1.1 : 1.3
  const focusDim = Math.max(maxDim * framingMargin, 0.08)

  //巡检序列固定使用斜俯视方向，避免每个构件根据自身包围盒改变为侧视。
  //相机只平移、缩放到当前构件，连续巡检时阅读方向保持不变。
  // Keep the top-oriented reading direction, but expose a little depth so
  // the inspected member is not rendered as a perfectly flat plan view.
  const tilt = THREE.MathUtils.degToRad(18)
  const viewDirection = new THREE.Vector3(0, Math.cos(tilt), Math.sin(tilt)).normalize()
  const cameraDirection = viewDirection.clone().multiplyScalar(-1)
  const cameraUp = new THREE.Vector3(0, 0, -1)
    .addScaledVector(cameraDirection, -cameraDirection.dot(new THREE.Vector3(0, 0, -1)))
    .normalize()
  const cameraRight = cameraDirection.clone().cross(cameraUp).normalize()
  const projectedWidth = Math.max(
    Math.abs(cameraRight.x) * size.x + Math.abs(cameraRight.y) * size.y + Math.abs(cameraRight.z) * size.z,
    0.08,
  )
  const projectedHeight = Math.max(
    Math.abs(cameraUp.x) * size.x + Math.abs(cameraUp.y) * size.y + Math.abs(cameraUp.z) * size.z,
    0.08,
  )
  let distance = Math.max(focusDim * 0.42, 0.08)
  if (isPerspectiveCamera(activeCamera)) {
    const verticalFov = THREE.MathUtils.degToRad(activeCamera.fov)
    const aspect = Math.max(activeCamera.aspect || 1, 0.1)
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * aspect)
    const horizontalDistance = projectedWidth / 2 / Math.tan(horizontalFov / 2)
    const verticalDistance = projectedHeight / 2 / Math.tan(verticalFov / 2)
    distance = Math.max(
      horizontalDistance * framingMargin,
      verticalDistance * framingMargin,
      focusDim * 0.42,
    )
  }
  const endPosition = endTarget.clone().addScaledVector(viewDirection, distance)
  const startOrthoSize = orthoViewSize
  const aspect = isPerspectiveCamera(activeCamera) ? Math.max(activeCamera.aspect || 1, 0.1) : Math.max(viewportEl.value?.clientWidth || 1, 1) / Math.max(viewportEl.value?.clientHeight || 1, 1)
  const endOrthoSize = Math.max(projectedHeight * framingMargin, projectedWidth / aspect * framingMargin, 0.2)
  const startedAt = performance.now()
  // Give sequence navigation a little more time to read the member while still
  // feeling responsive for a direct mouse pick.
  const duration = isManualFocus ? 680 : 860
  const wasControlsEnabled = controls.enabled
  controls.enabled = false
  rebarCameraControlsWasEnabled = wasControlsEnabled

  const animate = (now: number) => {
    if (!activeCamera || !controls) return
    const progress = Math.min(1, (now - startedAt) / duration)
    // Smootherstep removes the small velocity kink at the midpoint and makes
    // the camera feel deliberate when stepping through a long inspection list.
    const eased = THREE.MathUtils.smootherstep(progress, 0, 1)
    activeCamera.position.lerpVectors(startPosition, endPosition, eased)
    controls.target.lerpVectors(startTarget, endTarget, eased)
    // Keep the camera roll continuous while the inspection view changes. The
    // previous implementation calculated cameraUp but never applied it,
    // which made sequence navigation appear to snap into a side view.
    activeCamera.up.lerpVectors(startUp, cameraUp, eased).normalize()
    if (isOrthographicCamera(activeCamera)) {
      orthoViewSize = THREE.MathUtils.lerp(startOrthoSize, endOrthoSize, eased)
      updateOrthographicFrustum()
    }
    activeCamera.lookAt(controls.target)
    controls.update()
    requestRender()
    if (progress < 1) rebarCameraAnimationFrame = window.requestAnimationFrame(animate)
    else {
      activeCamera.position.copy(endPosition)
      controls.target.copy(endTarget)
      activeCamera.up.copy(cameraUp)
      activeCamera.lookAt(controls.target)
      controls.update()
      rebarCameraAnimationFrame = null
      if (rebarCameraControlsWasEnabled !== null) {
        controls.enabled = rebarCameraControlsWasEnabled
        rebarCameraControlsWasEnabled = null
      }
    }
  }
  rebarCameraAnimationFrame = window.requestAnimationFrame(animate)
}

function pauseRebarInspection() {
  rebarInspectionActive.value = false
  applyComparisonSelection()
}

function selectInspectionBar(index: number) {
  const bars = rebarInspectionBars.value
  if (!bars.length) return false
  const normalized = Math.min(bars.length - 1, Math.max(0, index))
  rebarInspectionActive.value = true
  selectedComparisonBarId.value = bars[normalized].ifcGlobalId
  applyComparisonSelection()
  void nextTick(() => focusComparisonBar(bars[normalized], { manual: false }))
  return true
}

function stepRebarInspection(direction: -1 | 1) {
  if (!rebarInspectionBars.value.length) {
    ElMessage.warning(rebarInspectionAbnormalOnly.value ? '当前没有异常钢筋' : '暂无可巡检钢筋')
    return
  }
  rebarInspectionActive.value = true
  const current = rebarInspectionIndex.value
  const next = current < 0 ? 0 : Math.min(rebarInspectionBars.value.length - 1, Math.max(0, current + direction))
  selectInspectionBar(next)
}

function resetRebarInspection() {
  stopRebarCameraAnimation()
  rebarInspectionActive.value = false
  rebarInspectionManualSelect.value = false
  selectedComparisonBarId.value = ''
  applyComparisonSelection()
}

function exitRebarInspection() {
  resetRebarInspection()
  ElMessage.info('已退出巡检模式')
}

function toggleRebarManualSelection() {
  rebarInspectionManualSelect.value = !rebarInspectionManualSelect.value
  if (import.meta.env.DEV) {
    console.debug('[rebar-inspection] manual selection', {
      enabled: rebarInspectionManualSelect.value,
      workflowStep: activeWorkflowStep.value,
      hasComparison: Boolean(comparison.value),
      pointcloudVisible: Boolean(denoisePreview?.visible || pointcloudGroup?.visible),
    })
  }
  if (rebarInspectionManualSelect.value) {
    ElMessage.info('手动选择已开启，请在视口中点击钢筋构件')
  } else {
    stopRebarCameraAnimation()
    ElMessage.info('已退出手动选择')
  }
  applyComparisonSelection()
}

function onInspectionFilterChange() {
  if (rebarInspectionActive.value) {
    if (rebarInspectionBars.value.length) selectInspectionBar(0)
    else {
      selectedComparisonBarId.value = ''
      applyComparisonSelection()
    }
  }
}

function selectInspectionBarById(ifcGlobalId: string) {
  const bar = comparisonBars.value.find(item => item.ifcGlobalId === ifcGlobalId)
  if (!bar) return false
  rebarInspectionActive.value = true
  selectedComparisonBarId.value = bar.ifcGlobalId
  applyComparisonSelection()
  void nextTick(() => focusComparisonBar(bar, { manual: true }))
  return true
}

function getComparisonBarIdFromHit(hit: THREE.Intersection) {
  let current: THREE.Object3D | null = hit.object
  while (current) {
    const candidate = [
      String(current.userData?.ifcGlobalId ?? '').trim(),
      String(current.name || '').trim(),
      guessIfcId(current.userData),
    ].find(Boolean)
    if (candidate) {
      const direct = comparisonBars.value.find(bar => bar.ifcGlobalId === candidate)
      if (direct) return direct.ifcGlobalId
      const metadataId = findMetadataElementById(candidate)?.id
      if (metadataId && comparisonBars.value.some(bar => bar.ifcGlobalId === metadataId)) return metadataId
    }
    if (current === bimPivot || current === c2mSceneGroup) break
    current = current.parent
  }

  // The colored/denoised PLY stores ownership per vertex rather than on the
  // mesh. Resolve either a point hit or an intersected triangle's instance
  // and map it to a comparison bar.
  const geometry = (hit.object as THREE.Mesh).geometry
  const instances = geometry?.getAttribute('instance')
  if (instances && typeof hit.index === 'number') {
    const instanceId = instances.getX(hit.index)
    if (Number.isInteger(instanceId)) {
      const bar = comparisonBars.value.find(item => item.instanceIds.includes(instanceId))
      if (bar) return bar.ifcGlobalId
    }
  }
  if (instances && typeof hit.faceIndex === 'number') {
    const index = geometry.getIndex()
    const first = hit.faceIndex * 3
    const ids = [0, 1, 2].map(offset => index ? index.getX(first + offset) : first + offset)
    for (const vertex of ids) {
      const instanceId = instances.getX(vertex)
      if (!Number.isInteger(instanceId)) continue
      const bar = comparisonBars.value.find(item => item.instanceIds.includes(instanceId))
      if (bar) return bar.ifcGlobalId
    }
  }
  return ''
}

function handleRebarInspectionPointerDown(event: PointerEvent) {
  if (import.meta.env.DEV) {
    console.debug('[rebar-inspection] pointerdown', {
      button: event.button,
      workflowStep: activeWorkflowStep.value,
      analysisMode: analysisMode.value,
      manualSelection: rebarInspectionManualSelect.value,
      hasComparison: Boolean(comparison.value),
    })
  }
  if (
    event.button !== 0 || event.shiftKey || analysisMode.value !== 'none' ||
    activeWorkflowStep.value !== 3 || !comparison.value || !rebarInspectionManualSelect.value ||
    !raycaster || !activeCamera
  ) return false
  const pointer = getPointerNdc(event)
  if (!pointer) return false
  // Point-cloud hit testing uses a world-space threshold. Scale it with the
  // current camera distance so manual selection remains usable after zooming.
  const viewportHeight = Math.max(viewportEl.value?.clientHeight || 1, 1)
  const viewDistance = controls?.target
    ? activeCamera.position.distanceTo(controls.target)
    : activeCamera.position.length()
  const worldUnitsPerPixel = isPerspectiveCamera(activeCamera)
    ? (2 * Math.max(viewDistance, 0.001) * Math.tan(THREE.MathUtils.degToRad(activeCamera.fov) * 0.5)) / viewportHeight
    : (2 * Math.max(orthoViewSize, 0.001)) / viewportHeight
  raycaster.params.Points.threshold = THREE.MathUtils.clamp(
    worldUnitsPerPixel * Math.max(pointcloudPointSize.value * 2, 6),
    1e-4,
    Math.max(pointcloudMaxDim * 0.05, 0.01),
  )
  raycaster.setFromCamera(pointer, activeCamera)
  const targets: THREE.Object3D[] = []
  if (c2mSceneGroup?.visible) targets.push(c2mSceneGroup)
  if (denoisePreview?.visible) targets.push(denoisePreview)
  if (pointcloudGroup?.visible) targets.push(pointcloudGroup)
  if (bimPivot?.visible) targets.push(bimPivot)
  if (!targets.length) return false
  const intersections = raycaster.intersectObjects(targets, true).filter(intersection => {
    const object = intersection.object as THREE.Object3D
    return !(object.userData as any)?.__viewerPickIgnore
  })
  // The nearest hit can be an unrelated shell or a mesh without metadata.
  // Resolve every hit from front to back so one click still selects the first
  // actual comparison member behind it.
  const resolvedHit = intersections
    .map((intersection) => ({ intersection, ifcGlobalId: getComparisonBarIdFromHit(intersection) }))
    .find((item) => Boolean(item.ifcGlobalId))
  const hit = resolvedHit?.intersection
  const ifcGlobalId = resolvedHit?.ifcGlobalId ?? ''
  if (import.meta.env.DEV) {
    console.debug('[rebar-inspection] hit test', {
      targets: targets.length,
      intersections: intersections.length,
      hit: Boolean(hit),
      object: hit?.object?.name || hit?.object?.type || '',
      distance: hit?.distance,
      pointsThreshold: raycaster.params.Points.threshold,
    })
  }
  if (!hit) return false
  if (import.meta.env.DEV) console.debug('[rebar-inspection] resolved member', ifcGlobalId || '(none)')
  if (!ifcGlobalId) return false
  selectInspectionBarById(ifcGlobalId)
  event.preventDefault()
  return true
}

const c2mSceneLoaded = ref(false)
const c2mSceneActive = computed(() => c2mSceneLoaded.value && activeWorkflowStep.value >= 3)
const c2mSceneLoading = ref(false)
const c2mRecoloring = ref(false)
const c2mRangeMode = ref<C2MRangeMode>('auto')
const c2mManualRangeMm = ref(30)
const c2mColorRangeMm = computed(() => resolveC2MRangeMm(c2mRangeMode.value, c2mManualRangeMm.value, c2mToleranceMm.value, c2mRangeSummary.value, c2mResult.value?.stats))
const c2mHistogramRangeMm = ref(30)
const c2mToleranceMm = ref(10)
const c2mHistogramBins = ref(60)
const c2mHistogramFollowsColor = ref(true)
const c2mColorMode = ref<C2MColorMode>('continuous')
const c2mBandCount = ref(7)
let c2mSceneGroup: THREE.Group | null = null
let c2mTileset: TilesRenderer | null = null
let c2mAnalysisSession: AnalysisMeshSession | null = null
let c2mAnalysisMaterial: THREE.Material | null = null
const c2mAnalysisDistances = new Map<string, Float32Array>()
const c2mAnalysisStatsByComponent = new Map<string, AnalysisC2MStats>()
const c2mDistances = ref<Float32Array | null>(null)
let c2mResultRequestId = 0
let c2mDistancesRequestId = 0
let c2mSceneLoadRequestId = 0
let c2mAnalysisPollingTimer: number | null = null
let bimVisibilityBeforeC2M: boolean | null = null
const remeshLoading = ref(false)
const remeshMeshLoaded = ref(false)
const remeshRestoreAvailable = ref(false)
const remeshSolidHidden = ref(false)
const remeshWireHidden = ref(false)
const remeshWireAvailable = ref(true)
let remeshSceneGroup: THREE.Group | null = null
type RemeshSceneSnapshot = {
  bimVisible: boolean
  pointcloudVisible: boolean
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

const meshReady = computed(() =>
  meshStatus.value?.status === 'succeeded' && meshStatus.value.algorithm === REBAR_SWEEP_ALGORITHM,
)
const meshAlgorithmDisplayName = '钢筋保形均匀化（多边形截面 / 沿轴等距）'
const meshAlgorithmVersion = computed(() => {
  const version = meshReady.value ? meshStatus.value?.implementationVersion : null
  return version ? `v${version}` : '待生成'
})
const meshAlgorithmParameterLabel = computed(() => {
  return `${meshCrossSectionSides.value} 边截面 · 轴向 ${(meshAxialSpacing.value * 1000).toFixed(1)} mm · 弦高 ${(meshMaxChordError.value * 1000).toFixed(2)} mm`
})
const meshTaskActive = computed(() =>
  meshStatus.value?.status === 'queued' || meshStatus.value?.status === 'processing',
)
const meshControlsDisabled = computed(() => meshRunning.value || meshTaskActive.value)
const canLoadRemesh = computed(() => meshReady.value && !remeshLoading.value && !!props.bimAssetId)
const canRunC2M = computed(() => Boolean(props.pointcloudAssetId && props.bimAssetId && canOpenDeviationStep.value && meshReady.value && !c2mRunning.value))
watch(comparisonReportPages, (pages) => {
  comparisonReportPageIndex.value = Math.min(
    comparisonReportPageIndex.value,
    Math.max(pages.length - 1, 0),
  )
})
const c2mSceneArtifactAvailable = computed(() => Boolean(
  c2mResult.value?.coloredPlyAvailable ||
  (
    c2mResult.value?.analysis?.status === 'ready' &&
    c2mResult.value.analysis.manifestUrl &&
    c2mResult.value.analysis.baseUrl &&
    c2mResult.value.analysis.analysisMeshTilesetUrl
  ),
))
const c2mOverlapWarning = computed(() => {
  const overlap = c2mResult.value?.diagnostics?.bboxOverlapIoU
  return typeof overlap === 'number' && overlap < 0.3
})
const c2mRequestedVisualization = computed<C2MVisualization>(() => ({
  maxColormapDistance: c2mColorRangeMm.value / 1000,
  maxHistogramDistance: (c2mHistogramFollowsColor.value ? c2mColorRangeMm.value : c2mHistogramRangeMm.value) / 1000,
  histogramBins: Math.round(c2mHistogramBins.value),
  toleranceLimit: c2mToleranceMm.value / 1000,
}))
const c2mSettingsDirty = computed(() => {
  const current = c2mResult.value?.visualization
  const requested = c2mRequestedVisualization.value
  if (!current) return Boolean(c2mResult.value)
  return !sameC2MValue(current.maxColormapDistance, requested.maxColormapDistance) ||
    !sameC2MValue(current.maxHistogramDistance, requested.maxHistogramDistance) ||
    current.histogramBins !== requested.histogramBins ||
    !sameC2MValue(current.toleranceLimit, requested.toleranceLimit)
})
const canRecolorC2M = computed(() => Boolean(
  canUseC2MResult.value &&
  c2mResult.value?.resultVersion &&
  c2mResult.value?.distancesAvailable !== false &&
  c2mSettingsDirty.value &&
  c2mColorRangeMm.value <= 10000 &&
  !c2mRunning.value &&
  !c2mSceneLoading.value &&
  !c2mRecoloring.value,
))
const c2mDisplayResult = computed<C2MResult | null>(() => {
  const result = c2mResult.value
  const distances = c2mDistances.value
  if (!result || !result.stats) return result
  if (!distances) {
    if (!c2mSettingsDirty.value) return result
    const stored = result.visualization
    return {
      ...result,
      visualization: c2mRequestedVisualization.value,
      histogram: stored && sameC2MValue(stored.maxHistogramDistance, c2mRequestedVisualization.value.maxHistogramDistance) && stored.histogramBins === c2mHistogramBins.value ? result.histogram : null,
      stats: { ...result.stats, withinToleranceRatio: stored && sameC2MValue(stored.toleranceLimit, c2mRequestedVisualization.value.toleranceLimit) ? result.stats.withinToleranceRatio : undefined },
    }
  }
  const tolerance = c2mRequestedVisualization.value.toleranceLimit
  let withinTolerance = 0
  let knownCount = 0
  distances.forEach((distance) => {
    if (!Number.isFinite(distance)) return
    knownCount += 1
    if (Math.abs(distance) <= tolerance) withinTolerance += 1
  })
  return {
    ...result,
    visualization: c2mRequestedVisualization.value,
    histogram: histogramFromC2MDistances(
      distances,
      c2mRequestedVisualization.value.maxHistogramDistance,
      c2mRequestedVisualization.value.histogramBins,
    ),
    stats: {
      ...result.stats,
      withinToleranceRatio: knownCount ? withinTolerance / knownCount : undefined,
    },
  }
})

function sameC2MValue(left: number, right: number) {
  return Math.abs(left - right) < 1e-9
}

function invalidateC2MResult(reason: string) {
  clearC2MAnalysisPolling()
  c2mResultRequestId += 1
  c2mDistancesRequestId += 1
  c2mDistances.value = null
  clearC2MScene()
  if (c2mResult.value) {
    c2mResult.value = {
      ...c2mResult.value,
      fresh: false,
      staleReason: reason,
    }
  }
}

function clearC2MAnalysisPolling() {
  if (c2mAnalysisPollingTimer !== null) {
    window.clearTimeout(c2mAnalysisPollingTimer)
    c2mAnalysisPollingTimer = null
  }
}

function scheduleC2MAnalysisPolling(result: C2MResult) {
  clearC2MAnalysisPolling()
  if (result.analysis?.status !== 'queued' && result.analysis?.status !== 'processing') return
  c2mAnalysisPollingTimer = window.setTimeout(() => {
    c2mAnalysisPollingTimer = null
    void loadLatestC2M()
  }, 2500)
}

const c2mRangeSummary = computed(() => c2mDistances.value ? summarizeC2MRange(c2mDistances.value) : null)

function selectC2MRangePreset(mode: C2MRangeMode) {
  if (mode === 'manual') c2mManualRangeMm.value = c2mColorRangeMm.value
  c2mRangeMode.value = mode
}

function onC2MColorRangeChange(value: number | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) c2mManualRangeMm.value = Math.max(0.1, value)
}

function onC2MToleranceChange(value: number | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) c2mToleranceMm.value = Math.max(0.1, value)
}



function onC2MHistogramRangeChange(value: number | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) c2mHistogramRangeMm.value = Math.max(1, value)
}

function onC2MHistogramBinsChange(value: number | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) c2mHistogramBins.value = Math.max(10, Math.min(200, Math.round(value)))
}

function onC2MHistogramFollowChange(follows: string | number | boolean) {
  if (follows) c2mHistogramRangeMm.value = c2mColorRangeMm.value
}

function onC2MBandCountChange(value: number | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) c2mBandCount.value = Math.max(2, Math.min(32, Math.round(value)))
}


function syncC2MControls(result: C2MResult) {
  const effective = result.diagnostics?.rebarComparison?.effective
  c2mMaxSearchDistanceMm.value = (effective?.maxSearchDistance ?? 0.2) * 1000
  if (effective) {
    c2mNormalConstraintEnabled.value = effective.normalConstraintEnabled
    c2mNormalMaxAngleDeg.value = effective.normalMaxAngleDeg
  }
  c2mVoxelSize.value = Math.max(0.001, result.voxelSize || 0.05)
  const visualization = result.visualization
  if (!visualization) {
    return
  }
  // Keep the chosen range mode while results are refreshed or saved.
  c2mManualRangeMm.value = visualization.maxColormapDistance * 1000
  c2mHistogramRangeMm.value = visualization.maxHistogramDistance * 1000
  c2mToleranceMm.value = visualization.toleranceLimit * 1000
  c2mHistogramBins.value = visualization.histogramBins
  c2mHistogramFollowsColor.value = sameC2MValue(
    visualization.maxColormapDistance,
    visualization.maxHistogramDistance,
  )
}

async function syncC2MDistances(result: C2MResult) {
  const requestId = ++c2mDistancesRequestId
  c2mDistances.value = null
  if (
    !isC2MResultFresh(result) ||
    result.distancesAvailable === false ||
    !props.pointcloudAssetId ||
    !props.bimAssetId
  ) return
  try {
    const buffer = await backendRequest<ArrayBuffer>(
      getC2MDistancesUrl(props.pointcloudAssetId, props.bimAssetId, result.resultVersion),
      { method: 'GET', responseType: 'arraybuffer' },
    )
    if (requestId !== c2mDistancesRequestId) return
    const distances = parseC2MDistances(buffer, result.meshVertexCount, Boolean(result.diagnostics?.rebarComparison))
    if (!distances) {
      console.warn(`[C2M] distances.bin 与结果声明的 ${result.meshVertexCount} 个顶点不匹配`)
      return
    }
    c2mDistances.value = distances
    previewC2MVisualization()
  } catch (error) {
    if (requestId === c2mDistancesRequestId) {
      console.info('[C2M] 原始逐顶点距离暂不可用', error)
    }
  }
}

function previewC2MVisualization() {
  const distances = c2mDistances.value
  if (!distances || !c2mSceneGroup) return
  const meshes: THREE.Mesh[] = []
  c2mSceneGroup.traverse((child) => {
    if (child instanceof THREE.Mesh) meshes.push(child)
  })
  // distances.bin is defined against one indexed remesh geometry. Do not
  // duplicate the full array across arbitrary multi-mesh scene graphs.
  if (meshes.length !== 1) return
  const [mesh] = meshes
  if (mesh.geometry.getAttribute('position')?.count !== distances.length) return
  mesh.geometry.setAttribute('distance', new THREE.BufferAttribute(distances, 1))
  applyC2MVertexColors(
    mesh.geometry,
    distances,
    c2mRequestedVisualization.value.maxColormapDistance,
    c2mRequestedVisualization.value.toleranceLimit,
    c2mColorMode.value === 'discrete', c2mBandCount.value,
  )
  rememberComparisonGeometryColors(mesh.geometry)
  applyComparisonSelection()
  requestRender()
}

function applyAnalysisC2MVertexColors(
  geometry: THREE.BufferGeometry,
  distances: Float32Array,
) {
  return applyC2MVertexColors(geometry, distances,
    c2mRequestedVisualization.value.maxColormapDistance,
    c2mRequestedVisualization.value.toleranceLimit,
    c2mColorMode.value === 'discrete', c2mBandCount.value)

}

function recolorAnalysisC2MScene() {
  if (!c2mSceneGroup || !c2mTileset) return
  c2mSceneGroup.traverse((object) => {
    const mesh = object as THREE.Mesh
    if (!mesh.isMesh || !mesh.geometry) return
    const distances = mesh.geometry.getAttribute('distance')?.array
    if (!(distances instanceof Float32Array)) return
    applyAnalysisC2MVertexColors(mesh.geometry, distances)
    const colors = mesh.geometry.getAttribute('color')
    if (colors) c2mOriginalVertexColors.set(mesh.geometry, new Float32Array(colors.array as ArrayLike<number>))
    if (Array.isArray(mesh.material)) mesh.material.forEach((material) => { material.needsUpdate = true })
    else mesh.material.needsUpdate = true
  })
  applyComparisonSelection()
  requestRender()
}

function createAnalysisC2MMaterial() {
  if (rendererMode === 'webgpu') {
    const material = new MeshBasicNodeMaterial()
    material.colorNode = tslVertexColor()
    material.vertexColors = true
    material.side = THREE.DoubleSide
    material.toneMapped = false
    material.polygonOffset = true
    material.polygonOffsetFactor = -1
    material.polygonOffsetUnits = -1
    return material
  }
  return new THREE.MeshBasicMaterial({
    vertexColors: true,
    side: THREE.DoubleSide,
    toneMapped: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  })
}


function formatC2MDistance(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(4)} m` : '--'
}

function formatC2MPercentage(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '--'
}

function formatReportInstanceIds(instanceIds: readonly number[]) {
  if (!instanceIds.length) return '无可靠对应'
  const visible = instanceIds.slice(0, 8).join('、')
  return instanceIds.length > 8 ? `${visible} 等，共 ${instanceIds.length} 个` : visible
}

type ReportViewProjection = 'top' | 'front' | 'side'

// SVG remains responsive for PDF export while preserving substantially more
// of each matched rebar's measured point cloud than the previous sparse view.
const REPORT_POINT_SOURCE_LIMIT = 16000
const REPORT_POINT_DRAW_LIMIT = 8000

function reportGeometryPointsForBar(bar: RebarComparisonBar, source: 'design' | 'pointcloud') {
  const points: THREE.Vector3[] = []
  const addMeshPoints = (mesh: THREE.Mesh) => {
    const positions = mesh.geometry?.getAttribute('position')
    if (!positions) return
    const stride = Math.max(1, Math.ceil(positions.count / 300))
    mesh.updateMatrixWorld(true)
    for (let index = 0; index < positions.count; index += 1) {
      points.push(new THREE.Vector3().fromBufferAttribute(positions, index).applyMatrix4(mesh.matrixWorld))
    }
  }
  if (source === 'design') {
    if (c2mAnalysisSession) c2mAnalysisSession.forEachComponentMesh(bar.ifcGlobalId, addMeshPoints)
    if (!points.length && bimPivot) {
      bimPivot.updateMatrixWorld(true)
      bimPivot.traverse((object) => {
        if (object instanceof THREE.Mesh && objectMatchesComparisonBar(object, bar.ifcGlobalId)) addMeshPoints(object)
      })
    }
  } else {
    const geometry = denoisePreview?.geometry
    const positions = geometry?.getAttribute('position')
    const instances = geometry?.getAttribute('instance')
    const labels = geometry?.getAttribute('label')
    if (!positions || !instances) return points
    const instanceIds = new Set(bar.instanceIds)
    denoisePreview?.updateMatrixWorld(true)
    const matrixWorld = denoisePreview?.matrixWorld ?? new THREE.Matrix4()
    let matchingCount = 0
    for (let index = 0; index < positions.count; index += 1) {
      if (labels && labels.getX(index) !== 3) continue
      if (instanceIds.has(Math.round(instances.getX(index)))) matchingCount += 1
    }
    const stride = Math.max(1, Math.ceil(matchingCount / REPORT_POINT_SOURCE_LIMIT))
    let matchingIndex = 0
    for (let index = 0; index < positions.count; index += 1) {
      if (labels && labels.getX(index) !== 3) continue
      if (!instanceIds.has(Math.round(instances.getX(index)))) continue
      if (matchingIndex++ % stride !== 0) continue
      points.push(new THREE.Vector3().fromBufferAttribute(positions, index).applyMatrix4(matrixWorld))
    }
  }
  return points
}

function reportPrincipalAxis(points: THREE.Vector3[]) {
  if (points.length < 2) return new THREE.Vector3(1, 0, 0)
  const center = points.reduce((sum, point) => sum.add(point), new THREE.Vector3()).multiplyScalar(1 / points.length)
  const covariance = new THREE.Matrix3()
  const values = new Array<number>(9).fill(0)
  points.forEach((point) => {
    const delta = point.clone().sub(center)
    values[0] += delta.x * delta.x; values[1] += delta.x * delta.y; values[2] += delta.x * delta.z
    values[4] += delta.y * delta.y; values[5] += delta.y * delta.z; values[8] += delta.z * delta.z
  })
  values[3] = values[1]; values[6] = values[2]; values[7] = values[5]
  covariance.fromArray(values.map((value) => value / points.length))
  let axis = new THREE.Vector3(1, 0, 0)
  for (let iteration = 0; iteration < 12; iteration += 1) {
    const next = axis.clone().applyMatrix3(covariance)
    if (next.lengthSq() < 1e-12) break
    axis.copy(next.normalize())
  }
  return axis.lengthSq() > 1e-8 ? axis : new THREE.Vector3(1, 0, 0)
}

const reportCanvasRefs = new Map<string, HTMLCanvasElement>()
const reportSvgMarkup = new Map<string, string>()
const reportSvgRevision = ref(0)
const reportViewRenderers = new Map<string, THREE.WebGLRenderer>()
const reportViewScenes = new Map<string, THREE.Scene>()
const reportViewCameras = new Map<string, THREE.OrthographicCamera>()

function reportCanvasKey(ifcGlobalId: string, projection: ReportViewProjection) {
  return `${ifcGlobalId}:${projection}`
}

function setReportCanvasRef(ifcGlobalId: string, projection: ReportViewProjection, element: Element | null) {
  const key = reportCanvasKey(ifcGlobalId, projection)
  if (element instanceof HTMLCanvasElement) reportCanvasRefs.set(key, element)
  else reportCanvasRefs.delete(key)
}

function disposeReportView(key: string) {
  reportViewRenderers.get(key)?.dispose()
  reportViewRenderers.delete(key)
  const sceneToDispose = reportViewScenes.get(key)
  if (sceneToDispose) {
    sceneToDispose.traverse((object) => {
      const renderable = object as THREE.Mesh | THREE.Points | THREE.Line
      if (!renderable.geometry) return
      renderable.geometry.dispose()
      const materials = Array.isArray(renderable.material) ? renderable.material : [renderable.material]
      materials.forEach((material) => material?.dispose())
    })
  }
  reportViewScenes.delete(key)
  reportViewCameras.delete(key)
}

function disposeReportModelViews() {
  Array.from(reportViewScenes.keys()).forEach(disposeReportView)
}

function subsetReportGeometry(source: THREE.BufferGeometry, start: number, count: number) {
  const positions = source.getAttribute('position')
  if (!positions || count <= 0 || start < 0 || start + count > positions.count) return null
  const end = start + count
  const geometry = new THREE.BufferGeometry()
  for (const name of ['position', 'normal', 'color', 'uv']) {
    const attribute = source.getAttribute(name)
    if (!attribute || attribute.itemSize <= 0 || start + count > attribute.count) continue
    const values = new Float32Array(count * attribute.itemSize)
    for (let index = start; index < end; index += 1) {
      for (let component = 0; component < attribute.itemSize; component += 1) {
        values[(index - start) * attribute.itemSize + component] = attribute.getComponent(index, component)
      }
    }
    geometry.setAttribute(name, new THREE.BufferAttribute(values, attribute.itemSize, attribute.normalized))
  }
  const originalIndex = source.getIndex()
  if (originalIndex) {
    const indices: number[] = []
    for (let index = 0; index + 2 < originalIndex.count; index += 3) {
      const a = originalIndex.getX(index)
      const b = originalIndex.getX(index + 1)
      const c = originalIndex.getX(index + 2)
      if (a >= start && a < end && b >= start && b < end && c >= start && c < end) {
        indices.push(a - start, b - start, c - start)
      }
    }
    if (indices.length) geometry.setIndex(indices)
  }
  geometry.computeBoundingBox()
  geometry.computeBoundingSphere()
  return geometry
}

function addReportDesignMeshes(root: THREE.Group, bar: RebarComparisonBar, points: THREE.Vector3[]) {
  const addMesh = (mesh: THREE.Mesh) => {
    const positions = mesh.geometry?.getAttribute('position')
    if (!positions) return
    mesh.updateMatrixWorld(true)
    const geometry = mesh.geometry.clone()
    geometry.applyMatrix4(mesh.matrixWorld)
    const material = new THREE.MeshBasicMaterial({
      color: '#3678c9',
      transparent: true,
      opacity: 0.34,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    })
    const copy = new THREE.Mesh(geometry, material)
    copy.renderOrder = 1
    root.add(copy)
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 24),
      new THREE.LineBasicMaterial({ color: '#1f5fb8', transparent: true, opacity: 1, toneMapped: false }),
    )
    edges.renderOrder = 2
    root.add(edges)
    const stride = Math.max(1, Math.ceil(positions.count / 300))
    for (let index = 0; index < positions.count; index += stride) {
      points.push(new THREE.Vector3().fromBufferAttribute(positions, index).applyMatrix4(mesh.matrixWorld))
    }
  }
  if (c2mAnalysisSession) c2mAnalysisSession.forEachComponentMesh(bar.ifcGlobalId, addMesh)
  if (!points.length && c2mSceneGroup) {
    c2mSceneGroup.updateMatrixWorld(true)
    c2mSceneGroup.traverse((object) => {
      if (!(object instanceof THREE.Mesh) || points.length) return
      const subset = subsetReportGeometry(object.geometry, bar.vertexStart, bar.vertexCount)
      if (!subset) return
      const matrixWorld = object.matrixWorld.clone()
      const positions = subset.getAttribute('position')
      const material = new THREE.MeshBasicMaterial({
        color: '#3678c9', transparent: true, opacity: 0.34,
        side: THREE.DoubleSide, depthWrite: false, toneMapped: false,
      })
      const copy = new THREE.Mesh(subset, material)
      copy.applyMatrix4(matrixWorld)
      copy.renderOrder = 1
      root.add(copy)
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(subset, 24),
        new THREE.LineBasicMaterial({ color: '#1f5fb8', transparent: true, opacity: 1, toneMapped: false }),
      )
      edges.applyMatrix4(matrixWorld)
      edges.renderOrder = 2
      root.add(edges)
      const stride = Math.max(1, Math.ceil(positions.count / 300))
      for (let index = 0; index < positions.count; index += stride) {
        points.push(new THREE.Vector3().fromBufferAttribute(positions, index).applyMatrix4(matrixWorld))
      }
    })
  }
  if (!points.length && bimPivot) {
    bimPivot.updateMatrixWorld(true)
    bimPivot.traverse((object) => {
      if (object instanceof THREE.Mesh && objectMatchesComparisonBar(object, bar.ifcGlobalId)) addMesh(object)
    })
  }
}

function addReportPointCloud(root: THREE.Group, bar: RebarComparisonBar, points: THREE.Vector3[]) {
  const geometry = denoisePreview?.geometry
  const positions = geometry?.getAttribute('position')
  const instances = geometry?.getAttribute('instance')
  const labels = geometry?.getAttribute('label')
  if (!positions || !instances || !denoisePreview) return
  const instanceIds = new Set(bar.instanceIds)
  denoisePreview.updateMatrixWorld(true)
  const matrixWorld = denoisePreview.matrixWorld
  const selected: number[] = []
  let matchingCount = 0
  for (let index = 0; index < positions.count; index += 1) {
    if (labels && labels.getX(index) !== 3) continue
    if (instanceIds.has(Math.round(instances.getX(index)))) matchingCount += 1
  }
  const stride = Math.max(1, Math.ceil(matchingCount / REPORT_POINT_SOURCE_LIMIT))
  let matchingIndex = 0
  for (let index = 0; index < positions.count; index += 1) {
    if (labels && labels.getX(index) !== 3) continue
    if (!instanceIds.has(Math.round(instances.getX(index)))) continue
    if (matchingIndex++ % stride !== 0) continue
    const point = new THREE.Vector3().fromBufferAttribute(positions, index).applyMatrix4(matrixWorld)
    points.push(point)
    selected.push(point.x, point.y, point.z)
  }
  if (!selected.length) return
  const pointGeometry = new THREE.BufferGeometry()
  pointGeometry.setAttribute('position', new THREE.Float32BufferAttribute(selected, 3))
  root.add(new THREE.Points(pointGeometry, new THREE.PointsMaterial({
    color: '#176b43',
    // Keep measured points legible at every zoom level in the report sheet.
    // A fixed pixel size avoids the sparse/near-invisible appearance caused
    // by perspective attenuation in small orthographic canvases.
    size: 5,
    sizeAttenuation: false,
    transparent: true,
    opacity: 1,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
  })))
}

function reportViewLabel(projection: ReportViewProjection) {
  // The report uses a member-local frame: local Y is the design axis and
  // local X/Z span the rebar cross-section.  Keep the labels tied to the
  // actual projection semantics so the circular end section is never shown
  // as the "正视" elevation.
  return projection === 'front' ? '正视图（从正面）' : projection === 'top' ? '俯视图（从上方）' : '侧视图（沿设计轴）'
}

function reportViewHint(projection: ReportViewProjection) {
  return projection === 'front'
    ? '设计轴 × 高程'
    : projection === 'top'
      ? '设计轴 × 横向'
      : '局部横向 × 局部高程（截面）'
}

type ReportPoint2D = { x: number; y: number }

function reportSvgFor(ifcGlobalId: string, projection: ReportViewProjection) {
  return reportSvgMarkup.get(reportCanvasKey(ifcGlobalId, projection)) ?? '<text x="200" y="380" text-anchor="middle" fill="#78909c" font-size="16">暂无可用构件数据</text>'
}

function reportConvexHull(points: ReportPoint2D[]) {
  const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y)
  if (sorted.length <= 2) return sorted
  const cross = (o: ReportPoint2D, a: ReportPoint2D, b: ReportPoint2D) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
  const lower: ReportPoint2D[] = []
  sorted.forEach((point) => { while (lower.length >= 2 && cross(lower[lower.length - 2]!, lower[lower.length - 1]!, point) <= 0) lower.pop(); lower.push(point) })
  const upper: ReportPoint2D[] = []
  sorted.slice().reverse().forEach((point) => { while (upper.length >= 2 && cross(upper[upper.length - 2]!, upper[upper.length - 1]!, point) <= 0) upper.pop(); upper.push(point) })
  return lower.slice(0, -1).concat(upper.slice(0, -1))
}

function reportSvgMarkupFor(bar: RebarComparisonBar, projection: ReportViewProjection) {
  const designPoints: THREE.Vector3[] = []
  const scanPoints: THREE.Vector3[] = []
  const root = new THREE.Group()
  addReportDesignMeshes(root, bar, designPoints)
  addReportPointCloud(root, bar, scanPoints)
  root.updateMatrixWorld(true)
  const all = [...designPoints, ...scanPoints]
  if (!all.length) return '<text x="200" y="380" text-anchor="middle" fill="#78909c" font-size="16">暂无可用构件数据</text>'

  // The viewer recentres BIM, tiles and preview PLY independently to avoid
  // float32 precision loss.  Their render-space origins can therefore differ
  // by metres even though the C2M calculation itself is in one source frame.
  // Detect that pure-origin jump and remove it for the report drawing only;
  // never let it become a fake 1–5 m "deviation" arrow.  Real construction
  // deviations (the table's millimetre values) remain untouched.
  const centroid3D = (items: THREE.Vector3[]) => items.reduce((sum, point) => sum.add(point), new THREE.Vector3()).multiplyScalar(1 / Math.max(items.length, 1))
  const designOrigin3D = centroid3D(designPoints.length ? designPoints : all)
  const scanOrigin3D = scanPoints.length ? centroid3D(scanPoints) : designOrigin3D.clone()
  const originDelta = scanOrigin3D.clone().sub(designOrigin3D)
  const designBox3D = new THREE.Box3().setFromPoints(designPoints.length ? designPoints : all)
  const designDiagonal = designBox3D.getSize(new THREE.Vector3()).length()
  // Anything above half a metre is an origin/normalisation jump for a
  // millimetre-level rebar inspection.  Keep the diagonal referenced so the
  // intent is explicit and avoid linting this diagnostic value away.
  const originJumpThreshold = Math.max(0.5, Math.min(2, designDiagonal * 0.1))
  const alignedScanPoints = scanPoints.length && originDelta.length() > originJumpThreshold
    ? scanPoints.map((point) => point.clone().sub(originDelta))
    : scanPoints

  // Establish a stable local frame per member: local Y follows the design
  // principal axis; local X/Z span its cross-section. All three projections
  // use this frame instead of world XY/XZ/YZ, so rotated members remain clear.
  const axis = reportPrincipalAxis(designPoints.length ? designPoints : all).normalize()
  const helper = Math.abs(axis.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1)
  const localX = new THREE.Vector3().crossVectors(helper, axis).normalize()
  const localZ = new THREE.Vector3().crossVectors(axis, localX).normalize()
  const local = (point: THREE.Vector3) => ({ x: point.dot(localX), y: point.dot(axis), z: point.dot(localZ) })
  // Projection contract:
  //   front = 正视：设计轴 × 高程 (local Y × local Z)
  //   top   = 俯视：设计轴 × 横向 (local Y × local X)
  //   side  = 侧视（沿设计轴）：截面 (local X × local Z)
  // The names are retained for compatibility with the existing template and
  // cache keys, but the mapping is intentionally explicit here.
  const project = (p: { x: number; y: number; z: number }): ReportPoint2D => projection === 'front'
    ? { x: p.y, y: p.z }
    : projection === 'top'
      ? { x: p.y, y: p.x }
      : { x: p.x, y: p.z }
  const design2D = designPoints.map(local).map(project)
  const scan2D = alignedScanPoints.map(local).map(project)
  const points = [...design2D, ...scan2D]
  let minX = Math.min(...points.map((p) => p.x)); let maxX = Math.max(...points.map((p) => p.x))
  let minY = Math.min(...points.map((p) => p.y)); let maxY = Math.max(...points.map((p) => p.y))
  const spanX = Math.max(maxX - minX, 0.02)
  const spanY = Math.max(maxY - minY, 0.02)
  const centerX = (minX + maxX) / 2
  const centerY = (minY + maxY) / 2
  // One shared scale for both axes is essential: using independent X/Y
  // scales turns round bar sections into ellipses. The portrait viewBox lets
  // the three panels use the page's remaining height without distorting data.
  const plotWidth = 352
  const plotHeight = 700
  const scale = Math.min(plotWidth / spanX, plotHeight / spanY)
  const sx = (x: number) => 200 + (x - centerX) * scale
  const sy = (y: number) => 380 - (y - centerY) * scale
  const designHull = reportConvexHull(design2D)
  const centroid = (items: ReportPoint2D[]) => items.reduce((sum, p) => ({ x: sum.x + p.x, y: sum.y + p.y }), { x: 0, y: 0 })
  // A real rebar is often thousands of millimetres long but only a few
  // millimetres thick.  Keep one uniform geometric scale (so circles never
  // become ellipses), then use a fixed-pixel outline/centreline for print
  // readability.  This changes only the drawing stroke, never the source
  // coordinates or deviation values.
  const projectedThickness = Math.max(0, maxY - minY)
  const designStroke = Math.max(8, Math.min(14, projectedThickness * scale * 0.42))
  const designPath = designHull.length >= 3
    ? `<polygon points="${designHull.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')}" fill="#3678c9" fill-opacity=".2" stroke="#1f5fb8" stroke-width="${designStroke.toFixed(1)}" stroke-linejoin="round"/>`
    : design2D.map((p) => `<circle cx="${sx(p.x).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="${Math.max(4, designStroke / 2).toFixed(1)}" fill="#3678c9"/>`).join('')
  const designCenter = design2D.length ? centroid(design2D) : { x: 0, y: 0 }
  if (design2D.length) {
    designCenter.x /= design2D.length
    designCenter.y /= design2D.length
  }
  const designCenterline = projection === 'side' || design2D.length < 2
    ? ''
    : (() => {
        const axisPoints = design2D.filter((point) => Number.isFinite(point.x))
        if (axisPoints.length < 2) return ''
        const axisMin = Math.min(...axisPoints.map((point) => point.x))
        const axisMax = Math.max(...axisPoints.map((point) => point.x))
        const y = sy(designCenter.y).toFixed(1)
        return `<line x1="${sx(axisMin).toFixed(1)}" y1="${y}" x2="${sx(axisMax).toFixed(1)}" y2="${y}" stroke="#1f5fb8" stroke-width="5" stroke-linecap="round" opacity=".92"/>`
      })()
  const designAxisLine = projection === 'side' || design2D.length < 2
    ? ''
    : (() => {
        const axisPoints = design2D.filter((point) => Number.isFinite(point.x))
        if (axisPoints.length < 2) return ''
        const axisMin = Math.min(...axisPoints.map((point) => point.x))
        const axisMax = Math.max(...axisPoints.map((point) => point.x))
        const y = sy(designCenter.y).toFixed(1)
        return `<line x1="${sx(axisMin).toFixed(1)}" y1="${y}" x2="${sx(axisMax).toFixed(1)}" y2="${y}" stroke="#d97706" stroke-width="4" stroke-dasharray="14 9" stroke-linecap="round" opacity="1"/>`
      })()
  const scanDots = scan2D
    .filter((_, index) => index % Math.max(1, Math.ceil(scan2D.length / REPORT_POINT_DRAW_LIMIT)) === 0)
    .map((p) => `<circle cx="${sx(p.x).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="2.8" fill="#0b8f5b" fill-opacity=".86"/>`)
    .join('')
  const scanCenter = scan2D.length ? centroid(scan2D) : { ...designCenter }
  if (scan2D.length) {
    scanCenter.x /= scan2D.length
    scanCenter.y /= scan2D.length
  }
  const dx = scanCenter.x - designCenter.x; const dy = scanCenter.y - designCenter.y
  const deviationMm = Math.hypot(dx, dy) * 1000
  const deviationColor = deviationMm > (c2mToleranceMm.value || 10) ? '#dc2626' : '#e8790c'
  // This arrow is a visual displacement between the design and measured
  // centroids. The authoritative C2M mean/RMSE/P95 values remain in the table;
  // label the diagram explicitly so it is not mistaken for meanAbs.
  const arrow = Math.hypot(dx, dy) > 1e-6 ? `<line x1="${sx(designCenter.x).toFixed(1)}" y1="${sy(designCenter.y).toFixed(1)}" x2="${sx(scanCenter.x).toFixed(1)}" y2="${sy(scanCenter.y).toFixed(1)}" stroke="${deviationColor}" stroke-width="3" marker-end="url(#report-arrow)"/><text x="${((sx(designCenter.x) + sx(scanCenter.x)) / 2).toFixed(1)}" y="${((sy(designCenter.y) + sy(scanCenter.y)) / 2 - 8).toFixed(1)}" text-anchor="middle" fill="${deviationColor}" font-size="14" font-weight="700">质心偏移 ${deviationMm.toFixed(1)} mm</text>` : ''
  const grid = [0, 1, 2, 3, 4, 5, 6].map((i) => `<line x1="${(24 + i * 58.67).toFixed(1)}" y1="30" x2="${(24 + i * 58.67).toFixed(1)}" y2="730" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="3 7"/><line x1="24" y1="${(30 + i * 116.67).toFixed(1)}" x2="376" y2="${(30 + i * 116.67).toFixed(1)}" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="3 7"/>`).join('')
  root.traverse((object) => { const geometry = (object as THREE.Object3D & { geometry?: THREE.BufferGeometry }).geometry; geometry?.dispose?.(); const material = (object as THREE.Object3D & { material?: THREE.Material | THREE.Material[] }).material; (Array.isArray(material) ? material : material ? [material] : []).forEach((item) => item.dispose()) })
  return `<defs><marker id="report-arrow" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="${deviationColor}"/></marker></defs><rect x="0" y="0" width="400" height="760" fill="#f8fafc"/>${grid}<line x1="24" y1="730" x2="376" y2="730" stroke="#94a3b8"/><line x1="24" y1="30" x2="24" y2="730" stroke="#94a3b8"/>${designPath}${designCenterline}${designAxisLine}${scanDots}${arrow}<circle cx="${sx(designCenter.x).toFixed(1)}" cy="${sy(designCenter.y).toFixed(1)}" r="6" fill="#fff" stroke="#1f5fb8" stroke-width="2.5"/>`
}

function reportViewCorners(box: THREE.Box3) {
  const { min, max } = box
  return [
    new THREE.Vector3(min.x, min.y, min.z), new THREE.Vector3(max.x, min.y, min.z),
    new THREE.Vector3(min.x, max.y, min.z), new THREE.Vector3(max.x, max.y, min.z),
    new THREE.Vector3(min.x, min.y, max.z), new THREE.Vector3(max.x, min.y, max.z),
    new THREE.Vector3(min.x, max.y, max.z), new THREE.Vector3(max.x, max.y, max.z),
  ]
}

function renderReportModelView(bar: RebarComparisonBar, projection: ReportViewProjection) {
  const key = reportCanvasKey(bar.ifcGlobalId, projection)
  const canvas = reportCanvasRefs.get(key)
  if (!canvas || canvas.clientWidth < 2 || canvas.clientHeight < 2) return
  disposeReportView(key)
  const designPoints: THREE.Vector3[] = []
  const scanPoints: THREE.Vector3[] = []
  const root = new THREE.Group()
  addReportDesignMeshes(root, bar, designPoints)
  addReportPointCloud(root, bar, scanPoints)
  if (!designPoints.length && !scanPoints.length) return
  root.updateMatrixWorld(true)
  const bounds = new THREE.Box3().setFromObject(root)
  const center = bounds.getCenter(new THREE.Vector3())
  const size = bounds.getSize(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z, 0.001)
  const axis = reportPrincipalAxis(designPoints.length ? designPoints : scanPoints)
  const viewDirection = projection === 'front'
    ? new THREE.Vector3(0, 0, 1)
    : projection === 'top'
      ? new THREE.Vector3(0, 1, 0)
      : axis.clone().normalize()
  if (viewDirection.lengthSq() < 1e-8) viewDirection.set(1, 0, 0)
  const viewUp = projection === 'front' ? new THREE.Vector3(0, 1, 0) : projection === 'top' ? new THREE.Vector3(0, 0, -1) : new THREE.Vector3(0, 1, 0)
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.001, maxDim * 20)
  camera.position.copy(center).addScaledVector(viewDirection, maxDim * 4)
  camera.up.copy(viewUp)
  camera.lookAt(center)
  camera.updateMatrixWorld(true)
  const aspect = Math.max(canvas.clientWidth / Math.max(canvas.clientHeight, 1), 0.1)
  let halfHeight = 0.001
  let halfWidth = 0.001
  reportViewCorners(bounds).forEach((corner) => {
    const local = camera.worldToLocal(corner.clone())
    halfWidth = Math.max(halfWidth, Math.abs(local.x))
    halfHeight = Math.max(halfHeight, Math.abs(local.y))
  })
  halfHeight = Math.max(halfHeight, halfWidth / aspect) * 1.16
  halfWidth = halfHeight * aspect
  camera.left = -halfWidth
  camera.right = halfWidth
  camera.top = halfHeight
  camera.bottom = -halfHeight
  camera.updateProjectionMatrix()

  if (projection === 'front' && designPoints.length > 1) {
    const designCenter = designPoints.reduce((sum, point) => sum.add(point), new THREE.Vector3()).multiplyScalar(1 / designPoints.length)
    const axisOffsets = designPoints.map((point) => point.clone().sub(designCenter).dot(axis))
    const axisStart = designCenter.clone().addScaledVector(axis, Math.min(...axisOffsets))
    const axisEnd = designCenter.clone().addScaledVector(axis, Math.max(...axisOffsets))
    const axisGeometry = new THREE.BufferGeometry().setFromPoints([axisStart, axisEnd])
    const axisMaterial = new THREE.LineDashedMaterial({ color: '#c47a25', dashSize: maxDim * 0.045, gapSize: maxDim * 0.025, transparent: true, opacity: 0.95, toneMapped: false })
    const axisLine = new THREE.Line(axisGeometry, axisMaterial)
    axisLine.computeLineDistances()
    axisLine.renderOrder = 4
    root.add(axisLine)
  }

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, preserveDrawingBuffer: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false)
  renderer.setClearColor('#f7fafb', 1)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  const viewScene = new THREE.Scene()
  viewScene.add(root)
  viewScene.add(new THREE.AmbientLight(0xffffff, 1))
  renderer.render(viewScene, camera)
  reportViewRenderers.set(key, renderer)
  reportViewScenes.set(key, viewScene)
  reportViewCameras.set(key, camera)
}

async function renderReportModelViews() {
  await nextTick()
  disposeReportModelViews()
  reportSvgMarkup.clear()
  const bars = comparisonReportPages.value[comparisonReportPageIndex.value] ?? []
  bars.forEach((bar) => {
    ;(['front', 'top', 'side'] as ReportViewProjection[]).forEach((projection) => {
      reportSvgMarkup.set(reportCanvasKey(bar.ifcGlobalId, projection), reportSvgMarkupFor(bar, projection))
    })
  })
  reportSvgRevision.value += 1
}

watch(
  [activeWorkflowStep, comparisonReportPageIndex, comparisonReportPages, reportGeometryRevision],
  () => {
    if (activeWorkflowStep.value === 4) void renderReportModelViews()
  },
  { deep: true },
)

async function runC2M() {
  if (!canRunC2M.value || !props.pointcloudAssetId || !props.bimAssetId) return
  if (meshTaskActive.value) {
    ElMessage.warning('网格均匀化任务正在排队或处理中，请稍候')
    return
  }
  c2mRunning.value = true
  c2mError.value = ''
  const resultRequestId = ++c2mResultRequestId
  clearC2MScene()
  c2mDistancesRequestId += 1
  c2mDistances.value = null
  try {
    const response = await computeC2M({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
      profile: 'quick',
      denoiseVersion: denoiseResult.value!.version,
      voxelSize: c2mVoxelSize.value,
      downsampleEnabled: false,
      normalConstraintEnabled: c2mNormalConstraintEnabled.value,
      normalHalfSpaceOnly: false,
      normalMaxAngleDeg: c2mNormalMaxAngleDeg.value,
      maxSearchDistance: c2mMaxSearchDistanceMm.value / 1000,
      normalFallbackMode: c2mNormalConstraintEnabled.value ? 'unknown' : 'nearest',
      knnK: 32,
      ...c2mRequestedVisualization.value,
    })
    if (resultRequestId !== c2mResultRequestId) return
    c2mResult.value = response.data
    syncC2MControls(response.data)
    await syncC2MDistances(response.data)
    scheduleC2MAnalysisPolling(response.data)
    if (!isC2MResultFresh(response.data)) clearC2MScene()
    if (isC2MResultFresh(response.data) && activeWorkflowStep.value === 3) {
      if (rebarDebugActive.value) selectRebarDebugResult()
      await loadC2MToScene()
    }
    ElMessage.success('逐钢筋偏差对比完成')
  } catch (error) {
    if (resultRequestId === c2mResultRequestId) {
      c2mError.value = error instanceof Error ? error.message : 'Scan vs BIM 计算失败'
      ElMessage.error(c2mError.value)
    }
  } finally {
    c2mRunning.value = false
  }
}

async function loadLatestC2M() {
  const requestId = ++c2mResultRequestId
  if (!props.pointcloudAssetId || !props.bimAssetId) {
    clearC2MAnalysisPolling()
    c2mResult.value = null
    c2mDistancesRequestId += 1
    c2mDistances.value = null
    clearC2MScene()
    return
  }
  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  try {
    const previousResultVersion = c2mResult.value?.resultVersion
    const upgradeLoadedScene = c2mSceneLoaded.value && !c2mTileset
    const response = await getLatestC2M(scanId, bimId)
    if (requestId !== c2mResultRequestId) return
    c2mResult.value = response.data
    if (previousResultVersion !== response.data.resultVersion) {
      clearC2MScene()
      syncC2MControls(response.data)
      void syncC2MDistances(response.data)
    }
    scheduleC2MAnalysisPolling(response.data)
    if (!isC2MResultFresh(response.data)) clearC2MScene()
    else if (activeWorkflowStep.value === 3 && !c2mSceneLoading.value &&
      ((!c2mSceneLoaded.value && c2mSceneArtifactAvailable.value) ||
        (upgradeLoadedScene && response.data.analysis?.status === 'ready'))) void loadC2MToScene()
  } catch {
    if (requestId !== c2mResultRequestId) return
    c2mResult.value = null
    clearC2MAnalysisPolling()
    c2mDistancesRequestId += 1
    c2mDistances.value = null
    clearC2MScene()
  }
}

async function loadAnalysisC2MToScene(result: C2MResult, requestId: number) {
  const analysis = result.analysis
  if (
    analysis?.status !== 'ready' ||
    !analysis.manifestUrl ||
    !analysis.baseUrl ||
    !analysis.analysisMeshTilesetUrl ||
    !analysis.contentHash ||
    !analysis.analysisMeshContentHash ||
    !scene ||
    !activeCamera ||
    !renderer
  ) {
    return false
  }

  const manifestValue = await backendRequest<unknown>(getBimGlbUrl(analysis.manifestUrl), {
    method: 'GET',
  })
  const manifest = parseAnalysisC2MManifest(manifestValue)
  if (
    manifest.contentHash !== analysis.contentHash ||
    manifest.inputAnalysisMesh.contentHash !== analysis.analysisMeshContentHash
  ) {
    throw new Error('C2M 距离分片与 analysis-mesh 版本不匹配')
  }
  if (requestId !== c2mSceneLoadRequestId) return true

  clearC2MScene(false)
  const bindings = new Map(manifest.tiles.map((tile) => [tile.tileId, tile]))
  const nextTileset = new TilesRenderer(getBimGlbUrl(analysis.analysisMeshTilesetUrl))
  nextTileset.displayActiveTiles = true
  nextTileset.errorTarget = 16
  nextTileset.downloadQueue.maxJobs = 8
  nextTileset.parseQueue.maxJobs = 2
  nextTileset.fetchOptions = { headers: createUploadHeaders({ Accept: '*/*' }) }

  const dracoLoader = new DRACOLoader(nextTileset.manager)
  dracoLoader.setDecoderPath('/draco/')
  dracoLoader.preload()
  nextTileset.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader }))
  nextTileset.setCamera(activeCamera)
  nextTileset.setResolutionFromRenderer?.(activeCamera, renderer as THREE.WebGLRenderer)

  const normalized = new THREE.Group()
  normalized.name = 'analysis-c2m-normalized'
  const [centerX, centerY, centerZ] = manifest.inputAnalysisMesh.modelFrame.normalizationCenter
  normalized.position.set(-centerX, -centerY, -centerZ)
  normalized.add(nextTileset.group)
  const group = new THREE.Group()
  group.name = 'c2m-analysis-result'
  group.add(normalized)
  if (bimPivot) {
    group.position.copy(bimPivot.position)
    group.quaternion.copy(bimPivot.quaternion)
    group.scale.copy(bimPivot.scale)
  }

  c2mTileset = nextTileset
  c2mAnalysisSession = new AnalysisMeshSession(nextTileset as unknown as TileRendererEvents)
  c2mAnalysisMaterial = createAnalysisC2MMaterial()
  c2mAnalysisDistances.clear()
  c2mAnalysisStatsByComponent.clear()
  manifest.components.forEach((component) => {
    c2mAnalysisStatsByComponent.set(component.ifcGlobalId, component.stats)
  })
  c2mSceneGroup = group
  ;(engineeringRoot ?? contentGroup ?? scene).add(group)

  nextTileset.addEventListener('load-model', ({ scene: tileScene }: any) => {
    if (!tileScene || requestId !== c2mSceneLoadRequestId || c2mTileset !== nextTileset) return
    reportGeometryRevision.value += 1
    if (rendererMode === 'webgpu') sanitizeObjectForWebGPU(tileScene)
    tileScene.traverse((object: THREE.Object3D) => {
      const mesh = object as THREE.Mesh
      if (!mesh.isMesh || !mesh.geometry) return
      const tileId = typeof mesh.userData.tileId === 'string' ? mesh.userData.tileId : ''
      const binding = bindings.get(tileId)
      const positions = mesh.geometry.getAttribute('position')
      if (
        !binding ||
        binding.positionHash !== mesh.userData.positionHash ||
        positions?.count !== binding.vertexCount
      ) {
        console.warn('[C2M] analysis tile identity mismatch', {
          tileId,
          positionHash: mesh.userData.positionHash,
        })
        return
      }

      const attachDistances = async () => {
        await verifyPositionStreamHash(mesh.geometry, binding.positionHash)
        if (requestId !== c2mSceneLoadRequestId || c2mTileset !== nextTileset) return
        const previousMaterial = mesh.material
        mesh.material = c2mAnalysisMaterial!
        mesh.renderOrder = 1
        if (Array.isArray(previousMaterial)) previousMaterial.forEach((item) => item.dispose())
        else previousMaterial.dispose()

        const cached = c2mAnalysisDistances.get(tileId)
        const initialDistances = cached ?? new Float32Array(binding.vertexCount).fill(Number.NaN)
        mesh.geometry.setAttribute('distance', new THREE.BufferAttribute(initialDistances, 1))
        applyAnalysisC2MVertexColors(mesh.geometry, initialDistances)
        if (cached) return

        const distanceUrl = resolveAnalysisArtifactURL(
          getBimGlbUrl(analysis.baseUrl!),
          binding.distancePath,
        )
        const buffer = await backendRequest<ArrayBuffer>(distanceUrl, { method: 'GET', responseType: 'arraybuffer' })
        await verifyPayloadHash(buffer, binding.sha256)
          if (requestId !== c2mSceneLoadRequestId || c2mTileset !== nextTileset) return
          const distances = parseAnalysisMeshDistances(buffer, binding)
          c2mAnalysisDistances.set(tileId, distances)
          if (
            mesh.geometry.getAttribute('position')?.count !== binding.vertexCount ||
            mesh.userData.positionHash !== binding.positionHash
          ) return
          mesh.geometry.setAttribute('distance', new THREE.BufferAttribute(distances, 1))
          applyAnalysisC2MVertexColors(mesh.geometry, distances)
          const colors = mesh.geometry.getAttribute('color')
          if (colors) c2mOriginalVertexColors.set(mesh.geometry, new Float32Array(colors.array as ArrayLike<number>))
          applyComparisonSelection()
          requestRender()
      }
      void attachDistances().catch((error) => {
        console.warn(`[C2M] 偏差分片 ${binding.tileId} 校验或加载失败，保持未知灰色`, error)
      })
    })
  })
  nextTileset.addEventListener('load-root-tileset', () => {
    if (requestId !== c2mSceneLoadRequestId || c2mTileset !== nextTileset) return
    reportGeometryRevision.value += 1
    requestRender()
  })
  nextTileset.addEventListener('load-error', ({ error }: any) => {
    if (c2mTileset === nextTileset) {
      console.error('[C2M] analysis-mesh tile load failed', error)
      c2mError.value = `analysis-mesh 分片加载失败：${error?.message || error}`
    }
  })

  c2mSceneLoaded.value = true
  showMeshWireframe.value = false
  setC2MWireframe(false)
  hideBimWhileC2MIsLoaded()
  statusText.value = manifest.global.unknownCount > 0
    ? `C2M 已加载，原 BIM 已隐藏；${manifest.global.unknownCount.toLocaleString()} 个未覆盖顶点显示为灰色`
    : 'C2M 分析网格已加载，原 BIM 已隐藏以避免叠面闪烁'
  requestRender()
  return true
}

async function loadC2MToScene() {
  await loadC2MSceneAttempt(true)
}

async function recoverC2MSceneVersion(failed: C2MResult, requestId: number) {
  const scanId = props.pointcloudAssetId!, bimId = props.bimAssetId!
  const resultRequestId = ++c2mResultRequestId
  const response = await getLatestC2M(scanId, bimId)
  const superseded = () => requestId !== c2mSceneLoadRequestId || resultRequestId !== c2mResultRequestId ||
    props.pointcloudAssetId !== scanId || props.bimAssetId !== bimId
  if (superseded()) return true
  const latest = response.data
  if (!isC2MResultFresh(latest)) {
    c2mResult.value = latest
    c2mDistancesRequestId++
    c2mDistances.value = null
    clearC2MScene(false)
    throw new Error(latest.staleReason || '比对输入已变化，请重新计算偏差')
  }
  if (latest.resultVersion === failed.resultVersion) return false
  c2mResult.value = latest
  clearC2MScene(false)
  syncC2MControls(latest)
  scheduleC2MAnalysisPolling(latest)
  await syncC2MDistances(latest)
  if (!superseded()) await loadC2MSceneAttempt(false)
  return true
}

async function loadC2MSceneAttempt(retryOnConflict: boolean) {
  const result = c2mResult.value
  if (
    !result ||
    !isC2MResultFresh(result) ||
    !c2mSceneArtifactAvailable.value ||
    !props.pointcloudAssetId ||
    !props.bimAssetId ||
    !scene
  ) return
  const requestId = ++c2mSceneLoadRequestId
  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  c2mSceneLoading.value = true
  c2mError.value = ''
  try {
    // Per-bar ranges refer to the combined comparison PLY, never to tile-local vertices.
    if (result.analysis?.status === 'ready' && !result.diagnostics?.rebarComparison) {
      try {
        if (await loadAnalysisC2MToScene(result, requestId)) {
          if (requestId === c2mSceneLoadRequestId) {
            ElMessage.success('逐构件 C2M 分析网格已加载；原 BIM 已隐藏以避免叠面闪烁')
          }
          return
        }
      } catch (error) {
        clearC2MScene(false)
        console.warn('[C2M] analysis-mesh 加载失败，尝试兼容 PLY', error)
        if (!result.coloredPlyAvailable) throw error
      }
    }
    if (!result.coloredPlyAvailable) throw new Error('C2M 场景资源尚未就绪')
    const blob = await backendRequest<Blob>(getC2MColoredPlyUrl(scanId, bimId, result.resultVersion), { method: 'GET', responseType: 'blob' })
    if (
      requestId !== c2mSceneLoadRequestId ||
      !isC2MResultFresh(c2mResult.value) ||
      c2mResult.value?.resultVersion !== result.resultVersion
    ) return
    const objectUrl = URL.createObjectURL(blob)
    try {
      const geometry = await new PLYLoader().loadAsync(objectUrl)
      if (
        requestId !== c2mSceneLoadRequestId ||
        !isC2MResultFresh(c2mResult.value) ||
        c2mResult.value?.resultVersion !== result.resultVersion
      ) {
        geometry.dispose()
        return
      }
      if (!geometry.attributes.position) throw new Error('C2M 着色 PLY 缺少顶点数据')
      const distances = c2mDistances.value
      if (distances?.length === geometry.attributes.position.count) {
        geometry.setAttribute('distance', new THREE.BufferAttribute(distances, 1))
        applyC2MVertexColors(
          geometry,
          distances,
          c2mRequestedVisualization.value.maxColormapDistance,
          c2mRequestedVisualization.value.toleranceLimit,
          c2mColorMode.value === 'discrete', c2mBandCount.value,
        )
      }
      if (!geometry.attributes.normal) geometry.computeVertexNormals()
      geometry.computeBoundingBox()
      // The PLY is authored in the BIM source frame. Reuse the exact center used
      // when the BIM pivot was normalized; centering from the result's own bounds
      // drifts whenever remeshing changes extents.
      const viewerCenter = bimPivot?.userData?.__viewerNormalizationCenter
      const center = viewerCenter instanceof THREE.Vector3
        ? viewerCenter
        : geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3()
      geometry.translate(-center.x, -center.y, -center.z)
      clearC2MScene(false)
      const group = new THREE.Group()
      group.name = 'c2m-colored-result'
      if (bimPivot) {
        // C2M and BIM now share the engineering root; copy the BIM local pose
        // instead of a world pose (which would apply the root transform twice).
        group.position.copy(bimPivot.position)
        group.quaternion.copy(bimPivot.quaternion)
        group.scale.copy(bimPivot.scale)
      }
      const material = new THREE.MeshBasicMaterial({
        vertexColors: Boolean(geometry.attributes.color),
        toneMapped: false,
        side: THREE.DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -1,
      })
      const mesh = new THREE.Mesh(geometry, material)
      mesh.renderOrder = 1
      group.add(mesh)
      ;(engineeringRoot ?? contentGroup ?? scene).add(group)
      c2mSceneGroup = group
      c2mSceneLoaded.value = true
      applyComparisonSelection()
      showMeshWireframe.value = false
      setC2MWireframe(false)
      hideBimWhileC2MIsLoaded()
      requestRender()
      ElMessage.success(rebarDebugActive.value && rebarDebugDisplaySurface.value !== 'result'
        ? '偏差结果已加载，点击“偏差着色”查看' : '偏差结果已自动显示')
    } finally { URL.revokeObjectURL(objectUrl) }
  } catch (error) {
    if (requestId === c2mSceneLoadRequestId) {
      if (retryOnConflict && (error as { response?: { status?: number } })?.response?.status === 409) {
        try {
          if (await recoverC2MSceneVersion(result, requestId)) return
        } catch (refreshError) { error = refreshError }
      }
      if (requestId !== c2mSceneLoadRequestId) return
      c2mError.value = error instanceof Error ? error.message : '加载 C2M 结果失败'
      ElMessage.error(c2mError.value)
    }
  } finally {
    if (requestId === c2mSceneLoadRequestId) {
      if (!c2mSceneLoaded.value) restoreBimVisibilityAfterC2M()
      c2mSceneLoading.value = false
    }
  }
}

function clearC2MScene(invalidateLoad = true) {
  clearRebarDebugOverlay()
  rebarDebugMaterials.clear()
  if (invalidateLoad) {
    c2mSceneLoadRequestId += 1
    c2mSceneLoading.value = false
  }
  c2mAnalysisSession?.dispose()
  c2mAnalysisSession = null
  const usedAnalysisTiles = Boolean(c2mTileset)
  c2mTileset?.dispose()
  c2mTileset = null
  c2mAnalysisMaterial?.dispose()
  c2mAnalysisMaterial = null
  c2mAnalysisDistances.clear()
  c2mAnalysisStatsByComponent.clear()
  c2mSceneGroup?.removeFromParent()
  if (c2mSceneGroup && !usedAnalysisTiles) disposeObject3D(c2mSceneGroup)
  c2mSceneGroup = null
  c2mSceneLoaded.value = false
  if (invalidateLoad) restoreBimVisibilityAfterC2M()
  syncWireframeStateFromCurrentMesh()
  requestRender()
}

function clearC2MSceneAndOpenCoarseEditor() {
  clearC2MScene()
  activeWorkflowStep.value = 1
  clearDenoisePreview()
  applySceneVisibility()
  if (!bimPivot) return

  registrationStage.value = 'coarse'
  fineAlignResult.value = null
  editMode.value = true
  selectedItemId.value = 'bim'
  transformMode.value = 'translate'
  enableElementPicking.value = false
  selectSceneObject('bim', { enableEdit: true })
  requestRender()
}

function hideBimWhileC2MIsLoaded() {
  if (!bimPivot) return
  if (bimVisibilityBeforeC2M === null) {
    bimVisibilityBeforeC2M = bimVisible.value
  }
  if (activeWorkflowStep.value >= 3) bimVisible.value = false
  applySceneVisibility()
}

function restoreBimVisibilityAfterC2M() {
  if (bimVisibilityBeforeC2M === null) return
  bimVisible.value = bimVisibilityBeforeC2M
  bimVisibilityBeforeC2M = null
  applySceneVisibility()
}

async function applyC2MVisualization() {
  const resultVersion = c2mResult.value?.resultVersion
  if (!canRecolorC2M.value || !resultVersion || !props.pointcloudAssetId || !props.bimAssetId) return
  c2mRecoloring.value = true
  c2mError.value = ''
  const resultRequestId = ++c2mResultRequestId
  const reloadScene = c2mSceneLoaded.value
  try {
    const response = await recolorC2M({
      modelScanFileId: props.pointcloudAssetId,
      modelBimFileId: props.bimAssetId,
      resultVersion,
      ...c2mRequestedVisualization.value,
    })
    if (resultRequestId !== c2mResultRequestId) return
    c2mResult.value = response.data
    // A save only changes presentation. Preserve newer edits made during the request,
    // and reuse the existing raw distances instead of briefly resetting the automatic range.
    if (!c2mDistances.value) void syncC2MDistances(response.data)
    scheduleC2MAnalysisPolling(response.data)
    if (!isC2MResultFresh(response.data)) clearC2MScene()
    else if (reloadScene) await loadC2MToScene()
    ElMessage.success('C2M 配色与直方图已更新')
  } catch (error) {
    if (resultRequestId === c2mResultRequestId) {
      c2mError.value = error instanceof Error ? error.message : '应用 C2M 配色失败'
      ElMessage.error(c2mError.value)
    }
  } finally {
    c2mRecoloring.value = false
  }
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
const meshProvenanceText = computed(() => {
  const status = meshStatus.value
  if (status?.status !== 'succeeded' || status.algorithm !== REBAR_SWEEP_ALGORITHM) return ''
  const version = status.implementationVersion ? `v${status.implementationVersion}` : '版本未知'
  return `${meshAlgorithmDisplayName} · ${version} · ${meshAlgorithmParameterLabel.value}`
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
    if (response.data.status === 'succeeded') {
      if (response.data.algorithm === REBAR_SWEEP_ALGORITHM) {
        const persistedCrossSectionSides = Number(response.data.parameters?.cross_section_sides)
        const persistedAxialSpacing = Number(response.data.parameters?.axial_spacing)
        const persistedMaxChordError = Number(response.data.parameters?.max_chord_error)
        if (Number.isInteger(persistedCrossSectionSides) && persistedCrossSectionSides >= 8 && persistedCrossSectionSides <= 128) {
          meshCrossSectionSides.value = persistedCrossSectionSides
        }
        if (Number.isFinite(persistedAxialSpacing) && persistedAxialSpacing >= 0.0001 && persistedAxialSpacing <= 1) {
          meshAxialSpacing.value = persistedAxialSpacing
        }
        if (Number.isFinite(persistedMaxChordError) && persistedMaxChordError >= 0.000001 && persistedMaxChordError <= 0.01) {
          meshMaxChordError.value = persistedMaxChordError
        }
      }
    }
    meshError.value = response.data.status === 'failed' ? response.data.lastError || '网格均匀化失败' : ''
    if (
      (response.data.status === 'queued' || response.data.status === 'processing') &&
      isC2MResultFresh(c2mResult.value)
    ) {
      invalidateC2MResult('网格均匀化任务已开始，请在完成后重新计算')
    }
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

async function runMeshRemesh() {
  if (!props.bimAssetId || meshRunning.value) return
  await refreshMeshStatus(true)
  if (meshTaskActive.value) {
    ElMessage.info('网格均匀化任务正在排队或处理中')
    return
  }
  meshRunning.value = true
  meshError.value = ''
  restoreRemeshScene(false)
  clearLoadedRemeshMesh()
  try {
    const response = await remeshBimAsset(props.bimAssetId, {
      algorithm: REBAR_SWEEP_ALGORITHM,
      params: {
        cross_section_sides: meshCrossSectionSides.value,
        axial_spacing: meshAxialSpacing.value,
        max_chord_error: meshMaxChordError.value,
      },
      force: meshStatus.value?.status === 'succeeded',
    })
    meshStats.value = null
    meshStatus.value = { supported: true, status: response.data.status }
    invalidateC2MResult('网格均匀化任务已开始，请在完成后重新计算')
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
  remeshSceneGroup.removeFromParent()
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
  remeshSceneSnapshot = {
    bimVisible: bimVisible.value,
    pointcloudVisible: pointcloudVisible.value,
    objects: objects.map((object) => ({
      object,
      visible: object.visible,
      position: object.position.clone(),
      quaternion: object.quaternion.clone(),
      scale: object.scale.clone(),
    })),
  }
}

function restoreRemeshScene(showMessage = true) {
  if (!remeshSceneSnapshot) return
  clearLoadedRemeshMesh()
  const snapshot = remeshSceneSnapshot
  snapshot.objects.forEach(({ object, visible, position, quaternion, scale }) => {
    if (!object.parent) return
    object.visible = visible
    object.position.copy(position)
    object.quaternion.copy(quaternion)
    object.scale.copy(scale)
    object.updateMatrixWorld(true)
  })
  bimVisible.value = snapshot.bimVisible
  pointcloudVisible.value = snapshot.pointcloudVisible
  remeshSceneSnapshot = null
  remeshRestoreAvailable.value = false
  applySceneVisibility()
  registrationStage.value = 'coarse'
  fineAlignResult.value = null
  editMode.value = true
  selectedItemId.value = 'bim'
  transformMode.value = 'translate'
  enableElementPicking.value = false
  refreshSelectedTransformUi()
  requestRender()
  if (showMessage) ElMessage.success('已恢复原始场景')
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
      const viewerCenter = bimPivot?.userData?.__viewerNormalizationCenter
      const center = viewerCenter instanceof THREE.Vector3
        ? viewerCenter
        : geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3()
      geometry.translate(-center.x, -center.y, -center.z)

      const group = new THREE.Group()
      group.name = 'remesh-result'
      const position = new THREE.Vector3()
      const quaternion = new THREE.Quaternion()
      const scale = new THREE.Vector3(1, 1, 1)
      if (bimPivot) {
        position.copy(bimPivot.position)
        quaternion.copy(bimPivot.quaternion)
        scale.copy(bimPivot.scale)
      }
      group.position.copy(position)
      group.quaternion.copy(quaternion)
      group.scale.copy(scale)

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
      ;(engineeringRoot ?? contentGroup ?? scene).add(group)
      remeshMeshLoaded.value = true
      remeshRestoreAvailable.value = Boolean(remeshSceneSnapshot)
      remeshSolidHidden.value = false
      remeshWireHidden.value = false
      showMeshWireframe.value = Boolean(wire)
      // The remesh result occupies the same surface as the source BIM. Keep the
      // source hidden until the user explicitly restores the captured scene.
      bimVisible.value = false
      applySceneVisibility()
      requestRender()
      ElMessage.closeAll()
      ElMessage.success(`均匀化结果已加载（${geometry.attributes.position.count.toLocaleString()} 顶点）`)
    } finally {
      URL.revokeObjectURL(objectUrl)
    }
  } catch (error) {
    ElMessage.closeAll()
    meshError.value = error instanceof Error ? error.message : '加载均匀化结果失败'
    restoreRemeshScene(false)
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
  showMeshWireframe.value = !remeshWireHidden.value
  requestRender()
}
const projectionMode = ref<ProjectionMode>('perspective')
const materialMode = ref<MaterialMode>('unlit')
const showGrid = ref(true)
const showMeshWireframe = ref(false)
const meshWireframeLabel = computed(() =>
  c2mSceneActive.value || remeshMeshLoaded.value ? '当前结果线框' : '原 BIM 线框',
)
const meshWireframeTooltip = computed(() =>
  `${showMeshWireframe.value ? '关闭' : '显示'}${meshWireframeLabel.value}`,
)
const showBounds = ref(false)
const backgroundColor = ref('#0b1020')
const isLightBackground = ref(false)
const pointcloudColor = ref('#86898D')
const persistedPointcloudColor = ref('#86898D')
const pointcloudColorOverridden = ref(true)
const pointcloudColorSaving = ref(false)
let pointcloudColorSaveTimer: number | null = null
const enableClipping = ref(false)
const clipAxis = ref<ClipAxis>('z')
const clipInvert = ref(false)
const clipPosition = ref(0)
const clipRange = ref({ min: -1, max: 1 })
// 粗配准默认进入几何载体编辑态，模型加载后自动显示组合变换 Gizmo。
const editMode = ref(true)
const showTransformHandles = ref(true)
const selectedItemId = ref<SelectedItemId>('bim')
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
const tilesErrorTarget = ref(32)
// 构件拾取与载体变换共用指针事件；用户可在高级设置中主动切换。
const enableElementPicking = ref(false)
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
const clipBoundsDisabledReason = computed(() => {
  if (enableClipping.value || showBounds.value) return ''
  if (loadingBim.value) return 'BIM 加载中，请稍候再开启剖切'
  if (!hasModel.value) return '请先加载 BIM 模型'
  if (loadingPointcloud.value) return '点云加载中，请等待加载完成后再开启剖切'
  if (!hasTileset.value) return '请先加载点云'
  return ''
})
const clipBoundsTooltip = computed(() => {
  if (enableClipping.value && showBounds.value) return '关闭剖切'
  return clipBoundsDisabledReason.value || '开启剖切'
})
const bimVisibilityLabel = computed(() => (bimVisible.value ? '隐藏模型' : '显示模型'))
const pointcloudVisibilityLabel = computed(() =>
  pointcloudVisible.value ? '隐藏点云' : '显示点云',
)
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
const originalMaterialStore = new WeakMap<THREE.Object3D, THREE.Material | THREE.Material[]>()
const originalPointcloudColors = new WeakMap<THREE.BufferGeometry, THREE.BufferAttribute | null>()
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
let edlPipeline: PointCloudEdlPipeline | null = null
const edlEnabled = ref(true)
let animationId = 0
let resizeObserver: ResizeObserver | null = null
let observedViewportEl: HTMLDivElement | null = null
let contentGroup: THREE.Group | null = null
// All engineering (N,E,Z) geometry is mounted below this single conversion
// node.  The viewport itself remains Three.js Y-up, matching the calibration
// workspace used by the analysis editor.
let engineeringRoot: THREE.Group | null = null
let clippingGroup: ClippingGroup | null = null
let gridHelper: InfiniteGroundGrid | null = null
let transformControls: ViewerTransformControls | null = null
let transformHelper: THREE.Object3D | null = null
let rotationControls: ViewerTransformControls | null = null
let rotationHelper: THREE.Object3D | null = null
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
let pointcloudLoadPromise: Promise<void> | null = null
let pointcloudLoadAssetId: number | null = null
let pointcloudRootReady = false
// Do not present the intermediate BIM-only camera pose while the point cloud
// and saved alignment are still being restored. The first visible frame should
// already represent the final combined scene.
let initialSceneReady = false
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
let analysisHoverPoint: THREE.Vector3 | null = null
let analysisAreaPoints: THREE.Vector3[] = []
let analysisGroup: THREE.Group | null = null
const archivedAnalysisGroups: THREE.Group[] = []
let analysisDistanceLine: Line2 | null = null
let analysisDistanceStartMarker: THREE.Sprite | null = null
let analysisDistanceEndMarker: THREE.Sprite | null = null
let analysisDistanceHoverMarker: THREE.Sprite | null = null
let analysisAreaLine: THREE.Line | null = null
let analysisAreaFill: THREE.Mesh | null = null
let analysisAreaMarkers: THREE.Sprite[] = []
let measurementModelDiagonal = 10
let analysisPointerDown: { x: number; y: number } | null = null
let lastMeasurementPickWarningAt = 0
const measurementBackendIds = new Map<string, number>()
const measurementPanelOffsets = new Map<string, { x: number; y: number }>()
const hiddenMeasurementIds = new Set<string>()
const measurementBadgeOffsetX = 34
const measurementBadges = ref<Array<{
  id: string
  kind: 'point' | 'distance' | 'area'
  title: string
  mainLabel?: string
  mainValue?: string
  rows: Array<{ label: string; value: string }>
  overlay: ViewerMeasurementBadgeOverlay
}>>([])

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

// 宿主可以用 `navigation` 回调，也可以监听 `back` / `stepChange` 事件。
function closePage() {
  if (props.navigation?.back) {
    props.navigation.back()
    return
  }

  emit('back')
}

function notifyStepChange(step: number) {
  if (props.navigation?.stepChange) {
    props.navigation.stepChange(step)
    return
  }

  emit('stepChange', step)
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

function applyEditorTheme() {
  const light = isLightBackground.value
  backgroundColor.value = light ? '#eef3f8' : '#0b1020'
  if (gridHelper) {
    gridHelper.setColor(light ? '#6d8399' : '#2a6f82')
  }
  onBackgroundColorChange()
  requestRender()
}

function toggleEditorTheme() {
  isLightBackground.value = !isLightBackground.value
  applyEditorTheme()
}

function normalizePointcloudColor(value: string, fallback = '#ffffff') {
  const normalized = value.trim()
  return /^#[0-9a-fA-F]{6}$/.test(normalized) ? normalized.toLowerCase() : fallback
}

function pointcloudAttribute(geometry: THREE.BufferGeometry, names: string[]) {
  const attrs = geometry.attributes as Record<string, THREE.BufferAttribute>
  const entry = Object.entries(attrs).find(([name]) => names.includes(name.toLowerCase()))
  return entry?.[1] ?? null
}

function pointcloudScalarSource(geometry: THREE.BufferGeometry) {
  const intensity = pointcloudAttribute(geometry, ['intensity', '_intensity', 'scalar_intensity'])
  if (intensity?.count) return (index: number) => intensity.getX(index)
  const colors = originalPointcloudColors.get(geometry) ?? (geometry.getAttribute('color') as THREE.BufferAttribute | undefined)
  if (colors?.count) return (index: number) => colors.getX(index) * 0.2126 + colors.getY(index) * 0.7152 + colors.getZ(index) * 0.0722
  return null
}

function samplePointcloudColorRamp(value: number): [number, number, number] {
  const t = THREE.MathUtils.clamp(value, 0, 1)
  if (pointcloudColorRamp.value === 'grayscale') return [t, t, t]
  if (pointcloudColorRamp.value === 'viridis') {
    const stops = [[0, 0.267, 0.005, 0.329], [0.25, 0.283, 0.141, 0.458], [0.5, 0.128, 0.567, 0.551], [0.75, 0.37, 0.789, 0.383], [1, 0.993, 0.906, 0.144]]
    const upper = stops.findIndex((stop) => t <= stop[0])
    const b = stops[Math.max(1, upper < 0 ? stops.length - 1 : upper)]
    const a = stops[Math.max(0, (upper < 0 ? stops.length - 1 : upper) - 1)]
    const mix = (t - a[0]) / Math.max(1e-6, b[0] - a[0])
    return [THREE.MathUtils.lerp(a[1], b[1], mix), THREE.MathUtils.lerp(a[2], b[2], mix), THREE.MathUtils.lerp(a[3], b[3], mix)]
  }
  const color = new THREE.Color().setHSL(((1 - t) * 240) / 360, 1, 0.5)
  return [color.r, color.g, color.b]
}

function collectPointcloudColorStats(root: THREE.Object3D) {
  const histogram = new Array(64).fill(0)
  root.traverse((obj: any) => {
    if (!obj?.isPoints) return
    const geometry = obj.geometry as THREE.BufferGeometry
    if (!originalPointcloudColors.has(geometry)) {
      originalPointcloudColors.set(geometry, (geometry.getAttribute('color') as THREE.BufferAttribute | undefined)?.clone() ?? null)
    }
    const scalar = pointcloudScalarSource(geometry)
    if (!scalar) return
    const count = geometry.getAttribute('position')?.count ?? 0
    const stride = Math.max(1, Math.ceil(count / 50000))
    let min = Infinity
    let max = -Infinity
    for (let index = 0; index < count; index += stride) {
      const value = scalar(index)
      if (Number.isFinite(value)) { min = Math.min(min, value); max = Math.max(max, value) }
    }
    const span = Math.max(1e-9, max - min)
    for (let index = 0; index < count; index += stride) {
      const value = scalar(index)
      if (Number.isFinite(value)) histogram[Math.min(63, Math.max(0, Math.floor(((value - min) / span) * 64)))] += 1
    }
  })
  pointcloudIntensityHistogram.value = histogram
}

function restorePointcloudOriginalColors(root: THREE.Object3D) {
  root.traverse((obj: any) => {
    if (!obj?.isPoints) return
    const geometry = obj.geometry as THREE.BufferGeometry
    const original = originalPointcloudColors.get(geometry)
    if (original) geometry.setAttribute('color', original.clone())
    else geometry.deleteAttribute('color')
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material]
    materials.forEach((material: any) => {
      if (!material) return
      if ('vertexColors' in material) material.vertexColors = Boolean(original)
      if ('colorNode' in material) material.colorNode = original ? tslVertexColor() : tslColor(0xffffff)
      material.needsUpdate = true
    })
  })
}

function applyPointcloudIntensityColoring(root: THREE.Object3D) {
  root.traverse((obj: any) => {
    if (!obj?.isPoints || !obj.geometry || !obj.material) return
    const geometry = obj.geometry as THREE.BufferGeometry
    if (!originalPointcloudColors.has(geometry)) originalPointcloudColors.set(geometry, (geometry.getAttribute('color') as THREE.BufferAttribute | undefined)?.clone() ?? null)
    const scalar = pointcloudScalarSource(geometry)
    const position = geometry.getAttribute('position') as THREE.BufferAttribute | undefined
    if (!scalar || !position) return
    let min = Infinity
    let max = -Infinity
    for (let index = 0; index < position.count; index++) {
      const value = scalar(index)
      if (Number.isFinite(value)) { min = Math.min(min, value); max = Math.max(max, value) }
    }
    const span = Math.max(1e-9, max - min)
    const rangeSpan = Math.max(0.01, pointcloudColorRange.value.max - pointcloudColorRange.value.min)
    const colors = new Float32Array(position.count * 3)
    for (let index = 0; index < position.count; index++) {
      const normalized = (scalar(index) - min) / span
      const displayed = (normalized - pointcloudColorRange.value.min) / rangeSpan
      const [r, g, b] = samplePointcloudColorRamp(displayed)
      colors[index * 3] = r; colors[index * 3 + 1] = g; colors[index * 3 + 2] = b
    }
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material]
    materials.forEach((material: any) => {
      if (material?.color?.isColor) material.color.set(0xffffff)
      if ('vertexColors' in material) material.vertexColors = true
      if ('colorNode' in material) material.colorNode = tslVertexColor()
      material.needsUpdate = true
    })
  })
  requestRender()
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

function handlePointcloudColorStats(stats: { histogram: number[]; hasIntensity: boolean; hasRgb: boolean }) {
  pointcloudIntensityHistogram.value = [...stats.histogram]
}

function applyPointcloudDisplay(root: THREE.Object3D | null = pointcloudGroup) {
  if (!root) return
  if (pointcloudColorMode.value === 'intensity') {
    applyPointcloudMaterialMode(root)
    applyPointcloudIntensityColoring(root)
    return
  }
  restorePointcloudOriginalColors(root)
  applyPointcloudMaterialMode(root)
  requestRender()
}

function updatePointcloudColorRange(range: PointcloudColorRange) {
  pointcloudColorRange.value = range
  applyPointcloudDisplay()
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
  isLightBackground.value = false
  applyEditorTheme()
}

function syncGridVisibility() {
  if (gridHelper) {
    gridHelper.visible = showGrid.value
  }
}

function isObjectEffectivelyVisible(object: THREE.Object3D | null) {
  if (!object) return false
  let current: THREE.Object3D | null = object
  while (current && current !== scene) {
    if (!current.visible) return false
    current = current.parent
  }
  return object.visible
}

function getVisibleSceneObjects() {
  const candidates: Array<THREE.Object3D | null> = [
    bimPivot,
    pointcloudWrapper,
    c2mSceneGroup,
    remeshSceneGroup,
    denoisePreview,
  ]
  return candidates.filter((object): object is THREE.Object3D => isObjectEffectivelyVisible(object))
}

function updateGridPlacement() {
  if (!gridHelper || !contentGroup) return
  // Root bounds stay stable as point-cloud LODs stream in and out. Keep the
  // scan as the ground reference while the BIM is moved during alignment.
  const box = (tileset ? getTilesetWorldBounds(tileset) : null) ?? getContentWorldBox()
  if (box) gridHelper.setBounds(box)
  if (activeCamera && gridHelper.visible) gridHelper.updateForCamera(activeCamera)
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

    shaft.scale.set(2, 30, 2)
    shaft.position.y = 15
    cone.scale.set(5, 18, 5)
    cone.position.y = 39
    hitArea.scale.set(9, 48, 9)
    hitArea.position.y = 24
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

function clipHandleWorldUnitsPerPixel(anchor: THREE.Vector3): number {
  const height = viewportEl.value?.clientHeight ?? 0
  if (!activeCamera || height <= 0) return 0
  activeCamera.updateMatrixWorld()
  const projectionScale = activeCamera.projectionMatrix.elements[5]!
  if (isPerspectiveCamera(activeCamera)) {
    const depth = Math.max(activeCamera.near, -anchor.clone().applyMatrix4(activeCamera.matrixWorldInverse).z)
    return 2 * depth / (height * projectionScale)
  }
  return 2 / (height * projectionScale)
}

function updateClipHandles(box: THREE.Box3) {
  const group = ensureClipHandlesGroup()
  if (!group) return
  const center = box.getCenter(new THREE.Vector3())
  group.visible = true
  group.children.forEach((child) => {
    const handle = child as THREE.Group
    const { axis, invert, normal, shaft, cone, idleColor, activeColor } = handle.userData as any
    const isActiveFace = axis === clipAxis.value && invert === clipInvert.value
    const color = isActiveFace ? activeColor : idleColor
    const anchor = axis === 'x'
      ? new THREE.Vector3(invert ? box.max.x : box.min.x, center.y, center.z)
      : axis === 'y'
        ? new THREE.Vector3(center.x, invert ? box.max.y : box.min.y, center.z)
        : new THREE.Vector3(center.x, center.y, invert ? box.max.z : box.min.z)
    const worldPerPixel = clipHandleWorldUnitsPerPixel(anchor)
    // 48px arrow + 8px face gap; neither depends on the clipping box dimensions.
    handle.scale.setScalar(worldPerPixel)
    handle.position.copy(anchor).addScaledVector(normal, worldPerPixel * 8)
    shaft.material.color.copy(color)
    shaft.material.opacity = isActiveFace ? 0.95 : 0.82
    cone.material.color.copy(color)
    cone.material.opacity = isActiveFace ? 1 : 0.9
  })
  group.updateMatrixWorld(true)
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
  analysisPointerDown = null
  if (controls) controls.enabled = true
  analysisStartPoint = null
  analysisHoverPoint = null
  analysisAreaPoints = []
  analysisPoint.value = null
  analysisDistance.value = null
  analysisPoints.value = []
  analysisDistances.value = []
  analysisAreas.value = []
  const groups = analysisGroup ? [analysisGroup, ...archivedAnalysisGroups] : [...archivedAnalysisGroups]
  groups.forEach((group) => {
    if (scene) scene.remove(group)
    group.traverse((child: any) => {
      child.geometry?.dispose?.()
      child.material?.dispose?.()
    })
  })
  archivedAnalysisGroups.splice(0)
  hiddenMeasurementIds.clear()
  measurementPanelOffsets.clear()
  analysisGroup = null
  analysisDistanceLine = null
  analysisDistanceStartMarker = null
  analysisDistanceEndMarker = null
  analysisDistanceHoverMarker = null
  analysisAreaLine = null
  analysisAreaFill = null
  analysisAreaMarkers = []
  measurementBadges.value = []
}

async function clearAllMeasurements() {
  clearAnalysis()
  const ids = new Set<number>(measurementBackendIds.values())
  measurementBackendIds.clear()
  const assetIds = [props.bimAssetId, props.pointcloudAssetId].filter(
    (id): id is number => typeof id === 'number' && id > 0,
  )
  await Promise.all(assetIds.map(async (assetId) => {
    try {
      const response = await listMeasurements(assetId)
      response.data.forEach((record) => ids.add(record.id))
    } catch (error) {
      console.warn('[BimPointcloudAlign] 读取测量记录失败', error)
    }
  }))
  await Promise.all([...ids].map(async (id) => {
    try {
      await deleteMeasurement(id)
    } catch (error) {
      console.warn('[BimPointcloudAlign] 删除测量记录失败', error)
    }
  }))
}

function measurementAssetId() {
  return props.pointcloudAssetId ?? props.bimAssetId
}

async function persistMeasurement(kind: MeasurementKind, payload: unknown) {
  const assetId = measurementAssetId()
  if (!assetId) return
  const localId = typeof payload === 'object' && payload && 'id' in payload
    ? String(payload.id)
    : ''
  try {
    const response = await createMeasurement(assetId, kind, payload)
    if (localId) measurementBackendIds.set(localId, response.data.id)
  } catch (error) {
    console.warn('[BimPointcloudAlign] 保存测量记录失败', error)
  }
}

function hideMeasurementBadge(id: string) {
  hiddenMeasurementIds.add(id)
  syncMeasurementBadges()
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

function deleteMeasurementBadge(badge: (typeof measurementBadges.value)[number]) {
  const index = badge.kind === 'point'
    ? analysisPoints.value.findIndex((item) => item.id === badge.id)
    : badge.kind === 'distance'
      ? analysisDistances.value.findIndex((item) => item.id === badge.id)
      : analysisAreas.value.findIndex((item) => item.id === badge.id)
  if (badge.kind === 'point') analysisPoints.value = analysisPoints.value.filter((_, i) => i !== index)
  if (badge.kind === 'distance') analysisDistances.value = analysisDistances.value.filter((_, i) => i !== index)
  if (badge.kind === 'area') analysisAreas.value = analysisAreas.value.filter((_, i) => i !== index)
  hiddenMeasurementIds.delete(badge.id)
  measurementPanelOffsets.delete(badge.id)
  const backendId = measurementBackendIds.get(badge.id)
  measurementBackendIds.delete(badge.id)
  if (backendId !== undefined) void deleteMeasurement(backendId).catch(() => undefined)
  rebuildAnalysisVisualsFromState()
  syncMeasurementBadges()
}

function rebuildAnalysisVisualsFromState() {
  const currentScene = scene
  if (!currentScene) return
  archivedAnalysisGroups.forEach((group) => {
    currentScene.remove(group)
    group.traverse((child: any) => {
      child.geometry?.dispose?.()
      child.material?.map?.dispose?.()
      child.material?.dispose?.()
    })
  })
  archivedAnalysisGroups.splice(0)
  analysisPoints.value.forEach((point) => {
    const group = new THREE.Group()
    group.add(Object.assign(createMeasurementPinSprite('#22d3ee'), { position: new THREE.Vector3(point.x, point.y, point.z) }))
    currentScene.add(group)
    archivedAnalysisGroups.push(group)
  })
  analysisDistances.value.forEach((record) => {
    const group = new THREE.Group()
    const start = new THREE.Vector3(record.start.x, record.start.y, record.start.z)
    const end = new THREE.Vector3(record.end.x, record.end.y, record.end.z)
    group.add(createAnalysisDistanceLine(start, end))
    const startMarker = createMeasurementPinSprite('#ff4040'); startMarker.position.copy(start); group.add(startMarker)
    const endMarker = createMeasurementPinSprite('#ff5a5a', .96); endMarker.position.copy(end); group.add(endMarker)
    currentScene.add(group)
    archivedAnalysisGroups.push(group)
  })
  syncAnalysisLineResolutions()
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
  marker.renderOrder = 10003
  return marker
}

function getAdaptiveMeasurementMarkerPixels() {
  const sizeFactor = Math.log10(Math.max(measurementModelDiagonal, 1))
  return THREE.MathUtils.clamp(10 + sizeFactor * 2, 10, 16)
}

function scaleMeasurementMarker(marker: THREE.Sprite, targetPixels?: number) {
  if (!activeCamera || !viewportEl.value || !marker.visible) return
  const rect = viewportEl.value.getBoundingClientRect()
  const viewportHeight = Math.max(rect.height, 1)
  const pixelSize = targetPixels ?? getAdaptiveMeasurementMarkerPixels()
  let worldUnitsPerPixel = 1
  if (isPerspectiveCamera(activeCamera)) {
    const distance = activeCamera.position.distanceTo(marker.position)
    const fov = THREE.MathUtils.degToRad(activeCamera.fov)
    worldUnitsPerPixel = (2 * distance * Math.tan(fov * 0.5)) / viewportHeight
  } else {
    worldUnitsPerPixel = (2 * orthoViewSize) / viewportHeight
  }
  marker.scale.set(
    Math.max(worldUnitsPerPixel * pixelSize, Number.EPSILON),
    Math.max(worldUnitsPerPixel * pixelSize, Number.EPSILON),
    1,
  )
}

function createAnalysisDistanceLine(start: THREE.Vector3, end: THREE.Vector3) {
  const line = new Line2(
    new LineGeometry(),
    new LineMaterial({
      color: '#d63d3d',
      dashed: true,
      dashSize: 0.9,
      gapSize: 0.48,
      transparent: true,
      opacity: 0.96,
      linewidth: 2.8,
      worldUnits: false,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  )
  line.geometry.setPositions([start.x, start.y, start.z, end.x, end.y, end.z])
  line.computeLineDistances()
  line.renderOrder = 10002
  return line
}

function syncAnalysisLineResolutions() {
  if (!renderer) return
  const width = renderer.domElement.clientWidth || 1
  const height = renderer.domElement.clientHeight || 1
  const groups = analysisGroup ? [analysisGroup, ...archivedAnalysisGroups] : archivedAnalysisGroups
  groups.forEach((group) => {
    group.traverse((child) => {
      if (child instanceof Line2) child.material.resolution.set(width, height)
      if (child instanceof THREE.Sprite) scaleMeasurementMarker(child)
    })
  })
}

function createMeasurementId() {
  return globalThis.crypto?.randomUUID?.() || `measurement-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function projectMeasurementPoint(point: THREE.Vector3) {
  if (!activeCamera || !viewportEl.value) return null
  const rect = viewportEl.value.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  const projected = point.clone().project(activeCamera)
  if (projected.z < -1 || projected.z > 1) return null
  return {
    x: ((projected.x + 1) * .5) * rect.width,
    y: ((-projected.y + 1) * .5) * rect.height,
  }
}

function formatMeasurementMeters(value: number, digits = 3) {
  return `${value.toFixed(digits)} m`
}

function syncMeasurementBadges() {
  const next: typeof measurementBadges.value = []
  analysisPoints.value.forEach((point, index) => {
    const id = point.id || `point-${index}`
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
      overlay: { visible: true, x: screenPoint.x + measurementBadgeOffsetX + offset.x, y: screenPoint.y - 18 + offset.y },
    })
  })
  analysisDistances.value.forEach((record, index) => {
    const id = record.id || `distance-${index}`
    if (hiddenMeasurementIds.has(id)) return
    const start = new THREE.Vector3(record.start.x, record.start.y, record.start.z)
    const end = new THREE.Vector3(record.end.x, record.end.y, record.end.z)
    // Keep the result card to the right of the clicked pair. Choosing the
    // rightmost endpoint makes the placement stable regardless of line direction.
    const startScreenPoint = projectMeasurementPoint(start)
    const endScreenPoint = projectMeasurementPoint(end)
    const screenPoint = startScreenPoint && endScreenPoint
      ? (startScreenPoint.x >= endScreenPoint.x ? startScreenPoint : endScreenPoint)
      : endScreenPoint ?? startScreenPoint
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
      overlay: { visible: true, x: screenPoint.x + measurementBadgeOffsetX + offset.x, y: screenPoint.y - 18 + offset.y },
    })
  })
  analysisAreas.value.forEach((record, index) => {
    const id = record.id || `area-${index}`
    if (hiddenMeasurementIds.has(id)) return
    const points = record.points.map((point) => new THREE.Vector3(point.x, point.y, point.z))
    const screenPoint = projectMeasurementPoint(createAreaMetrics(points)?.centroid ?? points[0])
    if (!screenPoint) return
    const offset = measurementPanelOffsets.get(id) ?? { x: 0, y: 0 }
    next.push({
      id,
      kind: 'area',
      title: `面积 #${index + 1}`,
      mainLabel: '面积',
      mainValue: `${record.area.toFixed(2)} m²`,
      rows: [{ label: '周长', value: `${record.perimeter.toFixed(2)} m` }],
      overlay: { visible: true, x: screenPoint.x + measurementBadgeOffsetX + offset.x, y: screenPoint.y - 18 + offset.y },
    })
  })
  measurementBadges.value = next
}

function createAreaMetrics(points: THREE.Vector3[]) {
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
  const area = Math.abs(twiceArea) * 0.5
  if (area <= 1e-8) return null
  let perimeter = 0
  points.forEach((point, index) => { perimeter += point.distanceTo(points[(index + 1) % points.length]) })
  return {
    area,
    perimeter,
    centroid: origin.clone()
      .addScaledVector(axisU, centroidX / (3 * twiceArea))
      .addScaledVector(axisV, centroidY / (3 * twiceArea)),
    projected,
  }
}

function pickAnalysisPoint(event: PointerEvent) {
  if (!raycaster || !activeCamera || !contentGroup) return null
  const pointer = getPointerNdc(event)
  if (!pointer) return null
  // Keep point-cloud picking usable at every zoom level. Three's default
  // Points threshold is a fixed world-space value, so it becomes unreliable
  // when the camera moves close to or far from a scan.
  const viewportHeight = Math.max(viewportEl.value?.clientHeight || 1, 1)
  const viewDistance = controls?.target
    ? activeCamera.position.distanceTo(controls.target)
    : activeCamera.position.length()
  const worldUnitsPerPixel = isPerspectiveCamera(activeCamera)
    ? (2 * Math.max(viewDistance, 0.001) * Math.tan(THREE.MathUtils.degToRad(activeCamera.fov) * 0.5)) / viewportHeight
    : (2 * Math.max(orthoViewSize, 0.001)) / viewportHeight
  raycaster.params.Points.threshold = THREE.MathUtils.clamp(
    worldUnitsPerPixel * Math.max(pointcloudPointSize.value * 1.5, 5),
    1e-4,
    Math.max(pointcloudMaxDim * 0.05, 0.01),
  )
  contentGroup.updateMatrixWorld(true)
  raycaster.setFromCamera(pointer, activeCamera)
  const targets: THREE.Object3D[] = []
  if (bimRoot) targets.push(bimRoot)
  if (tileset?.group) targets.push(tileset.group)
  if (c2mSceneGroup) targets.push(c2mSceneGroup)
  if (!targets.length) targets.push(...contentGroup.children)
  const hit = raycaster.intersectObjects(targets, true)[0]?.point?.clone() ?? null
  if (!hit) return null

  // Match the BIM preview's forgiving pick behavior: when the cursor is near
  // an existing measurement point, reuse that exact point instead of creating
  // a visually disconnected endpoint.
  const rect = renderer?.domElement?.getBoundingClientRect?.()
  if (!rect || !activeCamera) return hit
  const candidates = [
    analysisStartPoint,
    analysisPoint.value
      ? new THREE.Vector3(analysisPoint.value.x, analysisPoint.value.y, analysisPoint.value.z)
      : null,
    ...analysisPoints.value.map((point) => new THREE.Vector3(point.x, point.y, point.z)),
    ...analysisDistances.value.flatMap((record) => [
      new THREE.Vector3(record.start.x, record.start.y, record.start.z),
      new THREE.Vector3(record.end.x, record.end.y, record.end.z),
    ]),
    ...analysisAreas.value.flatMap((record) =>
      record.points.map((point) => new THREE.Vector3(point.x, point.y, point.z))),
    ...analysisAreaPoints,
  ].filter((point): point is THREE.Vector3 => !!point)
  let snapped = hit
  let nearest = 18
  candidates.forEach((candidate) => {
    const projected = candidate.clone().project(activeCamera!)
    if (projected.z < -1 || projected.z > 1) return
    const x = ((projected.x + 1) * 0.5) * rect.width
    const y = ((-projected.y + 1) * 0.5) * rect.height
    const distance = Math.hypot(x - (event.clientX - rect.left), y - (event.clientY - rect.top))
    if (distance <= nearest) {
      nearest = distance
      snapped = candidate.clone()
    }
  })
  return snapped
}

function renderAnalysisPoint(point: THREE.Vector3, color = '#22d3ee') {
  if (!scene) return
  if (!analysisGroup) { analysisGroup = new THREE.Group(); analysisGroup.renderOrder = 10001; scene.add(analysisGroup) }
  const marker = createMeasurementPinSprite(color)
  marker.position.copy(point)
  marker.visible = true
  analysisGroup.add(marker)
  scaleMeasurementMarker(marker)
  requestRender()
}

function updateAnalysisDistanceVisuals(
  start: THREE.Vector3,
  end: THREE.Vector3 | null,
  preview = false,
) {
  if (!scene) return
  if (!analysisGroup) {
    analysisGroup = new THREE.Group()
    analysisGroup.renderOrder = 10001
    scene.add(analysisGroup)
  }
  if (!analysisDistanceLine) {
    analysisDistanceLine = createAnalysisDistanceLine(start, start)
    analysisGroup.add(analysisDistanceLine)
  }
  if (!analysisDistanceStartMarker) {
    analysisDistanceStartMarker = createMeasurementPinSprite('#ff4040')
    analysisGroup.add(analysisDistanceStartMarker)
  }
  analysisDistanceStartMarker.position.copy(start)
  analysisDistanceStartMarker.visible = true
  scaleMeasurementMarker(analysisDistanceStartMarker)
  if (!end) {
    analysisDistanceLine.visible = false
    syncAnalysisLineResolutions()
    requestRender()
    return
  }
  if (!analysisDistanceEndMarker) {
    analysisDistanceEndMarker = createMeasurementPinSprite('#ff5a5a', .96)
    analysisGroup.add(analysisDistanceEndMarker)
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
      analysisGroup.add(analysisDistanceHoverMarker)
    }
    analysisDistanceHoverMarker.position.copy(end)
    analysisDistanceHoverMarker.visible = true
    scaleMeasurementMarker(analysisDistanceHoverMarker)
  } else if (analysisDistanceHoverMarker) {
    analysisDistanceHoverMarker.visible = false
  }
  syncAnalysisLineResolutions()
  requestRender()
}

function completeAnalysisDistance(end: THREE.Vector3) {
  if (!analysisStartPoint) return
  const start = analysisStartPoint.clone()
  const dx = end.x - start.x
  const dy = end.y - start.y
  const dz = end.z - start.z
  const horizontalDistance = Math.hypot(dx, dz)
  const verticalDistance = Math.abs(dy)
  const record: AnalysisDistance = {
    id: globalThis.crypto?.randomUUID?.() || `distance-${Date.now()}`,
    start: { x: start.x, y: start.y, z: start.z },
    end: { x: end.x, y: end.y, z: end.z },
    distance: start.distanceTo(end),
    heightDifference: verticalDistance,
    horizontalDistance,
    verticalDistance,
    slopeDegrees: horizontalDistance <= 1e-8
      ? (verticalDistance <= 1e-8 ? 0 : 90)
      : Math.atan2(verticalDistance, horizontalDistance) * 180 / Math.PI,
  }
  updateAnalysisDistanceVisuals(start, end)
  if (analysisGroup) archivedAnalysisGroups.push(analysisGroup)
  analysisGroup = null
  analysisDistanceLine = null
  analysisDistanceStartMarker = null
  analysisDistanceEndMarker = null
  analysisDistanceHoverMarker = null
  analysisStartPoint = null
  analysisHoverPoint = null
  analysisDistance.value = record
  analysisDistances.value = [...analysisDistances.value, record]
  syncMeasurementBadges()
  void persistMeasurement('distance', record)
}

function updateAnalysisAreaVisuals(points: THREE.Vector3[], previewPoint: THREE.Vector3 | null = null) {
  if (!scene) return
  if (!analysisGroup) { analysisGroup = new THREE.Group(); analysisGroup.renderOrder = 10001; scene.add(analysisGroup) }
  const displayedPoints = previewPoint ? [...points, previewPoint] : points
  if (!analysisAreaLine) {
    analysisAreaLine = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineDashedMaterial({
        color: 0xff5252,
        dashSize: 0.9,
        gapSize: 0.48,
        transparent: true,
        opacity: 0.92,
        depthTest: false,
        depthWrite: false,
      }),
    )
    analysisAreaLine.renderOrder = 10001
    analysisGroup.add(analysisAreaLine)
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
          opacity: 0.16,
          depthTest: false,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      )
      analysisAreaFill.renderOrder = 10000
      analysisGroup.add(analysisAreaFill)
    }
    const metrics = createAreaMetrics(displayedPoints)
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

  while (analysisAreaMarkers.length < points.length) {
    const marker = createMeasurementPinSprite('#ff4040')
    analysisAreaMarkers.push(marker)
    analysisGroup.add(marker)
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

function completeAnalysisArea() {
  const metrics = createAreaMetrics(analysisAreaPoints)
  if (!metrics) return
  const record: AnalysisArea = {
    id: globalThis.crypto?.randomUUID?.() || `area-${Date.now()}`,
    points: analysisAreaPoints.map((point) => ({ x: point.x, y: point.y, z: point.z })),
    area: metrics.area,
    perimeter: metrics.perimeter,
  }
  updateAnalysisAreaVisuals(analysisAreaPoints)
  if (analysisGroup) archivedAnalysisGroups.push(analysisGroup)
  analysisGroup = null
  analysisAreaLine = null
  analysisAreaFill = null
  analysisAreaMarkers = []
  analysisAreas.value = [...analysisAreas.value, record]
  analysisAreaPoints = []
  syncMeasurementBadges()
  void persistMeasurement('area', record)
}

function cancelActiveAnalysis() {
  analysisPointerDown = null
  if (controls) controls.enabled = true
  analysisStartPoint = null
  analysisHoverPoint = null
  analysisAreaPoints = []
  if (analysisGroup && scene) scene.remove(analysisGroup)
  analysisGroup?.traverse((child: any) => {
    child.geometry?.dispose?.()
    child.material?.dispose?.()
  })
  analysisGroup = null
  analysisDistanceLine = null
  analysisDistanceStartMarker = null
  analysisDistanceEndMarker = null
  analysisDistanceHoverMarker = null
  analysisAreaLine = null
  analysisAreaFill = null
  analysisAreaMarkers = []
}

function updateAnalysisDistancePreview(point: THREE.Vector3 | null) {
  analysisHoverPoint = point
  if (analysisStartPoint) updateAnalysisDistanceVisuals(analysisStartPoint, point, true)
}

function selectAnalysisMode(mode: AnalysisMode) {
  cancelActiveAnalysis()
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
  if (rebarDebugActive.value) return rebarDebugBounds()
  const box = new THREE.Box3()
  const objects = getVisibleSceneObjects()
  objects.forEach((object) => box.expandByObject(object))
  return objects.length && !box.isEmpty() ? box : null
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
  if (rect.width <= 0 || rect.height <= 0) return
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))
  const dpr = Math.min(window.devicePixelRatio || 1, dprCap)

  renderer.setPixelRatio(dpr)
  // Keep the canvas CSS box in sync with the viewport as the right panel is
  // opened or closed. Passing `false` here only resized the drawing buffer,
  // leaving the initial inline canvas width behind as a visible black strip.
  renderer.setSize(width, height)
  renderer.domElement.style.display = 'block'
  edlPipeline?.setSize(width, height)

  if (isPerspectiveCamera(activeCamera)) {
    activeCamera.aspect = width / height
    activeCamera.updateProjectionMatrix()
  } else {
    updateOrthographicFrustum()
  }

  updateTilesetResolution()
  syncAnalysisLineResolutions()
}

function observeViewport() {
  if (!resizeObserver || !viewportEl.value || observedViewportEl === viewportEl.value) return
  if (observedViewportEl) resizeObserver.unobserve(observedViewportEl)
  resizeObserver.observe(viewportEl.value)
  observedViewportEl = viewportEl.value
}

async function restoreViewportAfterWorkflowStep() {
  await nextTick()
  if (!renderer || !viewportEl.value) return

  // Preserve GPU resources across workflow steps; reconnect defensively if
  // a future layout change replaces the host.
  if (renderer.domElement.parentElement !== viewportEl.value) {
    viewportEl.value.appendChild(renderer.domElement)
  }
  observeViewport()
  syncRendererSize()
  requestRender()
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

    if (activeWorkflowStep.value === 4) return

    if (tileset && pointcloudWrapper?.visible) {
      runWithSuppressedConsoleAssert(() => {
        applyTilesErrorTarget()
        updateTilesetResolution()
        tileset!.setCamera(activeCamera!)
        tileset!.setResolutionFromRenderer?.(activeCamera!, renderer! as THREE.WebGLRenderer)
        tileset!.update()
      })
    }

    if (c2mTileset && c2mSceneActive.value) {
      runWithSuppressedConsoleAssert(() => {
        c2mTileset!.setCamera(activeCamera!)
        c2mTileset!.setResolutionFromRenderer?.(activeCamera!, renderer! as THREE.WebGLRenderer)
        c2mTileset!.update()
      })
    }

    syncDenoisePreviewTransform()
    syncBoundsHelpers()
    syncAnalysisLineResolutions()
    syncMeasurementBadges()
    syncPointcloudCameraPose()
    if (edlPipeline && edlEnabled.value && isPerspectiveCamera(activeCamera)) {
      const renderedWithEdl = edlPipeline.render(scene, activeCamera)
      if (!renderedWithEdl && !edlPipeline.enabled) edlEnabled.value = false
    } else {
      renderer.render(scene, activeCamera)
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

function mountControls(camera: THREE.PerspectiveCamera | THREE.OrthographicCamera) {
  if (!renderer) return

  const currentTarget = controls?.target.clone() ?? new THREE.Vector3(0, 0, 0)
  controls?.dispose()
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = false
  controls.target.copy(currentTarget)
  controls.addEventListener('change', () => {
    syncBoundsHelpers()
    syncPointcloudCameraPose()
  })
  controls.update()

  if (scene && renderer) {
    ;[transformHelper, rotationHelper].forEach((helper) => {
      if (helper) scene?.remove(helper)
    })
    transformControls?.dispose()
    rotationControls?.dispose()

    const configureTransformController = (
      controller: ViewerTransformControls,
      mode: TransformMode,
      size: number,
    ) => {
      controller.visible = false
      controller.enabled = false
      controller.setSize?.(size)
      controller.setSpace?.('world')
      controller.setMode(mode)
      const helper = controller.getHelper() as unknown as THREE.Object3D
      helper.visible = false
      helper.frustumCulled = false
      helper.traverse?.((obj: any) => {
        obj.frustumCulled = false
        if (!obj.material) return
        if (Array.isArray(obj.material)) {
          obj.material.forEach((item: any) => {
            if (item) item.depthTest = false
          })
        } else {
          obj.material.depthTest = false
        }
      })
      controller.addEventListener('dragging-changed', (event: any) => {
        const dragging = !!event?.value
        const anyDragging = dragging || Boolean(transformControls?.dragging || rotationControls?.dragging)
        if (controls) controls.enabled = !anyDragging
        if (!anyDragging) {
          syncTransformFixFromSelected()
          if (enableClipping.value) {
            updateClipRangeFromContent({ preserveT: true })
            applyClippingState()
          }
        }
      })
      bindTransformChangeEvents(
        controller,
        () => {
          if (registrationStage.value === 'coarse') coarseAlignmentDirty.value = true
        },
        () => {
          syncTransformFixFromSelected()
          syncBoundsHelpers()
          requestRender()
        },
      )
      scene?.add(helper)
      return helper
    }

    transformControls = new TransformControls(camera, renderer.domElement) as ViewerTransformControls
    rotationControls = new TransformControls(camera, renderer.domElement) as ViewerTransformControls
    // The two helpers share the same target, so the viewport reads as one
    // manipulator with arrows/planes plus a single green yaw ring.
    transformHelper = configureTransformController(transformControls, 'translate', 1.35)
    rotationHelper = configureTransformController(rotationControls, 'rotate', 1.55)
    applyTransformSelection()
    syncAllTransformFixValuesFromSelected()
  }
}

async function initScene() {
  if (!viewportEl.value || renderer) return
  if (initPromise) return initPromise

  initPromise = (async () => {
    if (!viewportEl.value) return

    scene = new THREE.Scene()
    contentGroup = new THREE.Group()
    contentGroup.name = 'Calibration content'
    engineeringRoot = new THREE.Group()
    engineeringRoot.name = 'Engineering (N,E,Z) to viewport Y-up'
    engineeringRoot.rotation.x = -Math.PI / 2
    contentGroup.add(engineeringRoot)
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
      renderer.domElement.addEventListener('contextmenu', handleViewportContextMenu)

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

      gridHelper = new InfiniteGroundGrid(isLightBackground.value ? '#6d8399' : '#2a6f82')
      scene.add(gridHelper)
      syncGridVisibility()

      resizeObserver = new ResizeObserver(() => {
        syncRendererSize()
      })
      observeViewport()

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
      edlPipeline = new PointCloudEdlPipeline(nextRenderer, {
        enabled: edlEnabled.value,
        strength: 1.0,
        radius: 1.0,
      })
      setupRendererCommon()
    }

    buildWebGLRenderer()
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

function syncPointcloudCameraPose() {
  if (!activeCamera || !controls) return
  pointcloudCameraPose.value = {
    camera: activeCamera.position.clone(),
    target: controls.target.clone(),
    up: activeCamera.up.clone(),
  }
}

function setPointcloudViewDirection(direction: [number, number, number]) {
  if (!activeCamera || !controls) return
  const box = getVisibleContentBox()
  if (!box) return

  const center = box.getCenter(new THREE.Vector3())
  const directionVector = new THREE.Vector3(...direction)
  if (directionVector.lengthSq() < 1e-6) return
  directionVector.normalize()

  controls.target.copy(center)
  const distance = Math.max(activeCamera.position.distanceTo(controls.target), 1)
  activeCamera.up.set(
    0,
    Math.abs(directionVector.y) > 0.98 ? 0 : 1,
    directionVector.y > 0.98 ? -1 : directionVector.y < -0.98 ? 1 : 0,
  )
  activeCamera.position.copy(center).addScaledVector(directionVector, distance)
  activeCamera.lookAt(center)
  activeCamera.updateProjectionMatrix()
  controls.update()
  activeView.value = ''
  syncPointcloudCameraPose()
  requestRender()
}

function orbitPointcloudFromCube(delta: { lon: number; lat: number }) {
  if (!activeCamera || !controls) return
  const offset = activeCamera.position.clone().sub(controls.target)
  if (offset.lengthSq() < 1e-8) return
  const spherical = new THREE.Spherical().setFromVector3(offset)
  spherical.theta += THREE.MathUtils.degToRad(delta.lon)
  spherical.phi = THREE.MathUtils.clamp(
    spherical.phi - THREE.MathUtils.degToRad(delta.lat),
    0.04,
    Math.PI - 0.04,
  )
  activeCamera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical))
  activeCamera.lookAt(controls.target)
  activeCamera.updateProjectionMatrix()
  controls.update()
  syncPointcloudCameraPose()
  requestRender()
}

function rollPointcloudView(direction: -1 | 1) {
  if (!activeCamera || !controls) return
  const viewDirection = activeCamera.position.clone().sub(controls.target).normalize()
  activeCamera.up.applyAxisAngle(viewDirection, direction * Math.PI / 2).normalize()
  activeCamera.lookAt(controls.target)
  activeCamera.updateProjectionMatrix()
  controls.update()
  syncPointcloudCameraPose()
  requestRender()
}

function applyPointcloudPointSize(root: THREE.Object3D | null) {
  if (!root) return
  root.traverse((obj: any) => {
    if (!obj?.isPoints || !obj.material) return
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material]
    materials.forEach((material: any) => {
      if (material && 'size' in material) {
        material.size = pointcloudPointSize.value
        material.needsUpdate = true
      }
      if (material && 'sizeNode' in material) {
        material.sizeNode = float(pointcloudPointSize.value)
        material.needsUpdate = true
      }
    })
  })
  requestRender()
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

function toggleProjectionMode() {
  setProjectionMode(projectionMode.value === 'perspective' ? 'orthographic' : 'perspective')
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
      size: 2.5,
      sizeAttenuation: false,
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
      size: 2.5,
      sizeAttenuation: false,
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

function normalizePointMaterial(obj: any) {
  if (!obj?.isPoints || !obj?.material) return
  const mats = Array.isArray(obj.material) ? obj.material : [obj.material]
  mats.forEach((m: any) => {
    if (m && 'size' in m) {
      m.sizeAttenuation = false
      m.size = pointcloudPointSize.value
      if (!m.userData?.roundPointsHooked) {
        m.userData = m.userData || {}
        m.userData.roundPointsHooked = true
        m.onBeforeCompile = (shader: any) => {
          shader.fragmentShader = shader.fragmentShader.replace(
            '#include <clipping_planes_fragment>',
            `#include <clipping_planes_fragment>
            if (length(gl_PointCoord - vec2(0.5)) > 0.5) discard;`
          )
        }
      }
      m.needsUpdate = true
    }
  })
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
      normalizePointMaterial(obj)
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
      } else if (original) {
        obj.material = getMaterialClone(original, materialMode.value, opts)
      }
      normalizePointMaterial(obj)
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
    } else if (original) {
      obj.material = useWebGPU
        ? getOrCreatePointcloudUnlitTSLMaterial(original, opts)
        : getOrCreatePointcloudUnlitMaterial(original, opts)
    }
    normalizePointMaterial(obj)
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

function onClippingButtonClick() {
  if (enableClipping.value || showBounds.value) {
    enableClipping.value = false
    showBounds.value = false
    return
  }

  if (clipBoundsDisabledReason.value) {
    ElMessage.warning(clipBoundsDisabledReason.value)
    return
  }

  enableClipping.value = true
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
  return null
}

function selectSceneObject(
  next: SelectedItemId,
  options?: { focus?: boolean; enableEdit?: boolean },
) {
  if (next !== 'bim') return

  selectedItemId.value = next

  if (options?.enableEdit) {
    editMode.value = true
  }

  refreshSelectedTransformUi()
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
  // BIM 是粗配准唯一可编辑的几何载体，点云只作为参考数据。
}

function syncOrientationFixFromSelected() {
  const target = getSelectedObject()
  if (!target) return

  const base = ensureOrientationBase(target)
  if (!base) return
  const offsetQuat = target.quaternion.clone().multiply(base.clone().invert())
  if (transformMode.value === 'rotate') {
    const offsetEuler = new THREE.Euler().setFromQuaternion(offsetQuat, 'YXZ')
    orientationDegX.value = THREE.MathUtils.radToDeg(offsetEuler.x)
    orientationDegY.value = roundToStep(
      normalizeDegrees(THREE.MathUtils.radToDeg(offsetEuler.y)),
    )
    orientationDegZ.value = THREE.MathUtils.radToDeg(offsetEuler.z)
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
  positionOffsetX.value = Number(offset.x.toFixed(6))
  positionOffsetY.value = Number(offset.z.toFixed(6))
  positionOffsetZ.value = Number(offset.y.toFixed(6))
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

function syncTransformHandleVisibility() {
  const visible = showTransformHandles.value && editMode.value && activeWorkflowStep.value === 1 &&
    !enableClipping.value && analysisMode.value === 'none' && !!getSelectedObject()
  for (const controller of [transformControls, rotationControls]) {
    if (!controller) continue
    controller.visible = visible
    controller.enabled = visible
  }
  for (const helper of [transformHelper, rotationHelper]) {
    if (helper) helper.visible = visible
  }
  requestRender()
}

function applyTransformSelection() {
  const target = getSelectedObject()
  if (transformControls && rotationControls) {
    if (!editMode.value || !selectedItemId.value || !target) {
      transformControls.detach()
      rotationControls.detach()
      transformControls.visible = false
      transformControls.enabled = false
      rotationControls.visible = false
      rotationControls.enabled = false
      if (transformHelper) transformHelper.visible = false
      if (rotationHelper) rotationHelper.visible = false
      requestRender()
      return
    }

    target.matrixAutoUpdate = true
    target.updateMatrixWorld(true)
    ensureInitialTransformState(target)
    syncTransformModeForSelection()

    transformControls.setSpace?.('world')
    transformControls.setMode('translate')
    rotationControls.setSpace?.('world')
    rotationControls.setMode('rotate')

    // 组合 Gizmo：平移箭头/平面 + 仅绕 Three Y（业务 Z）旋转的绿色环。
    transformControls.showX = true
    transformControls.showY = true
    transformControls.showZ = true
    rotationControls.showX = false
    rotationControls.showY = true
    rotationControls.showZ = false

    transformControls.attach(target)
    rotationControls.attach(target)
    syncTransformHandleVisibility()
    ensureOrientationBase(target)
    ensurePositionBase(target)
    syncAllTransformFixValuesFromSelected()
    requestRender()
  }

  updateSelectionHighlight()
}

function refreshSelectedTransformUi() {
  syncTransformModeForSelection()
  applyTransformSelection()
  syncBoundsHelpers()
  const target = getSelectedObject()
  ensureInitialTransformState(target)
  // Selection and workflow navigation must not change the numeric origin.
  syncAllTransformFixValuesFromSelected()
}

function setTransformMode(mode: TransformMode) {
  transformMode.value = mode
  refreshSelectedTransformUi()
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
    if (rotationControls) {
      rotationControls.detach()
      rotationControls.visible = false
      rotationControls.enabled = false
    }
    if (rotationHelper) rotationHelper.visible = false
    return
  }

  refreshSelectedTransformUi()
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
  refreshSelectedTransformUi()
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
  if (handleRebarInspectionPointerDown(event)) return
  if (event.shiftKey && event.button === 0 && c2mSceneGroup) {
    const pointer = getPointerNdc(event)
    if (pointer) {
      raycaster.setFromCamera(pointer, activeCamera)
      const hit = raycaster.intersectObject(c2mSceneGroup, true).find(
        (intersection) => intersection.object instanceof THREE.Mesh,
      )
      if (hit?.object instanceof THREE.Mesh) {
        const deviation = sampleC2MDeviationAtPick(hit, hit.object)
        if (deviation !== null) {
          const tolerance = c2mRequestedVisualization.value.toleranceLimit
          const ifcGlobalId = typeof hit.object.userData.ifcGlobalId === 'string'
            ? hit.object.userData.ifcGlobalId
            : ''
          const componentStats = c2mAnalysisStatsByComponent.get(ifcGlobalId)
          const componentSummary = componentStats
            ? ` · 构件均值 ${typeof componentStats.mean === 'number' ? `${(componentStats.mean * 1000).toFixed(1)} mm` : '无覆盖'} · 已知/未知 ${componentStats.knownCount}/${componentStats.unknownCount}`
            : ''
          ElMessage.info(
            `${ifcGlobalId ? `${ifcGlobalId} · ` : ''}${deviation >= 0 ? '+' : ''}${(deviation * 1000).toFixed(1)} mm · ${Math.abs(deviation) <= tolerance ? '容差内' : '超出容差'}${componentSummary}`,
          )
          return
        }
      }
    }
  }
  // Keep the pre-measurement right-button orbit/pan gesture. Only the left
  // button commits measurement points; right-button drags stay with OrbitControls.
  if (analysisMode.value !== 'none') {
    if (event.button === 0) {
      analysisPointerDown = { x: event.clientX, y: event.clientY }
      if (controls) controls.enabled = false
    }
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
  if (picked) {
    clearPickedElement()
    selectedItemId.value = picked
    refreshSelectedTransformUi()
    syncBoundsHelpers()
  }
}

function onViewportPointerMove(event: PointerEvent) {
  if ((event.buttons & 2) !== 0) return
  if (analysisMode.value === 'distance' && analysisStartPoint) {
    const point = pickAnalysisPoint(event)
    if (point) updateAnalysisDistancePreview(point)
    return
  }
  if (analysisMode.value === 'area' && analysisAreaPoints.length) {
    const point = pickAnalysisPoint(event)
    if (point) updateAnalysisAreaVisuals(analysisAreaPoints, point)
    return
  }
  if (!clipDragState) return
  onClipDragMove(event)
}

function handleViewportContextMenu(event: MouseEvent) {
  // A right-button drag is a valid measurement gesture. Prevent the browser
  // menu from interrupting the preview/commit sequence while measuring.
  if (analysisMode.value !== 'none') event.preventDefault()
}

function notifyMeasurementPickUnavailable() {
  const now = Date.now()
  if (now - lastMeasurementPickWarningAt < 800) return
  lastMeasurementPickWarningAt = now
  ElMessage({
    type: 'warning',
    message: '无法获取测量点，请点击 BIM 或点云的可见表面',
    duration: 1800,
    grouping: true,
  })
}

function onViewportPointerUp(event: PointerEvent) {
  if (analysisMode.value !== 'none' && analysisPointerDown) {
    const down = analysisPointerDown
    analysisPointerDown = null
    if (controls) controls.enabled = true
    if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) return
    const point = pickAnalysisPoint(event)
    if (!point) {
      notifyMeasurementPickUnavailable()
      return
    }
    const toPoint = (value: THREE.Vector3): AnalysisPoint => ({ x: value.x, y: value.y, z: value.z })
    if (analysisMode.value === 'locate') {
      renderAnalysisPoint(point, '#22d3ee')
      const record: AnalysisPoint = {
        id: globalThis.crypto?.randomUUID?.() || `locate-${Date.now()}`,
        ...toPoint(point),
      }
      if (analysisGroup) archivedAnalysisGroups.push(analysisGroup)
      analysisGroup = null
      analysisPoint.value = record
      analysisPoints.value = [...analysisPoints.value, record]
      syncMeasurementBadges()
      void persistMeasurement('locate', record)
    } else if (analysisMode.value === 'distance' && !analysisStartPoint) {
      analysisStartPoint = point
      analysisHoverPoint = null
      updateAnalysisDistanceVisuals(point, null)
    } else if (analysisMode.value === 'distance' && analysisStartPoint) {
      completeAnalysisDistance(point)
    } else if (analysisMode.value === 'area') {
      const closeThreshold = Math.max(0.15, (activeCamera?.position.distanceTo(point) ?? 1) * 0.025)
      if (analysisAreaPoints.length >= 3 && point.distanceTo(analysisAreaPoints[0]) < closeThreshold) {
        completeAnalysisArea()
      } else {
        analysisAreaPoints.push(point.clone())
        updateAnalysisAreaVisuals(analysisAreaPoints)
      }
    }
    return
  }
  if (!clipDragState && clipPointerCaptureId === null) return
  endClipDrag(event)
}

function onAnalysisKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && analysisMode.value === 'area') {
    completeAnalysisArea()
    return
  }
  if (event.key === 'Escape' && analysisMode.value !== 'none') {
    cancelActiveAnalysis()
    analysisMode.value = 'none'
  }
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
  rotationHelper?.updateMatrixWorld?.(true)
  syncBoundsHelpers()
  requestRender()
}

function setPositionOffsetAxis(axis: 'x' | 'y' | 'z', value: unknown) {
  if (value == null || String(value).trim() === '') return
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return
  const next = numeric
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

function setOrientationOffsetAxis(axis: 'x' | 'y' | 'z', value: string) {
  if (value.trim() === '') return
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return
  const limit = showOnlyVerticalAxis.value ? 180 : 10
  const next = roundToStep(clamp(numeric, -limit, limit))
  if (axis === 'x') orientationDegX.value = next
  if (axis === 'y') orientationDegY.value = next
  if (axis === 'z') orientationDegZ.value = next
  applyOrientationFixRealtime()
}

function onOrientationNumberInput(axis: 'x' | 'y' | 'z', event: Event) {
  setOrientationOffsetAxis(axis, (event.target as HTMLInputElement).value)
}

function onOrientationNumberBlur(axis: 'x' | 'y' | 'z', event: Event) {
  const input = event.target as HTMLInputElement
  setOrientationOffsetAxis(axis, input.value)
  input.value = formatRotationOffset(
    axis === 'x' ? orientationDegX.value : axis === 'y' ? orientationDegY.value : orientationDegZ.value,
  )
}

function onOrientationNumberKeydown(event: KeyboardEvent, axis: 'x' | 'y' | 'z') {
  if (event.key === 'Enter') {
    onOrientationNumberBlur(axis, event)
    ;(event.target as HTMLInputElement).blur()
  }
}

function applyOrientationFixRealtime() {
  const target = getSelectedObject()
  if (!target) return

  const base = ensureOrientationBase(target)
  if (!base) return
  const delta = showOnlyVerticalAxis.value
    ? new THREE.Quaternion().setFromEuler(
        new THREE.Euler(
          THREE.MathUtils.degToRad(orientationDegX.value),
          THREE.MathUtils.degToRad(orientationDegY.value),
          THREE.MathUtils.degToRad(orientationDegZ.value),
          'YXZ',
        ),
      )
    : new THREE.Quaternion().setFromEuler(
        new THREE.Euler(
          THREE.MathUtils.degToRad(orientationDegX.value),
          THREE.MathUtils.degToRad(orientationDegY.value),
          THREE.MathUtils.degToRad(orientationDegZ.value),
          'XYZ',
        ),
      )
  target.quaternion.copy(delta).multiply(base)
  target.updateMatrixWorld(true)
  if (registrationStage.value === 'coarse') coarseAlignmentDirty.value = true
  transformHelper?.updateMatrixWorld?.(true)
  rotationHelper?.updateMatrixWorld?.(true)
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
  rotationHelper?.updateMatrixWorld?.(true)
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
  if (rotationControls) {
    rotationControls.detach()
    rotationControls.visible = false
    rotationControls.enabled = false
  }
  if (rotationHelper) rotationHelper.visible = false
  updateSelectionHighlight()
}

function applySceneVisibility() {
  syncDenoisePreviewTransform()
  const steelOnly = activeWorkflowStep.value >= 3
  if (c2mSceneGroup) c2mSceneGroup.visible = c2mSceneActive.value && (!rebarDebugActive.value || (rebarDebugDisplaySurface.value === 'mesh' || rebarDebugDisplaySurface.value === 'result'))
  if (remeshSceneGroup) remeshSceneGroup.visible = !steelOnly && !c2mSceneActive.value
  if (denoisePreview) {
    denoisePreview.visible = rebarDebugActive.value ? rebarDebugScan.value : pointcloudVisible.value && (steelOnly || denoiseView.value !== 'source')
    // Keep the cleaned scan neutral so instance colors do not compete with the deviation map.
    const material = denoisePreview.material
    material.color.set(steelOnly ? '#86898D' : '#ffffff')
    if (material.vertexColors !== !steelOnly) {
      material.vertexColors = !steelOnly
      material.needsUpdate = true
    }
  }
  if (bimPivot) {
    const steelIds = new Set(rebarDebugActive.value ? (rebarDebugBar.value ? [rebarDebugBar.value.ifcGlobalId] : []) : comparisonInventory.value?.inventory.bars.map(bar => bar.ifcGlobalId) ?? [])
    bimPivot.traverse(object => {
      if (!(object instanceof THREE.Mesh)) return
      if (steelOnly) {
        if (!comparisonBimVisibility.has(object)) comparisonBimVisibility.set(object, object.visible)
        object.visible = comparisonMeshMatches(object, steelIds) && (rebarDebugActive.value || comparisonBimVisibility.get(object) !== false)
      } else if (comparisonBimVisibility.has(object)) {
        object.visible = comparisonBimVisibility.get(object)!
        comparisonBimVisibility.delete(object)
      }
    })
  }
  if (bimPivot) {
    // Both result variants occupy the BIM surface. Do not allow a generic
    // visibility action to reintroduce coplanar source geometry and z-fighting.
    bimPivot.visible = rebarDebugActive.value ? rebarDebugDisplaySurface.value === 'source' : bimVisible.value && !c2mSceneActive.value && !remeshMeshLoaded.value
  }
  if (pointcloudWrapper) {
    pointcloudWrapper.visible = !steelOnly && pointcloudVisible.value && denoiseView.value === 'source'
  }

  const selectedTarget = getSelectedObject()
  if (selectedTarget && !selectedTarget.visible) {
    clearPickedState()
  } else {
    updateSelectionHighlight()
  }

  clearPickedElement()
  if (steelOnly) applyComparisonSelection()
  else clearRebarDebugOverlay()
  syncBoundsHelpers()
  scheduleClipRangeUpdate()
}

function toggleBimVisibility() {
  if (!bimPivot) return
  if ((c2mSceneActive.value || remeshMeshLoaded.value) && !bimVisible.value) {
    ElMessage.warning('当前结果网格与原 BIM 表面重合；请先清空 C2M 结果或复原均匀化场景，再显示原 BIM')
    return
  }
  bimVisible.value = !bimVisible.value
  applySceneVisibility()
}

function togglePointcloudVisibility() {
  if (!pointcloudWrapper) return
  pointcloudVisible.value = !pointcloudVisible.value
  applySceneVisibility()
}

function toggleEdl() {
  edlEnabled.value = !edlEnabled.value
  edlPipeline?.setEnabled(edlEnabled.value)
  requestRender()
}

function toggleMeshWireframe() {
  const next = !showMeshWireframe.value
  if (c2mSceneActive.value) {
    setC2MWireframe(next)
  } else if (remeshMeshLoaded.value) {
    setRemeshWireframe(next)
  } else {
    setObjectWireframe(bimRoot, next)
  }
  showMeshWireframe.value = next
  requestRender()
}

function setObjectWireframe(root: THREE.Object3D | null, enabled: boolean) {
  if (!root) return
  root.traverse((obj: any) => {
    const material = obj?.material as THREE.Material | THREE.Material[] | undefined
    if (!material) return
    const materials = Array.isArray(material) ? material : [material]
    materials.forEach((item) => {
      const wireframeMaterial = item as THREE.Material & { wireframe?: boolean }
      if (!originalWireframeStore.has(item)) {
        originalWireframeStore.set(item, Boolean(wireframeMaterial.wireframe))
      }
      if ('wireframe' in wireframeMaterial) {
        wireframeMaterial.wireframe = enabled
        wireframeMaterial.needsUpdate = true
      }
    })
  })
}

function setC2MWireframe(enabled: boolean) {
  setObjectWireframe(c2mSceneGroup, enabled)
  const material = c2mAnalysisMaterial as (THREE.Material & { wireframe?: boolean }) | null
  if (material && 'wireframe' in material) {
    material.wireframe = enabled
    material.needsUpdate = true
  }
}

function setRemeshWireframe(enabled: boolean) {
  const wire = remeshSceneGroup?.children.find((child): child is THREE.LineSegments => child instanceof THREE.LineSegments)
  if (!wire) {
    showMeshWireframe.value = false
    return
  }
  remeshWireHidden.value = !enabled
  wire.visible = enabled
}

function syncWireframeStateFromCurrentMesh() {
  if (c2mSceneActive.value) return
  if (remeshMeshLoaded.value) {
    showMeshWireframe.value = !remeshWireHidden.value && remeshWireAvailable.value
    return
  }
  let wireframe = false
  bimRoot?.traverse((obj: any) => {
    if (wireframe) return
    const material = obj?.material as (THREE.Material & { wireframe?: boolean }) | undefined
    const first = Array.isArray(material) ? material[0] : material
    wireframe = Boolean(first?.wireframe)
  })
  showMeshWireframe.value = wireframe
}

function toggleAllVisibility() {
  const shouldShowAll = !bimVisible.value && !pointcloudVisible.value

  if (bimPivot) {
    bimVisible.value = (c2mSceneActive.value || remeshMeshLoaded.value) ? false : shouldShowAll
  }
  if (pointcloudWrapper) {
    pointcloudVisible.value = shouldShowAll
  }

  applySceneVisibility()
}

function resetView() {
  if (bimPivot) {
    bimVisible.value = !c2mSceneActive.value && !remeshMeshLoaded.value
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
  contentGroup.position.copy(center).negate()
  contentGroup.updateMatrixWorld(true)
  syncDenoisePreviewTransform()
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

// 实现集中在 @cloudbim/viewer-core 的 features/alignment/matrix（含往返一致性测试）。
const getRawMatrixWorldForCalibration = rawWorldMatrixForAlignment

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

function revealInitialSceneWhenReady() {
  if (initialSceneReady || !bimPivot) return
  const needsPointcloud = Boolean(props.pointcloudAssetId)
  if (needsPointcloud && (!pointcloudRootReady || !loggedSavedAlignmentKey)) return
  initialSceneReady = true
  fitCameraToContent()
  syncBoundsHelpers()
  requestRender()
}

function getAlignmentRestoreKey(alignment: BimAlignmentResult) {
  return JSON.stringify([bimPivot?.uuid, pointcloudGroup?.uuid,
    alignment.modelScanFileId, alignment.modelBimFileId, alignment.modelId,
    alignmentMatrixFromResult(alignment).toArray()])
}

function tryRestoreSavedAlignment(alignment: BimAlignmentResult) {
  if (!bimPivot || !pointcloudWrapper || !pointcloudGroup || !pointcloudRootReady) {
    return false
  }

  const restoreKey = getAlignmentRestoreKey(alignment)
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

  const alignmentMatrix = alignmentMatrixFromResult(alignment)
  const pointcloudRawMatrixWorld = getRawMatrixWorldForCalibration(pointcloudGroup)
  // 先摆到「原始几何中心」的目标世界位置，再把居中 pivot 的偏移去掉，得到本地姿态。
  const desiredBimWorld = desiredBimWorldMatrix({
    pointcloudRawWorld: pointcloudRawMatrixWorld,
    alignmentMatrix,
    bimNormalizationCenter: bimCenter,
  })
  const localMatrix = toLocalMatrix(desiredBimWorld, bimPivot.parent?.matrixWorld ?? null)

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
  editMode.value = activeWorkflowStep.value === 1
  selectedItemId.value = 'bim'
  refreshSelectedTransformUi()
  void nextTick(() => {
    applyTransformSelection()
    syncAllTransformFixValuesFromSelected()
    requestRender()
  })
  syncBoundsHelpers()
  updateClipRangeFromContent({ preserveT: true })
  applyClippingState()
  restoredSavedAlignmentKey = restoreKey
  sceneAlignmentReady.value = true

  console.info('[BimPointcloudAlign] 已恢复后端校准矩阵到场景', {
    restoreKey,
    bimPivotPosition: vectorToPlainObject(bimPivot.position.clone()),
    bimPivotQuaternion: quaternionToPlainObject(bimPivot.quaternion.clone()),
  })
  logBimRelativeTransform()
  return true
}

async function fetchAndLogSavedAlignmentIfExists() {
  if (!props.pointcloudAssetId || !props.bimAssetId) {
    return
  }

  // 1. 优先拉取后端保存的配准数据，解除对 3D 渲染完成时机的强依赖
  if (!hasSavedAlignmentMatrix.value && !latestAlignmentResult.value) {
    try {
      const response = await getBimAlignment({
        modelScanFileId: props.pointcloudAssetId,
        modelBimFileId: props.bimAssetId,
      })

      if (response?.data && !latestAlignmentResult.value) {
        latestAlignmentResult.value = response.data
        logSavedAlignmentMatrix(response.data)
        hasSavedAlignmentMatrix.value = true
        coarseAlignmentDirty.value = false
      }
    } catch (error: any) {
      const status = error?.response?.status
      if ((status === 400 || status === 404) && !latestAlignmentResult.value) {
        hasSavedAlignmentMatrix.value = false
        loggedSavedAlignmentKey = `${props.pointcloudAssetId}:${props.bimAssetId}`
        revealInitialSceneWhenReady()
        return
      }

      console.error('[BimPointcloudAlign] 获取后端校准矩阵失败', error)
    }
  }

  // 2. 检查 3D 视口渲染对象是否已完全就绪
  if (!bimPivot || !pointcloudGroup || !pointcloudRootReady) {
    return
  }

  const logKey = `${props.pointcloudAssetId}:${props.bimAssetId}`

  if (latestAlignmentResult.value) {
    const restored = tryRestoreSavedAlignment(latestAlignmentResult.value)
    logBimRelativeTransform()
    if (restored) {
      loggedSavedAlignmentKey = logKey
      revealInitialSceneWhenReady()
    } else {
      window.setTimeout(() => {
        void fetchAndLogSavedAlignmentIfExists()
      }, 250)
    }
  } else {
    loggedSavedAlignmentKey = logKey
    revealInitialSceneWhenReady()
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

  const rigid = scanToBimRigidTransform(
    getRawMatrixWorldForCalibration(bimPivot),
    getRawMatrixWorldForCalibration(pointcloudGroup),
  )
  const modelPairs = modelPairsFromRigidTransform(rigid)

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
    // The saved matrix already describes this scene; a later load callback
    // must not restore it over subsequent manual edits.
    restoredSavedAlignmentKey = getAlignmentRestoreKey(response.data)
    sceneAlignmentReady.value = true
    hasSavedAlignmentMatrix.value = true
    coarseAlignmentDirty.value = false
    invalidateC2MResult('配准矩阵已保存，请重新计算')
    denoiseResult.value = null
    clearDenoisePreview()
    void loadLatestDenoise()
    void loadLatestC2M()
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
  if (!canSaveCalibration.value || savingCalibration.value) return
  savingCalibration.value = true
  try {
    // Saving owns the success/error notice; the completion action must not repeat it.
    await handleSaveAlignment()
  } finally {
    savingCalibration.value = false
  }
}

function activateCoarseRegistration() {
  registrationStage.value = 'coarse'
  fineAlignResult.value = null
}

function activateFineRegistration() {
  registrationStage.value = 'fine'
  fineAlignResult.value = null
  if (!hasSavedAlignmentMatrix.value || coarseAlignmentDirty.value) {
    ElMessage.warning('请先完成粗配准保存，再进行精细化配准')
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
    if (result.appliedFineResult) {
      invalidateC2MResult('精细化配准已应用，请重新计算')
    }
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
  initialSceneReady = false

  const token = ++bimLoadToken
  loadingBim.value = true
  bimMetadata.value = null

  try {
    const assetDetailResult = await getAssetDetail(props.bimAssetId)
    const assetDetail = assetDetailResult.data
    if (token !== bimLoadToken) return
    if (assetDetail.type !== 'bim' || assetDetail.status !== 'ready' || !assetDetail.glbUrl) {
      statusText.value = 'BIM 模型尚未就绪'
      if (!silent) {
        ElMessage.warning('BIM 模型尚未就绪，暂时无法加载')
      }
      return
    }

    const glbUrl = getBimGlbUrl(assetDetail.glbUrl)

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
    if (token !== bimLoadToken) return

    const loader = new GLTFLoader()
    const dracoLoader = new DRACOLoader()
    dracoLoader.setDecoderPath('/draco/')
    loader.setDRACOLoader(dracoLoader)
    loader.setRequestHeader(
      createUploadHeaders({ Accept: 'model/gltf-binary,application/octet-stream,*/*' }),
    )

    await new Promise<void>((resolve, reject) => {
      loader.load(
        glbUrl,
        (gltf: GLTF) => {
          if (token !== bimLoadToken) {
            disposeObject3D(gltf.scene)
            dracoLoader.dispose()
            resolve()
            return
          }
          dracoLoader.dispose()

          if (bimPivot) {
            bimPivot.removeFromParent()
            disposeObject3D(bimPivot)
          }

          const root = gltf.scene
          flattenStaticMeshesToRoot(root)
          const pivot = createCenteredPivot(root)
          ;(engineeringRoot ?? nextContentGroup).add(pivot)

          bimRoot = root
          bimPivot = pivot
          sceneAlignmentReady.value = false
          ensureOrientationBase(pivot)
          ensurePositionBase(pivot)
          bimLoaded.value = true
          bimVisible.value = true
          reportGeometryRevision.value += 1

          ensureInitialTransformState(bimPivot)
          recenterLoadedContentAsWhole()
          applySceneVisibility()
          applyBimMaterialMode(bimPivot)
          updateClipRangeFromContent({ preserveT: true })
          applyClippingState()
          syncBoundsHelpers()
          fitCameraToObject(bimPivot)
          revealInitialSceneWhenReady()
          // 粗配准默认选中 BIM 几何载体，视口显示组合平移/旋转 Gizmo。
          if (editMode.value) {
            selectSceneObject('bim', { enableEdit: true })
          }
          statusText.value = `已加载 BIM：${props.bimDisplayName || assetDetail.sourceName}`
          logScenePoseDiagnostics('after-bim-load')
          if (pointcloudRootReady) {
            void fetchAndLogSavedAlignmentIfExists()
          }
          resolve()
        },
        undefined,
        (error) => {
          dracoLoader.dispose()
          if (token !== bimLoadToken) {
            resolve()
            return
          }
          reject(error)
        },
      )
    })

  } catch (error) {
    if (token !== bimLoadToken) return
    console.error(error)
    if (!silent) {
      ElMessage.error(error instanceof Error ? error.message : '加载 BIM 失败')
    }
    statusText.value = '加载 BIM 失败'
  } finally {
    if (token === bimLoadToken) {
      loadingBim.value = false
    }
  }
}

async function runPointcloudPreprocess() {
  if (!props.pointcloudAssetId || pointcloudPreprocessRunning.value) return
  pointcloudPreprocessRunning.value = true
  pointcloudPreprocessError.value = ''
  try {
    pointcloudPreprocessResult.value = (await computePointcloudPreprocess(props.pointcloudAssetId)).data
    await handleLoadPointCloudFromApi(false, true)
    if (!pointcloudPreprocessRequired.value) ElMessage.success('台面识别与点云预处理完成')
  } catch (error) {
    pointcloudPreprocessError.value = error instanceof Error ? error.message : '点云预处理失败'
  } finally {
    pointcloudPreprocessRunning.value = false
  }
}

async function handleLoadPointCloudFromApi(silent = false, force = false) {
  const assetId = props.pointcloudAssetId
  if (!assetId) {
    if (!silent) {
      ElMessage.warning('缺少点云资产 ID')
    }
    return
  }

  if (!force && pointcloudLoadPromise && pointcloudLoadAssetId === assetId) {
    return pointcloudLoadPromise
  }
  if (!force && pointcloudLoadAssetId === assetId && tileset && pointcloudWrapper && pointcloudLoaded.value) {
    return
  }

  const loadPromise = loadPointcloudFromApi(assetId, silent)
  pointcloudLoadPromise = loadPromise
  pointcloudLoadAssetId = assetId
  try {
    await loadPromise
  } finally {
    if (pointcloudLoadPromise === loadPromise) pointcloudLoadPromise = null
  }
}

async function loadPointcloudFromApi(assetId: number, silent: boolean) {
  await initScene()
  if (!scene || !contentGroup || !activeCamera || !renderer) return
  const nextContentGroup = contentGroup
  initialSceneReady = false

  const token = ++pointcloudLoadToken
  loadingPointcloud.value = true

  try {
    if (pointcloudWrapper) {
      pointcloudWrapper.removeFromParent()
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
    sceneAlignmentReady.value = false
    clearDenoisePreview()
    pointcloudPreprocessRequired.value = false
    pointcloudPreprocessError.value = ''
    pointcloudPreprocessResult.value = null
    const assetDetailResult = await getAssetDetail(assetId)
    const assetDetail = assetDetailResult.data
    if (
      assetDetail.type !== 'pointcloud' ||
      assetDetail.status !== 'ready'
    ) {
      throw new Error('点云资源尚未就绪，暂时无法加载')
    }

    const [representationsResult, preprocessResult] = await Promise.allSettled([
      getAssetRepresentations(assetId),
      getPointcloudPreprocess(assetId),
    ])
    if (preprocessResult.status === 'fulfilled') {
      pointcloudPreprocessResult.value = preprocessResult.value.data
    }
    if (representationsResult.status === 'rejected') throw representationsResult.reason
    if (preprocessResult.status === 'rejected' || !preprocessResult.value.data) {
      pointcloudPreprocessRequired.value = true
      throw preprocessResult.status === 'rejected'
        ? preprocessResult.reason
        : new Error('分析需要先生成无台面点云')
    }
    const selectedRepresentation = selectPointcloudRepresentation(
      assetDetail.tilesetUrl || '',
      representationsResult.value.data?.list || [],
      true,
      preprocessResult.value.data.version,
    )
    if (selectedRepresentation.kind !== 'table-free') {
      pointcloudPreprocessRequired.value = true
      throw new Error('分析需要先生成无台面点云')
    }

    const savedPointcloudColor = normalizePointcloudColor(assetDetail.pointcloudColor || '', '')
    pointcloudColorOverridden.value = true
    pointcloudColor.value = savedPointcloudColor || '#86898D'
    persistedPointcloudColor.value = savedPointcloudColor || '#86898D'

    const url = getPointcloudTilesetUrl(selectedRepresentation.url)
    const nextTileset = new TilesRenderer(url)
    // Keep the parent tile visible while finer children load so zooming never
    // causes a temporary drop in point density.
    nextTileset.displayActiveTiles = true
    nextTileset.errorTarget = tilesErrorTarget.value
    nextTileset.fetchOptions = {
      headers: createUploadHeaders({ Accept: '*/*' }),
    }

    const dracoLoader = new DRACOLoader(nextTileset.manager)
    dracoLoader.setDecoderPath('/draco/')
    dracoLoader.preload()
    nextTileset.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader }))
    nextTileset.setCamera(activeCamera)
    updateTilesetResolution()

    const wrapper = new THREE.Group()
  wrapper.add(nextTileset.group)
  ;(engineeringRoot ?? nextContentGroup).add(wrapper)

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
      collectPointcloudColorStats(nextTileset.group)
      applyPointcloudDisplay(nextTileset.group)
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
      collectPointcloudColorStats(tileScene)
      applyPointcloudDisplay(tileScene)
    })
    nextTileset.addEventListener('load-error', (event: any) => {
      console.error(event)
    })

    statusText.value = `已加载点云：${props.pointcloudDisplayName || assetDetail.sourceName}`
  } catch (error) {
    console.error(error)
    pointcloudRootReady = false
    if (pointcloudPreprocessRequired.value) {
      pointcloudPreprocessError.value = '当前资产尚无可用于分析的无台面点云，请先补充预处理。'
    }
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

// Watch sources are evaluated during registration, so their computed dependencies
// (including canUseC2MResult) must already be initialized.
watch([rebarDebugActive, rebarDebugBars], () => {
  if (rebarDebugActive.value) {
    if (!rebarDebugBar.value) {
      const requested = rebarDebugBars.value.find(bar => bar.ifcGlobalId === route.query.debugBar)
      selectedComparisonBarId.value = requested?.ifcGlobalId ?? rebarDebugBars.value[0]?.ifcGlobalId ?? ''
      rebarDebugFocusPending = true
    }
  } else selectedComparisonBarId.value = ''
  applySceneVisibility()
})
watch([rebarDebugDisplaySurface, rebarDebugScan, rebarDebugCluster, rebarDebugNormals, rebarDebugNormalMode, rebarDebugAllNormals, rebarDebugScanNormals, rebarDebugLengthMm, rebarDebugLimit], applySceneVisibility)
watch([rebarDebugActive, rebarDebugScan], () => {
  if (activeWorkflowStep.value === 3 && (!rebarDebugActive.value || rebarDebugScan.value)) {
    void showDenoisePreview(denoiseColorMode.value)
  }
})
watch(() => route.query.debug, value => { rebarDebugEnabled.value = value === 'rebar' })
watch(() => route.query.debugBar, value => {
  if (rebarDebugActive.value && typeof value === 'string' && rebarDebugBars.value.some(bar => bar.ifcGlobalId === value)) selectedComparisonBarId.value = value
})

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

watch(pointcloudPointSize, () => {
  if (denoisePreview) denoisePreview.material.size = pointcloudPointSize.value
  applyPointcloudPointSize(pointcloudGroup)
})

watch(showPanel, async () => {
  // v-if removes the panel before the grid transition settles. Resize once
  // after Vue has patched the DOM so the canvas immediately fills the new
  // viewport, while ResizeObserver continues to cover the transition frames.
  await nextTick()
  syncRendererSize()
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

watch(showTransformHandles, syncTransformHandleVisibility)

watch(editMode, () => {
  onEditModeChange()
})

watch(enableElementPicking, () => {
  onElementPickingChange()
})

watch(transformMode, () => {
  setTransformMode(transformMode.value)
})

watch(coarseAlignmentDirty, (dirty) => {
  if (dirty) clearDenoisePreview()
  if (dirty && isC2MResultFresh(c2mResult.value)) {
    invalidateC2MResult('配准变换存在未保存修改，请保存后重新计算')
  }
})

watch([c2mColorRangeMm, c2mToleranceMm, c2mColorMode, c2mBandCount], ([colorRange]) => {
  if (c2mHistogramFollowsColor.value && c2mHistogramRangeMm.value !== colorRange) {
    c2mHistogramRangeMm.value = colorRange
  }
  previewC2MVisualization()
  recolorAnalysisC2MScene()
})

// dev-hong 侧新增 sceneAlignmentReady 守卫（等场景恢复完再跳步）；
// 步骤来源用 props.initialStep，保持包不依赖 vue-router。
watch([() => props.initialStep, workflowRouteReady, bimLoaded, pointcloudLoaded, sceneAlignmentReady], () => {
  if (!workflowRouteReady.value || !bimLoaded.value || !pointcloudLoaded.value) return
  if (hasSavedAlignmentMatrix.value && !sceneAlignmentReady.value) return
  const candidates = allowedWorkflowStepIds.value
  const requested = Number(props.initialStep ?? 1)
  const step: WorkflowStepId = candidates.includes(requested as WorkflowStepId)
    ? (requested as WorkflowStepId)
    : (candidates[0] ?? 1)
  // 目标步骤尚不可用时，退到允许范围内第一个可用步骤（而不是硬编码回第 1 步）。
  const available: WorkflowStepId = workflowStepDisabled(step)
    ? (candidates.filter((id) => !workflowStepDisabled(id))[0] ?? candidates[0] ?? 1)
    : step
  if (activeWorkflowStep.value !== available) openWorkflowStep(available)
  if (step !== available) notifyStepChange(available)
})
watch([activeWorkflowStep, viewportEl], ([step]) => {
  if (workflowRouteReady.value && String(route.query.step || 1) !== String(step)) {
    void router.replace({ query: { ...route.query, step: String(step) } })
  }
  if (step !== 4) {
    void restoreViewportAfterWorkflowStep()
  }
}, { flush: 'post' })

onMounted(async () => {
  window.addEventListener('keydown', onAnalysisKeydown)
  syncPositionStepPreset()
  syncRotationStepPreset()
  await refreshMeshStatus()
  await initScene()
  await preloadFromRoute()
  await Promise.all([loadLatestC2M(), loadLatestDenoise()])
  // Tile loading and GLTF loading finish independently. Make one final
  // restore attempt after both route preload tasks have settled.
  await fetchAndLogSavedAlignmentIfExists()
  workflowRouteReady.value = true
})

onBeforeUnmount(() => {
  disposeReportModelViews()
  reportCanvasRefs.clear()
  resetRebarInspection()
  denoiseRequestId++
  clearDenoisePreview()
  window.removeEventListener('keydown', onAnalysisKeydown)
  bimLoadToken++
  pointcloudLoadToken++
  pointcloudLoadPromise = null
  pointcloudLoadAssetId = null
  clearAnalysis()
  clearMeshStatusPolling()
  clearPointcloudColorSaveTimer()
  clearLoadedRemeshMesh()
  clearC2MScene()
  clearC2MAnalysisPolling()
  c2mDistancesRequestId += 1
  c2mDistances.value = null
  pointcloudRootReady = false
  loggedSavedAlignmentKey = ''
  endClipDrag()
  stopRenderLoop()
  resizeObserver?.disconnect()
  observedViewportEl = null
  controls?.dispose()
  gridHelper?.dispose()
  gridHelper = null
  if (scene && transformHelper) {
    scene.remove(transformHelper)
  }
  if (scene && rotationHelper) {
    scene.remove(rotationHelper)
  }
  transformControls?.dispose()
  rotationControls?.dispose()
  tileset?.dispose?.()
  renderer?.domElement?.removeEventListener?.('pointerdown', handleViewportPointerDown)
  renderer?.domElement?.removeEventListener?.('pointermove', onViewportPointerMove)
  renderer?.domElement?.removeEventListener?.('pointerup', onViewportPointerUp)
  renderer?.domElement?.removeEventListener?.('pointercancel', onViewportPointerCancel)
  renderer?.domElement?.removeEventListener?.('contextmenu', handleViewportContextMenu)
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
  <section class="BimPointcloudAlign-container calibration-page">
    <ViewerAnalysisOverlay
      :mode="analysisMode"
      :point="analysisPoint"
      :distance="analysisDistance"
      :points="analysisPoints"
      :distances="analysisDistances"
      :areas="analysisAreas"
      @clear="clearAllMeasurements"
    />

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
    <header class="topbar calibration-header">
      <div class="topbar-left title-block">
        <el-button text :icon="ArrowLeft" aria-label="返回扫描点云" title="返回扫描点云" @click="closePage" />
        <div class="alignment-title-context">
          <h1 class="brand-title">BIM 与点云校准</h1>
          <span class="alignment-file-context" :title="pointcloudDisplayName">{{ pointcloudDisplayName || '未选择点云' }}</span>
        </div>
      </div>

      <nav class="alignment-workflow-nav" aria-label="BIM 与点云分析流程">
        <div class="alignment-workflow-track">
          <button
            v-for="step in visibleWorkflowSteps"
            :key="step.id"
            type="button"
            class="alignment-workflow-step"
            :class="{
              'is-active': activeWorkflowStep === step.id,
              'is-completed': activeWorkflowStep > step.id,
              'is-disabled': workflowStepDisabled(step.id),
            }"
            :disabled="workflowStepDisabled(step.id)"
            :aria-current="activeWorkflowStep === step.id ? 'step' : undefined"
            :title="workflowStepDisabled(step.id)
              ? step.id >= 3 ? '请先完成当前配准下的点云去噪' : '需先完成并保存点云与工程坐标配准'
              : undefined"
            @click="openWorkflowStep(step.id)"
          >
            <span class="alignment-workflow-step__number">{{ String(step.id).padStart(2, '0') }}</span>
            <span class="alignment-workflow-step__copy">
              <strong>{{ step.title }}</strong>
              <small>{{ step.subtitle }}</small>
            </span>
          </button>
        </div>
      </nav>

    </header>

    <div
      class="main-content calibration-main"
      :class="{
        'is-panel-hidden': !showPanel,
        'is-report-step': activeWorkflowStep === 4,
        'is-report-editor': activeWorkflowStep === 4 && reportEditing,
      }"
    >
      <aside v-if="activeWorkflowStep !== 4" class="left-toolbar view-toolbar" aria-label="视图工具">
        <el-tooltip content="重置视角" placement="right">
          <div class="tool-item">
            <el-button class="tool-btn" circle text :icon="RefreshLeft" aria-label="重置视角" :disabled="!hasModel" @click="resetView" />
          </div>
        </el-tooltip>

        <el-tooltip :content="projectionMode === 'perspective' ? '当前透视 · 切换正交' : '当前正交 · 切换透视'" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--img"
              :class="{ 'is-on': projectionMode === 'orthographic', 'tool-btn--orthographic': projectionMode === 'orthographic' }"
              :aria-label="projectionMode === 'perspective' ? '当前透视，切换正交' : '当前正交，切换透视'"
              :aria-pressed="projectionMode === 'orthographic'"
              circle text
              @click="setProjectionMode(projectionMode === 'perspective' ? 'orthographic' : 'perspective')"
            >
              <img class="tool-btn__img1" :src="projectionMode === 'perspective' ? toushiIcon : zhengjiaoIcon" alt="" />
            </el-button>
          </div>
        </el-tooltip>

        <el-tooltip content="网格" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--img"
              :class="{ 'is-on': showGrid }"
              :aria-pressed="showGrid"
              aria-label="参考网格"
              circle
              text
              @click="showGrid = !showGrid"
            >
              <ViewportToolGlyph name="grid" />
            </el-button>
          </div>
        </el-tooltip>

        <el-tooltip :content="isLightBackground ? '切换夜间背景' : '切换白昼背景'" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn"
              :class="{ 'is-on': isLightBackground }"
              circle
              text
              :icon="isLightBackground ? Moon : Sunny"
              :aria-label="isLightBackground ? '切换夜间背景' : '切换白昼背景'"
              @click="toggleEditorTheme"
            />
          </div>
        </el-tooltip>

        <el-tooltip :content="meshWireframeTooltip" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--svg"
              :class="{ 'is-on': showMeshWireframe }"
              :aria-label="meshWireframeTooltip"
              :aria-pressed="showMeshWireframe"
              circle
              text
              :disabled="!hasModel"
              @click="toggleMeshWireframe"
            >
              <ViewportToolGlyph name="wireframe" />
            </el-button>
          </div>
        </el-tooltip>

        <el-tooltip content="BIM 材质" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--material"
              :class="{ 'is-on': showAdvancedSettings }"
              circle
              text
              :disabled="!hasModel"
              aria-label="BIM 材质"
              :aria-expanded="showAdvancedSettings"
              @click="showAdvancedSettings = !showAdvancedSettings"
            >
              <svg class="tool-btn__svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 3 21 8 12 13 3 8 12 3Z" />
                <path d="m3 12 9 5 9-5" />
                <path d="m3 16 9 5 9-5" />
              </svg>
            </el-button>
          </div>
        </el-tooltip>

        <el-tooltip :content="clipBoundsTooltip" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn tool-btn--svg"
              :class="{
                'is-on': enableClipping && showBounds,
                'is-disabled': !enableClipping && !!clipBoundsDisabledReason,
              }"
              circle
              text
              @click="onClippingButtonClick"
              :aria-label="clipBoundsTooltip"
              :aria-pressed="enableClipping && showBounds"
            >
              <ViewportToolGlyph name="clipping" />
            </el-button>
          </div>
        </el-tooltip>

        <div class="tool-item measurement-tool-item">
            <MeasurementToolbar
            v-model:collapsed="analysisToolbarCollapsed"
            class="alignment-measurement-toolbar"
            :mode="analysisMode"
            :disabled="!hasModel"
            clear-on-toggle-off
            default-mode-on-open="distance"
            toggle-icon="fixed"
            placement="left"
            position="static"
            @update:mode="selectAnalysisMode"
              @clear="clearAllMeasurements"
            />
        </div>

        <el-divider />

        <el-tooltip :content="bimVisibilityLabel" placement="right">
          <div class="tool-item">
            <el-button
              class="tool-btn"
              :class="{ 'is-on': hasModel && bimVisible }"
              circle
              text
              :aria-label="bimVisibilityLabel"
              :aria-pressed="bimVisible"
              :disabled="!hasModel"
              @click="toggleBimVisibility"
            >
              <ViewportToolGlyph name="solidModel" :hidden="!bimVisible" />
            </el-button>
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
              :aria-label="pointcloudVisibilityLabel"
              :aria-pressed="pointcloudVisible"
            >
              <ViewportToolGlyph name="pointCloud" :hidden="!pointcloudVisible" />
            </el-button>
          </div>
        </el-tooltip>

        <el-popover v-model:visible="showPointcloudSettings" placement="right-end" :width="304" trigger="click" :teleported="true">
          <template #reference>
            <div class="tool-item">
              <el-button class="tool-btn tool-btn--svg" circle text :disabled="!hasTileset"
                :class="{ 'is-on': showPointcloudSettings }" aria-label="点云显示（点大小、配色、EDL）"
                title="点云显示（点大小、配色、EDL）" :aria-expanded="showPointcloudSettings">
                <svg class="tool-btn__svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <circle cx="5" cy="12" r="1.5" /><circle cx="11" cy="12" r="2.5" /><circle cx="19" cy="12" r="4" />
                </svg>
              </el-button>
            </div>
          </template>
          <div class="pointcloud-tools-popover" @keydown.esc.stop="showPointcloudSettings = false">
            <strong>点云显示</strong>
            <div class="pointcloud-edl-control">
              <span id="alignment-edl-label">EDL 深度增强</span>
              <el-switch :model-value="edlEnabled" :disabled="!hasTileset || projectionMode === 'orthographic'"
                aria-label="EDL 深度增强" @change="toggleEdl" />
            </div>
            <p v-if="projectionMode === 'orthographic'" class="pointcloud-display-hint">正交视图下暂停 EDL，切回透视后恢复。</p>
            <label class="pointcloud-size-control">
              <span>点大小</span>
              <input v-model.number="pointcloudPointSize" aria-label="点大小" type="range" min="1" max="5" step="0.1" />
              <output>{{ pointcloudPointSize.toFixed(1) }} px</output>
            </label>
            <div v-if="activeWorkflowStep === 1 || (activeWorkflowStep === 2 && denoiseView === 'source')" class="pointcloud-display-row">
              <div class="pointcloud-segmented pointcloud-color-modes" role="group" aria-label="点云着色">
                <button type="button" :class="{ on: pointcloudColorMode === 'rgb' }" :aria-pressed="pointcloudColorMode === 'rgb'" @click="pointcloudColorMode = 'rgb'; applyPointcloudDisplay()">真彩</button>
                <button type="button" :class="{ on: pointcloudColorMode === 'intensity' }" :aria-pressed="pointcloudColorMode === 'intensity'" @click="pointcloudColorMode = 'intensity'; applyPointcloudDisplay()">强度</button>
              </div>
              <div v-if="pointcloudColorMode === 'intensity'" class="pointcloud-segmented pointcloud-ramp-modes" role="group" aria-label="强度色带">
                <button type="button" :disabled="pointcloudColorMode !== 'intensity'" :class="{ on: pointcloudColorRamp === 'grayscale' }" :aria-pressed="pointcloudColorRamp === 'grayscale'" @click="pointcloudColorRamp = 'grayscale'; applyPointcloudDisplay()">灰度</button>
                <button type="button" :disabled="pointcloudColorMode !== 'intensity'" :class="{ on: pointcloudColorRamp === 'spectrum' }" :aria-pressed="pointcloudColorRamp === 'spectrum'" @click="pointcloudColorRamp = 'spectrum'; applyPointcloudDisplay()">彩虹</button>
                <button type="button" :disabled="pointcloudColorMode !== 'intensity'" :class="{ on: pointcloudColorRamp === 'viridis' }" :aria-pressed="pointcloudColorRamp === 'viridis'" @click="pointcloudColorRamp = 'viridis'; applyPointcloudDisplay()">紫黄</button>
              </div>
            </div>
          </div>
        </el-popover>

      </aside>

      <div v-if="activeWorkflowStep !== 4 && showAdvancedSettings" class="left-material-popover" role="menu" aria-label="BIM 材质模式">
        <button type="button" :class="{ 'is-active': materialMode === 'original' }" role="menuitemradio" :aria-checked="materialMode === 'original'" @click="materialMode = 'original'; showAdvancedSettings = false">原始材质</button>
        <button type="button" :class="{ 'is-active': materialMode === 'unlit' }" role="menuitemradio" :aria-checked="materialMode === 'unlit'" @click="materialMode = 'unlit'; showAdvancedSettings = false">无光照</button>
        <button type="button" :class="{ 'is-active': materialMode === 'lambert' }" role="menuitemradio" :aria-checked="materialMode === 'lambert'" @click="materialMode = 'lambert'; showAdvancedSettings = false">漫反射</button>
      </div>

      <div v-show="activeWorkflowStep !== 4" ref="viewportEl" class="viewport viewport-shell three-view-pane">
        <PointcloudViewCube
          :pose="pointcloudCameraPose"
          @home="resetView"
          @select-direction="setPointcloudViewDirection"
          @orbit="orbitPointcloudFromCube"
          @roll="rollPointcloudView"
        />
        <PointcloudAxesTriad
          v-show="pointcloudShowAxes"
          class="alignment-pointcloud-axes-triad"
          :pose="pointcloudCameraPose"
        />
        <PointcloudColorRangeBar
          v-if="pointcloudColorMode === 'intensity' && (activeWorkflowStep === 1 || (activeWorkflowStep === 2 && denoiseView === 'source'))"
          v-model:range="pointcloudColorRange"
          class="pointcloud-bottom-color-bar alignment-pointcloud-bottom-color-bar"
          :ramp="pointcloudColorRamp"
          :histogram="pointcloudIntensityHistogram"
          @update:range="updatePointcloudColorRange"
        />
      </div>

      <button
        v-if="activeWorkflowStep !== 4"
        type="button"
        class="right-panel-toggle"
        aria-controls="alignment-control-panel"
        :aria-expanded="showPanel"
        :aria-label="showPanel ? '收起控制面板' : '展开控制面板'"
        :title="showPanel ? '收起控制面板' : '展开控制面板'"
        @click="showPanel = !showPanel"
      >
        <el-icon :size="16"><component :is="showPanel ? DArrowRight : DArrowLeft" /></el-icon>
      </button>

      <section v-if="activeWorkflowStep === 4" ref="reportWorkspaceEl" class="report-preview-workspace" :class="{ 'is-report-fullscreen': reportFullscreen }" aria-label="报告预览">
        <div v-if="!reportToolbarCollapsed" class="report-reader-toolbar" aria-label="报告预览工具栏">
          <button type="button" title="缩小" aria-label="缩小" @click="changeReportZoom(-10)"><el-icon><ZoomOut /></el-icon></button>
          <strong>{{ reportZoom }}%</strong>
          <button type="button" title="放大" aria-label="放大" @click="changeReportZoom(10)"><el-icon><ZoomIn /></el-icon></button>
          <span class="report-reader-format" title="当前导出格式">{{ reportFormat === 'xls' ? 'CSV' : reportFormat.toUpperCase() }}</span>
          <button type="button" title="适应页面" aria-label="适应页面" @click="fitReportPage"><el-icon><View /></el-icon></button>
          <span class="report-reader-divider"></span>
          <button type="button" :aria-label="reportEditing ? '退出编辑模式' : '编辑报告内容'" class="editing-toggle" :class="{ 'is-exit': reportEditing }" :title="reportEditing ? '退出编辑模式' : '编辑报告内容'" @click="reportEditing ? leaveReportEditor() : enterReportEditor()"><el-icon><Close v-if="reportEditing" /><EditPen v-else /></el-icon></button>
          <button v-if="reportEditing" type="button" title="完成本次编辑（仅当前页面）" aria-label="完成本次编辑" @click="saveReportEdits"><el-icon><Check /></el-icon></button>
          <button type="button" :title="reportFullscreen ? '退出全屏预览' : '全屏预览'" :aria-label="reportFullscreen ? '退出全屏预览' : '全屏预览'" :aria-pressed="reportFullscreen" :class="{ active: reportFullscreen }" @click="toggleReportFullscreen"><el-icon><FullScreen /></el-icon></button>
          <button type="button" title="下载报告" aria-label="下载报告" :disabled="!canUseC2MResult" @click="reportAction('export')"><el-icon><Download /></el-icon></button>
          <button type="button" title="收起工具栏" aria-label="收起工具栏" @click="reportToolbarCollapsed = true"><el-icon><ArrowUp /></el-icon></button>
        </div>
        <button v-else type="button" class="report-reader-toolbar-reopen" title="展开报告工具栏" aria-label="展开报告工具栏" @click="reportToolbarCollapsed = false"><el-icon><ArrowDown /></el-icon></button>
        <div class="report-paper-stage" :style="{ width: `${794 * reportZoom / 100}px`, height: `${1123 * reportZoom / 100}px` }">
          <div class="report-preview-page cover-paper" :style="{ transform: `translateX(-50%) scale(${reportZoom / 100})` }">
            <header class="cover-header">
              <div class="cover-brand"><div class="report-preview-mark"><img :src="reportBrandMark" alt="系统标识" /></div><div><span>点云与工程坐标配准</span><strong>BIM 与点云校准系统</strong></div></div>
              <div class="cover-report-number"><small>报告编号</small><strong>REPORT / 001</strong></div>
            </header>
            <div class="cover-main"><h1 :contenteditable="reportEditing" @blur="updateReportField('title', $event)">{{ reportTitle }}</h1><p>Scan vs BIM Deviation Report</p><i aria-hidden="true"></i></div>
            <dl class="cover-details"><div><dt>项目名称</dt><dd :contenteditable="reportEditing" @blur="updateReportField('project', $event)">{{ reportProjectName }}</dd></div><div><dt>扫描点云文件</dt><dd>{{ pointcloudDisplayName || '未选择' }}</dd></div><div><dt>检测单位</dt><dd :contenteditable="reportEditing" @blur="updateReportField('organization', $event)">{{ reportOrganization }}</dd></div><div><dt>检测人员</dt><dd :contenteditable="reportEditing" @blur="updateReportField('inspectors', $event)">{{ reportInspectors }}</dd></div><div><dt>审核人员</dt><dd :contenteditable="reportEditing" @blur="updateReportField('reviewer', $event)">{{ reportReviewer }}</dd></div><div><dt>生成日期</dt><dd :contenteditable="reportEditing" @blur="updateReportField('date', $event)">{{ reportDate }}</dd></div></dl>
            <div class="cover-status"><span></span><div><small>当前检测状态</small><strong>{{ canUseC2MResult ? '逐钢筋偏差结果已生成' : '待生成有效偏差结果' }}</strong></div></div>
            <div class="cover-footer"><span>BIM 与点云校准</span><span>第 01 页</span></div>
          </div>
        </div>
        <div v-if="canUseC2MResult" class="report-paper-stage" :style="{ width: `${794 * reportZoom / 100}px`, height: `${1123 * reportZoom / 100}px` }">
          <article class="report-preview-page rebar-report-page report-summary-page" :style="{ transform: `translateX(-50%) scale(${reportZoom / 100})` }">
            <section v-for="item in reportContents.filter(entry => entry.enabled)" :key="item.id" class="report-data-section">
              <h2>{{ item.title }}</h2>
              <template v-if="item.id === 'summary'">
                <p>{{ reportProjectName }} · {{ pointcloudDisplayName }}</p>
                <p>共 {{ comparisonBars.length }} 根设计钢筋，工程容差 ±{{ comparisonReportToleranceMm }} mm。测量方式：{{ comparisonMeasurementLabel }}。</p>
              </template>
              <dl v-else-if="item.id === 'statistics'" class="report-statistics">
                <div><dt>平均绝对偏差</dt><dd>{{ ((c2mResult?.stats?.meanAbs ?? 0) * 1000).toFixed(2) }} mm</dd></div>
                <div><dt>RMSE</dt><dd>{{ ((c2mResult?.stats?.rmse ?? 0) * 1000).toFixed(2) }} mm</dd></div>
                <div><dt>P95 绝对偏差</dt><dd>{{ ((c2mResult?.stats?.p95Abs ?? 0) * 1000).toFixed(2) }} mm</dd></div>
                <div><dt>容差内（已覆盖）</dt><dd>{{ formatC2MPercentage(c2mDisplayResult?.stats?.withinToleranceRatio) }}</dd></div>
              </dl>
              <C2MHistogramLegend v-else-if="item.id === 'histogram' && c2mDisplayResult" :result="c2mDisplayResult" :color-mode="c2mColorMode" :band-count="c2mBandCount" />
              <p v-else-if="item.id === 'conclusion'">本报告提供实例约束的偏差预估。缺测与待复核钢筋不出具偏差结论，请结合逐筋明细复核；容差内比例仅统计已覆盖顶点。</p>
            </section>
            <footer>第 2 页 · 分析摘要</footer>
          </article>
        </div>
        <div v-for="(bars, page) in comparisonReportPages" :key="page" class="report-paper-stage" :style="{ width: `${794 * reportZoom / 100}px`, height: `${1123 * reportZoom / 100}px` }">
          <article class="report-preview-page rebar-report-page" :style="{ transform: `translateX(-50%) scale(${reportZoom / 100})` }">
            <h2>逐钢筋偏差明细</h2>
            <p>{{ reportProjectName }} · 容差 ±{{ comparisonReportToleranceMm }} mm · 已排除设计夹具</p>
            <p>{{ comparisonMeasurementLabel }}。覆盖率与容差内比例分列；缺测、待复核项不出具偏差结论。</p>
            <table class="rebar-report-table">
              <colgroup><col class="rebar-report-table__member" /><col class="rebar-report-table__status" /><col class="rebar-report-table__coverage" /><col class="rebar-report-table__metric" /><col class="rebar-report-table__metric" /><col class="rebar-report-table__metric" /><col class="rebar-report-table__tolerance" /></colgroup>
              <thead><tr><th scope="col">钢筋信息</th><th scope="col">状态 / 点数</th><th scope="col">覆盖率</th><th scope="col">平均绝对偏差<br /><small>mm</small></th><th scope="col">RMSE<br /><small>mm</small></th><th scope="col">P95 绝对偏差<br /><small>mm</small></th><th scope="col">容差内<br /><small>已覆盖</small></th></tr></thead>
              <tbody><tr v-for="bar in bars" :key="bar.ifcGlobalId">
                <td><strong>{{ bar.name || bar.designBarId }}</strong><small>{{ bar.ifcGlobalId }}</small><small>实例 {{ formatReportInstanceIds(bar.instanceIds) }}</small></td>
                <td><span class="rebar-report-status" :class="`is-${bar.status}`">{{ rebarStatusLabel(bar.status) }}</span><small>{{ bar.pointCount.toLocaleString() }} 点</small></td>
                <td class="rebar-report-table__number">{{ formatC2MPercentage(bar.vertexCount ? bar.knownCount / bar.vertexCount : undefined) }}</td>
                <td class="rebar-report-table__number">{{ bar.stats?.meanAbs === undefined ? '--' : (bar.stats.meanAbs * 1000).toFixed(2) }}</td>
                <td class="rebar-report-table__number">{{ bar.stats?.rmse === undefined ? '--' : (bar.stats.rmse * 1000).toFixed(2) }}</td>
                <td class="rebar-report-table__number">{{ bar.stats?.p95Abs === undefined ? '--' : (bar.stats.p95Abs * 1000).toFixed(2) }}</td>
                <td class="rebar-report-table__number">{{ formatC2MPercentage(bar.stats?.withinToleranceRatio) }}</td>
              </tr></tbody>
            </table>
            <p class="rebar-report-provenance">结果版本：{{ c2mResult?.resultVersion }}<br />实例映射：{{ comparison?.instanceMapHash }}</p>
            <footer>第 {{ page + 3 }} 页 · 钢筋 {{ page * 14 + 1 }}–{{ page * 14 + bars.length }} / {{ comparisonBars.length }}</footer>
          </article>
        </div>
        <div v-for="(bars, page) in comparisonDetailPages" :key="`detail-${page}`" class="report-paper-stage" :style="{ width: `${794 * reportZoom / 100}px`, height: `${1123 * reportZoom / 100}px` }">
          <article class="report-preview-page rebar-report-page rebar-projection-page" :style="{ transform: `translateX(-50%) scale(${reportZoom / 100})` }">
            <h2>逐钢筋投影与偏差</h2>
            <p>{{ reportProjectName }} · {{ comparisonMeasurementLabel }} · 图形等比例，尺寸单位 mm</p>
            <div class="rebar-projection-page__bars">
              <RebarDeviationDetail v-for="bar in bars" :key="bar.ifcGlobalId" :bar="bar" report />
            </div>
            <p>灰色虚线：设计中心线；蓝色实线及圆点：实测拟合中心线。断开处为缺测，最大值仅针对已测范围。弯曲为各直线段扣除整体偏移与倾斜后的残余弓高估计，覆盖不足不出具结论。</p>
            <p class="rebar-report-provenance">结果版本：{{ c2mResult?.resultVersion }}</p>
            <footer>第 {{ comparisonReportPages.length + page + 3 }} 页 · 钢筋 {{ page * reportBarsPerPage + 1 }}–{{ page * reportBarsPerPage + bars.length }} / {{ comparisonBars.length }}</footer>
          </article>
        </div>
      </section>

      <aside v-if="(showPanel && activeWorkflowStep !== 4) || (activeWorkflowStep === 4 && !reportEditing)" id="alignment-control-panel" class="right-panel control-panel is-workflow-panel">
        <div class="control-panel-header">
          <div class="panel-heading">
            <strong>{{ activeWorkflowStep === 2 ? '点云分类与去噪' : activeWorkflowStep === 3 ? '偏差对比' : activeWorkflowStep === 4 ? '出报告' : '配准控制' }}</strong>
          </div>
          <div class="panel-step-actions">
            <button v-if="activeWorkflowStep > 1" class="panel-step-count panel-next-step panel-prev-step" type="button"
              :disabled="workflowStepDisabled((activeWorkflowStep - 1) as WorkflowStepId)"
              @click="openWorkflowStep((activeWorkflowStep - 1) as WorkflowStepId)">
              <el-icon aria-hidden="true"><DArrowLeft /></el-icon>上一步
            </button>
            <button v-if="activeWorkflowStep < 4" class="panel-step-count panel-next-step" type="button"
              :disabled="workflowStepDisabled((activeWorkflowStep + 1) as WorkflowStepId)"
              @click="openWorkflowStep((activeWorkflowStep + 1) as WorkflowStepId)">
              下一步<el-icon aria-hidden="true"><DArrowRight /></el-icon>
            </button>
          </div>
        </div>
        <div class="panel-body">
          <p v-if="activeWorkflowStep === 1" class="workflow-guidance">
            调整模型位置并保存粗配准，再进行精细配准。完成校准后可进入点云分类与去噪。
          </p>
          <div v-if="pointcloudPreprocessRequired" class="panel-section denoise-panel">
            <div class="section-heading">
              <h4>分析点云未就绪</h4>
              <span class="stage-state">需预处理</span>
            </div>
            <p class="denoise-note">分析仅使用上传阶段生成的无台面点云。历史资产可在此补算法向量并识别、移除台面。</p>
            <p v-if="pointcloudPreprocessResult" class="denoise-note">预处理记录包含 {{ pointcloudPreprocessResult.result.pointsAfter.toLocaleString() }} 个保留点，正在等待无台面切片就绪。</p>
            <el-button type="primary" :loading="pointcloudPreprocessRunning" @click="runPointcloudPreprocess">
              补充预处理
            </el-button>
            <el-alert v-if="pointcloudPreprocessError" :title="pointcloudPreprocessError" type="warning" :closable="false" show-icon />
          </div>
          <DenoisePanel
            v-if="activeWorkflowStep === 2"
            :result="denoiseResult"
            :running="denoiseRunning"
            :loading="denoiseLoading"
            :error="denoiseError"
            :can-run="canOpenDenoiseStep"
            :can-inspect="canOpenDeviationStep"
            :view="denoiseView"
            :color-mode="denoiseColorMode"
            :preview-loading="denoisePreviewLoading"
            :visible-classes="denoiseVisibleClasses"
            :visible-point-count="denoiseVisiblePointCount"
            @run="runDenoise"
            @preview="showDenoisePreview"
            @visibility="setDenoiseVisibleClasses"
            @download="downloadDenoise"
          />
          <div v-if="activeWorkflowStep === 4" class="panel-section report-config-panel">
            <div class="section-heading report-section-heading">
              <div><h2>报告配置</h2><span class="stage-state ready">{{ reportEnabledCount }} 项</span></div>
              <span class="stage-state ready">待发布</span>
            </div>
            <div class="report-flow"><span class="done">配置</span><i></i><span class="done">预览</span><i></i><span>导出</span></div>
            <div class="workflow-form">
              <label class="field-block"><span>报告名称</span><el-input v-model="reportTitle" maxlength="40" /></label>
              <div class="field-block"><span>每页钢筋投影</span><el-radio-group v-model="reportBarsPerPage" aria-label="每页显示的钢筋数量"><el-radio-button :value="1">1 根</el-radio-button><el-radio-button :value="2">2 根</el-radio-button><el-radio-button :value="3">3 根</el-radio-button></el-radio-group></div>
              <div class="field-block"><span>输出格式</span><el-radio-group v-model="reportFormat" class="compact-segment"><el-radio-button value="pdf">PDF</el-radio-button><el-radio-button value="json">检测 JSON</el-radio-button><el-radio-button value="docx" disabled title="Word 导出尚未开放">Word</el-radio-button><el-radio-button value="xls">CSV · Excel</el-radio-button><el-radio-button value="dxf" disabled title="DXF 导出尚未开放">DXF</el-radio-button></el-radio-group></div>
            </div>
            <div class="content-config-heading"><div><strong>报告内容配置</strong><span>{{ reportContents.length }} 项 · {{ reportEnabledCount }} 项显示</span></div><button type="button" class="reset-content-button" title="全部开启" aria-label="全部开启报告章节" @click="reportContents.forEach((item) => item.enabled = true)"><el-icon><RefreshLeft /></el-icon></button></div>
            <div class="dynamic-content-list">
              <section v-for="group in [...new Set(reportContents.map((item) => item.group))]" :key="group">
                <header v-if="group !== '基础信息'"><strong>{{ group }}</strong><span>{{ reportContents.filter((item) => item.group === group).length }}</span></header>
                <article v-for="item in reportContents.filter((entry) => entry.group === group)" :key="item.id" :class="{ disabled: !item.enabled }">
                  <el-switch v-model="item.enabled" :disabled="item.locked" :aria-label="`显示或隐藏${item.title}`" />
                  <el-input v-model="item.title" :disabled="item.locked" :aria-label="`${item.title}章节名称`" maxlength="40" />
                  <div class="content-actions"><button type="button" :title="`删除${item.title}章节`" :aria-label="`删除${item.title}章节`" :disabled="item.locked" @click="reportContents = reportContents.filter((entry) => entry.id !== item.id)"><el-icon><Delete /></el-icon></button></div>
                  <small v-if="item.locked">模板固定内容</small>
                </article>
              </section>
            </div>
            <div class="report-source-status"><div><small>偏差项</small><strong>{{ c2mResult?.stats ? '已计算' : '待计算' }}</strong></div><div><small>已显示</small><strong>{{ reportEnabledCount }}</strong></div><div><small>状态</small><strong class="is-alert">草稿</strong></div></div>
            <el-alert v-if="!canUseC2MResult" class="report-alert" type="info" :closable="false" show-icon title="请先完成有效的逐钢筋对比，再导出报告" />
            <div class="report-mode-label">预览与编辑</div>
            <div class="panel-action-row report-actions"><el-button :icon="View" @click="fitReportPage">适应页面</el-button><el-button :icon="EditPen" :disabled="!canUseC2MResult" @click="enterReportEditor">编辑预览</el-button><el-button :icon="Check" disabled title="在线草稿保存尚未开放">保存草稿</el-button><el-button :icon="DocumentChecked" disabled title="正式归档尚未开放">发布终稿</el-button></div>
            <p class="report-local-note">编辑仅保留在当前页面。在线保存与发布尚未开放，请导出到本地。</p>
            <div class="report-mode-label">浏览器本地导出</div>
            <div class="panel-action-row report-actions"><el-button type="primary" :icon="Download" :disabled="!canUseC2MResult" :loading="inspectionDownloading" @click="reportAction('export')">{{ reportFormat === 'json' ? '导出已保存检测 JSON' : reportFormat === 'xls' ? '导出逐筋明细 CSV' : '打印 / 导出 PDF' }}</el-button></div>
          </div>
         <div v-if="activeWorkflowStep === 1" class="panel-section registration-edit-panel">
          <div class="registration-stage-row" role="group" aria-label="配准阶段">
            <button class="registration-stage-btn" :class="{ 'is-active': registrationStage === 'coarse' }" :aria-pressed="registrationStage === 'coarse'" :disabled="!hasModel" @click="activateCoarseRegistration">粗配准</button>
            <button class="registration-stage-btn" :class="{ 'is-active': registrationStage === 'fine' }" :aria-pressed="registrationStage === 'fine'" :disabled="!hasSavedAlignmentMatrix" :title="!hasSavedAlignmentMatrix ? '请先保存粗配准' : undefined" @click="activateFineRegistration">精细配准</button>
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

          <div class="registration-handle-control">
            <label for="registration-handles">操作手柄</label>
            <el-switch id="registration-handles" v-model="showTransformHandles"
              :disabled="!hasModel || !editMode || fineAlignLoading || savingCalibration"
              aria-label="显示配准操作手柄" />
          </div>
          <p class="registration-handle-hint">{{ enableClipping ? '剖切时暂时隐藏配准手柄。' : '控制画布中的平移与旋转手柄；关闭后仍可输入数值调整。' }}</p>
          <div class="transform-mode" role="tablist" aria-label="变换方式">
            <button type="button" class="transform-mode-button" :class="{ 'is-active': transformMode === 'translate' }" role="tab" :aria-selected="transformMode === 'translate'" :disabled="!editMode || !hasModel" @click="setTransformMode('translate')">移动</button>
            <button type="button" class="transform-mode-button" :class="{ 'is-active': transformMode === 'rotate' }" role="tab" :aria-selected="transformMode === 'rotate'" :disabled="!editMode || !hasModel" @click="setTransformMode('rotate')">旋转</button>
          </div>

          <div
            class="control-row control-row--orientation"
            :class="{ disabled: !editMode || !selectedItemId }"
          >
            <div v-if="transformMode === 'rotate'" class="orientation-sliders">
              <div
                class="control-row control-row--compact"
                :class="{ disabled: !editMode || !selectedItemId }"
              >
                <div class="step-control-group">
                  <span class="step-control-label">步长</span>
                  <div class="step-control-fields">
                    <el-select
                      v-model="rotationStepPreset"
                      class="step-select"
                      size="small"
                      popper-class="bpa-right-popper"
                      placement="bottom-start"
                      :fallback-placements="[]"
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
                    <el-input-number
                      :model-value="rotationAdjustStep"
                      class="step-input-number"
                      size="small"
                      :min="0.01"
                      :max="45"
                      :step="0.01"
                      :precision="3"
                      controls-position="right"
                      :disabled="!editMode || !selectedItemId"
                      @update:model-value="onRotationStepPresetChange(String($event ?? 1))"
                    />
                  </div>
                </div>
              </div>
              <template v-if="showOnlyVerticalAxis">
                <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                  <span class="axis axis--rotation">Z</span>
                  <input
                    v-model.number="orientationDegY"
                    type="range"
                    min="-180"
                    max="180"
                    :step="rotationAdjustStep"
                    :disabled="!editMode || !selectedItemId"
                    @input="applyOrientationFixRealtime"
                  />
                  <div class="slider__controls slider__controls--rotation">
                    <input class="axis-number-input axis-number-input--rotation" aria-label="Y 轴旋转，单位度" :value="formatRotationOffset(orientationDegY)" type="number" inputmode="decimal" min="-180" max="180" :step="rotationAdjustStep" :disabled="!editMode || !selectedItemId" @input="onOrientationNumberInput('y', $event)" @blur="onOrientationNumberBlur('y', $event)" @keydown="onOrientationNumberKeydown($event, 'y')" />
                    <span class="slider__hint">deg</span>
                  </div>
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
                  <div class="slider__controls slider__controls--rotation">
                    <input class="axis-number-input axis-number-input--rotation" aria-label="X 轴旋转，单位度" :value="formatRotationOffset(orientationDegX)" type="number" inputmode="decimal" min="-10" max="10" :step="rotationAdjustStep" :disabled="!editMode || !selectedItemId" @input="onOrientationNumberInput('x', $event)" @blur="onOrientationNumberBlur('x', $event)" @keydown="onOrientationNumberKeydown($event, 'x')" />
                    <span class="slider__hint">deg</span>
                  </div>
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
                  <div class="slider__controls slider__controls--rotation">
                    <input class="axis-number-input axis-number-input--rotation" aria-label="Y 轴旋转，单位度" :value="formatRotationOffset(orientationDegY)" type="number" inputmode="decimal" min="-10" max="10" :step="rotationAdjustStep" :disabled="!editMode || !selectedItemId" @input="onOrientationNumberInput('y', $event)" @blur="onOrientationNumberBlur('y', $event)" @keydown="onOrientationNumberKeydown($event, 'y')" />
                    <span class="slider__hint">deg</span>
                  </div>
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
                  <div class="slider__controls slider__controls--rotation">
                    <input class="axis-number-input axis-number-input--rotation" aria-label="Z 轴旋转，单位度" :value="formatRotationOffset(orientationDegZ)" type="number" inputmode="decimal" min="-10" max="10" :step="rotationAdjustStep" :disabled="!editMode || !selectedItemId" @input="onOrientationNumberInput('z', $event)" @blur="onOrientationNumberBlur('z', $event)" @keydown="onOrientationNumberKeydown($event, 'z')" />
                    <span class="slider__hint">deg</span>
                  </div>
                </label>
              </template>
            </div>
            <div v-else class="orientation-sliders">
              <div
                class="control-row control-row--compact"
                :class="{ disabled: !editMode || !selectedItemId }"
              >
                <div class="step-control-group">
                  <span class="step-control-label">步长</span>
                  <div class="step-control-fields">
                    <el-select
                      v-model="positionStepPreset"
                      class="step-select"
                      size="small"
                      popper-class="bpa-right-popper"
                      placement="bottom-start"
                      :fallback-placements="[]"
                      filterable
                      allow-create
                      default-first-option
                      :disabled="!editMode || !selectedItemId"
                      @change="onPositionStepPresetChange"
                    >
                      <el-option
                        v-for="stepOption in positionStepOptions"
                        :key="`position-${stepOption}`"
                        :label="formatPositionStepLabel(stepOption)"
                        :value="String(stepOption)"
                      />
                    </el-select>
                    <el-input-number
                      :model-value="positionAdjustStep"
                      class="step-input-number"
                      size="small"
                      :min="0.001"
                      :max="10"
                      :step="0.001"
                      :precision="3"
                      controls-position="right"
                      :disabled="!editMode || !selectedItemId"
                      @update:model-value="onPositionStepPresetChange(String($event ?? 0.01))"
                    />
                  </div>
                </div>
              </div>
              <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                <span class="axis">X</span>
                <input
                  v-model.number="positionOffsetX"
                  type="range"
                  :min="positionSliderRange.min"
                  :max="positionSliderRange.max"
                  :step="positionAdjustStep"
                  :disabled="!editMode || !selectedItemId"
                  @input="applyPositionFixRealtime"
                />
                <div class="slider__controls">
                  <input class="axis-number-input" aria-label="X 轴位移，单位米" :value="positionOffsetX" type="number" inputmode="decimal" :step="positionAdjustStep" :disabled="!editMode || !selectedItemId" @input="onPositionNumberInput('x', $event)" @blur="onPositionNumberBlur('x', $event)" @keydown="onPositionNumberKeydown($event, 'x')" />
                  <span class="slider__hint">m</span>
                </div>
              </label>
              <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                <span class="axis">Y</span>
                <input
                  v-model.number="positionOffsetY"
                  type="range"
                  :min="positionSliderRange.min"
                  :max="positionSliderRange.max"
                  :step="positionAdjustStep"
                  :disabled="!editMode || !selectedItemId"
                  @input="applyPositionFixRealtime"
                />
                <div class="slider__controls">
                  <input class="axis-number-input" aria-label="Y 轴位移，单位米" :value="positionOffsetY" type="number" inputmode="decimal" :step="positionAdjustStep" :disabled="!editMode || !selectedItemId" @input="onPositionNumberInput('y', $event)" @blur="onPositionNumberBlur('y', $event)" @keydown="onPositionNumberKeydown($event, 'y')" />
                  <span class="slider__hint">m</span>
                </div>
              </label>
              <label class="slider" :class="{ disabled: !editMode || !selectedItemId }">
                <span class="axis">Z</span>
                <input
                  v-model.number="positionOffsetZ"
                  type="range"
                  :min="positionSliderRange.min"
                  :max="positionSliderRange.max"
                  :step="positionAdjustStep"
                  :disabled="!editMode || !selectedItemId"
                  @input="applyPositionFixRealtime"
                />
                <div class="slider__controls">
                  <input class="axis-number-input" aria-label="Z 轴位移，单位米" :value="positionOffsetZ" type="number" inputmode="decimal" :step="positionAdjustStep" :disabled="!editMode || !selectedItemId" @input="onPositionNumberInput('z', $event)" @blur="onPositionNumberBlur('z', $event)" @keydown="onPositionNumberKeydown($event, 'z')" />
                  <span class="slider__hint">m</span>
                </div>
              </label>
            </div>
          </div>
          <div v-if="registrationStage === 'coarse'" class="registration-footer-actions">
            <el-button size="large" :disabled="!editMode || !selectedItemId" @click="resetTransformFixRealtime">重置变换</el-button>
            <el-button
              type="primary"
              size="large"
              title="保存当前粗配准矩阵并继续当前流程"
              :loading="savingCalibration"
              :disabled="!canSaveCoarseAlignment"
              @click="saveCoarseAlignmentMatrix"
            >
              保存粗配准
            </el-button>
          </div>
          <el-button
            class="registration-complete-button"
            :loading="savingCalibration"
            :disabled="!canSaveCalibration"
            title="保存当前配准结果"
            @click="handleCalibrationComplete"
          >
            完成校准
          </el-button>
        </div>
        <div v-if="activeWorkflowStep === 3" class="panel-section c2m-panel c2m-deviation-workspace">
          <RebarDebugPanel
            :bars="rebarDebugBars" :selected-id="selectedComparisonBarId" :active="rebarDebugActive"
            :surface="rebarDebugDisplaySurface" :scan="rebarDebugScan" :cluster="rebarDebugCluster"
            :normals="rebarDebugNormals" :scan-normals="rebarDebugScanNormals"
            :normal-mode="rebarDebugNormalMode" :all-normals="rebarDebugAllNormals" :mesh-counts="rebarDebugMeshCounts"
            :length-mm="rebarDebugLengthMm" :limit="rebarDebugLimit"
            :point-count="denoiseVisiblePointCount" :normal-count="rebarDebugNormalCount" :scan-normal-count="rebarDebugScanNormalCount"
            :has-result="Boolean(comparison)" :result-version="c2mResult?.resultVersion"
            :effective="comparison?.effective" :algorithm="c2mResult?.algorithmVersion"
            :loading="denoisePreviewLoading || c2mSceneLoading"
            :scene-loaded="c2mSceneLoaded" :scene-error="c2mError"
            @toggle="toggleRebarDebug" @select="selectRebarDebugBar" @move="moveRebarDebugBar" @focus="focusRebarDebug"
            @open="openRebarDebugWindow" @export="exportRebarDebug"
            @result="showRebarDebugResult" @pair="showRebarDebugPair"
            @surface="rebarDebugSurface = $event" @scan="rebarDebugScan = $event" @cluster="rebarDebugCluster = $event"
            @normals="rebarDebugNormals = $event" @scan-normals="rebarDebugScanNormals = $event"
            @normal-mode="rebarDebugNormalMode = $event"
            @all-normals="rebarDebugAllNormals = $event"
            @length="rebarDebugLengthMm = $event" @limit="rebarDebugLimit = $event"
          />
          <section class="c2m-primary-card" aria-label="偏差计算">
            <div class="c2m-compute-heading">
              <div class="section-title c2m-panel__title">偏差计算</div>
              <label class="c2m-normal-toggle">
                <span>法向约束 · 同侧表面</span>
                <el-switch v-model="c2mNormalConstraintEnabled" :disabled="c2mRunning" aria-label="启用钢筋同侧表面的法向约束" />
              </label>
            </div>
            <div class="c2m-search-fields" :class="{ 'is-single': !c2mNormalConstraintEnabled }">
              <label v-if="c2mNormalConstraintEnabled" class="c2m-search-field">
                <span>朝向容差 <small>°</small></span>
                <el-input-number v-model="c2mNormalMaxAngleDeg" controls-position="right" :min="1" :max="90" :step="5" :precision="0" :value-on-clear="30" :disabled="c2mRunning" aria-label="同侧表面朝向容差，单位度" />
              </label>
              <label class="c2m-search-field">
                <span>最大搜索距离 <small>mm</small></span>
                <el-input-number v-model="c2mMaxSearchDistanceMm" controls-position="right" :min="0.1" :max="200" :step="1" :precision="1" :value-on-clear="200" :disabled="c2mRunning" aria-label="最大搜索距离，单位毫米" />
              </label>
            </div>
            <p class="c2m-search-hint">{{ c2mNormalConstraintEnabled ? '沿钢筋中心线辨别同侧方向；无可靠扫描支持的位置保留缺测' : '搜索范围限定在同一钢筋实例内' }}</p>
            <el-button class="c2m-run-button" type="primary" :loading="c2mRunning" :disabled="!canRunC2M" @click="runC2M">
              {{ c2mResult ? '重新计算偏差' : '计算偏差' }}
            </el-button>
          </section>

          <section v-if="comparison" class="rebar-comparison-card" aria-label="逐钢筋比对">
            <strong>逐钢筋比对 · {{ comparisonBars.length }} 根</strong>
            <p>已排除 {{ comparison.excludedComponentCount }} 个非钢筋构件；{{ comparison.unassignedPointCount.toLocaleString() }} 个点未建立可靠对应。</p>
            <div class="rebar-inspection" aria-label="逐钢筋手动巡视">
              <div class="rebar-inspection__header">
                <div>
                  <strong>巡检模式</strong>
                  <span>{{ rebarInspectionIndex >= 0 ? rebarInspectionIndex + 1 : 0 }} / {{ rebarInspectionBars.length }}</span>
                </div>
                <el-switch
                  v-model="rebarInspectionAbnormalOnly"
                  size="small"
                  active-text="仅异常"
                  aria-label="仅巡检异常钢筋"
                  @change="onInspectionFilterChange"
                />
              </div>
              <el-progress
                :percentage="rebarInspectionBars.length && rebarInspectionIndex >= 0 ? ((rebarInspectionIndex + 1) / rebarInspectionBars.length) * 100 : 0"
                :show-text="false"
                :stroke-width="4"
              />
              <div class="rebar-inspection__controls">
                <el-button
                  aria-label="上一根钢筋"
                  title="上一根钢筋"
                  :icon="ArrowLeft"
                  :disabled="!rebarInspectionBars.length || rebarInspectionIndex <= 0"
                  @click="stepRebarInspection(-1)"
                />
                <el-button
                  class="rebar-inspection__manual-select"
                  :type="rebarInspectionManualSelect ? 'primary' : 'default'"
                  :plain="!rebarInspectionManualSelect"
                  :aria-pressed="rebarInspectionManualSelect"
                  :title="rebarInspectionManualSelect ? '退出手动选择' : '开启手动选择'"
                  @click.stop="toggleRebarManualSelection"
                >
                  {{ rebarInspectionManualSelect ? '退出手动选择' : '手动选择' }}
                </el-button>
                <el-button
                  aria-label="下一根钢筋"
                  title="下一根钢筋"
                  :icon="ArrowRight"
                  :disabled="!rebarInspectionBars.length || rebarInspectionIndex >= rebarInspectionBars.length - 1"
                  @click="stepRebarInspection(1)"
                />
                <el-button
                  class="rebar-inspection__exit"
                  aria-label="退出巡检模式"
                  title="退出巡检模式"
                  :icon="Close"
                  :disabled="!rebarInspectionActive && !rebarInspectionManualSelect"
                  @click="exitRebarInspection"
                />
              </div>
              <p v-if="rebarInspectionAbnormalOnly && !rebarInspectionBars.length" class="rebar-inspection__empty">当前容差下没有异常钢筋。</p>
            </div>
            <div v-if="selectedComparisonBar" class="rebar-comparison-details">
              <span>IFC：{{ selectedComparisonBar.ifcGlobalId }}</span>
              <span>实例：{{ selectedComparisonBar.instanceIds.join('、') || '无可靠对应' }}</span>
              <span v-if="selectedComparisonBar.reviewInstanceIds?.length">待复核实例：{{ selectedComparisonBar.reviewInstanceIds.join('、') }}（{{ selectedComparisonBar.reviewPointCount }} 点）</span>
              <span>点数：{{ selectedComparisonBar.pointCount.toLocaleString() }}（预览 {{ denoiseVisiblePointCount.toLocaleString() }}）</span>
              <span>覆盖率：{{ formatC2MPercentage(selectedComparisonBar.vertexCount ? selectedComparisonBar.knownCount / selectedComparisonBar.vertexCount : undefined) }}</span>
              <span>平均绝对偏差：{{ formatC2MDistance(selectedComparisonBar.stats?.meanAbs) }}</span>
              <span>RMSE / P95：{{ formatC2MDistance(selectedComparisonBar.stats?.rmse) }} / {{ formatC2MDistance(selectedComparisonBar.stats?.p95Abs) }}</span>
              <span>容差内（已覆盖）：{{ formatC2MPercentage(selectedComparisonBar.stats?.withinToleranceRatio) }}</span>
              <p v-if="!selectedComparisonBar.stats">缺测或待复核，暂不出具偏差结论。</p>
            </div>
            <p>浅灰色表示缺测或待复核；超出显示范围仍用蓝色 / 红色显示。容差内比例仅统计已覆盖顶点。</p>
            <div class="panel-action-row">
              <el-button size="small" @click="downloadRebarReport(false)">导出全部逐筋明细</el-button>
              <el-button size="small" :disabled="!selectedComparisonBar" @click="downloadRebarReport(true)">导出当前钢筋</el-button>
            </div>
            <div class="c2m-scene-status" role="status" aria-live="polite">
              <span v-if="!meshReady">{{ meshTaskActive ? '网格处理中…' : '请先完成 BIM 网格均匀化' }}</span>
              <span v-else-if="c2mRunning">正在计算…</span>
              <span v-else-if="c2mSceneLoading">正在加载偏差…</span>
              <span v-else-if="c2mCalculationSettingsDirty">参数已修改，重新计算后生效</span>
              <span v-else-if="c2mSceneLoaded">{{ rebarDebugActive && rebarDebugDisplaySurface !== 'result' ? '结果已加载，未显示偏差着色' : '偏差已显示' }}<span v-if="comparison?.timings?.total !== undefined"> · 耗时 {{ comparison.timings.total.toFixed(2) }} s</span></span>
              <span v-else-if="canUseC2MResult && !c2mSceneArtifactAvailable">结果生成中…</span>
              <el-button v-if="canUseC2MResult && c2mSceneArtifactAvailable && !c2mSceneLoaded && !c2mSceneLoading && !c2mRunning" size="small" @click="loadC2MToScene">重试显示结果</el-button>
            </div>
          </section>

          <section class="c2m-color-controls" aria-label="配色调整">
            <div class="section-title">配色调整</div>
            <div class="c2m-setting-row c2m-setting-row--plain">
              <span>工程容差 ± <small>mm</small></span>
              <el-input-number :model-value="c2mToleranceMm" :value-on-clear="c2mToleranceMm" :min="0.1" :max="10000" :step="1" :precision="1" controls-position="right" aria-label="工程容差半宽，单位毫米" @change="onC2MToleranceChange" />
            </div>
            <div class="c2m-setting-row c2m-setting-row--plain">
              <span>配色模式</span>
              <el-select v-model="c2mColorMode" popper-class="bpa-right-popper" aria-label="C2M 网格配色模式">
                <el-option label="连续渐变" value="continuous" />
                <el-option label="离散分区" value="discrete" />
              </el-select>
            </div>
            <div v-if="c2mColorMode === 'discrete'" class="c2m-setting-row c2m-setting-row--plain">
              <span>每区色阶数</span>
              <el-input-number :model-value="c2mBandCount" :value-on-clear="c2mBandCount" @change="onC2MBandCountChange" :min="2" :max="32" :step="1" :precision="0" controls-position="right" aria-label="C2M 离散每区色阶数" />
            </div>
            <div class="c2m-preset-block">
              <span class="c2m-preset-block__label">显示范围</span>
              <div class="c2m-range-presets" role="group" aria-label="偏差显示范围模式">
                <el-button :type="c2mRangeMode === 'auto' ? 'primary' : 'default'" :aria-pressed="c2mRangeMode === 'auto'" @click="selectC2MRangePreset('auto')">自适应</el-button>
                <el-button :type="c2mRangeMode === 'full' ? 'primary' : 'default'" :aria-pressed="c2mRangeMode === 'full'" @click="selectC2MRangePreset('full')">完整范围</el-button>
                <el-button :type="c2mRangeMode === 'manual' ? 'primary' : 'default'" :aria-pressed="c2mRangeMode === 'manual'" @click="selectC2MRangePreset('manual')">手动</el-button>
              </div>
              <div v-if="c2mRangeMode === 'manual'" class="c2m-setting-row c2m-setting-row--plain">
                <span>显示半宽 ± <small>mm</small></span>
                <el-input-number :model-value="c2mColorRangeMm" :value-on-clear="c2mColorRangeMm" :min="Math.max(1, c2mToleranceMm * 1.25)" :step="1" :precision="2" controls-position="right" aria-label="配色色域半宽，单位毫米" @change="onC2MColorRangeChange" />
              </div>
              <div v-else class="c2m-range-readout">−{{ c2mColorRangeMm }} <span>至</span> +{{ c2mColorRangeMm }} <small>mm</small></div>
            </div>
            <p v-if="c2mColorRangeMm > 10000" class="c2m-display-help" role="status">范围超过 ±10,000 mm，仅支持预览。</p>
            <el-button class="c2m-apply-button" :loading="c2mRecoloring" :disabled="!canRecolorC2M" @click="applyC2MVisualization">
              {{ c2mSettingsDirty ? '保存配色设置' : '配色设置已保存' }}
            </el-button>
            <C2MHistogramLegend v-if="c2mDisplayResult" :result="c2mDisplayResult" :color-mode="c2mColorMode" :band-count="c2mBandCount" compact />
            <details class="c2m-chart-options">
              <summary>直方图设置</summary>
              <div class="c2m-setting-row c2m-setting-row--plain">
                <span>范围跟随色标</span>
                <el-switch v-model="c2mHistogramFollowsColor" aria-label="直方图范围跟随配色色域" @change="onC2MHistogramFollowChange" />
              </div>
              <div v-if="!c2mHistogramFollowsColor" class="c2m-setting-row c2m-setting-row--plain">
                <span>直方图半宽 ± <small>mm</small></span>
                <el-input-number :model-value="c2mHistogramRangeMm" :value-on-clear="c2mHistogramRangeMm" :min="1" :max="10000" :step="1" :precision="1" controls-position="right" aria-label="直方图视窗半宽，单位毫米" @change="onC2MHistogramRangeChange" />
              </div>
              <div class="c2m-setting-row c2m-setting-row--plain">
                <span>直方图桶数</span>
                <el-input-number :model-value="c2mHistogramBins" :value-on-clear="c2mHistogramBins" :min="10" :max="200" :step="10" :precision="0" controls-position="right" aria-label="直方图桶数" @change="onC2MHistogramBinsChange" />
              </div>
            </details>
          </section>

          <RebarInspectionSummary v-if="comparison?.inspection" :inspection="comparison.inspection" :bars="comparisonBars" :selected-id="selectedComparisonBarId" @select="selectRebarDebugBar" />
          <section v-if="comparison" class="c2m-primary-card" aria-label="检测数据">
            <el-button :loading="inspectionDownloading" :disabled="!c2mResult?.resultVersion" @click="downloadInspectionJSON">导出已保存检测 JSON</el-button>
            <p class="c2m-search-hint">包含全部钢筋、间距、观测质量及计算参数。修改容差后请先保存配色设置，再导出新批次。</p>
            <RebarReportHistory v-if="pointcloudAssetId && bimAssetId" :scan-id="pointcloudAssetId" :bim-id="bimAssetId" :result-version="c2mResult?.resultVersion" />
          </section>
          <div v-if="c2mError" class="mesh-remesh-error" role="alert">{{ c2mError }}</div>
          <div v-if="c2mResult" class="c2m-result-notices">
            <div v-if="!c2mResultIsFresh" class="c2m-result-warning c2m-result-warning--stale" role="alert">结果已过期，请重新计算。{{ c2mResult.staleReason }}</div>
            <div v-if="c2mOverlapWarning" class="c2m-result-warning" role="status">模型重叠度低于 30%，请检查配准位置。</div>
            <div v-if="c2mResult.analysis?.status === 'failed'" class="c2m-result-warning" role="alert">分析网格生成失败。{{ c2mResult.analysis.error || '' }}</div>
          </div>
        </div>
        </div>
      </aside>
    </div>

    <footer class="status-bar">
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

<style scoped>
.denoise-panel { display: flex; flex-direction: column; gap: 16px; }
.denoise-note { margin: 0; font-size: 12px; line-height: 1.8; color: var(--el-text-color-secondary); }
</style>
