<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { RefreshLeft } from '@element-plus/icons-vue'

export type PointcloudColorRamp = 'grayscale' | 'spectrum' | 'viridis'
export type PointcloudColorRange = { min: number; max: number }

const props = withDefaults(defineProps<{
  ramp?: PointcloudColorRamp
  range?: PointcloudColorRange
  histogram?: ArrayLike<number>
}>(), {
  ramp: 'grayscale',
  range: () => ({ min: 0, max: 1 }),
  histogram: () => [],
})

const emit = defineEmits<{
  (event: 'update:range', range: PointcloudColorRange): void
}>()

const trackRef = ref<HTMLDivElement | null>(null)
const draft = ref<PointcloudColorRange | null>(null)
const visibleRange = computed(() => draft.value ?? props.range)
const minPct = computed(() => visibleRange.value.min * 100)
const maxPct = computed(() => visibleRange.value.max * 100)
let dragMode: 'min' | 'max' | 'pan' | null = null
let dragX = 0
let dragStart: PointcloudColorRange = { min: 0, max: 1 }

const gradient = computed(() => {
  if (props.ramp === 'spectrum') {
    return 'linear-gradient(to right, #0000ff 0%, #00ffff 25%, #00ff00 50%, #ffff00 75%, #ff0000 100%)'
  }
  if (props.ramp === 'viridis') {
    return 'linear-gradient(to right, #440154 0%, #3b528b 25%, #21918c 50%, #5ec962 75%, #fde725 100%)'
  }
  return 'linear-gradient(to right, #000 0%, #fff 100%)'
})

const histogramValues = computed<ArrayLike<number>>(() => {
  if ((props.histogram?.length ?? 0) >= 2) {
    let peak = 0
    for (let index = 0; index < props.histogram.length; index++) {
      peak = Math.max(peak, Number(props.histogram[index]) || 0)
    }
    if (peak > 0) return props.histogram
  }
  return Array.from({ length: 64 }, (_, index) => {
    const x = index / 63
    const main = Math.exp(-Math.pow((x - 0.57) / 0.2, 2))
    const shoulder = 0.42 * Math.exp(-Math.pow((x - 0.25) / 0.1, 2))
    const ripple = 0.08 * (Math.sin(index * 0.82) + 1)
    return main + shoulder + ripple
  })
})

const histogramPoints = computed(() => {
  const values = histogramValues.value
  const count = values.length
  let peak = 0
  for (let i = 0; i < count; i++) peak = Math.max(peak, Number(values[i]) || 0)
  if (!peak) return []
  const points: Array<{ x: number; y: number }> = []
  for (let i = 0; i < count; i++) {
    const x = ((i + 0.5) / count) * 100
    const y = 28 - ((Number(values[i]) || 0) / peak) * 25
    points.push({ x, y })
  }
  return points
})

const histogramStrokePath = computed(() => {
  const points = histogramPoints.value
  if (!points.length) return ''
  let path = `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`
  for (let i = 1; i < points.length; i++) {
    const previous = points[i - 1]
    const current = points[i]
    path += ` Q ${previous.x.toFixed(2)} ${previous.y.toFixed(2)} ${((previous.x + current.x) / 2).toFixed(2)} ${((previous.y + current.y) / 2).toFixed(2)}`
  }
  const last = points[points.length - 1]
  return `${path} L ${last.x.toFixed(2)} ${last.y.toFixed(2)}`
})

const histogramFillPath = computed(() => {
  if (!histogramStrokePath.value) return ''
  return `M 0 28 L ${histogramStrokePath.value.slice(2)} L 100 28 Z`
})

function clampRange(min: number, max: number) {
  let low = Math.min(1, Math.max(0, min))
  let high = Math.min(1, Math.max(0, max))
  if (high - low < 0.01) {
    if (dragMode === 'min') low = Math.max(0, high - 0.01)
    else high = Math.min(1, low + 0.01)
  }
  return { min: low, max: high }
}

function pointerValue(event: PointerEvent) {
  const rect = trackRef.value?.getBoundingClientRect()
  if (!rect?.width) return 0
  return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
}

function beginDrag(mode: 'min' | 'max' | 'pan', event: PointerEvent) {
  event.preventDefault()
  event.stopPropagation()
  dragMode = mode
  dragX = event.clientX
  dragStart = { ...visibleRange.value }
  draft.value = { ...visibleRange.value }
  window.addEventListener('pointermove', moveDrag)
  window.addEventListener('pointerup', endDrag)
  window.addEventListener('pointercancel', endDrag)
}

function beginTrackDrag(event: PointerEvent) {
  const value = pointerValue(event)
  const current = visibleRange.value
  beginDrag(value < (current.min + current.max) / 2 ? 'min' : 'max', event)
  draft.value = value < (current.min + current.max) / 2
    ? clampRange(value, current.max)
    : clampRange(current.min, value)
}

