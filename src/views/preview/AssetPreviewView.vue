<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Aim,
  ArrowLeft,
  ArrowRightBold,
  Close,
  DArrowRight,
  FullScreen,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { useBimRemeshDisplay } from './bimRemeshDisplay'
import { getAssetDetail } from '@/api/backend-file'
import {
  DEFAULT_REBAR_SWEEP_PARAMS,
  REBAR_SWEEP_ALGORITHM,
  getRemeshStatus,
  remeshBimAsset,
  type RemeshStatus,
} from '@/api/backend-mesh'
import {
  computePointcloudPreprocess,
  getPointcloudPreprocess,
  type PointcloudPreprocessResult,
} from '@/api/backend-pointcloud-preprocess'
import { POINTCLOUD_CATEGORY_COLORS, validPointcloudTablePlane } from '@/features/pointcloud/tableVisibility'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'
import PointcloudAxesTriad from '@/components/preview/PointcloudAxesTriad.vue'
import PointcloudViewCube from '@/components/preview/PointcloudViewCube.vue'
import PointcloudColorRangeBar, {
  type PointcloudColorRamp,
  type PointcloudColorRange,
} from '@/components/preview/PointcloudColorRangeBar.vue'
import type { CameraPose, PointcloudColorMode } from '@/components/preview/UnifiedViewer3D.vue'
import ViewerAnalysisOverlay, {
  type AnalysisDistance,
  type AnalysisArea,
  type AnalysisMode,
  type AnalysisPoint,
} from '@/components/preview/ViewerAnalysisOverlay.vue'
import MeasurementToolbar from '@/components/preview/MeasurementToolbar.vue'
import {
  createMeasurement,
  deleteMeasurement,
  listMeasurements,
  type MeasurementKind,
} from '@/api/backend-measurement'

const DEFAULT_POINT_COLOR = '#86898D'

type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'

const props = defineProps<{
  previewType: 'bim' | 'pointcloud'
  assetId: number | null
  displayName?: string
  projectId?: number | null
  projectName?: string
}>()

const router = useRouter()
const bimPanelRef = ref<any>(null)
const bimLoaded = ref(false)
const bimRemeshStatus = ref<RemeshStatus | null>(null)
const bimRemeshSubmitting = ref(false)
const bimRemeshError = ref('')
let bimRemeshGeneration = 0
let bimRemeshTimer: ReturnType<typeof setTimeout> | undefined
const bimRemeshReady = computed(() =>
  bimRemeshStatus.value?.status === 'succeeded' &&
  bimRemeshStatus.value.algorithm === REBAR_SWEEP_ALGORITHM &&
  Boolean(bimRemeshStatus.value.resultFileId),
)
const bimRemeshArtifactKey = computed(() => bimRemeshReady.value
  ? `${props.assetId}:${bimRemeshStatus.value?.contentHash}:${bimRemeshStatus.value?.finishedAt}`
  : '')
const bimRemeshDisplay = useBimRemeshDisplay(bimLoaded, bimRemeshArtifactKey, async (visible) => {
  if (visible && !bimPanelRef.value) throw new Error('BIM 预览尚未准备好')
  await bimPanelRef.value?.setBimRemeshVisible(visible)
})
const { visible: bimRemeshVisible, error: bimRemeshDisplayError } = bimRemeshDisplay
const bimRemeshBusy = computed(() => bimRemeshSubmitting.value || bimRemeshDisplay.busy.value)
const bimRemeshRunning = computed(() => ['queued', 'processing'].includes(bimRemeshStatus.value?.status || ''))
const bimRemeshCanRun = computed(() => Boolean(
  bimRemeshStatus.value?.supported && !bimRemeshRunning.value &&
  (bimRemeshStatus.value.status === 'succeeded' || bimRemeshStatus.value.canManualRetry),
))
const bimRemeshActionText = computed(() => {
  if (bimRemeshBusy.value) return '请稍候…'
  if (bimRemeshRunning.value) return '正在生成网格…'
  if (bimRemeshStatus.value?.status === 'succeeded') return '重新生成网格'
  if (bimRemeshStatus.value?.status === 'failed') return '重试生成网格'
  return '生成网格'
})
const bimRemeshStatusText = computed(() => {
  if (!bimRemeshStatus.value) return '正在查询均匀化状态…'
  if (!bimRemeshStatus.value.supported) return '当前模型不支持网格均匀化'
  switch (bimRemeshStatus.value.status) {
    case 'queued': return '任务已排队，完成后自动显示保形网格'
    case 'processing': return '正在生成保形网格，完成后自动显示'
    case 'succeeded': return !bimRemeshReady.value ? '已有结果版本不匹配，请重新生成网格' : bimRemeshVisible.value ? '当前显示：保形网格' : '当前显示：原始模型；可开启保形网格'
    case 'failed': return '均匀化失败，可重试'
    default: return '尚未生成均匀化网格'
  }
})

async function refreshBimRemeshStatus() {
  clearTimeout(bimRemeshTimer)
  const assetId = props.assetId
  const generation = bimRemeshGeneration
  if (props.previewType !== 'bim' || !assetId) return
  try {
    const { data } = await getRemeshStatus(assetId)
    if (generation !== bimRemeshGeneration) return
    bimRemeshStatus.value = data
    bimRemeshError.value = data.status === 'failed' ? data.lastError || '网格均匀化失败' : ''
    if (data.supported && ['idle', 'queued', 'processing'].includes(data.status || 'idle')) {
      bimRemeshTimer = setTimeout(() => void refreshBimRemeshStatus(), 4000)
    }
  } catch (error) {
    if (generation === bimRemeshGeneration) bimRemeshError.value = error instanceof Error ? error.message : '查询均匀化状态失败'
  }
}

function toggleBimRemesh(visible = !bimRemeshVisible.value) {
  if (bimRemeshBusy.value || !bimLoaded.value || !bimRemeshReady.value) return
  if (visible === bimRemeshVisible.value) return
  bimRemeshError.value = ''
  bimRemeshDisplay.toggle()
}

async function retryBimRemesh() {
  if (!props.assetId || !bimRemeshCanRun.value || bimRemeshBusy.value) return
  // Retire in-flight status reads so they cannot restore the previous result.
  const generation = ++bimRemeshGeneration
  clearTimeout(bimRemeshTimer)
  const force = bimRemeshStatus.value?.status === 'succeeded'
  bimRemeshSubmitting.value = true
  bimRemeshError.value = ''
  try {
    await remeshBimAsset(props.assetId, {
      algorithm: REBAR_SWEEP_ALGORITHM,
      params: { ...DEFAULT_REBAR_SWEEP_PARAMS },
      ...(force ? { force: true } : {}),
    })
    if (generation !== bimRemeshGeneration) return
    // A fast rerun may finish before the next read and retain the same hash.
    // Always retire the displayed PLY after an accepted submission.
    bimRemeshStatus.value = { ...bimRemeshStatus.value!, status: 'queued', canManualRetry: false }
    await nextTick() // Let the display watcher retire the old artifact, even for a fast rerun.
    if (generation !== bimRemeshGeneration) return
    await refreshBimRemeshStatus()
  } catch (error) {
    if (generation === bimRemeshGeneration) bimRemeshError.value = error instanceof Error ? error.message : '提交均匀化任务失败'
  } finally {
    if (generation === bimRemeshGeneration) bimRemeshSubmitting.value = false
  }
}

