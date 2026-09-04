<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Aim,
  ArrowLeft,
  ArrowRightBold,
  Close,
  DArrowRight,
  FullScreen,
} from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'
import PointcloudViewCube from '@/components/preview/PointcloudViewCube.vue'
import PointcloudColorRangeBar, {
  type PointcloudColorRamp,
  type PointcloudColorRange,
} from '@/components/preview/PointcloudColorRangeBar.vue'
import type { CameraPose } from '@/components/preview/UnifiedViewer3D.vue'
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
}>()

const router = useRouter()
const bimPanelRef = ref<any>(null)
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

const bimControls = reactive({
  showAxes: true,
  showGrid: true,
  wireframe: false,
  sectionEnabled: false,
})

const pointcloudControls = reactive({
  showAxes: false,
  showGrid: false,
  sectionEnabled: false,
  colorMode: 'intensity' as 'rgb' | 'intensity' | 'original' | 'custom',
  pointColor: DEFAULT_POINT_COLOR,
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

  void router.push('/upload')
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
    panel.setSectionState?.(bimControls.sectionEnabled)
    return
  }

  panel.setShowAxes?.(pointcloudControls.showAxes)
  panel.setShowGrid?.(pointcloudControls.showGrid)
  panel.setSectionState?.(pointcloudControls.sectionEnabled)
  if (pointcloudControls.colorMode === 'custom') {
    panel.setPointColor?.(pointcloudControls.pointColor)
  } else {
    panel.setPointcloudColorDisplay?.(
      pointcloudControls.colorMode === 'intensity' ? 'intensity' : 'rgb',
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
  document.addEventListener('fullscreenchange', syncFullscreenState)
  applyPanelSettings()
  void loadMeasurements()
})

onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', syncFullscreenState)
})

watch(
  () => [props.assetId, props.previewType] as const,
  () => {
    analysisMode.value = 'none'
    pointcloudIntensityHistogram.value = []
    void loadMeasurements()
  },
)
</script>

