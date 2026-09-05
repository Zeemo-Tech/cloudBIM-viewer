<script setup lang="ts">
import { ref, watch } from 'vue'
import * as THREE from 'three'
import UnifiedViewer3D from './UnifiedViewer3D.vue'
import C2MHistogramLegend from './C2MHistogramLegend.vue'
import { getLatestC2M } from '@/api/backend-c2m'
import type { C2MResult } from '@/api/backend-c2m'
import type { C2MColorMode } from '@/features/analysis-mesh'
import type { CameraPose, CameraRotation, PreviewBackgroundTheme } from './UnifiedViewer3D.vue'

const props = defineProps<{
  scanAssetId: number | null
  bimAssetId: number | null
  result?: C2MResult | null
  calibration?: { modelMatrix: number[] } | null
  bimWorldPose?: {
    position: THREE.Vector3
    quaternion: THREE.Quaternion
    scale: THREE.Vector3
  } | null
}>()

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
}>()

const viewerRef = ref<InstanceType<typeof UnifiedViewer3D> | null>(null)
const localResult = ref<C2MResult | null>(props.result ?? null)
const colorMode = ref<C2MColorMode>('continuous')
const bandCount = ref(7)
let resultRequestId = 0

async function refreshResult() {
  const requestId = ++resultRequestId
  if (props.result !== undefined) {
    localResult.value = props.result ?? null
    return
  }
  if (!props.scanAssetId || !props.bimAssetId) {
    localResult.value = null
    return
  }
  try {
    const response = await getLatestC2M(props.scanAssetId, props.bimAssetId)
    if (requestId === resultRequestId) localResult.value = response.data
  } catch {
    if (requestId === resultRequestId) localResult.value = null
  }
}

async function reload() {
  await refreshResult()
  await viewerRef.value?.reload()
}

function clearResult() {
  resultRequestId += 1
  localResult.value = null
  viewerRef.value?.clearC2MResult()
}

watch(
  () => [props.result, props.scanAssetId, props.bimAssetId] as const,
  () => void refreshResult(),
  { immediate: true },
)

defineExpose({
  reload,
  loadResult: reload,
  clearResult,
  resetView: () => viewerRef.value?.resetView(),
  setBackgroundTheme: (theme: PreviewBackgroundTheme) => viewerRef.value?.setBackgroundTheme(theme),
  setBackgroundColor: (color: string) => viewerRef.value?.setBackgroundColor(color),
  setShowGrid: (show: boolean) => viewerRef.value?.setShowGrid(show),
  setGridColor: (color: string) => viewerRef.value?.setGridColor(color),
  getCameraPose: () => viewerRef.value?.getCameraPose(),
  syncFromExternalPose: (pose: CameraPose | null) => viewerRef.value?.syncFromExternalPose(pose),
  syncInitialViewFromExternalPose: (pose: CameraPose | null) => viewerRef.value?.syncFromExternalPose(pose),
  getCameraDistance: () => viewerRef.value?.getCameraDistance() ?? 1,
  getCameraOrientation: () => viewerRef.value?.getCameraOrientation() ?? { lon: 0, lat: 0 },
  syncFromRotation: (deltaLon: number, deltaLat: number) => viewerRef.value?.syncFromRotation(deltaLon, deltaLat),
  syncFromCameraDistance: (scale: number) => viewerRef.value?.syncFromCameraDistance(scale),
  applyBimWorldPose: (pose: { position: THREE.Vector3; quaternion: THREE.Quaternion; scale: THREE.Vector3 } | null) =>
    viewerRef.value?.applyBimWorldPose(pose),
  setAnalysisComponentVisible: (ifcGlobalId: string, visible: boolean) =>
    viewerRef.value?.setAnalysisComponentVisible(ifcGlobalId, visible),
  focusAnalysisComponent: (ifcGlobalId: string) =>
    viewerRef.value?.focusAnalysisComponent(ifcGlobalId),
})
</script>

<template>
  <div class="c2m-result-preview">
    <UnifiedViewer3D
      ref="viewerRef"
      type="c2m"
      :scan-asset-id="scanAssetId"
      :bim-asset-id="bimAssetId"
      :c2m-result="localResult"
      :c2m-color-mode="colorMode"
      :c2m-band-count="bandCount"
      :calibration="calibration"
      :bim-world-pose="bimWorldPose"
      @loaded-change="emit('loaded-change', $event)"
      @camera-change="emit('camera-change', $event)"
    />
    <div v-if="localResult?.analysis?.status === 'ready'" class="c2m-result-preview__mode">
      <button :class="{ active: colorMode === 'continuous' }" @click="colorMode = 'continuous'">连续渐变</button>
      <button :class="{ active: colorMode === 'discrete' }" @click="colorMode = 'discrete'">离散色带</button>
      <label v-if="colorMode === 'discrete'">
        <span>分区</span>
        <input v-model.number="bandCount" type="number" min="2" max="20" />
      </label>
    </div>
    <C2MHistogramLegend
      v-if="localResult"
      class="c2m-result-preview__legend"
      :result="localResult"
      compact
    />
  </div>
</template>

<style scoped>
.c2m-result-preview {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.c2m-result-preview__legend {
  position: absolute;
  right: 12px;
  bottom: 12px;
  left: 12px;
  z-index: 5;
  width: auto;
  max-width: 520px;
}

.c2m-result-preview__mode {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 6;
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 5px;
  border: 1px solid rgb(148 163 184 / 30%);
  border-radius: 7px;
  color: #cbd5e1;
  background: rgb(8 17 29 / 88%);
  font-size: 12px;
}

.c2m-result-preview__mode button,
.c2m-result-preview__mode input {
  border: 1px solid rgb(148 163 184 / 35%);
  border-radius: 4px;
  color: inherit;
  background: rgb(30 41 59 / 80%);
}

.c2m-result-preview__mode button {
  padding: 4px 7px;
  cursor: pointer;
}

.c2m-result-preview__mode button.active {
  border-color: #38bdf8;
  color: #7dd3fc;
}

.c2m-result-preview__mode label {
  display: flex;
  gap: 4px;
  align-items: center;
  padding-left: 4px;
}

.c2m-result-preview__mode input {
  width: 42px;
  padding: 3px 4px;
}

@media (max-width: 640px) {
  .c2m-result-preview__legend {
    right: 8px;
    bottom: 8px;
    left: 8px;
  }
}
</style>