function resetBimRemeshState() {
  bimRemeshGeneration++
  clearTimeout(bimRemeshTimer)
  bimLoaded.value = false
  bimRemeshStatus.value = null
  bimRemeshDisplay.reset()
  bimRemeshSubmitting.value = false
  bimRemeshError.value = ''
}
const pointcloudPanelRef = ref<any>(null)
const pointcloudStageRef = ref<HTMLElement | null>(null)

const backgroundTheme = ref<PreviewBackgroundTheme>('deep')
const sidebarCollapsed = ref(false)
const analysisMode = ref<AnalysisMode>('none')
const analysisPoint = ref<AnalysisPoint | null>(null)
const analysisDistance = ref<AnalysisDistance | null>(null)
const analysisAreas = ref<AnalysisArea[]>([])
const analysisPoints = ref<AnalysisPoint[]>([])
const analysisDistances = ref<AnalysisDistance[]>([])
const analysisToolbarCollapsed = ref(false)
const pointcloudLoaded = ref(false)
const pointcloudEdlEnabled = ref(true)
const pointcloudSize = ref(2.5)
const pointcloudFullscreen = ref(false)
const pointcloudCameraPose = ref<CameraPose | null>(null)
const pointcloudColorRamp = ref<PointcloudColorRamp>('grayscale')
const pointcloudColorRange = ref<PointcloudColorRange>({ min: 0, max: 1 })
const pointcloudIntensityHistogram = ref<number[]>([])
const measurementBackendIds = new Map<string, number>()
let measurementLoadToken = 0
let pointcloudAppearanceLoadToken = 0
let pointcloudResourceLoadToken = 0
const pointcloudTableVisible = ref(false)
const pointcloudSourceUrl = ref('')
const pointcloudPreprocess = ref<PointcloudPreprocessResult | null>(null)
const pointcloudResourceResolved = ref(false)
const pointcloudPreprocessRunning = ref(false)
const pointcloudResourceError = ref('')
const pointcloudTablePlane = computed(() => {
  const plane = pointcloudPreprocess.value?.result.plane
  return validPointcloudTablePlane(plane) ? plane : null
})
const pointcloudTableFilterAvailable = computed(() => Boolean(pointcloudTablePlane.value))
const pointcloudTableShown = computed(() => !pointcloudTableFilterAvailable.value || pointcloudTableVisible.value)

const bimControls = reactive({
  showAxes: true,
  showGrid: true,
  wireframe: false,
  sectionEnabled: false,
})

const pointcloudControls = reactive({
  showAxes: true,
  showGrid: false,
  sectionEnabled: false,
  colorMode: 'table-class' as PointcloudColorMode | 'original' | 'custom',
  pointColor: DEFAULT_POINT_COLOR,
})

const pointcloudDisplayColorMode = computed(() => {
  const mode = pointcloudControls.colorMode
  return mode === 'original' || (mode === 'table-class' && !pointcloudTableFilterAvailable.value) ? 'rgb' : mode
})

const pointColorPresets = [
  { label: '白色', value: '#f8fafc' },
  { label: '青色', value: '#67e8f9' },
  { label: '橙色', value: '#fb923c' },
  { label: '绿色', value: '#4ade80' },
  { label: '灰色', value: DEFAULT_POINT_COLOR },
]

const backgroundOptions: Array<{ label: string; value: PreviewBackgroundTheme }> = [
  { label: '蓝色', value: 'gradient' },
  { label: '深色', value: 'deep' },
  { label: '浅色', value: 'light' },
  { label: '纯黑', value: 'black' },
]

const pageTitle = computed(() => {
  return props.previewType === 'bim' ? 'BIM 全屏预览' : '点云全屏预览'
})

const emptyText = computed(() => {
  return props.previewType === 'bim'
    ? '请从上传页重新点击“预览”打开 BIM 全屏页。'
    : '请从上传页重新点击“预览”打开点云全屏页。'
})

const currentPanelRef = computed(() => {
  return props.previewType === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
})

function closePage() {
  if (window.opener) {
    window.close()
    return
  }

  if (props.projectId) {
    void router.push({
      path: props.previewType === 'bim' ? '/design/bim' : '/survey',
      query: {
        projectId: props.projectId,
        projectName: props.projectName,
      },
    })
    return
  }

  void router.push('/projects')
}

function resetView() {
  if (props.previewType === 'bim') {
    bimPanelRef.value?.resetView?.()
    return
  }

  pointcloudPanelRef.value?.resetPointcloudView?.()
}

function applyPanelSettings() {
  const panel = currentPanelRef.value
  if (!panel) {
    return
  }

  panel.setBackgroundTheme?.(backgroundTheme.value)

  if (props.previewType === 'bim') {
    panel.setShowAxes?.(bimControls.showAxes)
    panel.setShowGrid?.(bimControls.showGrid)
    panel.setWireframe?.(bimControls.wireframe)
    panel.setSectionState?.({ enabled: bimControls.sectionEnabled })
    return
  }

  // Match the calibration page: this setting controls the camera-linked
  // viewport triad, not an additional world-space AxesHelper in the scene.
  panel.setShowAxes?.(false)
  panel.setShowGrid?.(pointcloudControls.showGrid)
  panel.setSectionState?.(pointcloudControls.sectionEnabled)
  if (pointcloudDisplayColorMode.value === 'custom') {
    panel.setPointColor?.(pointcloudControls.pointColor)
  } else {
    panel.setPointcloudColorDisplay?.(
      pointcloudDisplayColorMode.value,
      pointcloudColorRamp.value,
      pointcloudColorRange.value,
    )
  }
  panel.setPointSize?.(pointcloudSize.value)
  panel.setEdlEnabled?.(pointcloudEdlEnabled.value)
}

