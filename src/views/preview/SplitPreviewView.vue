<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  ArrowLeft,
  Connection,
  Fold,
  Menu,
  Refresh,
  RefreshRight,
  View,
} from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'
import C2MResultPreviewPanel from '@/components/preview/C2MResultPreviewPanel.vue'
import ViewerAnalysisOverlay, {
  type AnalysisDistance,
  type AnalysisMode,
  type AnalysisPoint,
} from '@/components/preview/ViewerAnalysisOverlay.vue'
import { getBimAlignment, type BimAlignmentResult } from '@/api/backend-alignment'
import * as THREE from 'three'

type CameraPose = {
  camera: any
  target: any
}

const props = defineProps<{
  bimAssetId: number | null
  pointcloudAssetId: number | null
  bimDisplayName?: string
  pointcloudDisplayName?: string
}>()

const router = useRouter()
const isReady = computed(() => {
  return !!props.bimAssetId && !!props.pointcloudAssetId
})

// 与四分屏一致：默认由两个视图各自控制，点击联动后才同步旋转增量。
const syncActive = ref(false)
const bimLoaded = ref(false)
const pointcloudLoaded = ref(false)
const pointcloudPanelRef = ref<any>(null)
const bimPanelRef = ref<any>(null)
const consistencyPanelRef = ref<any>(null)
const applyingViewSync = ref(false)
const toolsExpanded = ref(true)
const analysisMode = ref<AnalysisMode>('none')
const analysisPoint = ref<AnalysisPoint | null>(null)
const analysisDistance = ref<AnalysisDistance | null>(null)
type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'
const splitBackgrounds = reactive<Record<SyncSource, PreviewBackgroundTheme>>({
  bim: 'deep',
  pointcloud: 'deep',
  consistency: 'deep',
})
const splitBackgroundColors = reactive<Record<SyncSource, string>>({
  bim: '#08111d',
  pointcloud: '#08111d',
  consistency: '#08111d',
})
const pointcloudColorMode = ref<'original' | 'custom'>('custom')
const pointcloudColor = ref('#86898D')
const edlEnabled = ref(true)
const edlStrength = ref(1.0)
const viewVisibility = reactive({
  bim: true,
  pointcloud: true,
  consistency: true,
})
const calibration = ref<BimAlignmentResult | null>(null)
const bimWorldPose = ref<{
  position: THREE.Vector3
  quaternion: THREE.Quaternion
  scale: THREE.Vector3
} | null>(null)
// Do not mount either renderer before the saved alignment lookup completes.
// This matches the reference page, which loads calibration before creating
// its BIM/point-cloud viewers and avoids an uncalibrated first frame winning
// a race with the async model loader.
const calibrationReady = ref(false)

let calibrationRequestId = 0
async function loadCalibration() {
  const bimId = props.bimAssetId
  const pointcloudId = props.pointcloudAssetId
  const requestId = ++calibrationRequestId
  calibration.value = null
  calibrationReady.value = false
  if (!bimId || !pointcloudId) {
    calibrationReady.value = true
    return
  }

  try {
    const response = await getBimAlignment({
      modelScanFileId: pointcloudId,
      modelBimFileId: bimId,
    })
    if (requestId === calibrationRequestId) calibration.value = response.data
  } catch (error) {
    if (requestId === calibrationRequestId) {
      console.info('[SplitPreview] 当前组合暂无已保存校准矩阵', error)
    }
  } finally {
    if (requestId === calibrationRequestId) calibrationReady.value = true
  }
}

type Rotation = { lon: number; lat: number }
type SyncSource = 'bim' | 'pointcloud' | 'consistency'

const rotationBases: Record<SyncSource, Rotation | null> = {
  bim: null,
  pointcloud: null,
  consistency: null,
}
const lastBroadcastRotations: Record<SyncSource, Rotation | null> = {
  bim: null,
  pointcloud: null,
  consistency: null,
}
const distanceBases: Record<SyncSource, number | null> = {
  bim: null,
  pointcloud: null,
  consistency: null,
}
const lastBroadcastDistances: Record<SyncSource, number | null> = {
  bim: null,
  pointcloud: null,
  consistency: null,
}
const poseBases: Record<SyncSource, CameraPose | null> = {
  bim: null,
  pointcloud: null,
  consistency: null,
}
const lastBroadcastPoses: Record<SyncSource, CameraPose | null> = {
  bim: null,
  pointcloud: null,
  consistency: null,
}

