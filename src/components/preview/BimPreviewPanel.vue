<script setup lang="ts">
import { ref } from 'vue'
import UnifiedViewer3D from './UnifiedViewer3D.vue'
import type { AnalysisArea, AnalysisDistance, AnalysisMode, AnalysisPoint } from './ViewerAnalysisOverlay.vue'
import type { CameraPose, CameraRotation, ClipBoxState, PreviewBackgroundTheme } from './UnifiedViewer3D.vue'

const props = withDefaults(
  defineProps<{
    assetId: number | null
    displayName?: string
    minimal?: boolean
    calibration?: { modelMatrix: number[] } | null
    fusionMode?: boolean
    analysisMode?: AnalysisMode
  }>(),
  {
    displayName: undefined,
    minimal: false,
    fusionMode: false,
    analysisMode: 'none',
  },
)

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
  (event: 'analysis-point', point: AnalysisPoint): void
  (event: 'analysis-distance', distance: AnalysisDistance): void
  (event: 'analysis-area', area: AnalysisArea): void
}>()

const viewerRef = ref<InstanceType<typeof UnifiedViewer3D> | null>(null)

defineExpose({
  reload: () => viewerRef.value?.reload(),
  getModelWorldPose: () => viewerRef.value?.getModelWorldPose(),
  getCameraPose: () => viewerRef.value?.getCameraPose(),
  getCameraOrientation: () => viewerRef.value?.getCameraOrientation() ?? { lon: 0, lat: 0 },
  getCameraDistance: () => viewerRef.value?.getCameraDistance() ?? 1,
  syncFromRotation: (deltaLon: number, deltaLat: number) => viewerRef.value?.syncFromRotation(deltaLon, deltaLat),
  syncFromCameraDistance: (scale: number) => viewerRef.value?.syncFromCameraDistance(scale),
  setCameraPose: (pose: CameraPose | null) => viewerRef.value?.setCameraPose(pose),
  syncFromExternalPose: (pose: CameraPose | null) => viewerRef.value?.syncFromExternalPose(pose),
  resetView: () => viewerRef.value?.resetView(),
  setBackgroundTheme: (theme: PreviewBackgroundTheme) => viewerRef.value?.setBackgroundTheme(theme),
  setBackgroundColor: (color: string) => viewerRef.value?.setBackgroundColor(color),
  setShowAxes: (show: boolean) => viewerRef.value?.setShowAxes(show),
  setShowGrid: (show: boolean) => viewerRef.value?.setShowGrid(show),
  setWireframe: (wireframe: boolean) => viewerRef.value?.setWireframe(wireframe),
  setSectionState: (state: { enabled?: boolean; ratio?: number; box?: any }) => viewerRef.value?.setSectionState(state),
  clearAnalysis: () => viewerRef.value?.clearAnalysis(),
})
</script>

<template>
  <UnifiedViewer3D
    ref="viewerRef"
    type="bim"
    :asset-id="assetId"
    :display-name="displayName"
    :minimal="minimal"
    :calibration="calibration"
    :fusion-mode="fusionMode"
    :analysis-mode="analysisMode"
    @loaded-change="emit('loaded-change', $event)"
    @camera-change="emit('camera-change', $event)"
    @analysis-point="emit('analysis-point', $event)"
    @analysis-distance="emit('analysis-distance', $event)"
    @analysis-area="emit('analysis-area', $event)"
  />
</template>