function applyPointColorPreset(color: string) {
  pointcloudControls.colorMode = 'custom'
  pointcloudControls.pointColor = color
  pointcloudPanelRef.value?.setPointColor?.(color)
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function setPointcloudViewDirection(direction: [number, number, number]) {
  pointcloudPanelRef.value?.setViewDirection?.(direction)
}

function orbitPointcloudFromCube(delta: { lon: number; lat: number }) {
  pointcloudPanelRef.value?.syncFromRotation?.(delta.lon, delta.lat)
}

function rollPointcloudView(direction: -1 | 1) {
  pointcloudPanelRef.value?.rollView?.(direction)
}

function handlePointcloudColorStats(stats: {
  histogram: number[]
  hasIntensity: boolean
  hasRgb: boolean
}) {
  pointcloudIntensityHistogram.value = stats.histogram
}

function normalizePointcloudColor(value: string | null | undefined) {
  const normalized = value?.trim().toLowerCase() || ''
  return /^#[0-9a-f]{6}$/.test(normalized) ? normalized : null
}

async function loadPointcloudAppearance() {
  const assetId = props.assetId
  const token = ++pointcloudAppearanceLoadToken
  if (props.previewType !== 'pointcloud' || !assetId) return

  try {
    const response = await getAssetDetail(assetId)
    if (token !== pointcloudAppearanceLoadToken) return

    const savedColor = normalizePointcloudColor(response.data?.pointcloudColor)
    if (savedColor) {
      pointcloudControls.pointColor = savedColor
    }
  } catch (error) {
    if (token === pointcloudAppearanceLoadToken) {
      console.warn('[AssetPreview] 读取点云显示颜色失败', error)
    }
  }
}

async function loadPointcloudResources() {
  const assetId = props.assetId
  const token = ++pointcloudResourceLoadToken
  pointcloudResourceError.value = ''
  if (props.previewType !== 'pointcloud' || !assetId) return

  try {
    const [detailResponse, preprocessResponse] = await Promise.all([
      getAssetDetail(assetId),
      getPointcloudPreprocess(assetId),
    ])
    if (token !== pointcloudResourceLoadToken) return
    pointcloudSourceUrl.value = detailResponse.data?.tilesetUrl || ''
    pointcloudPreprocess.value = preprocessResponse.data
  } catch (error) {
    if (token !== pointcloudResourceLoadToken) return
    pointcloudPreprocess.value = null
    // Preserve historical-asset preview even if the derivative endpoints are
    // temporarily unavailable.
    try {
      const detailResponse = await getAssetDetail(assetId)
      if (token !== pointcloudResourceLoadToken) return
      pointcloudSourceUrl.value = detailResponse.data?.tilesetUrl || ''
    } catch {
      // Report the original error because it normally contains the useful API message.
    }
    pointcloudResourceError.value = error instanceof Error ? error.message : '读取点云预处理结果失败'
  } finally {
    if (token === pointcloudResourceLoadToken) pointcloudResourceResolved.value = true
  }
}

async function runPointcloudPreprocess() {
  if (!props.assetId || pointcloudPreprocessRunning.value) return
  pointcloudPreprocessRunning.value = true
  pointcloudResourceError.value = ''
  try {
    await computePointcloudPreprocess(props.assetId)
    await loadPointcloudResources()
    if (pointcloudTableFilterAvailable.value) {
      pointcloudTableVisible.value = false
      ElMessage.success('台面识别与点云预处理完成')
    } else {
      ElMessage.warning('预处理已完成，未检测到可隐藏的台面')
    }
  } catch (error) {
    pointcloudResourceError.value = error instanceof Error ? error.message : '点云预处理失败'
  } finally {
    pointcloudPreprocessRunning.value = false
  }
}

function togglePointcloudEdl() {
  pointcloudEdlEnabled.value = !pointcloudEdlEnabled.value
}

async function togglePointcloudFullscreen() {
  const stage = pointcloudStageRef.value
  if (!stage) return
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
    } else {
      await stage.requestFullscreen()
    }
  } catch (error) {
    console.warn('[AssetPreview] 切换全屏失败', error)
  }
}

function syncFullscreenState() {
  pointcloudFullscreen.value = document.fullscreenElement === pointcloudStageRef.value
}

function selectAnalysisMode(mode: AnalysisMode) {
  currentPanelRef.value?.cancelAnalysis?.()
  analysisMode.value = analysisMode.value === mode ? 'none' : mode
}

function handleAnalysisModeExit() {
  analysisMode.value = 'none'
}

function clearAnalysis() {
  analysisMode.value = 'none'
  analysisPoint.value = null
  analysisDistance.value = null
  analysisAreas.value = []
  analysisPoints.value = []
  analysisDistances.value = []
  currentPanelRef.value?.clearAnalysis?.()
  const backendIds = [...new Set(measurementBackendIds.values())]
  measurementBackendIds.clear()
  backendIds.forEach((id) => {
    void deleteMeasurement(id).catch((error) => {
      console.warn('[AssetPreview] 删除测量记录失败', error)
    })
  })
}

function removeAnalysisById(kind: 'point' | 'distance' | 'area', id: string) {
  currentPanelRef.value?.removeAnalysisVisual?.(kind, id)

  if (kind === 'point') {
    analysisPoints.value = analysisPoints.value.filter(
      (record, index) => (record.id || `point-${index}`) !== id,
    )
    analysisPoint.value = analysisPoints.value.at(-1) ?? null
  }
  if (kind === 'distance') {
    analysisDistances.value = analysisDistances.value.filter(
      (record, index) => (record.id || `distance-${index}`) !== id,
    )
    analysisDistance.value = analysisDistances.value.at(-1) ?? null
  }
  if (kind === 'area') {
    analysisAreas.value = analysisAreas.value.filter(
      (record, index) => (record.id || `area-${index}`) !== id,
    )
  }

  const backendId = measurementBackendIds.get(id)
  measurementBackendIds.delete(id)
  if (backendId !== undefined) {
    void deleteMeasurement(backendId).catch((error) => {
      console.warn('[AssetPreview] 删除测量记录失败', error)
    })
  }
}

function handleAnalysisPoint(point: AnalysisPoint) {
  analysisPoint.value = point
  analysisPoints.value = [...analysisPoints.value, point]
  persistMeasurement('locate', point)
}

function handleAnalysisDistance(distance: AnalysisDistance) {
  analysisDistance.value = distance
  analysisDistances.value = [...analysisDistances.value, distance]
  persistMeasurement('distance', distance)
}

function handleAnalysisArea(area: AnalysisArea) {
  analysisAreas.value = [...analysisAreas.value, area]
  persistMeasurement('area', area)
}

function hasMeasurement(kind: MeasurementKind, id: string) {
  if (kind === 'locate') return analysisPoints.value.some((record) => record.id === id)
  if (kind === 'distance') return analysisDistances.value.some((record) => record.id === id)
  return analysisAreas.value.some((record) => record.id === id)
}

async function persistMeasurement(kind: MeasurementKind, payload: unknown) {
  if (!props.assetId) return
  const localId = typeof payload === 'object' && payload && 'id' in payload
    ? String(payload.id)
    : ''
  try {
    const response = await createMeasurement(props.assetId, kind, payload)
    if (!localId) return
    if (hasMeasurement(kind, localId)) {
      measurementBackendIds.set(localId, response.data.id)
    } else {
      void deleteMeasurement(response.data.id).catch(() => undefined)
    }
  } catch (error) {
    console.warn('[AssetPreview] 保存测量记录失败', error)
  }
}

async function loadMeasurements() {
  const assetId = props.assetId
  const token = ++measurementLoadToken
  analysisPoint.value = null
  analysisDistance.value = null
  analysisAreas.value = []
  analysisPoints.value = []
  analysisDistances.value = []
  measurementBackendIds.clear()
  if (!assetId) return

  try {
    const response = await listMeasurements(assetId)
    if (token !== measurementLoadToken) return
    response.data.forEach((record) => {
      const payload = record.payload && typeof record.payload === 'object'
        ? { ...(record.payload as Record<string, unknown>) }
        : {}
      const id = typeof payload.id === 'string' && payload.id
        ? payload.id
        : `${record.kind}-${record.id}`
      measurementBackendIds.set(id, record.id)
      if (record.kind === 'locate') analysisPoints.value.push({ ...payload, id } as AnalysisPoint)
      if (record.kind === 'distance') analysisDistances.value.push({ ...payload, id } as AnalysisDistance)
      if (record.kind === 'area') analysisAreas.value.push({ ...payload, id } as AnalysisArea)
    })
    analysisPoint.value = analysisPoints.value.at(-1) ?? null
    analysisDistance.value = analysisDistances.value.at(-1) ?? null
  } catch (error) {
    console.warn('[AssetPreview] 读取测量记录失败', error)
  }
}