function clampLatitude(value: number) {
  return Math.max(-85, Math.min(85, value))
}

function normalizeLongitude(value: number) {
  const wrapped = ((((value + 180) % 360) + 360) % 360) - 180
  return Object.is(wrapped, -0) ? 0 : wrapped
}

function normalizeRotation(rotation: Rotation): Rotation {
  return {
    lon: Number(normalizeLongitude(rotation.lon).toFixed(6)),
    lat: Number(clampLatitude(rotation.lat).toFixed(6)),
  }
}

function getPanel(source: SyncSource) {
  if (source === 'bim') return bimPanelRef.value
  if (source === 'pointcloud') return pointcloudPanelRef.value
  return consistencyPanelRef.value
}

function isSameRotation(
  first: Rotation | null | undefined,
  second: Rotation | null | undefined,
  epsilon = 1e-6,
) {
  if (!first || !second) return false
  return (
    Math.abs(first.lon - second.lon) <= epsilon &&
    Math.abs(first.lat - second.lat) <= epsilon
  )
}

function getCurrentRotation(source: SyncSource): Rotation | null {
  const panel = getPanel(source)
  const rotation = panel?.getCameraOrientation?.() as Rotation | null
  return rotation ? normalizeRotation(rotation) : null
}

function clonePose(pose: CameraPose | null): CameraPose | null {
  if (!pose?.camera || !pose.target) return null
  return {
    camera: pose.camera.clone(),
    target: pose.target.clone(),
  }
}

function getCurrentPose(source: SyncSource): CameraPose | null {
  const panel = getPanel(source)
  return clonePose((panel?.getCameraPose?.() as CameraPose | null) ?? null)
}

function isSamePose(first: CameraPose | null, second: CameraPose | null, epsilon = 1e-5) {
  if (!first || !second) return false
  return first.camera.distanceToSquared(second.camera) <= epsilon ** 2 &&
    first.target.distanceToSquared(second.target) <= epsilon ** 2
}

function normalizeDistance(distance: number) {
  return Number(Math.max(distance, 0.01).toFixed(6))
}

function isSameDistance(
  first: number | null | undefined,
  second: number | null | undefined,
) {
  if (!first || !second) return false
  const epsilon = Math.max(1e-6, Math.max(first, second) * 1e-6)
  return Math.abs(first - second) <= epsilon
}

function getCurrentDistance(source: SyncSource): number | null {
  const panel = getPanel(source)
  const distance = panel?.getCameraDistance?.()
  return Number.isFinite(distance) && distance > 0 ? normalizeDistance(distance) : null
}

function captureSyncBases() {
  ;(['bim', 'pointcloud', 'consistency'] as SyncSource[]).forEach((source) => {
    rotationBases[source] = getCurrentRotation(source)
    lastBroadcastRotations[source] = rotationBases[source]
    distanceBases[source] = getCurrentDistance(source)
    lastBroadcastDistances[source] = distanceBases[source]
    poseBases[source] = getCurrentPose(source)
    lastBroadcastPoses[source] = clonePose(poseBases[source])
  })
}

function clearRotationSyncState() {
  ;(['bim', 'pointcloud', 'consistency'] as SyncSource[]).forEach((source) => {
    rotationBases[source] = null
    lastBroadcastRotations[source] = null
    distanceBases[source] = null
    lastBroadcastDistances[source] = null
    poseBases[source] = null
    lastBroadcastPoses[source] = null
  })
}

