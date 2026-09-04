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
import MeasurementToolbar from '@/components/preview/MeasurementToolbar.vue'
import ViewerAnalysisOverlay, {
  type AnalysisDistance,
  type AnalysisArea,
  type AnalysisMode,
  type AnalysisPoint,
} from '@/components/preview/ViewerAnalysisOverlay.vue'
import { getBimAlignment, type BimAlignmentResult } from '@/api/backend-alignment'
import * as THREE from 'three'
import {
  createMeasurement,
  deleteMeasurement,
  listMeasurements,
  type MeasurementKind,
} from '@/api/backend-measurement'

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
const measurementToolbarCollapsed = ref(true)
const analysisMode = ref<AnalysisMode>('none')
const analysisPoint = ref<AnalysisPoint | null>(null)
const analysisDistance = ref<AnalysisDistance | null>(null)
const analysisAreas = ref<AnalysisArea[]>([])
const analysisPoints = ref<AnalysisPoint[]>([])
const analysisDistances = ref<AnalysisDistance[]>([])
const measurementBackendIds = new Map<string, number>()
let measurementLoadToken = 0
type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'
type InterfaceStyle = 'dark' | 'light'
const interfaceStyle = ref<InterfaceStyle>('dark')
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
const pointcloudColorMode = ref<'original' | 'custom'>('original')
const pointcloudColor = ref('#86898D')
const showGrid = ref(false)
const gridColor = ref('#2a6f82')
const edlEnabled = ref(true)
const edlStrength = ref(1)
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
  bimPanelRef.value?.cancelAnalysis?.()
  pointcloudPanelRef.value?.cancelAnalysis?.()
  analysisMode.value = analysisMode.value === mode ? 'none' : mode
}

function handleAnalysisModeExit() {
  analysisMode.value = 'none'
  analysisPoint.value = null
  analysisDistance.value = null
}

function clearAnalysis() {
  analysisMode.value = 'none'
  analysisPoint.value = null
  analysisDistance.value = null
  analysisAreas.value = []
  analysisPoints.value = []
  analysisDistances.value = []
  bimPanelRef.value?.clearAnalysis?.()
  pointcloudPanelRef.value?.clearAnalysis?.()
  const backendIds = [...new Set(measurementBackendIds.values())]
  measurementBackendIds.clear()
  backendIds.forEach((id) => {
    void deleteMeasurement(id).catch((error) => {
      console.warn('[SplitPreview] 删除测量记录失败', error)
    })
  })
}