watch(
  () => [
    props.previewType,
    backgroundTheme.value,
    bimControls.showAxes,
    bimControls.showGrid,
    bimControls.wireframe,
    bimControls.sectionEnabled,
    pointcloudControls.showAxes,
    pointcloudControls.showGrid,
    pointcloudControls.sectionEnabled,
    pointcloudControls.colorMode,
    pointcloudDisplayColorMode.value,
    pointcloudControls.pointColor,
    pointcloudColorRamp.value,
    pointcloudColorRange.value.min,
    pointcloudColorRange.value.max,
    pointcloudSize.value,
    pointcloudEdlEnabled.value,
    bimPanelRef.value,
    pointcloudPanelRef.value,
  ] as const,
  () => {
    applyPanelSettings()
  },
  { immediate: true },
)

onMounted(() => {
  void refreshBimRemeshStatus()
  document.addEventListener('fullscreenchange', syncFullscreenState)
  applyPanelSettings()
  void loadMeasurements()
  void loadPointcloudAppearance()
  void loadPointcloudResources()
})

onBeforeUnmount(() => {
  resetBimRemeshState()
  pointcloudResourceLoadToken++
  document.removeEventListener('fullscreenchange', syncFullscreenState)
})

watch(
  () => [props.assetId, props.previewType] as const,
  () => {
    resetBimRemeshState()
    void refreshBimRemeshStatus()
    analysisMode.value = 'none'
    pointcloudIntensityHistogram.value = []
    pointcloudTableVisible.value = false
    pointcloudResourceResolved.value = false
    pointcloudSourceUrl.value = ''
    pointcloudPreprocess.value = null
    void loadMeasurements()
    void loadPointcloudAppearance()
    void loadPointcloudResources()
  },
)
</script>