function moveDrag(event: PointerEvent) {
  if (!dragMode) return
  if (dragMode === 'pan') {
    const width = trackRef.value?.getBoundingClientRect().width ?? 1
    const span = dragStart.max - dragStart.min
    let min = dragStart.min + (event.clientX - dragX) / width
    min = Math.min(1 - span, Math.max(0, min))
    draft.value = { min, max: min + span }
    return
  }
  const value = pointerValue(event)
  draft.value = dragMode === 'min'
    ? clampRange(value, visibleRange.value.max)
    : clampRange(visibleRange.value.min, value)
}

function endDrag() {
  if (draft.value) emit('update:range', { ...draft.value })
  draft.value = null
  dragMode = null
  window.removeEventListener('pointermove', moveDrag)
  window.removeEventListener('pointerup', endDrag)
  window.removeEventListener('pointercancel', endDrag)
}

function setRange(range: PointcloudColorRange) {
  draft.value = null
  emit('update:range', range)
}

onBeforeUnmount(endDrag)
</script>

<template>
  <div class="pointcloud-color-range" role="group" aria-label="强度显示区间">
    <div class="range-edge">
      <button type="button" title="重置为全幅 0-1" aria-label="重置显示区间" @click="setRange({ min: 0, max: 1 })">
        <el-icon :size="13"><RefreshLeft /></el-icon>
      </button>
      <span>{{ visibleRange.min.toFixed(2) }}</span>
    </div>

    <div class="range-body">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
        <path v-if="histogramFillPath" :d="histogramFillPath" class="histogram-fill" />
        <path v-if="histogramStrokePath" :d="histogramStrokePath" class="histogram-stroke" />
      </svg>
      <div ref="trackRef" class="range-track" :style="{ background: gradient }" @pointerdown="beginTrackDrag">
        <i class="range-dim range-dim-start" :style="{ width: `${minPct}%` }"></i>
        <i class="range-dim range-dim-end" :style="{ width: `${100 - maxPct}%` }"></i>
        <i
          class="range-selection"
          :style="{ left: `${minPct}%`, width: `${maxPct - minPct}%` }"
          @pointerdown="beginDrag('pan', $event)"
        ></i>
        <button
          class="range-handle"
          type="button"
          aria-label="区间下限"
          :style="{ left: `${minPct}%` }"
          @pointerdown="beginDrag('min', $event)"
        ></button>
        <button
          class="range-handle"
          type="button"
          aria-label="区间上限"
          :style="{ left: `${maxPct}%` }"
          @pointerdown="beginDrag('max', $event)"
        ></button>
      </div>
    </div>

    <div class="range-edge range-edge-end">
      <button type="button" title="收束到 2%-98%" aria-label="百分位显示区间 2% 到 98%" @click="setRange({ min: 0.02, max: 0.98 })">%</button>
      <span>{{ visibleRange.max.toFixed(2) }}</span>
    </div>
  </div>
</template>

<style scoped>
.pointcloud-color-range {
  box-sizing: border-box;
  width: 100%;
  height: 52px;
  padding: 6px 8px;
  display: flex;
  align-items: stretch;
  gap: 8px;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 8px;
  color: rgb(226 232 240 / 82%);
  background: rgb(26 29 36 / 90%);
  box-shadow: 0 10px 28px rgb(0 0 0 / 28%);
  backdrop-filter: blur(6px);
  user-select: none;
  touch-action: none;
}

.range-edge {
  min-width: 36px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.range-edge button {
  width: 20px;
  height: 20px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 6px;
  color: rgb(226 232 240 / 78%);
  background: rgb(255 255 255 / 10%);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.range-edge button:hover {
  color: #9ec1ff;
  background: rgb(255 255 255 / 16%);
}

.range-body {
  position: relative;
  flex: 1 1 auto;
  min-width: 0;
}

.range-body svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.histogram-fill {
  fill: rgb(186 214 255 / 42%);
  stroke: none;
}

.histogram-stroke {
  fill: none;
  stroke: #e8f1ff;
  stroke-width: 1.5;
  stroke-linejoin: round;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}

.range-track {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 12px;
  border-radius: 6px;
  cursor: pointer;
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 18%), 0 1px 2px rgb(0 0 0 / 30%);
}

.range-dim,
.range-selection,
.range-handle {
  position: absolute;
}

.range-dim {
  top: 0;
  bottom: 0;
  background: rgb(8 10 14 / 55%);
  pointer-events: none;
}

.range-dim-start {
  left: 0;
  border-radius: 7px 0 0 7px;
}

.range-dim-end {
  right: 0;
  border-radius: 0 7px 7px 0;
}

.range-selection {
  top: -2px;
  bottom: -2px;
  border: 1px solid rgb(255 255 255 / 88%);
  border-radius: 8px;
  box-shadow: 0 0 0 1px rgb(0 0 0 / 30%);
  cursor: grab;
}

.range-handle {
  top: 50%;
  width: 8px;
  height: 18px;
  padding: 0;
  border: 1px solid rgb(255 255 255 / 95%);
  border-radius: 999px;
  background: #6d8fff;
  box-shadow: 0 1px 3px rgb(0 0 0 / 40%);
  transform: translate(-50%, -50%);
  cursor: ew-resize;
}
</style>