function removeAnalysisById(kind: 'point' | 'distance' | 'area', id: string) {
  if (kind === 'point') {
    analysisPoints.value = analysisPoints.value.filter((record, index) => (record.id || `point-${index}`) !== id)
    analysisPoint.value = analysisPoints.value.at(-1) ?? null
  }
  if (kind === 'distance') {
    analysisDistances.value = analysisDistances.value.filter((record, index) => (record.id || `distance-${index}`) !== id)
    analysisDistance.value = analysisDistances.value.at(-1) ?? null
  }
  if (kind === 'area') {
    analysisAreas.value = analysisAreas.value.filter((record, index) => (record.id || `area-${index}`) !== id)
  }

  const backendId = measurementBackendIds.get(id)
  measurementBackendIds.delete(id)
  bimPanelRef.value?.removeAnalysisVisual?.(kind, id)
  pointcloudPanelRef.value?.removeAnalysisVisual?.(kind, id)
  if (backendId !== undefined) {
    void deleteMeasurement(backendId).catch((error) => {
      console.warn('[SplitPreview] 删除测量记录失败', error)
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
  if (!props.bimAssetId) return
  const localId = typeof payload === 'object' && payload && 'id' in payload
    ? String(payload.id)
    : ''
  try {
    const response = await createMeasurement(props.bimAssetId, kind, payload)
    if (!localId) return
    if (hasMeasurement(kind, localId)) {
      measurementBackendIds.set(localId, response.data.id)
    } else {
      void deleteMeasurement(response.data.id).catch(() => undefined)
    }
  } catch (error) {
    console.warn('[SplitPreview] 保存测量记录失败', error)
  }
}

async function loadMeasurements() {
  const assetId = props.bimAssetId
  const token = ++measurementLoadToken
  analysisPoint.value = null
  analysisDistance.value = null
  analysisPoints.value = []
  analysisDistances.value = []
  analysisAreas.value = []
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
    console.warn('[SplitPreview] 读取测量记录失败', error)
  }
}

function applySplitPresentation() {
  bimPanelRef.value?.setBackgroundTheme?.(splitBackgrounds.bim)
  pointcloudPanelRef.value?.setBackgroundTheme?.(splitBackgrounds.pointcloud)
  consistencyPanelRef.value?.setBackgroundTheme?.(splitBackgrounds.consistency)
  bimPanelRef.value?.setBackgroundColor?.(splitBackgroundColors.bim)
  pointcloudPanelRef.value?.setBackgroundColor?.(splitBackgroundColors.pointcloud)
  consistencyPanelRef.value?.setBackgroundColor?.(splitBackgroundColors.consistency)
  bimPanelRef.value?.setShowGrid?.(showGrid.value)
  pointcloudPanelRef.value?.setShowGrid?.(showGrid.value)
  consistencyPanelRef.value?.setShowGrid?.(showGrid.value)
  bimPanelRef.value?.setGridColor?.(gridColor.value)
  pointcloudPanelRef.value?.setGridColor?.(gridColor.value)
  consistencyPanelRef.value?.setGridColor?.(gridColor.value)
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

function applyInterfaceStyle(style: InterfaceStyle) {
  const backgroundTheme: PreviewBackgroundTheme = style === 'light' ? 'light' : 'deep'
  ;(['bim', 'pointcloud', 'consistency'] as const).forEach((source) => {
    splitBackgrounds[source] = backgroundTheme
    syncBackgroundColorFromTheme(source)
  })
  requestAnimationFrame(applySplitPresentation)
}

function clearScreen(source: SyncSource) {
  if (source === 'bim') bimPanelRef.value?.clearAnalysis?.()
  if (source === 'pointcloud') pointcloudPanelRef.value?.clearAnalysis?.()
  if (source === 'consistency') consistencyPanelRef.value?.clearResult?.()
}

onMounted(() => {
  void loadMeasurements()
  void loadCalibration()
  requestAnimationFrame(applySplitPresentation)
})

watch(
  () => [props.bimAssetId, props.pointcloudAssetId] as const,
  () => {
    clearRotationSyncState()
    void loadMeasurements()
    void loadCalibration()
    requestAnimationFrame(applySplitPresentation)
  },
)

watch(interfaceStyle, applyInterfaceStyle)

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
    showGrid,
    gridColor,
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
  <section class="split-preview-page" :class="`theme-${interfaceStyle}`">
    <button class="page-back-btn" type="button" title="返回上传页" @click="closePage">
      <el-icon><ArrowLeft /></el-icon>
      <span>返回</span>
    </button>

    <MeasurementToolbar
      v-if="isReady"
      v-model:collapsed="measurementToolbarCollapsed"
      class="split-measurement-toolbar"
      :mode="analysisMode"
      @update:mode="selectAnalysisMode"
      @clear="clearAnalysis"
    />
    <div v-if="isReady" class="floating-controls">
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
          <span>风格</span>
          <select v-model="interfaceStyle">
            <option value="dark">暗夜</option>
            <option value="light">白昼</option>
          </select>
        </label>
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
          <span>显示网格</span>
          <input v-model="showGrid" type="checkbox" />
        </label>
        <label class="tool-select-row">
          <span>网格颜色</span>
          <input v-model="gridColor" type="color" :disabled="!showGrid" />
        </label>
        <label class="tool-select-row">
          <span>EDL</span>
          <input v-model="edlEnabled" type="checkbox" />
        </label>
        <label class="tool-range-row">
          <span>EDL 强度 {{ Math.round(edlStrength * 100) }}%</span>
          <input
            v-model.number="edlStrength"
            type="range"
            min="0.1"
            max="1"
            step="0.05"
            :disabled="!edlEnabled"
          />
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
          :analysis-points="analysisPoints"
          :analysis-distances="analysisDistances"
          :analysis-areas="analysisAreas"
          fusion-mode
          minimal
          @loaded-change="handleBimLoadedChange"
          @camera-change="handleBimCameraChange"
          @analysis-point="handleAnalysisPoint"
          @analysis-area="handleAnalysisArea"
          @analysis-distance="handleAnalysisDistance"
          @analysis-delete="removeAnalysisById($event.kind, $event.id)"
          @analysis-mode-exit="handleAnalysisModeExit"
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
          :show-edl-control="false"
          :analysis-mode="analysisMode"
          :analysis-points="analysisPoints"
          :analysis-distances="analysisDistances"
          :analysis-areas="analysisAreas"
          minimal
          @loaded-change="handlePointcloudLoadedChange"
          @camera-change="handlePointcloudCameraChange"
          @analysis-point="handleAnalysisPoint"
          @analysis-area="handleAnalysisArea"
          @analysis-distance="handleAnalysisDistance"
          @analysis-delete="removeAnalysisById($event.kind, $event.id)"
          @analysis-mode-exit="handleAnalysisModeExit"
        />
      </div>
      <ViewerAnalysisOverlay
        :mode="analysisMode"
        :point="analysisPoint"
        :distance="analysisDistance"
        :points="analysisPoints"
        :distances="analysisDistances"
        :areas="analysisAreas"
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
  transition: background 0.25s ease;
}

.split-preview-page.theme-light {
  background:
    radial-gradient(circle at top, rgba(14, 165, 233, 0.1), transparent 24%),
    linear-gradient(180deg, #f8fafc 0%, #e8eef6 100%);
}

.floating-controls {
  position: fixed;
  top: 50%;
  left: 14px;
  z-index: 20;
  transform: translateY(-50%);
}

.tools-panel {
  position: absolute;
  top: 50%;
  left: 50px;
  transform: translateY(-50%);
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

.page-back-btn {
  position: fixed;
  top: 18px;
  right: 18px;
  z-index: 30;
  height: 42px;
  padding: 0 18px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #e0f2fe;
  font-size: 13px;
  cursor: pointer;
  background: rgba(8, 17, 29, 0.86);
  box-shadow: 0 12px 30px rgba(2, 6, 23, 0.36);
  backdrop-filter: blur(18px) saturate(135%);
}

.page-back-btn:hover {
  color: #fff;
  border-color: rgba(103, 232, 249, 0.5);
  background: rgba(12, 30, 50, 0.92);
}

.split-preview-page :deep(.split-measurement-toolbar) {
  top: 18px;
  right: 108px;
}

.split-preview-page.theme-light .tools-panel,
.split-preview-page.theme-light .tools-toggle,
.split-preview-page.theme-light .page-back-btn,
.split-preview-page.theme-light :deep(.split-measurement-toolbar) {
  border-color: rgba(100, 116, 139, 0.28);
  color: #0f172a;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 14px 36px rgba(15, 23, 42, 0.14);
}

.split-preview-page.theme-light .tools-toggle:hover,
.split-preview-page.theme-light .page-back-btn:hover {
  border-color: rgba(37, 99, 235, 0.44);
  color: #1d4ed8;
  background: #fff;
}

.split-preview-page.theme-light :deep(.measurement-toggle),
.split-preview-page.theme-light :deep(.measurement-action) {
  color: #334155;
}

.split-preview-page.theme-light :deep(.measurement-toggle-icon) {
  filter: none;
}

.split-preview-page.theme-light :deep(.measurement-toggle:hover),
.split-preview-page.theme-light :deep(.measurement-action:hover:not(:disabled)) {
  border-color: rgba(37, 99, 235, 0.28);
  background: rgba(37, 99, 235, 0.08);
}

.split-preview-page.theme-light :deep(.measurement-action.is-active) {
  border-color: rgba(220, 38, 38, 0.42);
  color: #b91c1c;
  background: rgba(254, 226, 226, 0.9);
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
  background:
    linear-gradient(180deg, rgba(14, 116, 144, 0.72), rgba(8, 47, 73, 0.86));
  box-shadow:
    inset 0 0 0 1px rgba(103, 232, 249, 0.78),
    inset 0 0 18px rgba(34, 211, 238, 0.18),
    0 0 34px rgba(34, 211, 238, 0.34),
    0 20px 40px rgba(2, 6, 23, 0.44);
}

.floating-btn.is-active:hover {
  background:
    linear-gradient(180deg, rgba(21, 133, 163, 0.82), rgba(8, 61, 91, 0.92));
  box-shadow:
    inset 0 0 0 1px rgba(165, 243, 252, 0.9),
    inset 0 0 20px rgba(34, 211, 238, 0.24),
    0 0 38px rgba(34, 211, 238, 0.4),
    0 20px 40px rgba(2, 6, 23, 0.44);
}

.split-preview-page.theme-light .floating-btn {
  color: #334155;
  background: linear-gradient(180deg, #fff, #f8fafc);
  box-shadow:
    inset 0 0 0 1px rgba(100, 116, 139, 0.22),
    0 10px 24px rgba(15, 23, 42, 0.1);
}

.split-preview-page.theme-light .floating-btn:hover {
  color: #1d4ed8;
  box-shadow:
    inset 0 0 0 1px rgba(37, 99, 235, 0.34),
    0 12px 28px rgba(15, 23, 42, 0.14);
}

.split-preview-page.theme-light .floating-btn.is-active {
  color: #fff;
  background: linear-gradient(180deg, #0ea5e9, #2563eb);
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.42),
    0 10px 24px rgba(37, 99, 235, 0.24);
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

.split-preview-page.theme-light .tools-divider {
  background: rgba(100, 116, 139, 0.24);
}

.split-preview-page.theme-light .tool-select-row,
.split-preview-page.theme-light .tool-clear-row,
.split-preview-page.theme-light .tool-range-row {
  color: #334155;
}

.split-preview-page.theme-light .tool-select-row select {
  border-color: rgba(100, 116, 139, 0.3);
  color: #0f172a;
  background: #fff;
}

.split-preview-page.theme-light .layer-btn:not(.is-active) {
  color: #64748b;
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

.split-preview-page.theme-light .empty-state {
  color: #334155;
  background: rgba(255, 255, 255, 0.78);
  box-shadow:
    inset 0 0 0 1px rgba(100, 116, 139, 0.16),
    0 18px 48px rgba(15, 23, 42, 0.12);
}

.split-preview-page.theme-light .empty-state h2 {
  color: #0f172a;
}

.split-preview-page.theme-light .empty-state p {
  color: #64748b;
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

.split-preview-page.theme-light .viewer-label {
  border-color: rgba(100, 116, 139, 0.26);
  color: #0f172a;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.1);
}

.split-preview-page.theme-light .viewer-label__name {
  color: #475569;
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
}

@media (max-width: 640px) {
  .tools-panel {
    width: min(176px, calc(100vw - 72px));
    max-height: calc(100vh - 36px);
  }

  .page-back-btn {
    top: 12px;
    right: 12px;
  }

  .split-preview-page :deep(.split-measurement-toolbar) {
    top: 12px;
    right: 102px;
  }
}
</style>
