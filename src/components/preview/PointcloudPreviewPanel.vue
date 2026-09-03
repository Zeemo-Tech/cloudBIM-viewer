<script setup lang="ts">
import { ref } from 'vue'
import UnifiedViewer3D from './UnifiedViewer3D.vue'
import type { AnalysisDistance, AnalysisMode, AnalysisPoint } from './ViewerAnalysisOverlay.vue'
import type { CameraPose, CameraRotation, PreviewBackgroundTheme } from './UnifiedViewer3D.vue'

const props = withDefaults(
  defineProps<{
    assetId: number | null
    minimal?: boolean
    analysisMode?: AnalysisMode
  }>(),
  {
    minimal: false,
    analysisMode: 'none',
  },
)

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
  (event: 'analysis-point', point: AnalysisPoint): void
  (event: 'analysis-distance', distance: AnalysisDistance): void
}>()

const viewerRef = ref<InstanceType<typeof UnifiedViewer3D> | null>(null)

defineExpose({
  reload: () => viewerRef.value?.reload(),
  resetPointcloudView: () => viewerRef.value?.resetPointcloudView(),
  resetView: () => viewerRef.value?.resetView(),
  getCameraPose: () => viewerRef.value?.getCameraPose(),
  getCameraOrientation: () => viewerRef.value?.getCameraOrientation() ?? { lon: 0, lat: 0 },
  getCameraDistance: () => viewerRef.value?.getCameraDistance() ?? 1,
  syncFromRotation: (deltaLon: number, deltaLat: number) => viewerRef.value?.syncFromRotation(deltaLon, deltaLat),
  syncFromCameraDistance: (scale: number) => viewerRef.value?.syncFromCameraDistance(scale),
  syncFromExternalPose: (pose: CameraPose | null) => viewerRef.value?.syncFromExternalPose(pose),
  setCameraPose: (pose: CameraPose | null) => viewerRef.value?.setCameraPose(pose),
  setBackgroundTheme: (theme: PreviewBackgroundTheme) => viewerRef.value?.setBackgroundTheme(theme),
  setBackgroundColor: (color: string) => viewerRef.value?.setBackgroundColor(color),
  setShowAxes: (show: boolean) => viewerRef.value?.setShowAxes(show),
  setShowGrid: (show: boolean) => viewerRef.value?.setShowGrid(show),
  setSectionState: (state: { enabled?: boolean; ratio?: number; box?: any }) => viewerRef.value?.setSectionState(state),
  setPointColor: (color: string | null) => viewerRef.value?.setPointColor(color),
  setEdlEnabled: (enabled: boolean) => viewerRef.value?.setEdlEnabled(enabled),
  setEdlStrength: (strength: number) => viewerRef.value?.setEdlStrength(strength),
  setRendererPreference: (_pref: string) => {},
  clearAnalysis: () => viewerRef.value?.clearAnalysis(),
})
</script>

<template>
  <UnifiedViewer3D
    ref="viewerRef"
    type="pointcloud"
    :asset-id="assetId"
    :minimal="minimal"
    :analysis-mode="analysisMode"
    @loaded-change="emit('loaded-change', $event)"
    @camera-change="emit('camera-change', $event)"
    @analysis-point="emit('analysis-point', $event)"
    @analysis-distance="emit('analysis-distance', $event)"
  />
</template>