function buildTargetPose(source: SyncSource, target: SyncSource, sourcePose: CameraPose) {
  const sourceBase = poseBases[source]
  const targetBase = poseBases[target]
  if (!sourceBase || !targetBase) return clonePose(sourcePose)

  const cameraDelta = sourcePose.camera.clone().sub(sourceBase.camera)
  const targetDelta = sourcePose.target.clone().sub(sourceBase.target)
  return {
    camera: targetBase.camera.clone().add(cameraDelta),
    target: targetBase.target.clone().add(targetDelta),
  }
}

function buildTargetRotation(
  source: SyncSource,
  target: SyncSource,
  sourceRotation: Rotation,
) {
  const normalizedSource = normalizeRotation(sourceRotation)
  const sourceBase = rotationBases[source]
  const targetBase = rotationBases[target]
  if (!sourceBase || !targetBase) return normalizedSource

  const deltaLongitude = normalizeLongitude(normalizedSource.lon - sourceBase.lon)
  const deltaLatitude = normalizedSource.lat - sourceBase.lat
  return normalizeRotation({
    lon: targetBase.lon + deltaLongitude,
    lat: targetBase.lat + deltaLatitude,
  })
}

const canSync = computed(() => {
  return isReady.value && bimLoaded.value && pointcloudLoaded.value
})
const visibleViewCount = computed(
  () => Number(viewVisibility.bim) + Number(viewVisibility.pointcloud) + Number(viewVisibility.consistency),
)

function closePage() {
  if (window.opener) {
    window.close()
    return
  }

  void router.push('/upload')
}

function syncRotation(source: SyncSource, rotation: Rotation | null) {
  if (!syncActive.value || !canSync.value || applyingViewSync.value || !rotation) {
    return
  }

  const sourceRotation = normalizeRotation(rotation)
  if (isSameRotation(lastBroadcastRotations[source], sourceRotation)) return
  if (!rotationBases[source]) {
    captureSyncBases()
  }
  lastBroadcastRotations[source] = sourceRotation

  applyingViewSync.value = true
  try {
    ;(['bim', 'pointcloud', 'consistency'] as SyncSource[]).filter((target) => target !== source).forEach((target) => {
      const targetPanel = getPanel(target)
      if (!targetPanel) return
      const targetRotation = buildTargetRotation(source, target, sourceRotation)
      lastBroadcastRotations[target] = targetRotation
      targetPanel.syncFromRotation?.(targetRotation)
    })
  } finally {
    applyingViewSync.value = false
  }
}

function syncZoom(source: SyncSource, distance: number | null) {
  if (!syncActive.value || !canSync.value || applyingViewSync.value || !distance) {
    return
  }

  const sourceDistance = normalizeDistance(distance)
  if (isSameDistance(lastBroadcastDistances[source], sourceDistance)) return
  if (!distanceBases[source]) {
    captureSyncBases()
  }
  lastBroadcastDistances[source] = sourceDistance

  const sourceBase = distanceBases[source]
  if (!sourceBase) return

  const zoomRatio = sourceDistance / sourceBase
  if (!Number.isFinite(zoomRatio) || zoomRatio <= 0) return

  applyingViewSync.value = true
  try {
    ;(['bim', 'pointcloud', 'consistency'] as SyncSource[]).filter((target) => target !== source).forEach((target) => {
      const targetBase = distanceBases[target]
      const targetPanel = getPanel(target)
      if (!targetBase || !targetPanel) return
      const targetDistance = normalizeDistance(targetBase * zoomRatio)
      lastBroadcastDistances[target] = targetDistance
      targetPanel.syncFromCameraDistance?.(targetDistance)
    })
  } finally {
    applyingViewSync.value = false
  }
}

function syncPose(source: SyncSource, pose: CameraPose | null) {
  if (!syncActive.value || !canSync.value || applyingViewSync.value || !pose) return false
  const sourcePose = clonePose(pose)
  if (!sourcePose || isSamePose(lastBroadcastPoses[source], sourcePose)) return false
  if (!poseBases[source]) captureSyncBases()
  lastBroadcastPoses[source] = sourcePose

  applyingViewSync.value = true
  try {
    ;(['bim', 'pointcloud', 'consistency'] as SyncSource[]).filter((target) => target !== source).forEach((target) => {
      const targetPanel = getPanel(target)
      const targetPose = buildTargetPose(source, target, sourcePose)
      if (!targetPanel || !targetPose) return
      lastBroadcastPoses[target] = targetPose
      targetPanel.syncFromExternalPose?.(targetPose)
    })
  } finally {
    applyingViewSync.value = false
  }
  return true
}

