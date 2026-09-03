<script setup lang="ts">
import { computed } from 'vue'

export type AnalysisMode = 'none' | 'distance' | 'locate'
export type AnalysisPoint = { x: number; y: number; z: number }
export type AnalysisDistance = {
  start: AnalysisPoint
  end: AnalysisPoint
  distance: number
  heightDifference: number
}

const props = defineProps<{
  mode: AnalysisMode
  point: AnalysisPoint | null
  distance: AnalysisDistance | null
}>()

const emit = defineEmits<{
  (event: 'clear'): void
}>()

const pointText = computed(() => {
  if (!props.point) return ''
  return `X ${props.point.x.toFixed(3)}  Y ${props.point.y.toFixed(3)}  Z ${props.point.z.toFixed(3)}`
})

const distanceText = computed(() => {
  if (!props.distance) return ''
  return `${props.distance.distance.toFixed(3)} m`
})
</script>

<template>
  <div v-if="props.mode !== 'none'" class="analysis-overlay">
    <div class="analysis-toolbar">
      <strong>{{ props.mode === 'distance' ? '全局测距' : '全局定位' }}</strong>
      <span v-if="props.mode === 'distance' && !props.distance">依次点击两个 BIM 或点云位置</span>
      <span v-else-if="props.mode === 'locate' && !props.point">点击 BIM 或点云中的任意位置</span>
      <span v-else-if="props.mode === 'distance'" class="analysis-value">
        {{ distanceText }}
        <small>高差 {{ props.distance?.heightDifference.toFixed(3) }} m</small>
      </span>
      <span v-else class="analysis-value">{{ pointText }}</span>
      <button type="button" class="analysis-clear" title="清除当前分析" @click="emit('clear')">清除</button>
    </div>
  </div>
</template>

<style scoped>
.analysis-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  pointer-events: none;
}

.analysis-toolbar {
  position: absolute;
  top: 18px;
  left: 50%;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  max-width: calc(100% - 32px);
  padding: 8px 10px 8px 14px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  background: rgba(8, 17, 29, 0.86);
  color: #f8fafc;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.24);
  backdrop-filter: blur(14px);
  transform: translateX(-50%);
  pointer-events: auto;
  white-space: nowrap;
}

.analysis-toolbar span {
  color: rgba(226, 232, 240, 0.78);
  font-size: 12px;
}

.analysis-value {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  color: #fca5a5 !important;
  font-weight: 700;
}

.analysis-value small {
  color: rgba(254, 202, 202, 0.78);
  font-size: 11px;
  font-weight: 500;
}

.analysis-clear {
  border: 1px solid rgba(248, 113, 113, 0.4);
  border-radius: 6px;
  padding: 4px 9px;
  background: transparent;
  color: #fecaca;
  cursor: pointer;
  font-size: 12px;
}

.analysis-clear:hover {
  background: rgba(248, 113, 113, 0.16);
}

@media (max-width: 640px) {
  .analysis-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
    white-space: normal;
  }
}
</style>