<template>
  <section v-if="previewType === 'pointcloud'" class="pointcloud-preview-page">
    <header class="pointcloud-preview-header">
      <span class="pointcloud-preview-heading">
        <strong>{{ displayName || '点云预览' }}</strong>
        <small>扫描记录 · 扫描点云</small>
      </span>

      <div class="pointcloud-header-controls" role="group" aria-label="预览背景">
        <span class="pointcloud-header-label">背景</span>
        <div class="pointcloud-segmented">
          <button
            v-for="option in backgroundOptions"
            :key="option.value"
            type="button"
            :class="{ on: backgroundTheme === option.value }"
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
        ref="pointcloudPanelRef"
        class="pointcloud-viewer-panel"
        :asset-id="assetId"
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
                :class="{ on: pointcloudControls.colorMode === 'rgb' }"
                @click="pointcloudControls.colorMode = 'rgb'"
              >
                真彩
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.colorMode === 'intensity' }"
                @click="pointcloudControls.colorMode = 'intensity'"
              >
                强度
              </button>
            </div>
            <div
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
              <span>点</span>
              <input v-model.number="pointcloudSize" type="range" min="1" max="5" step="0.1" />
              <output>{{ pointcloudSize.toFixed(1) }}</output>
            </label>
          </div>

          <div class="pointcloud-display-row">
            <div class="pointcloud-segmented" role="group" aria-label="场景辅助显示">
              <button
                type="button"
                :class="{ on: pointcloudControls.showAxes }"
                @click="pointcloudControls.showAxes = !pointcloudControls.showAxes"
              >
                坐标轴
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.showGrid }"
                @click="pointcloudControls.showGrid = !pointcloudControls.showGrid"
              >
                网格
              </button>
              <button
                type="button"
                :class="{ on: pointcloudControls.sectionEnabled }"
                @click="pointcloudControls.sectionEnabled = !pointcloudControls.sectionEnabled"
              >
                剖切
              </button>
            </div>
          </div>
        </div>
      </div>

      <PointcloudViewCube
        :pose="pointcloudCameraPose"
        @home="resetView"
        @select-direction="setPointcloudViewDirection"
        @orbit="orbitPointcloudFromCube"
        @roll="rollPointcloudView"
      />

      <div class="pointcloud-axes-triad" aria-hidden="true">
        <span class="axis axis-x">X</span>
        <span class="axis axis-y">Y</span>
        <span class="axis axis-z">Z</span>
        <i></i>
      </div>

      <PointcloudColorRangeBar
        v-model:range="pointcloudColorRange"
        class="pointcloud-bottom-color-bar"
        :ramp="pointcloudColorRamp"
        :histogram="pointcloudIntensityHistogram"
      />

      <div class="pointcloud-viewer-status" role="status">
        <i :class="{ loading: !pointcloudLoaded }" aria-hidden="true"></i>
        {{ pointcloudLoaded ? '点云已加载' : '正在加载点云' }}
        <span v-if="pointcloudEdlEnabled">显示增强</span>
      </div>

      <div class="pointcloud-measurement-dock">
        <MeasurementToolbar
          v-model:collapsed="analysisToolbarCollapsed"
          :mode="analysisMode"
          position="static"
          @update:mode="selectAnalysisMode"
          @clear="clearAnalysis"
        />
      </div>
    </main>
  </section>

  <section v-else class="asset-preview-page" :class="`theme-${backgroundTheme}`">
    <div class="floating-controls">
      <button class="floating-btn" type="button" @click="closePage">
        <el-icon><ArrowLeft /></el-icon>
        <span>关闭</span>
      </button>
    </div>
    <MeasurementToolbar
      v-if="assetId"
      v-model:collapsed="analysisToolbarCollapsed"
      :mode="analysisMode"
      @update:mode="selectAnalysisMode"
      @clear="clearAnalysis"
    />

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

      <aside class="sidebar" :class="{ 'is-collapsed': sidebarCollapsed }">
        <el-scrollbar class="sidebar-scrollbar">
          <el-space direction="vertical" fill :size="14" class="sidebar-stack">
            <section class="sidebar-card sidebar-toolbar">
              <div class="card-head">
                <el-tooltip
                  :content="sidebarCollapsed ? '展开工具栏' : '收起工具栏'"
                  placement="left"
                >
                  <el-button
                    class="icon-btn"
                    circle
                    type="default"
                    @click="toggleSidebar"
                  >
                    <el-icon>
                      <ArrowRightBold v-if="sidebarCollapsed" />
                      <DArrowRight v-else />
                    </el-icon>
                  </el-button>
                </el-tooltip>
                <button
                  v-if="!sidebarCollapsed"
                  class="ghost-btn"
                  type="button"
                  @click="resetView"
                >
                  重置
                </button>
              </div>
            </section>

            <div v-show="!sidebarCollapsed" class="sidebar-sections">
              <section class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Environment</p>
                  <h3>背景切换</h3>
                  <p class="section-desc">切换观察环境，快速增强浅色模型和点云轮廓对比。</p>
                </div>
                <div class="option-row">
                  <button
                    v-for="option in backgroundOptions"
                    :key="option.value"
                    class="chip-btn theme-chip"
                    :class="{ 'is-active': backgroundTheme === option.value }"
                    type="button"
                    @click="backgroundTheme = option.value"
                  >
                    <span class="theme-swatch" :class="`theme-swatch-${option.value}`"></span>
                    <span>{{ option.label }}</span>
                  </button>
                </div>
              </section>

              <section class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Scene</p>
                  <h3>辅助显示</h3>
                  <p class="section-desc">保持方向感和尺度感，适合定位模型朝向与地平面。</p>
                </div>
                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>坐标轴</strong>
                    <small>显示更大的 XYZ 方向参考，便于判断朝向。</small>
                  </span>
                  <span class="switch">
                    <input
                      v-if="previewType === 'bim'"
                      v-model="bimControls.showAxes"
                      type="checkbox"
                    />
                    <input
                      v-else
                      v-model="pointcloudControls.showAxes"
                      type="checkbox"
                    />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>网格</strong>
                    <small>铺满视窗的参考地面，便于观察高度和投影关系。</small>
                  </span>
                  <span class="switch">
                    <input
                      v-if="previewType === 'bim'"
                      v-model="bimControls.showGrid"
                      type="checkbox"
                    />
                    <input
                      v-else
                      v-model="pointcloudControls.showGrid"
                      type="checkbox"
                    />
                    <span class="switch-track"></span>
                  </span>
                </label>
              </section>

              <section v-if="previewType === 'bim'" class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Model</p>
                  <h3>BIM 显示</h3>
                  <p class="section-desc">面向结构查看的显示控制，适合查看边界和剖切关系。</p>
                </div>
                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>线框模式</strong>
                    <small>突出结构边界和构件轮廓，便于快速检查层次。</small>
                  </span>
                  <span class="switch">
                    <input v-model="bimControls.wireframe" type="checkbox" />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>剖切启用</strong>
                    <small>开启后可直接拖拽场景中的 6 个方向箭头，按方向裁切模型。</small>
                  </span>
                  <span class="switch">
                    <input v-model="bimControls.sectionEnabled" type="checkbox" />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <div class="range-row" :class="{ 'is-disabled': !bimControls.sectionEnabled }">
                  <div class="range-head">
                    <span>交互说明</span>
                    <strong>{{ bimControls.sectionEnabled ? '已启用' : '未启用' }}</strong>
                  </div>
                  <p class="section-desc section-desc--inline">
                    与校准页一致，模型外侧会显示 6 个箭头和包围盒，可拖拽任一方向进行剖切。
                  </p>
                </div>
              </section>

              <section v-else class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Point Cloud</p>
                  <h3>点云显示</h3>
                  <p class="section-desc">默认保留后端原始颜色，也支持与 BIM 一致的 6 向剖切交互。</p>
                </div>

                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>剖切启用</strong>
                    <small>开启后会显示 6 个方向箭头，可直接拖拽裁切点云范围。</small>
                  </span>
                  <span class="switch">
                    <input v-model="pointcloudControls.sectionEnabled" type="checkbox" />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <div class="range-row" :class="{ 'is-disabled': !pointcloudControls.sectionEnabled }">
                  <div class="range-head">
                    <span>交互说明</span>
                    <strong>{{ pointcloudControls.sectionEnabled ? '已启用' : '未启用' }}</strong>
                  </div>
                  <p class="section-desc section-desc--inline">
                    启用后，点云外侧会出现包围盒和 6 个方向箭头，可像校准页一样拖拽任一方向进行剖切。
                  </p>
                </div>

                <div class="option-row">
                  <button
                    class="chip-btn"
                    :class="{ 'is-active': pointcloudControls.colorMode === 'original' }"
                    type="button"
                    @click="pointcloudControls.colorMode = 'original'"
                  >
                    原始颜色
                  </button>
                  <button
                    class="chip-btn"
                    :class="{ 'is-active': pointcloudControls.colorMode === 'custom' }"
                    type="button"
                    @click="pointcloudControls.colorMode = 'custom'"
                  >
                    自定义颜色
                  </button>
                </div>

                <div class="color-block" :class="{ 'is-disabled': pointcloudControls.colorMode !== 'custom' }">
                  <div class="range-head">
                    <span>覆盖颜色</span>
                    <strong>{{ pointcloudControls.pointColor.toUpperCase() }}</strong>
                  </div>
                  <div class="color-row">
                    <input
                      v-model="pointcloudControls.pointColor"
                      class="color-input"
                      type="color"
                      :disabled="pointcloudControls.colorMode !== 'custom'"
                    />
                  </div>
                  <div class="option-row">
                    <button
                      v-for="preset in pointColorPresets"
                      :key="preset.value"
                      class="chip-btn color-chip"
                      :class="{ 'is-active': pointcloudControls.pointColor === preset.value && pointcloudControls.colorMode === 'custom' }"
                      type="button"
                      @click="applyPointColorPreset(preset.value)"
                    >
                      <span class="preset-swatch" :style="{ background: preset.value }"></span>
                      <span>{{ preset.label }}</span>
                    </button>
                  </div>
                </div>
              </section>
            </div>
          </el-space>
        </el-scrollbar>
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
  font-size: 10px;
  line-height: 14px;
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
  font-size: 11px;
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
  font-size: 11px;
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