function handleSync() {
  syncActive.value = !syncActive.value
  if (syncActive.value) {
    captureSyncBases()
  } else {
    clearRotationSyncState()
  }
}

function handleResetView() {
  applyingViewSync.value = true
  try {
    bimPanelRef.value?.resetView?.()
    pointcloudPanelRef.value?.resetPointcloudView?.()
    consistencyPanelRef.value?.resetView?.()
  } finally {
    applyingViewSync.value = false
  }
  requestAnimationFrame(syncConsistencyInitialPose)
  if (syncActive.value) requestAnimationFrame(captureSyncBases)
}

function handleReload() {
  applyingViewSync.value = true
  clearRotationSyncState()
  bimLoaded.value = false
  pointcloudLoaded.value = false
  bimWorldPose.value = null
  try {
    bimPanelRef.value?.reload?.()
    pointcloudPanelRef.value?.reload?.()
    consistencyPanelRef.value?.reload?.()
  } finally {
    applyingViewSync.value = false
  }
}

function toggleViewVisibility(view: 'bim' | 'pointcloud' | 'consistency') {
  if (viewVisibility[view] && visibleViewCount.value === 1) return
  viewVisibility[view] = !viewVisibility[view]
}

function handleBimLoadedChange(value: boolean) {
  bimLoaded.value = value
  if (value) {
    bimWorldPose.value = bimPanelRef.value?.getModelWorldPose?.() ?? null
    requestAnimationFrame(() => {
      // C2M may have completed before BIM; propagate the pose once it exists.
      consistencyPanelRef.value?.applyBimWorldPose?.()
      // Re-apply the point-cloud camera after the companion mesh receives its
      // final calibrated world position. This keeps the result centered even
      // when the C2M download wins the loading race.
      syncConsistencyInitialPose()
    })
  } else {
    bimWorldPose.value = null
  }
  if (value && syncActive.value && canSync.value) requestAnimationFrame(captureSyncBases)
}

function handlePointcloudLoadedChange(value: boolean) {
  pointcloudLoaded.value = value
  if (value) requestAnimationFrame(syncConsistencyInitialPose)
  if (value && syncActive.value && canSync.value) requestAnimationFrame(captureSyncBases)
}

function handleConsistencyLoadedChange(value: boolean) {
  if (value) requestAnimationFrame(syncConsistencyInitialPose)
  if (value && syncActive.value) requestAnimationFrame(captureSyncBases)
}

function syncConsistencyInitialPose() {
  const pose = pointcloudPanelRef.value?.getCameraPose?.() as CameraPose | null
  const panel = consistencyPanelRef.value
  if (!pose || !panel?.syncFromExternalPose) return
  applyingViewSync.value = true
  try {
    panel.syncInitialViewFromExternalPose?.(clonePose(pose))
    if (!panel.syncInitialViewFromExternalPose) {
      panel.syncFromExternalPose(clonePose(pose))
    }
  } finally {
    applyingViewSync.value = false
  }
}

function handleBimCameraChange(_pose: CameraPose | null) {
  if (applyingViewSync.value) return
  if (syncPose('bim', _pose)) return
  syncRotation('bim', getCurrentRotation('bim'))
  syncZoom('bim', getCurrentDistance('bim'))
}

function handlePointcloudCameraChange(_pose: CameraPose | null) {
  if (applyingViewSync.value) return
  if (syncPose('pointcloud', _pose)) return
  syncRotation('pointcloud', getCurrentRotation('pointcloud'))
  syncZoom('pointcloud', getCurrentDistance('pointcloud'))
}

function handleConsistencyCameraChange(_pose: CameraPose | null) {
  if (applyingViewSync.value) return
  if (syncPose('consistency', _pose)) return
  syncRotation('consistency', getCurrentRotation('consistency'))
  syncZoom('consistency', getCurrentDistance('consistency'))
}

