<script setup lang="ts">
import { ref, watch } from 'vue'
import * as THREE from 'three'
import UnifiedViewer3D from './UnifiedViewer3D.vue'
import C2MHistogramLegend from './C2MHistogramLegend.vue'
import { getLatestC2M } from '@/api/backend-c2m'
import type { C2MResult } from '@/api/backend-c2m'
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
      :calibration="calibration"
      :bim-world-pose="bimWorldPose"
      @loaded-change="emit('loaded-change', $event)"
      @camera-change="emit('camera-change', $event)"
    />
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

@media (max-width: 640px) {
  .c2m-result-preview__legend {
    right: 8px;
    bottom: 8px;
    left: 8px;
  }
}
</style>