<template>
  <section v-if="previewType === 'pointcloud'" class="pointcloud-preview-page">
    <header class="pointcloud-preview-header">
      <span class="pointcloud-preview-heading">
        <strong>{{ displayName || '点云预览' }}</strong>
        <small>{{ projectName || '实测扫描' }} · 点云预览</small>
      </span>

      <div class="pointcloud-header-controls" role="group" aria-label="预览背景">
        <span class="pointcloud-header-label">背景</span>
        <div class="pointcloud-segmented">
          <button
            v-for="option in backgroundOptions"
            :key="option.value"
            type="button"
            :class="{ on: backgroundTheme === option.value }"
            :aria-pressed="backgroundTheme === option.value"
            @click="backgroundTheme = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>

      <el-tooltip content="关闭预览" placement="bottom">
        <button class="pointcloud-close" type="button" aria-label="关闭预览" @click="closePage">
          <el-icon><Close /></el-icon>
        </button>
      </el-tooltip>
    </header>

    <div v-if="!assetId" class="pointcloud-empty-state">
      <h2>{{ pageTitle }}</h2>
      <p>{{ emptyText }}</p>
    </div>

    <main
      v-else
      ref="pointcloudStageRef"
      class="pointcloud-preview-stage"
      :class="`theme-${backgroundTheme}`"
    >
      <PointcloudPreviewPanel
        v-if="pointcloudResourceResolved && pointcloudSourceUrl"
        ref="pointcloudPanelRef"
        class="pointcloud-viewer-panel"
        :asset-id="assetId"
        :tileset-url="pointcloudSourceUrl"
        :table-plane="pointcloudTablePlane"
        :table-visible="pointcloudTableShown"
        :analysis-mode="analysisMode"
        :analysis-points="analysisPoints"
        :analysis-distances="analysisDistances"
        :analysis-areas="analysisAreas"
        :show-edl-control="false"
        minimal
        @loaded-change="pointcloudLoaded = $event"
        @camera-change="pointcloudCameraPose = $event"
        @pointcloud-color-stats="handlePointcloudColorStats"
        @analysis-point="handleAnalysisPoint"
        @analysis-distance="handleAnalysisDistance"
        @analysis-area="handleAnalysisArea"
        @analysis-delete="removeAnalysisById($event.kind, $event.id)"
        @analysis-mode-exit="handleAnalysisModeExit"
        @pointcloud-source-fallback="pointcloudControls.colorMode = 'rgb'"
        @edl-fallback="pointcloudEdlEnabled = false"
      />

      <ViewerAnalysisOverlay
        :mode="analysisMode"
        :point="analysisPoint"
        :distance="analysisDistance"
        :points="analysisPoints"
        :distances="analysisDistances"
        :areas="analysisAreas"
        @clear="clearAnalysis"
      />

      <div class="pointcloud-viewport-toolbar">
        <div class="pointcloud-toolbar-cluster">
          <el-tooltip content="重置视角" placement="bottom">
            <button type="button" aria-label="重置视角" @click="resetView">
              <el-icon><Aim /></el-icon>
            </button>
          </el-tooltip>
          <el-tooltip :content="pointcloudFullscreen ? '退出全屏' : '进入全屏'" placement="bottom">
            <button
              type="button"
              :class="{ 'is-active': pointcloudFullscreen }"
              :aria-label="pointcloudFullscreen ? '退出全屏' : '进入全屏'"
              @click="togglePointcloudFullscreen"
            >
              <el-icon><FullScreen /></el-icon>
            </button>
          </el-tooltip>
        </div>

        <div class="pointcloud-display-panel">
          <div class="pointcloud-display-row">
            <div class="pointcloud-segmented pointcloud-color-modes" role="group" aria-label="点云着色">
              <button
                type="button"
                :disabled="!pointcloudTableFilterAvailable"
                :class="{ on: pointcloudDisplayColorMode === 'table-class' }"
                :aria-pressed="pointcloudDisplayColorMode === 'table-class'"
                @click="pointcloudControls.colorMode = 'table-class'"
              >
                台面分色
              </button>
              <button
                type="button"
                :class="{ on: pointcloudDisplayColorMode === 'rgb' }"
                :aria-pressed="pointcloudDisplayColorMode === 'rgb'"
                @click="pointcloudControls.colorMode = 'rgb'"
              >
                真彩
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.colorMode === 'intensity' }"
                :aria-pressed="pointcloudControls.colorMode === 'intensity'"
                @click="pointcloudControls.colorMode = 'intensity'"
              >
                强度
              </button>
            </div>
            <div
              v-if="pointcloudControls.colorMode === 'intensity'"
              class="pointcloud-segmented pointcloud-ramp-modes"
              :class="{ 'is-disabled': pointcloudControls.colorMode !== 'intensity' }"
              role="group"
              aria-label="色带"
            >
              <button
                type="button"
                :disabled="pointcloudControls.colorMode !== 'intensity'"
                :class="{ on: pointcloudColorRamp === 'grayscale' }"
                @click="pointcloudColorRamp = 'grayscale'"
              >
                灰度
              </button>
              <button
                type="button"
                :disabled="pointcloudControls.colorMode !== 'intensity'"
                :class="{ on: pointcloudColorRamp === 'spectrum' }"
                @click="pointcloudColorRamp = 'spectrum'"
              >
                彩虹
              </button>
              <button
                type="button"
                :disabled="pointcloudControls.colorMode !== 'intensity'"
                :class="{ on: pointcloudColorRamp === 'viridis' }"
                @click="pointcloudColorRamp = 'viridis'"
              >
                紫黄
              </button>
            </div>
          </div>

          <div class="pointcloud-display-row pointcloud-display-settings">
            <div class="pointcloud-segmented">
              <button
                type="button"
                :class="{ on: pointcloudEdlEnabled }"
                :aria-pressed="pointcloudEdlEnabled"
                @click="togglePointcloudEdl"
              >
                显示增强
              </button>
            </div>
            <label class="pointcloud-size-control" title="点大小">
              <span>点大小</span>
              <input v-model.number="pointcloudSize" aria-label="点大小" type="range" min="1" max="5" step="0.1" />
              <output>{{ pointcloudSize.toFixed(1) }}</output>
            </label>
          </div>

          <div class="pointcloud-display-row">
            <div class="pointcloud-segmented" role="group" aria-label="场景辅助显示">
              <button
                type="button"
                :disabled="!pointcloudTableFilterAvailable"
                :class="{ on: pointcloudTableShown }"
                :aria-pressed="pointcloudTableShown"
                @click="pointcloudTableVisible = !pointcloudTableVisible"
              >
                显示台面
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.showAxes }"
                :aria-pressed="pointcloudControls.showAxes"
                @click="pointcloudControls.showAxes = !pointcloudControls.showAxes"
              >
                坐标轴
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.showGrid }"
                :aria-pressed="pointcloudControls.showGrid"
                @click="pointcloudControls.showGrid = !pointcloudControls.showGrid"
              >
                网格
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.sectionEnabled }"
                :aria-pressed="pointcloudControls.sectionEnabled"
                @click="pointcloudControls.sectionEnabled = !pointcloudControls.sectionEnabled"
              >
                剖切
              </button>
            </div>
          </div>

          <div v-if="pointcloudResourceResolved && !pointcloudTableFilterAvailable" class="pointcloud-preprocess-notice">
            <span>{{ pointcloudPreprocess ? '该点云未检测到可隐藏的台面，当前显示全部点。' : '该历史点云尚未识别台面，当前显示全部点。' }}</span>
            <el-button size="small" :loading="pointcloudPreprocessRunning" @click="runPointcloudPreprocess">
              补充预处理
            </el-button>
          </div>
          <el-alert
            v-if="pointcloudResourceError"
            class="pointcloud-resource-error"
            :title="pointcloudResourceError"
            type="warning"
            :closable="false"
          />
        </div>
      </div>

      <PointcloudViewCube
        :pose="pointcloudCameraPose"
        @home="resetView"
        @select-direction="setPointcloudViewDirection"
        @orbit="orbitPointcloudFromCube"
        @roll="rollPointcloudView"
      />

      <PointcloudAxesTriad
        v-show="pointcloudControls.showAxes"
        class="preview-pointcloud-axes-triad"
        :pose="pointcloudCameraPose"
      />

      <PointcloudColorRangeBar
        v-if="pointcloudDisplayColorMode === 'intensity'"
        v-model:range="pointcloudColorRange"
        class="pointcloud-bottom-color-bar"
        :ramp="pointcloudColorRamp"
        :histogram="pointcloudIntensityHistogram"
      />

      <div v-if="pointcloudDisplayColorMode === 'table-class'" class="pointcloud-category-legend" role="group" aria-label="台面分色图例">
        <span>
          <i :style="{ backgroundColor: POINTCLOUD_CATEGORY_COLORS.table }" aria-hidden="true"></i>
          台面<small v-if="!pointcloudTableShown">（已隐藏）</small>
        </span>
        <span>
          <i :style="{ backgroundColor: POINTCLOUD_CATEGORY_COLORS.nonTable }" aria-hidden="true"></i>
          非台面
        </span>
      </div>

      <div class="pointcloud-viewer-status" role="status">
        <i :class="{ loading: !pointcloudLoaded }" aria-hidden="true"></i>
        {{ pointcloudLoaded ? (pointcloudTableShown ? '点云已加载 · 台面显示' : '点云已加载 · 台面隐藏') : '正在加载点云' }}
        <span v-if="pointcloudEdlEnabled">显示增强</span>
      </div>

      <div class="pointcloud-measurement-dock">
        <MeasurementToolbar
          v-model:collapsed="analysisToolbarCollapsed"
          :mode="analysisMode"
          orientation="vertical"
          position="static"
          @update:mode="selectAnalysisMode"
          @clear="clearAnalysis"
        />
      </div>
    </main>
  </section>

  <section v-else class="asset-preview-page" :class="`theme-${backgroundTheme}`">
    <header class="bim-preview-header">
      <button class="preview-button" type="button" @click="closePage">
        <el-icon><ArrowLeft /></el-icon>
        <span>返回模型列表</span>
      </button>
      <div class="bim-file-context">
        <strong :title="displayName">{{ displayName || 'BIM 模型预览' }}</strong>
        <span :title="projectName">{{ projectName || (projectId ? `项目 ${projectId}` : '模型预览') }}</span>
      </div>
      <div v-if="assetId" class="bim-header-tools">
        <span class="toolbar-label">测量</span>
        <MeasurementToolbar
          v-model:collapsed="analysisToolbarCollapsed"
          :mode="analysisMode"
          :disabled="!bimLoaded"
          position="static"
          @update:mode="selectAnalysisMode"
          @clear="clearAnalysis"
        />
        <button class="preview-button" type="button" :disabled="!bimLoaded" @click="resetView">
          <el-icon><Aim /></el-icon>
          <span>重置视角</span>
        </button>
      </div>
    </header>

    <div v-if="!assetId" class="empty-state">
      <h2>{{ pageTitle }}</h2>
      <p>{{ emptyText }}</p>
    </div>

    <div v-else class="layout-shell" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
      <div class="viewer-region" :class="`theme-${backgroundTheme}`">
        <BimPreviewPanel
          v-if="previewType === 'bim'"
          ref="bimPanelRef"
          class="viewer-panel"
          :asset-id="assetId"
          :display-name="displayName"
          :analysis-mode="analysisMode"
          :analysis-points="analysisPoints"
          :analysis-distances="analysisDistances"
          :analysis-areas="analysisAreas"
          @loaded-change="bimLoaded = $event"
          @analysis-point="handleAnalysisPoint"
          @analysis-distance="handleAnalysisDistance"
          @analysis-area="handleAnalysisArea"
          @analysis-delete="removeAnalysisById($event.kind, $event.id)"
          @analysis-mode-exit="handleAnalysisModeExit"
          minimal
        />

        <PointcloudPreviewPanel
          v-else
          ref="pointcloudPanelRef"
          class="viewer-panel"
          :asset-id="assetId"
          :analysis-mode="analysisMode"
          :analysis-points="analysisPoints"
          :analysis-distances="analysisDistances"
          :analysis-areas="analysisAreas"
          @analysis-point="handleAnalysisPoint"
          @analysis-distance="handleAnalysisDistance"
          @analysis-area="handleAnalysisArea"
          @analysis-delete="removeAnalysisById($event.kind, $event.id)"
          @analysis-mode-exit="handleAnalysisModeExit"
          @edl-fallback="pointcloudEdlEnabled = false"
          minimal
        />
        <ViewerAnalysisOverlay
          :mode="analysisMode"
          :point="analysisPoint"
          :distance="analysisDistance"
          :points="analysisPoints"
          :distances="analysisDistances"
          :areas="analysisAreas"
          @clear="clearAnalysis"
        />
      </div>

      <aside class="sidebar" aria-label="模型工具" :class="{ 'is-collapsed': sidebarCollapsed }">
        <div class="sidebar-heading">
          <h2 v-if="!sidebarCollapsed">模型工具</h2>
          <button
            class="preview-button icon-btn"
            type="button"
            :aria-label="sidebarCollapsed ? '展开模型工具' : '收起模型工具'"
            :title="sidebarCollapsed ? '展开模型工具' : '收起模型工具'"
            :aria-expanded="!sidebarCollapsed"
            aria-controls="bim-tool-sections"
            @click="toggleSidebar"
          >
            <el-icon><ArrowLeft v-if="sidebarCollapsed" /><DArrowRight v-else /></el-icon>
          </button>
        </div>
        <div v-show="!sidebarCollapsed" id="bim-tool-sections" class="sidebar-sections">
          <section class="tool-section" aria-labelledby="mesh-heading">
            <h3 id="mesh-heading">模型与网格</h3>
            <div class="model-view-options" role="group" aria-label="模型显示内容">
              <button class="preview-button" :class="{ 'is-active': !bimRemeshVisible }" type="button"
                :aria-pressed="!bimRemeshVisible" :disabled="!bimLoaded || bimRemeshBusy"
                @click="toggleBimRemesh(false)">原始 IFC</button>
              <button class="preview-button" :class="{ 'is-active': bimRemeshVisible }" type="button"
                :aria-pressed="bimRemeshVisible" :disabled="!bimLoaded || !bimRemeshReady || bimRemeshBusy"
                @click="toggleBimRemesh(true)">网格结果</button>
            </div>
            <p class="mesh-status" role="status" :class="{ 'is-ready': bimRemeshReady, 'is-error': bimRemeshStatus?.status === 'failed' }">
              {{ bimRemeshStatusText }}
            </p>
            <table v-if="bimRemeshReady && bimRemeshStatus?.stats" class="mesh-stats" aria-label="网格均匀化前后统计">
              <thead><tr><th scope="col">几何统计</th><th scope="col">原始 IFC</th><th scope="col">网格结果</th></tr></thead>
              <tbody>
                <tr><th scope="row">顶点</th><td>{{ bimRemeshStatus.stats.vertexBefore.toLocaleString() }}</td><td>{{ bimRemeshStatus.stats.vertexAfter.toLocaleString() }}</td></tr>
                <tr><th scope="row">三角面</th><td>{{ bimRemeshStatus.stats.faceBefore.toLocaleString() }}</td><td>{{ bimRemeshStatus.stats.faceAfter.toLocaleString() }}</td></tr>
              </tbody>
            </table>
            <div class="mesh-actions">
              <button class="preview-button primary-button" type="button" :disabled="!bimRemeshCanRun || bimRemeshBusy" @click="retryBimRemesh">{{ bimRemeshActionText }}</button>
              <button class="preview-button" type="button" :disabled="bimRemeshBusy || bimRemeshRunning" @click="refreshBimRemeshStatus">刷新状态</button>
            </div>
            <p v-if="bimRemeshError || bimRemeshDisplayError" role="alert" class="error-message">{{ bimRemeshError || bimRemeshDisplayError }}</p>
            <p class="section-note">{{ bimRemeshVisible ? '橙色模型为均匀化结果；开启线框可检查三角网格。' : '切换网格结果后，可用线框检查网格化效果。' }}</p>
            <label class="toggle-row">
              <span>线框模式</span>
              <el-switch v-model="bimControls.wireframe" aria-label="线框模式" />
            </label>
          </section>
          <section class="tool-section" aria-labelledby="section-heading">
            <h3 id="section-heading">剖切</h3>
            <label class="toggle-row">
              <span>启用剖切</span>
              <el-switch v-model="bimControls.sectionEnabled" aria-label="启用剖切" />
            </label>
            <p class="section-note">开启后拖拽模型外侧的 6 个方向箭头，调整剖切范围。</p>
          </section>
          <section class="tool-section" aria-labelledby="scene-heading">
            <h3 id="scene-heading">辅助显示</h3>
            <label class="toggle-row"><span>坐标轴</span><el-switch v-model="bimControls.showAxes" aria-label="坐标轴" /></label>
            <label class="toggle-row"><span>参考网格</span><el-switch v-model="bimControls.showGrid" aria-label="参考网格" /></label>
          </section>
          <section class="tool-section" aria-labelledby="background-heading">
            <h3 id="background-heading">画布背景</h3>
            <div class="background-options" role="group" aria-label="画布背景">
              <button v-for="option in backgroundOptions" :key="option.value" class="preview-button theme-chip"
                :class="{ 'is-active': backgroundTheme === option.value }" type="button"
                :aria-pressed="backgroundTheme === option.value" @click="backgroundTheme = option.value">
                <span class="theme-swatch" :class="`theme-swatch-${option.value}`"></span>{{ option.label }}
              </button>
            </div>
          </section>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.pointcloud-preview-page {
  --viewer-stage: #0c1224;
  --viewer-chrome: rgb(12 18 36 / 88%);
  --viewer-ink: #e8ecf8;
  --viewer-muted: #9aa8c7;
  --viewer-accent: #9ec1ff;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: var(--viewer-stage);
}