function selectAnalysisMode(mode: AnalysisMode) {
  analysisMode.value = analysisMode.value === mode ? 'none' : mode
  analysisPoint.value = null
  analysisDistance.value = null
  bimPanelRef.value?.clearAnalysis?.()
  pointcloudPanelRef.value?.clearAnalysis?.()
}

function clearAnalysis() {
  analysisMode.value = 'none'
  analysisPoint.value = null
  analysisDistance.value = null
  bimPanelRef.value?.clearAnalysis?.()
  pointcloudPanelRef.value?.clearAnalysis?.()
}

function applySplitPresentation() {
  bimPanelRef.value?.setBackgroundTheme?.(splitBackgrounds.bim)
  pointcloudPanelRef.value?.setBackgroundTheme?.(splitBackgrounds.pointcloud)
  consistencyPanelRef.value?.setBackgroundTheme?.(splitBackgrounds.consistency)
  bimPanelRef.value?.setBackgroundColor?.(splitBackgroundColors.bim)
  pointcloudPanelRef.value?.setBackgroundColor?.(splitBackgroundColors.pointcloud)
  consistencyPanelRef.value?.setBackgroundColor?.(splitBackgroundColors.consistency)
  pointcloudPanelRef.value?.setPointColor?.(
    pointcloudColorMode.value === 'custom' ? pointcloudColor.value : null,
  )
  pointcloudPanelRef.value?.setEdlEnabled?.(edlEnabled.value)
  pointcloudPanelRef.value?.setEdlStrength?.(edlStrength.value)
}

function syncBackgroundColorFromTheme(source: SyncSource) {
  const colors: Record<PreviewBackgroundTheme, string> = {
    deep: '#08111d',
    light: '#f7fbff',
    black: '#000000',
    gradient: '#17365f',
  }
  splitBackgroundColors[source] = colors[splitBackgrounds[source]]
}

function clearScreen(source: SyncSource) {
  if (source === 'bim') bimPanelRef.value?.clearAnalysis?.()
  if (source === 'pointcloud') pointcloudPanelRef.value?.clearAnalysis?.()
  if (source === 'consistency') consistencyPanelRef.value?.clearResult?.()
}

onMounted(() => {
  void loadCalibration()
  requestAnimationFrame(applySplitPresentation)
})

watch(
  () => [props.bimAssetId, props.pointcloudAssetId] as const,
  () => {
    clearRotationSyncState()
    void loadCalibration()
    requestAnimationFrame(applySplitPresentation)
  },
)

watch(
  [
    () => splitBackgrounds.bim,
    () => splitBackgrounds.pointcloud,
    () => splitBackgrounds.consistency,
    () => splitBackgroundColors.bim,
    () => splitBackgroundColors.pointcloud,
    () => splitBackgroundColors.consistency,
    pointcloudColorMode,
    pointcloudColor,
    edlEnabled,
    edlStrength,
    bimPanelRef,
    pointcloudPanelRef,
    consistencyPanelRef,
  ],
  applySplitPresentation,
)
</script>