.pointcloud-axes-triad {
  position: absolute;
  z-index: 25;
  left: 24px;
  bottom: 24px;
  width: 88px;
  height: 88px;
  pointer-events: none;
}

.pointcloud-axes-triad i {
  position: absolute;
  left: 42px;
  bottom: 40px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #dbe5f8;
}

.pointcloud-axes-triad .axis {
  position: absolute;
  left: 44px;
  bottom: 42px;
  width: 34px;
  height: 2px;
  transform-origin: left center;
  font-size: 10px;
  font-weight: 700;
}

.pointcloud-axes-triad .axis::before {
  content: '';
  position: absolute;
  inset: 0;
  background: currentColor;
}

.pointcloud-axes-triad .axis::after {
  content: '';
  position: absolute;
  right: -1px;
  top: -3px;
  border-left: 6px solid currentColor;
  border-top: 4px solid transparent;
  border-bottom: 4px solid transparent;
}

.pointcloud-axes-triad .axis-x {
  color: #f06969;
  transform: rotate(12deg);
}

.pointcloud-axes-triad .axis-y {
  color: #6ecb8b;
  transform: rotate(-108deg);
}

.pointcloud-axes-triad .axis-z {
  color: #72a8f2;
  transform: rotate(142deg);
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

.pointcloud-measurement-dock {
  position: absolute;
  z-index: 80;
  top: 16px;
  right: 116px;
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
  min-height: 100vh;
  padding: 18px;
  display: flex;
  flex-direction: column;
}

.asset-preview-page.theme-gradient {
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 22%),
    radial-gradient(circle at 15% 20%, rgba(59, 130, 246, 0.12), transparent 24%),
    linear-gradient(180deg, #06111f 0%, #09172a 42%, #070d18 100%);
}

.asset-preview-page.theme-deep {
  background: linear-gradient(180deg, #081221 0%, #0d1b2f 52%, #09111d 100%);
}

.asset-preview-page.theme-light {
  background: linear-gradient(180deg, #eef4fb 0%, #dde7f2 100%);
}

.asset-preview-page.theme-black {
  background: #000;
}

.floating-controls {
  position: fixed;
  top: 18px;
  left: 18px;
  z-index: 20;
  display: flex;
  gap: 10px;
}

.floating-btn,
.ghost-btn,
.chip-btn {

  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease,
    background-color 0.2s ease;
}

.floating-btn {
  height: 42px;
  padding: 0 16px;
  border: 0;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: #d8f3ff;
  font-size: 13px;
  letter-spacing: 0.04em;
  backdrop-filter: blur(18px);
  background:
    linear-gradient(180deg, rgba(10, 20, 38, 0.82), rgba(7, 14, 28, 0.64));
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.18),
    0 0 22px rgba(56, 189, 248, 0.12),
    0 16px 32px rgba(2, 6, 23, 0.34);
}

.floating-btn:hover,
.ghost-btn:hover,
.chip-btn:hover {
  transform: translateY(-1px);
}

.layout-shell,
.empty-state {
  flex: 1;
  margin-top: 54px;
}

.layout-shell {
  min-height: calc(100vh - 90px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 368px;
  gap: 20px;
  transition: grid-template-columns 0.24s ease;
}

.layout-shell.is-sidebar-collapsed {
  grid-template-columns: minmax(0, 1fr) 82px;
}

.viewer-region {
  min-height: calc(100vh - 90px);
  border-radius: 28px;
  overflow: hidden;
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.12),
    0 22px 60px rgba(2, 6, 23, 0.34);
}

.viewer-region.theme-gradient {
  background:
    radial-gradient(circle at 15% 15%, rgba(34, 211, 238, 0.18), transparent 22%),
    linear-gradient(180deg, #081425 0%, #11213a 100%);
}

.viewer-region.theme-deep {
  background: linear-gradient(180deg, #07111f 0%, #0c1728 100%);
}

.viewer-region.theme-light {
  background: linear-gradient(180deg, #f8fbff 0%, #e8eef6 100%);
}

.viewer-region.theme-black {
  background: #000;
}

.viewer-region.theme-gradient :deep(.preview-panel),
.viewer-region.theme-deep :deep(.preview-panel),
.viewer-region.theme-light :deep(.preview-panel),
.viewer-region.theme-black :deep(.preview-panel) {
  min-height: calc(100vh - 90px);
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.viewer-region.theme-gradient :deep(.preview-panel),
.viewer-region.theme-gradient :deep(.preview-panel.is-minimal) {
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 26%),
    linear-gradient(180deg, #071323 0%, #12233d 100%);
}

.viewer-region.theme-deep :deep(.preview-panel),
.viewer-region.theme-deep :deep(.preview-panel.is-minimal) {
  background: linear-gradient(180deg, #07111f 0%, #0c1728 100%);
}

.viewer-region.theme-light :deep(.preview-panel),
.viewer-region.theme-light :deep(.preview-panel.is-minimal) {
  background: linear-gradient(180deg, #f8fbff 0%, #e8eef6 100%);
}

.viewer-region.theme-black :deep(.preview-panel),
.viewer-region.theme-black :deep(.preview-panel.is-minimal) {
  background: #000;
}

.viewer-region.theme-light :deep(.panel-chip),
.viewer-region.theme-light :deep(.panel-title),
.viewer-region.theme-light :deep(.panel-status) {
  color: #0f172a;
}

.viewer-panel {
  height: 100%;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 90px);
  overflow: auto;
  padding-right: 4px;
}

.sidebar.is-collapsed {
  gap: 0;
}

.sidebar-scrollbar {
  height: 100%;
}

.sidebar-stack {
  width: 100%;
}

.sidebar-stack :deep(.el-space__item) {
  width: 100%;
}

.sidebar.is-collapsed .sidebar-scrollbar :deep(.el-scrollbar__view) {
  display: flex;
  justify-content: center;
}

.sidebar-sections {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sidebar-card {
  position: relative;
  padding: 18px 18px 20px;
  border-radius: 24px;
  color: #e2e8f0;
  background:
    linear-gradient(180deg, rgba(10, 18, 32, 0.96), rgba(6, 12, 22, 0.88));
  backdrop-filter: blur(22px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),
    inset 0 0 0 1px rgba(148, 163, 184, 0.14),
    0 20px 48px rgba(2, 6, 23, 0.28);
}

.asset-preview-page.theme-light .sidebar-card {
  color: #0f172a;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(244, 248, 252, 0.9));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    inset 0 0 0 1px rgba(148, 163, 184, 0.16),
    0 24px 54px rgba(15, 23, 42, 0.12);
}

.sidebar-toolbar {
  padding: 10px 12px;
}

.sidebar-toolbar::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background:
    radial-gradient(circle at top right, rgba(34, 211, 238, 0.18), transparent 34%),
    linear-gradient(135deg, rgba(14, 165, 233, 0.08), transparent 58%);
}

.card-heading,
.toggle-row,
.range-row,
.color-block {
  position: relative;
  z-index: 1;
}

.card-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  min-height: 0;
}

.sidebar.is-collapsed .card-head {
  justify-content: center;
}

.card-head h2,
.sidebar-card h3 {
  margin: 0;
}

.card-eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #67e8f9;
}
.section-kicker,
.range-head span {
  font-size: 12px;
  color: #94a3b8;
}

.asset-preview-page.theme-light .section-kicker,
.asset-preview-page.theme-light .range-head span {
  color: #64748b;
}

.range-head strong {
  font-size: 14px;
}

.card-heading {
  margin-bottom: 14px;
}

.section-kicker {
  margin: 0 0 8px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.section-desc {
  margin: 8px 0 0;
  line-height: 1.6;
  font-size: 13px;
  color: #8ea0b7;
}

.section-desc--inline {
  margin-top: 0;
}

.asset-preview-page.theme-light .section-desc {
  color: #5f7085;
}

.ghost-btn {
  width: 30%;
  height: 34px;
  padding: 0 14px;
  border: 1px solid rgba(103, 232, 249, 0.22);
  border-radius: 999px;
  color: inherit;
  background:
    linear-gradient(180deg, rgba(34, 211, 238, 0.18), rgba(56, 189, 248, 0.08));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 10px 24px rgba(8, 47, 73, 0.18);
  font-size: 12px;
  letter-spacing: 0.04em;
}

.analysis-action.is-active {
  border-color: rgba(248, 113, 113, 0.72);
  background: rgba(220, 38, 38, 0.24);
  color: #fecaca;
}

.asset-preview-page.theme-light .ghost-btn {
  background: rgba(255, 255, 255, 0.88);
}

.icon-btn {
  width: 34px;
  height: 34px;
  min-height: 34px;
  padding: 0;
  border: 1px solid rgba(148, 163, 184, 0.2);
  color: #d8f3ff;
  background: rgba(15, 23, 42, 0.22);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),
    0 8px 20px rgba(15, 23, 42, 0.14);
}

.icon-btn:hover,
.icon-btn:focus-visible {
  color: #d8f3ff;
  border-color: rgba(103, 232, 249, 0.22);
  background: rgba(21, 34, 54, 0.42);
}

.asset-preview-page.theme-light .icon-btn {
  color: #0f172a;
  background: rgba(241, 245, 249, 0.96);
}

.asset-preview-page.theme-light .icon-btn:hover,
.asset-preview-page.theme-light .icon-btn:focus-visible {
  color: #0f172a;
  background: rgba(255, 255, 255, 0.98);
}

.icon-btn :deep(.el-icon) {
  font-size: 14px;
}

.option-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.chip-btn {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 16px;
  color: inherit;
  background: rgba(15, 23, 42, 0.24);
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.chip-btn.is-active {
  border-color: rgba(34, 211, 238, 0.4);
  background: rgba(34, 211, 238, 0.14);
  box-shadow: inset 0 0 0 1px rgba(34, 211, 238, 0.16);
}

.theme-chip {
  flex: 1 1 calc(50% - 4px);
  justify-content: flex-start;
}

.theme-swatch,
.preset-swatch {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  flex: 0 0 auto;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
}

.theme-swatch-gradient {
  background: linear-gradient(135deg, #0f172a 15%, #0ea5e9 100%);
}

.theme-swatch-deep {
  background: linear-gradient(135deg, #081221 0%, #163256 100%);
}

.theme-swatch-light {
  background: linear-gradient(135deg, #f8fbff 0%, #cbd5e1 100%);
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.5);
}

.theme-swatch-black {
  background: #000;
}

.toggle-row {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 14px 14px 16px;
  border-radius: 18px;
  background: rgba(15, 23, 42, 0.22);
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.1);
}

.asset-preview-page.theme-light .toggle-row {
  background: rgba(248, 250, 252, 0.82);
}

.toggle-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.toggle-copy strong {
  font-size: 14px;
  font-weight: 600;
}

.toggle-copy small {
  line-height: 1.55;
  color: #8ea0b7;
}

.asset-preview-page.theme-light .toggle-copy small {
  color: #5f7085;
}

.switch {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
}

.switch input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.switch-track {
  width: 50px;
  height: 30px;
  border-radius: 999px;
  background: rgba(51, 65, 85, 0.92);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.16),
    inset 0 10px 18px rgba(15, 23, 42, 0.18);
  transition: background-color 0.2s ease;
}

.switch-track::after {
  content: '';
  position: absolute;
  top: 4px;
  left: 4px;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: linear-gradient(180deg, #f8fafc 0%, #dbe6f2 100%);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.24);
  transition: transform 0.2s ease;
}

.switch input:checked + .switch-track {
  background: linear-gradient(135deg, #0891b2 0%, #22d3ee 100%);
}

.switch input:checked + .switch-track::after {
  transform: translateX(20px);
}

.range-row,
.color-block {
  margin-top: 14px;
  padding: 14px 16px 16px;
  border-radius: 18px;
  background: rgba(15, 23, 42, 0.22);
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.1);
}

.asset-preview-page.theme-light .range-row,
.asset-preview-page.theme-light .color-block {
  background: rgba(248, 250, 252, 0.82);
}

.range-row.is-disabled {
  opacity: 0.5;
}

.range-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.range-row input[type='range'] {
  width: 100%;
  accent-color: #22d3ee;
  cursor: pointer;
}

.color-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(8, 15, 30, 0.36);
}

.asset-preview-page.theme-light .color-row {
  background: rgba(255, 255, 255, 0.9);
}

.color-input {
  width: 56px;
  height: 36px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  border-radius: 28px;
  text-align: center;
  color: #cbd5e1;
  background: rgba(8, 15, 30, 0.52);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.12),
    0 18px 48px rgba(2, 6, 23, 0.34);
}

.empty-state h2 {
  margin: 0 0 12px;
  font-size: 1.4rem;
  color: #f8fafc;
}

.empty-state p {
  margin: 0;
  color: #94a3b8;
}

@media (max-width: 1180px) {
  .layout-shell {
    grid-template-columns: 1fr;
  }

  .layout-shell.is-sidebar-collapsed {
    grid-template-columns: 1fr;
  }

  .sidebar {
    position: static;
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .viewer-region {
    min-height: 60vh;
  }
}
</style>