.pointcloud-preview-header {
  position: relative;
  z-index: 100;
  width: 100%;
  height: 64px;
  padding: 8px 64px 8px 20px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 16px;
  border-bottom: 1px solid #cfd7e8;
  background: #e6ebf5;
}

.pointcloud-preview-heading {
  min-width: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
}

.pointcloud-preview-heading strong,
.pointcloud-preview-heading small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pointcloud-preview-heading strong {
  color: #1a1d24;
  font-size: 14px;
  line-height: 19px;
}

.pointcloud-preview-heading small {
  margin-top: 2px;
  color: #6b7280;
  font-size: 12px;
  line-height: 18px;
}

.pointcloud-header-controls {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.pointcloud-header-label {
  color: #6b7280;
  font-size: 12px;
}

.pointcloud-close {
  position: absolute;
  top: 16px;
  right: 18px;
  width: 32px;
  height: 32px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 6px;
  color: #6b7280;
  background: transparent;
  cursor: pointer;
}

.pointcloud-close:hover {
  color: #4e66cc;
  background: rgb(255 255 255 / 60%);
}

.pointcloud-preview-stage {
  position: relative;
  width: 100%;
  height: calc(100vh - 64px);
  min-height: 320px;
  overflow: hidden;
  background: var(--viewer-stage);
}

.pointcloud-preview-stage:fullscreen {
  height: 100vh;
}

.pointcloud-viewer-panel {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.pointcloud-preview-stage :deep(.unified-viewer-3d),
.pointcloud-preview-stage :deep(.preview-panel),
.pointcloud-preview-stage :deep(.preview-panel.is-minimal) {
  min-height: 100%;
  border: 0;
  border-radius: 0;
  background: var(--viewer-stage);
  box-shadow: none;
}

/* ViewCube 属于视口导航层，必须高于钢筋分析面板(36)，但低于测量工具和页面级面板。 */
.pointcloud-preview-stage :deep(.pointcloud-view-cube) {
  z-index: 60;
}

.pointcloud-preview-stage.theme-deep,
.pointcloud-preview-stage.theme-deep :deep(.unified-viewer-3d) {
  background: #0c1224;
}

.pointcloud-preview-stage.theme-black,
.pointcloud-preview-stage.theme-black :deep(.unified-viewer-3d) {
  background: #000;
}

.pointcloud-preview-stage.theme-light,
.pointcloud-preview-stage.theme-light :deep(.unified-viewer-3d) {
  background: #e8eef6;
}

.pointcloud-preview-stage.theme-gradient,
.pointcloud-preview-stage.theme-gradient :deep(.unified-viewer-3d) {
  background: #10213b;
}

.pointcloud-viewport-toolbar {
  position: absolute;
  z-index: 30;
  top: 16px;
  left: 16px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  max-width: calc(100% - 140px);
  pointer-events: none;
}

.pointcloud-toolbar-cluster {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  pointer-events: auto;
}

.pointcloud-toolbar-cluster button {
  width: 36px;
  height: 36px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 6px;
  color: var(--viewer-ink);
  background: var(--viewer-chrome);
  cursor: pointer;
}

.pointcloud-toolbar-cluster button:hover,
.pointcloud-toolbar-cluster button.is-active {
  border-color: rgb(115 162 243 / 55%);
  color: var(--viewer-accent);
  background: rgb(24 42 72 / 88%);
}

.pointcloud-display-panel {
  width: max-content;
  max-width: min(720px, calc(100vw - 140px));
  padding: 8px 10px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 8px;
  background: rgb(26 29 36 / 90%);
  backdrop-filter: blur(6px);
  pointer-events: auto;
}

.pointcloud-display-row,
.pointcloud-display-settings {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pointcloud-preprocess-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: rgb(255 255 255 / 76%);
  font-size: 11px;
}

.pointcloud-resource-error {
  max-width: 520px;
}

.pointcloud-segmented {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  border-radius: 8px;
  background: rgb(255 255 255 / 10%);
}

.pointcloud-header-controls .pointcloud-segmented {
  background: rgb(255 255 255 / 55%);
}

.pointcloud-segmented button {
  min-width: 0;
  padding: 4px 8px;
  border: 0;
  border-radius: 6px;
  color: rgb(255 255 255 / 72%);
  background: transparent;
  font-size: 13px;
  line-height: 20px;
  cursor: pointer;
}

.pointcloud-header-controls .pointcloud-segmented button {
  padding: 6px 12px;
  color: #3d4450;
  font-size: 12px;
}

.pointcloud-segmented button:hover {
  color: var(--viewer-ink);
}

.pointcloud-segmented button:disabled,
.pointcloud-ramp-modes.is-disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.pointcloud-segmented button.on {
  color: var(--viewer-accent);
  background: rgb(255 255 255 / 14%);
  box-shadow: 0 0 0 1px rgb(255 255 255 / 12%);
}

.pointcloud-header-controls .pointcloud-segmented button.on {
  color: #4e66cc;
  background: #fff;
  box-shadow: 0 0 0 1px #cfd7e8;
  font-weight: 600;
}

.pointcloud-size-control {
  min-width: 164px;
  height: 34px;
  padding: 3px 7px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 7px;
  border-radius: 8px;
  color: rgb(255 255 255 / 72%);
  background: rgb(255 255 255 / 10%);
  font-size: 12px;
}

.pointcloud-size-control input {
  flex: 1 1 auto;
  min-width: 60px;
  height: 4px;
  accent-color: var(--viewer-accent);
  cursor: pointer;
}

.pointcloud-size-control output {
  min-width: 24px;
  color: var(--viewer-muted);
  font-variant-numeric: tabular-nums;
}

.preview-pointcloud-axes-triad {
  position: absolute;
  z-index: 25;
  left: 8px;
  bottom: 8px;
  width: 112px;
  height: 112px;
  pointer-events: none;
}

.pointcloud-viewer-status {
  position: absolute;
  z-index: 25;
  left: 14px;
  bottom: 126px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--viewer-muted);
  font-size: 11px;
  pointer-events: none;
}

.pointcloud-viewer-status > i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4aa896;
  box-shadow: 0 0 8px rgb(74 168 150 / 60%);
}