<template>
  <section class="split-preview-page">
    <div v-if="isReady" class="floating-controls" :class="{ 'is-collapsed': !toolsExpanded }">
      <button
        class="tools-toggle"
        type="button"
        :aria-expanded="toolsExpanded"
        :title="toolsExpanded ? '收起工具' : '展开工具'"
        @click="toolsExpanded = !toolsExpanded"
      >
        <el-icon><component :is="toolsExpanded ? Fold : Menu" /></el-icon>
      </button>

      <div v-if="toolsExpanded" class="tools-panel">
        <button class="floating-btn" type="button" title="返回上传页" @click="closePage">
          <el-icon><ArrowLeft /></el-icon>
          <span>返回</span>
        </button>

        <button class="floating-btn" type="button" title="重置三个视图" @click="handleResetView">
          <el-icon><RefreshRight /></el-icon>
          <span>重置</span>
        </button>

        <button class="floating-btn" type="button" title="重新加载三个视图" @click="handleReload">
          <el-icon><Refresh /></el-icon>
          <span>重新加载</span>
        </button>

        <button
          class="floating-btn is-sync"
          :class="{ 'is-active': syncActive }"
          type="button"
          :aria-pressed="syncActive"
          :disabled="!canSync"
          :title="canSync ? (syncActive ? '关闭视角联动' : '开启视角联动') : '等待两个视图加载完成'"
          @click="handleSync"
        >
          <el-icon><Connection /></el-icon>
          <span>同步</span>
        </button>

        <button
          class="floating-btn layer-btn"
          :class="{ 'is-active': analysisMode === 'distance' }"
          type="button"
          title="全局测距"
          @click="selectAnalysisMode('distance')"
        >测距</button>
        <button
          class="floating-btn layer-btn"
          :class="{ 'is-active': analysisMode === 'locate' }"
          type="button"
          title="全局定位"
          @click="selectAnalysisMode('locate')"
        >定位</button>

        <span class="tools-divider" aria-hidden="true" />

        <button
          class="floating-btn layer-btn"
          :class="{ 'is-active': viewVisibility.bim }"
          type="button"
          :aria-pressed="viewVisibility.bim"
          :title="viewVisibility.bim ? '隐藏 BIM 模型' : '显示 BIM 模型'"
          @click="toggleViewVisibility('bim')"
        >
          <el-icon><View /></el-icon>
          <span>模型</span>
        </button>

        <button
          class="floating-btn layer-btn"
          :class="{ 'is-active': viewVisibility.pointcloud }"
          type="button"
          :aria-pressed="viewVisibility.pointcloud"
          :title="viewVisibility.pointcloud ? '隐藏点云' : '显示点云'"
          @click="toggleViewVisibility('pointcloud')"
        >
          <el-icon><View /></el-icon>
          <span>点云</span>
        </button>

        <button
          class="floating-btn layer-btn"
          :class="{ 'is-active': viewVisibility.consistency }"
          type="button"
          :aria-pressed="viewVisibility.consistency"
          :title="viewVisibility.consistency ? '隐藏实模一致对比' : '显示实模一致对比'"
          @click="toggleViewVisibility('consistency')"
        >
          <el-icon><View /></el-icon>
          <span>一致结果</span>
        </button>

        <span class="tools-divider" aria-hidden="true" />
        <label class="tool-select-row">
          <span>BIM 自定义</span>
          <input v-model="splitBackgroundColors.bim" type="color" />
        </label>
        <label class="tool-select-row">
          <span>点云自定义</span>
          <input v-model="splitBackgroundColors.pointcloud" type="color" />
        </label>
        <label class="tool-select-row">
          <span>一致自定义</span>
          <input v-model="splitBackgroundColors.consistency" type="color" />
        </label>
        <label class="tool-select-row">
          <span>EDL</span>
          <input v-model="edlEnabled" type="checkbox" />
        </label>
        <label class="tool-range-row">
          <span>EDL 强度</span>
          <input v-model.number="edlStrength" type="range" min="0" max="1" step="0.05" :disabled="!edlEnabled" />
        </label>
        <label class="tool-select-row">
          <span>点云颜色</span>
          <select v-model="pointcloudColorMode">
            <option value="original">原始颜色</option><option value="custom">自定义</option>
          </select>
        </label>
        <label v-if="pointcloudColorMode === 'custom'" class="tool-select-row">
          <span>颜色</span>
          <input v-model="pointcloudColor" type="color" />
        </label>


      </div>
    </div>

    <div v-if="!isReady" class="empty-state">
      <h2>缺少预览参数</h2>
      <p>请从上传页重新点击“实模对比”打开当前页面。</p>
    </div>

    <div v-else-if="!calibrationReady" class="empty-state">
      <h2>正在读取校准结果</h2>
      <p>正在加载当前 BIM 与点云组合的校准矩阵。</p>
    </div>

    <div
      v-else
      class="viewer-shell"
      :class="{
        'viewer-shell--single': visibleViewCount === 1,
        'viewer-shell--triple': visibleViewCount === 3,
        'viewer-shell--tools-expanded': toolsExpanded,
      }"
    >
      <div v-show="viewVisibility.bim" class="viewer-slot">
        <div class="viewer-label viewer-label--bim">
          <span class="viewer-label__dot" />
          <span>BIM</span>
          <span v-if="bimDisplayName" class="viewer-label__name">{{ bimDisplayName }}</span>
        </div>
        <BimPreviewPanel
          ref="bimPanelRef"
          :asset-id="bimAssetId"
          :display-name="bimDisplayName"
          :calibration="calibration"
          :analysis-mode="analysisMode"
          fusion-mode
          minimal
          @loaded-change="handleBimLoadedChange"
          @camera-change="handleBimCameraChange"
          @analysis-point="analysisPoint = $event"
          @analysis-distance="analysisDistance = $event"
        />
      </div>
      <div v-show="viewVisibility.pointcloud" class="viewer-slot">
        <div class="viewer-label viewer-label--pointcloud">
          <span class="viewer-label__dot" />
          <span>点云</span>
          <span v-if="pointcloudDisplayName" class="viewer-label__name">{{ pointcloudDisplayName }}</span>
        </div>
        <PointcloudPreviewPanel
          ref="pointcloudPanelRef"
          :asset-id="pointcloudAssetId"
          :analysis-mode="analysisMode"
          minimal
          @loaded-change="handlePointcloudLoadedChange"
          @camera-change="handlePointcloudCameraChange"
          @analysis-point="analysisPoint = $event"
          @analysis-distance="analysisDistance = $event"
        />
      </div>
      <ViewerAnalysisOverlay
        :mode="analysisMode"
        :point="analysisPoint"
        :distance="analysisDistance"
        @clear="clearAnalysis"
      />
      <div v-show="viewVisibility.consistency" class="viewer-slot">
        <C2MResultPreviewPanel
          ref="consistencyPanelRef"
          :scan-asset-id="pointcloudAssetId"
          :bim-asset-id="bimAssetId"
          :calibration="calibration"
          :bim-world-pose="bimWorldPose"
          @loaded-change="handleConsistencyLoadedChange"
          @camera-change="handleConsistencyCameraChange"
        />
      </div>

     
    </div>
  </section>
