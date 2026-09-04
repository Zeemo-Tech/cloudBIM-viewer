<script setup lang="ts">
import { ref } from 'vue'
import * as THREE from 'three'
import UnifiedViewer3D from './UnifiedViewer3D.vue'
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

defineExpose({
  reload: () => viewerRef.value?.reload(),
  loadResult: () => viewerRef.value?.reload(),
  clearResult: () => viewerRef.value?.reload(),
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
  <UnifiedViewer3D
    ref="viewerRef"
    type="c2m"
    :scan-asset-id="scanAssetId"
    :bim-asset-id="bimAssetId"
    :c2m-result="result"
    :calibration="calibration"
    :bim-world-pose="bimWorldPose"
    @loaded-change="emit('loaded-change', $event)"
    @camera-change="emit('camera-change', $event)"
  />
</template>
