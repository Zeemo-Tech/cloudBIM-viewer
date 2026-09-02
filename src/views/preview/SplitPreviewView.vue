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
import { getBimAlignment, type BimAlignmentResult } from '@/api/backend-alignment'

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
const applyingViewSync = ref(false)
const toolsExpanded = ref(true)
const viewVisibility = reactive({
  bim: true,
  pointcloud: true,
})
const calibration = ref<BimAlignmentResult | null>(null)
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
type SyncSource = 'bim' | 'pointcloud'

const rotationBases: Record<SyncSource, Rotation | null> = {
  bim: null,
  pointcloud: null,
}
const lastBroadcastRotations: Record<SyncSource, Rotation | null> = {
  bim: null,
  pointcloud: null,
}
const distanceBases: Record<SyncSource, number | null> = {
  bim: null,
  pointcloud: null,
}
const lastBroadcastDistances: Record<SyncSource, number | null> = {
  bim: null,
  pointcloud: null,
}
const poseBases: Record<SyncSource, CameraPose | null> = {
  bim: null,
  pointcloud: null,
}
const lastBroadcastPoses: Record<SyncSource, CameraPose | null> = {
  bim: null,
  pointcloud: null,
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
  const panel = source === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
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
  const panel = source === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
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
  const panel = source === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
  const distance = panel?.getCameraDistance?.()
  return Number.isFinite(distance) && distance > 0 ? normalizeDistance(distance) : null
}

function captureSyncBases() {
  ;(['bim', 'pointcloud'] as SyncSource[]).forEach((source) => {
    rotationBases[source] = getCurrentRotation(source)
    lastBroadcastRotations[source] = rotationBases[source]
    distanceBases[source] = getCurrentDistance(source)
    lastBroadcastDistances[source] = distanceBases[source]
    poseBases[source] = getCurrentPose(source)
    lastBroadcastPoses[source] = clonePose(poseBases[source])
  })
}

function clearRotationSyncState() {
  ;(['bim', 'pointcloud'] as SyncSource[]).forEach((source) => {
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
  () => Number(viewVisibility.bim) + Number(viewVisibility.pointcloud),
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

  const target: SyncSource = source === 'bim' ? 'pointcloud' : 'bim'
  const targetPanel = target === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
  if (!targetPanel) return

  const targetRotation = buildTargetRotation(source, target, sourceRotation)
  lastBroadcastRotations[target] = targetRotation
  applyingViewSync.value = true
  try {
    targetPanel.syncFromRotation?.(targetRotation)
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
  const target: SyncSource = source === 'bim' ? 'pointcloud' : 'bim'
  const targetBase = distanceBases[target]
  const targetPanel = target === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
  if (!sourceBase || !targetBase || !targetPanel) return

  const zoomRatio = sourceDistance / sourceBase
  if (!Number.isFinite(zoomRatio) || zoomRatio <= 0) return

  const targetDistance = normalizeDistance(targetBase * zoomRatio)
  lastBroadcastDistances[target] = targetDistance
  applyingViewSync.value = true
  try {
    targetPanel.syncFromCameraDistance?.(targetDistance)
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

  const target: SyncSource = source === 'bim' ? 'pointcloud' : 'bim'
  const targetPanel = target === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
  if (!targetPanel) return false
  const targetPose = buildTargetPose(source, target, sourcePose)
  if (!targetPose) return false
  lastBroadcastPoses[target] = targetPose
  applyingViewSync.value = true
  try {
    targetPanel.syncFromExternalPose?.(targetPose)
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
  } finally {
    applyingViewSync.value = false
  }
  if (syncActive.value) requestAnimationFrame(captureSyncBases)
}

function handleReload() {
  applyingViewSync.value = true
  clearRotationSyncState()
  bimLoaded.value = false
  pointcloudLoaded.value = false
  try {
    bimPanelRef.value?.reload?.()
    pointcloudPanelRef.value?.reload?.()
  } finally {
    applyingViewSync.value = false
  }
}

function toggleViewVisibility(view: 'bim' | 'pointcloud') {
  if (viewVisibility[view] && visibleViewCount.value === 1) return
  viewVisibility[view] = !viewVisibility[view]
}

function handleBimLoadedChange(value: boolean) {
  bimLoaded.value = value
  if (value && syncActive.value && canSync.value) requestAnimationFrame(captureSyncBases)
}

function handlePointcloudLoadedChange(value: boolean) {
  pointcloudLoaded.value = value
  if (value && syncActive.value && canSync.value) requestAnimationFrame(captureSyncBases)
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

onMounted(() => {
  void loadCalibration()
})

watch(
  () => [props.bimAssetId, props.pointcloudAssetId] as const,
  () => {
    clearRotationSyncState()
    void loadCalibration()
  },
)
</script>

<template>
  <section class="split-preview-page">
    <div class="floating-controls" :class="{ 'is-collapsed': !toolsExpanded }">
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

        <button class="floating-btn" type="button" title="重置两个视图" @click="handleResetView">
          <el-icon><RefreshRight /></el-icon>
          <span>重置</span>
        </button>

        <button class="floating-btn" type="button" title="重新加载模型和点云" @click="handleReload">
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
      </div>
    </div>

    <div v-if="!isReady" class="empty-state">
      <h2>缺少预览参数</h2>
      <p>请从上传页重新点击“二分屏预览”打开当前页面。</p>
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
          fusion-mode
          minimal
          @loaded-change="handleBimLoadedChange"
          @camera-change="handleBimCameraChange"
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
          minimal
          @loaded-change="handlePointcloudLoadedChange"
          @camera-change="handlePointcloudCameraChange"
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
  top: 18px;
  left: 18px;
  z-index: 20;
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.tools-panel {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: min(calc(100vw - 86px), 720px);
  padding: 5px;
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
  width: 1px;
  height: 24px;
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
  min-width: 74px;
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
    min-height: auto;
  }

  .viewer-label__name {
    max-width: 52vw;
  }

  .tools-panel {
    flex-wrap: wrap;
    max-width: min(calc(100vw - 86px), 420px);
  }

  .viewer-shell--tools-expanded .viewer-label {
    top: 142px;
  }
}
</style>