</template>

<style scoped>
.split-preview-page {
  min-height: 100vh;
  padding: 18px;
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 22%),
    radial-gradient(circle at 15% 20%, rgba(59, 130, 246, 0.12), transparent 24%),
    linear-gradient(180deg, #06111f 0%, #09172a 42%, #070d18 100%);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.floating-controls {
  position: fixed;
  top: 50%;
  left: 14px;
  z-index: 20;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  transform: translateY(-50%);
}

.tools-panel {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  width: 190px;
  max-height: calc(100vh - 36px);
  overflow-y: auto;
  padding: 8px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 12px;
  background: rgba(8, 17, 29, 0.82);
  box-shadow: 0 20px 50px rgba(1, 8, 13, 0.34);
  backdrop-filter: blur(18px) saturate(135%);
}

.tools-toggle {
  width: 42px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 12px;
  color: #d8f3ff;
  cursor: pointer;
  background: rgba(10, 20, 38, 0.86);
  box-shadow: 0 0 22px rgba(56, 189, 248, 0.12);
}

.tools-toggle:hover {
  color: #fff;
  border-color: rgba(103, 232, 249, 0.42);
}

.tools-divider {
  width: 100%;
  height: 1px;
  flex: 0 0 auto;
  background: rgba(148, 163, 184, 0.24);
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
  cursor: pointer;
  backdrop-filter: blur(18px);
  background:
    linear-gradient(180deg, rgba(10, 20, 38, 0.82), rgba(7, 14, 28, 0.64));
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.18),
    0 0 22px rgba(56, 189, 248, 0.12),
    0 16px 32px rgba(2, 6, 23, 0.34);
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease,
    color 0.2s ease;
}