.pointcloud-viewer-status > i.loading {
  background: #e0b85f;
  box-shadow: 0 0 8px rgb(224 184 95 / 55%);
}

.pointcloud-viewer-status span {
  padding: 2px 6px;
  border-radius: 4px;
  color: #a9bee8;
  background: rgb(34 53 86 / 80%);
}

.pointcloud-bottom-color-bar {
  position: absolute;
  z-index: 28;
  left: 126px;
  right: 14px;
  bottom: 12px;
  width: auto !important;
  max-width: calc(100% - 140px);
}

.pointcloud-category-legend {
  position: absolute;
  z-index: 28;
  left: 126px;
  bottom: 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  max-width: calc(100% - 160px);
  padding: 10px 14px;
  border: 1px solid rgb(148 163 184 / 30%);
  border-radius: 8px;
  color: #e2e8f0;
  background: rgb(15 23 42 / 90%);
  font-size: 12px;
  pointer-events: none;
}

.pointcloud-category-legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.pointcloud-category-legend i {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.pointcloud-category-legend small {
  color: #cbd5e1;
  font-size: inherit;
}

.pointcloud-measurement-dock {
  position: absolute;
  z-index: 80;
  top: clamp(112px, calc(100% - 216px), 176px);
  right: 20px;
}

.pointcloud-measurement-dock :deep(.measurement-toolbar) {
  top: auto;
  right: auto;
}

.pointcloud-empty-state {
  height: calc(100vh - 64px);
  display: grid;
  place-content: center;
  text-align: center;
  color: var(--viewer-ink);
  background: var(--viewer-stage);
}

.pointcloud-empty-state h2,
.pointcloud-empty-state p {
  margin: 0;
}

.pointcloud-empty-state p {
  margin-top: 8px;
  color: var(--viewer-muted);
}

@media (max-width: 900px) {
  .pointcloud-preview-header {
    padding-left: 14px;
  }

  .pointcloud-header-label {
    display: none;
  }

  .pointcloud-header-controls .pointcloud-segmented button {
    padding-inline: 8px;
  }

  .pointcloud-color-modes button:nth-child(n + 4) {
    display: none;
  }
}

@media (max-width: 640px) {
  .pointcloud-preview-heading small,
  .pointcloud-header-controls {
    display: none;
  }

  .pointcloud-preview-header {
    height: 56px;
  }

  .pointcloud-preview-stage,
  .pointcloud-empty-state {
    height: calc(100vh - 56px);
  }

  .pointcloud-viewport-toolbar {
    top: 10px;
    left: 10px;
    max-width: calc(100% - 110px);
  }

  .pointcloud-display-panel {
    max-width: calc(100vw - 120px);
    overflow-x: auto;
  }

  .pointcloud-measurement-dock {
    top: auto;
    right: 12px;
    bottom: 14px;
  }
}

.asset-preview-page {
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: var(--text-primary);
  background: var(--bg-page);
  font-size: var(--font-size-sm);
}

.bim-preview-header {
  flex: 0 0 64px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-card);
}

.bim-file-context {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--spacing-compact);
  padding-left: var(--spacing-md);
  border-left: 1px solid var(--border-color);
}

.bim-file-context strong,
.bim-file-context span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.bim-file-context strong { font-weight: 600; }
.bim-file-context span { color: var(--text-secondary); font-size: var(--font-size-xs); }
.bim-header-tools { display: flex; align-items: center; gap: var(--spacing-compact); }
.toolbar-label { color: var(--text-secondary); }

