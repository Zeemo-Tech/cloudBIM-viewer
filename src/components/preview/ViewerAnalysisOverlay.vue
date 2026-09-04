<script setup lang="ts">
import { computed } from 'vue'

export type AnalysisMode = 'none' | 'distance' | 'locate' | 'area'
export type AnalysisPoint = { id?: string; x: number; y: number; z: number }
export type AnalysisDistance = {
  id?: string
  start: AnalysisPoint
  end: AnalysisPoint
  distance: number
  heightDifference: number
  horizontalDistance?: number
  verticalDistance?: number
  slopeDegrees?: number
}
export type AnalysisArea = {
  id?: string
  points: AnalysisPoint[]
  area: number
  perimeter: number
}

const props = defineProps<{
  mode: AnalysisMode
  point: AnalysisPoint | null
  distance: AnalysisDistance | null
  points?: AnalysisPoint[]
  distances?: AnalysisDistance[]
  areas?: AnalysisArea[]
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

const latestDistanceMetrics = computed(() => {
  const record = props.distance
  if (!record) return null
  const dx = record.end.x - record.start.x
  const dy = record.end.y - record.start.y
  const dz = record.end.z - record.start.z
  const horizontalDistance = record.horizontalDistance ?? Math.hypot(dx, dz)
  const verticalDistance = record.verticalDistance ?? Math.abs(dy)
  const slopeDegrees = record.slopeDegrees
    ?? (horizontalDistance <= 1e-8
      ? (verticalDistance <= 1e-8 ? 0 : 90)
      : Math.atan2(verticalDistance, horizontalDistance) * 180 / Math.PI)
  return { horizontalDistance, verticalDistance, slopeDegrees }
})

const pointRecords = computed(() => props.points ?? (props.point ? [props.point] : []))
const distanceRecords = computed(() => props.distances ?? (props.distance ? [props.distance] : []))
const areaRecords = computed(() => props.areas ?? [])
</script>

<template>
  <div v-if="props.mode !== 'none'" class="analysis-overlay">
    <div class="analysis-toolbar">
      <strong>{{ props.mode === 'distance' ? '全局测距' : props.mode === 'area' ? '面积测量' : '全局定位' }}</strong>
      <span v-if="props.mode === 'distance' && distanceRecords.length === 0">依次点击两点完成一段测距</span>
      <span v-else-if="props.mode === 'locate' && pointRecords.length === 0">点击 BIM 或点云中的任意位置拾取坐标</span>
      <span v-else-if="props.mode === 'area' && areaRecords.length === 0">连续点击至少三个点，双击首点闭合区域</span>
      <span v-else-if="props.mode === 'distance'" class="analysis-value">
        {{ distanceRecords.length }} 段，最近 {{ distanceText }}
        <small v-if="latestDistanceMetrics">
          水平 {{ latestDistanceMetrics.horizontalDistance.toFixed(3) }} m
          · 垂直 {{ latestDistanceMetrics.verticalDistance.toFixed(3) }} m
          · 坡度 {{ latestDistanceMetrics.slopeDegrees.toFixed(2) }}°
        </small>
      </span>
      <span v-else-if="props.mode === 'area'" class="analysis-value">{{ areaRecords.length }} 个区域</span>
      <span v-else class="analysis-value">{{ pointRecords.length }} 个点，最近 {{ pointText }}</span>
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