.floating-btn:disabled {
  cursor: not-allowed;
  opacity: 0.48;
  transform: none;
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.12);
}

.floating-btn:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 0 0 1px rgba(103, 232, 249, 0.26),
    0 0 28px rgba(34, 211, 238, 0.18),
    0 18px 36px rgba(2, 6, 23, 0.4);
}

.floating-btn.is-active {
  color: #ecfeff;
  box-shadow:
    inset 0 0 0 1px rgba(34, 211, 238, 0.36),
    0 0 34px rgba(34, 211, 238, 0.28),
    0 20px 40px rgba(2, 6, 23, 0.44);
}

.layer-btn {
  width: 100%;
  justify-content: flex-start;
  min-width: 0;
}

.tool-select-row,
.tool-clear-row,
.tool-range-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #cbd5e1;
  font-size: 12px;
}

.tool-range-row {
  flex-direction: column;
  align-items: stretch;
}

.tool-range-row input[type='range'] {
  width: 100%;
  accent-color: #38bdf8;
}

.tool-select-row select {
  width: 92px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 6px;
  padding: 5px 6px;
  background: rgba(15, 23, 42, 0.86);
  color: #e2e8f0;
  font-size: 12px;
}

.tool-clear-row {
  flex-wrap: wrap;
  justify-content: flex-start;
}

.tool-clear-row > span {
  width: 100%;
}

.tool-clear-row button {
  flex: 1;
  border: 1px solid rgba(248, 113, 113, 0.38);
  border-radius: 6px;
  padding: 5px 6px;
  background: rgba(127, 29, 29, 0.3);
  color: #fecaca;
  cursor: pointer;
  font-size: 11px;
}

.layer-btn:not(.is-active) {
  color: #94a3b8;
  opacity: 0.72;
}

.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  border-radius: 28px;
  text-align: center;
  background: rgba(8, 15, 30, 0.52);
  color: #cbd5e1;
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.12),
    0 18px 48px rgba(2, 6, 23, 0.34);
}

.empty-state h2 {
  margin: 0 0 10px;
  color: #f8fafc;
}

.empty-state p {
  margin: 0;
  color: #94a3b8;
}

.viewer-shell {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
  min-height: calc(100vh - 36px);
}

.viewer-shell--single {
  grid-template-columns: minmax(0, 1fr);
}

.viewer-shell--triple {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.viewer-slot {
  position: relative;
  min-width: 0;
  min-height: 0;
}

.viewer-label {
  position: absolute;
  top: 14px;
  left: 16px;
  z-index: 4;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: calc(100% - 32px);
  padding: 7px 11px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 999px;
  color: #f8fafc;
  font-size: 12px;
  line-height: 1;
  pointer-events: none;
  background: rgba(15, 23, 42, 0.68);
  backdrop-filter: blur(8px);
}

/* Keep the pane labels clear of the expanded global tools panel. */
.viewer-shell--tools-expanded .viewer-label {
  top: 78px;
}

.viewer-label__dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #38bdf8;
  box-shadow: 0 0 10px rgba(56, 189, 248, 0.75);
}

.viewer-label--pointcloud .viewer-label__dot {
  background: #a3e635;
  box-shadow: 0 0 10px rgba(163, 230, 53, 0.7);
}

.viewer-label__name {
  max-width: min(34vw, 280px);
  overflow: hidden;
  color: rgba(226, 232, 240, 0.7);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1080px) {
  .split-preview-page {
    padding-top: 72px;
  }

  .viewer-shell {
    grid-template-columns: 1fr;
    grid-template-rows: repeat(3, minmax(240px, 1fr));
    min-height: auto;
  }

  .viewer-label__name {
    max-width: 52vw;
  }

  .tools-panel {
    width: 176px;
  }

  .viewer-shell--tools-expanded .viewer-label {
    top: 142px;
  }
}

@media (max-width: 640px) {
  .floating-controls {
    top: 12px;
    left: 12px;
    transform: none;
  }

  .tools-panel {
    width: min(176px, calc(100vw - 72px));
    max-height: calc(100vh - 72px);
  }
}
</style>