.preview-button {
  min-height: var(--control-height);
  padding: 0 var(--spacing-compact);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xs);
  background: var(--bg-card);
  color: var(--text-secondary);
  font: inherit;
  cursor: pointer;
  transition: background-color var(--transition-fast), border-color var(--transition-fast);
}

.preview-button:hover:not(:disabled) { background: var(--bg-control-hover); border-color: var(--border-color-hover); }
.preview-button.is-active { background: var(--color-primary-soft); border-color: var(--color-primary); color: var(--color-primary-active); font-weight: 600; }
.preview-button:active:not(:disabled) { background: var(--bg-control); }
.primary-button { background: var(--color-primary-hover); border-color: var(--color-primary-hover); color: var(--bg-card); }
.primary-button:hover:not(:disabled) { background: var(--color-primary-active); border-color: var(--color-primary-active); }
.primary-button:active:not(:disabled) { background: var(--color-primary-active); }
.preview-button:disabled { cursor: not-allowed; color: var(--text-disabled); background: var(--bg-muted); border-color: var(--border-color-light); }
.preview-button:focus-visible,
.bim-header-tools :deep(button:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }

.layout-shell {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--viewer-panel-width);
}
.layout-shell.is-sidebar-collapsed { grid-template-columns: minmax(0, 1fr) 56px; }
.viewer-region { position: relative; min-width: 0; min-height: 0; overflow: hidden; }
.viewer-panel { width: 100%; height: 100%; }
/* The renderer resizes its buffer without rewriting its initial inline CSS size. */
.viewer-region :deep(.unified-viewer-viewport > canvas) { width: 100% !important; height: 100% !important; }
.viewer-region :deep(.preview-panel),
.viewer-region :deep(.preview-panel.is-minimal) { height: 100%; min-height: 0; border: 0; border-radius: 0; box-shadow: none; }

.sidebar { min-height: 0; display: flex; flex-direction: column; border-left: 1px solid var(--border-color); background: var(--bg-card); }
.sidebar-heading { min-height: 56px; display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-sm); padding: var(--spacing-sm) var(--spacing-md); border-bottom: 1px solid var(--border-color-light); }
.sidebar-heading h2 { margin: 0; font-size: var(--font-size-sm); font-weight: 600; }
.icon-btn { width: var(--control-height); flex: 0 0 var(--control-height); padding: 0; }
.sidebar.is-collapsed .sidebar-heading { padding-inline: var(--spacing-sm); justify-content: center; }
.sidebar-sections { min-height: 0; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--border-color-hover) transparent; }
.tool-section { padding: var(--spacing-md); border-bottom: 1px solid var(--border-color-light); }
.tool-section h3 { margin: 0 0 var(--spacing-compact); color: var(--text-primary); font-size: var(--font-size-sm); font-weight: 600; }
.model-view-options { display: grid; grid-template-columns: 1fr 1fr; gap: var(--spacing-sm); }
.mesh-status { margin: var(--spacing-compact) 0; color: var(--text-secondary); font-size: var(--font-size-xs); line-height: var(--line-height-base); overflow-wrap: anywhere; }
.mesh-status.is-ready { color: var(--color-success); }
.mesh-status.is-error, .error-message { color: var(--text-danger); }
.mesh-stats { width: 100%; margin-bottom: var(--spacing-md); border-collapse: collapse; font-size: var(--font-size-xs); font-variant-numeric: tabular-nums; }
.mesh-stats th, .mesh-stats td { padding: var(--spacing-xs) 0; text-align: right; font-weight: 400; }
.mesh-stats th:first-child { text-align: left; color: var(--text-secondary); }
.mesh-stats thead { color: var(--text-secondary); border-bottom: 1px solid var(--border-color-light); }
.mesh-stats thead th { padding-bottom: var(--spacing-sm); }
.mesh-stats tbody tr:first-child th, .mesh-stats tbody tr:first-child td { padding-top: var(--spacing-sm); }
.mesh-actions { display: flex; gap: var(--spacing-sm); }
.mesh-actions .primary-button { flex: 1; }
.error-message { margin: var(--spacing-sm) 0 0; overflow-wrap: anywhere; font-size: var(--font-size-xs); }
.section-note { margin: var(--spacing-sm) 0 0; color: var(--text-secondary); font-size: var(--font-size-xs); line-height: var(--line-height-base); }
.toggle-row { min-height: var(--control-height); display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-compact); }
.tool-section > .section-note + .toggle-row { margin-top: var(--spacing-sm); }
.toggle-row :deep(.el-switch) { --el-switch-on-color: var(--color-primary); }
.background-options { display: grid; grid-template-columns: 1fr 1fr; gap: var(--spacing-sm); }
.theme-chip { justify-content: flex-start; }
.theme-swatch { width: 16px; height: 16px; flex: 0 0 auto; border: 1px solid var(--border-color); border-radius: var(--spacing-xs); }
.theme-swatch-gradient { background: #10213b; }
.theme-swatch-deep { background: #0c1224; }
.theme-swatch-light { background: #e8eef6; }
.theme-swatch-black { background: #000; }

.bim-header-tools :deep(.measurement-toolbar) { background: transparent; border-radius: 0; }
.bim-header-tools :deep(.measurement-toggle),
.bim-header-tools :deep(.measurement-action) { min-height: var(--control-height); color: var(--text-secondary); border: 1px solid var(--border-color); border-radius: var(--radius-xs); background: var(--bg-card); font-size: var(--font-size-sm); }
.bim-header-tools :deep(.measurement-toggle) { width: var(--control-height); height: var(--control-height); }
.bim-header-tools :deep(.measurement-toggle-icon) { filter: none; }
.bim-header-tools :deep(.measurement-toggle:hover),
.bim-header-tools :deep(.measurement-action:hover:not(:disabled)),
.bim-header-tools :deep(.measurement-action.is-active) { color: var(--color-primary-active); background: var(--color-primary-soft); border-color: var(--color-primary); }
.bim-header-tools :deep(.measurement-action--clear) { color: var(--text-danger); margin-left: var(--spacing-sm); }
.empty-state { flex: 1; display: grid; place-content: center; padding: var(--spacing-lg); text-align: center; }
.empty-state h2 { font-size: var(--font-size-lg); }
.empty-state p { color: var(--text-secondary); }

@media (max-width: 1600px) {
  .asset-preview-page { --viewer-panel-width: 320px; }
  .bim-file-context { flex-direction: column; align-items: flex-start; gap: 0; }
  .bim-file-context strong, .bim-file-context span { max-width: 100%; }
}
@media (max-width: 1000px) {
  .bim-header-tools { gap: var(--spacing-sm); }
  .toolbar-label { display: none; }
  .bim-header-tools :deep(.measurement-action span) { display: none; }
  .bim-header-tools :deep(.measurement-action) { width: var(--control-height); padding: 0; }
}
@media (max-width: 720px) {
  .bim-preview-header { flex-wrap: wrap; gap: var(--spacing-sm); }
  .bim-file-context { flex-basis: 40%; }
  .bim-header-tools { margin-left: auto; }
  .layout-shell { grid-template-columns: minmax(0, 1fr) min(280px, 48vw); }
  .mesh-actions { flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) {
  .asset-preview-page *, .asset-preview-page :deep(*) { transition: none !important; }
}
</style>
