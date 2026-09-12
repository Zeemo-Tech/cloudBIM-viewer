<script setup lang="ts">
import { computed } from 'vue'
import { c2mColorCss } from '@/utils/c2mColormap'
import type { C2MResult, C2MVisualization } from '@/api/backend-c2m'

const props = withDefaults(defineProps<{
  result: C2MResult
  compact?: boolean
  colorMode?: 'continuous' | 'discrete'
  bandCount?: number
}>(), {
  compact: false,
  colorMode: 'continuous',
  bandCount: 7,
})

const DEFAULT_VISUALIZATION: C2MVisualization = {
  maxColormapDistance: 0.1,
  maxHistogramDistance: 0.1,
  histogramBins: 50,
  toleranceLimit: 0.05,
}

const visualization = computed(() => {
  const source = props.result.visualization ?? DEFAULT_VISUALIZATION
  const maxColormapDistance = positiveOr(source.maxColormapDistance, DEFAULT_VISUALIZATION.maxColormapDistance)
  return {
    maxColormapDistance,
    maxHistogramDistance: positiveOr(source.maxHistogramDistance, DEFAULT_VISUALIZATION.maxHistogramDistance),
    histogramBins: Math.max(1, Math.round(positiveOr(source.histogramBins, DEFAULT_VISUALIZATION.histogramBins))),
    toleranceLimit: Math.min(
      positiveOr(source.toleranceLimit, DEFAULT_VISUALIZATION.toleranceLimit),
      maxColormapDistance,
    ),
  }
})

const histogram = computed(() => {
  const source = props.result.histogram
  if (!source || Array.isArray(source) || !Array.isArray(source.binEdges) || !Array.isArray(source.counts)) return null
  if (!source.counts.length || source.binEdges.length !== source.counts.length + 1) return null
  if (!source.binEdges.every(Number.isFinite)) return null

  const counts = source.counts.map((value) => Math.max(0, Number(value) || 0))
  const min = source.binEdges[0]
  const max = source.binEdges[source.binEdges.length - 1]
  if (!(max > min)) return null

  const inRangeCount = counts.reduce((sum, count) => sum + count, 0)
  const reportedOverflow = Number(source.overflowCount)
  const derivedOverflow = Number.isFinite(props.result.meshVertexCount)
    ? Math.max(0, (props.result.diagnostics?.rebarComparison?.knownVertexCount ?? props.result.meshVertexCount) - inRangeCount)
    : 0

  return {
    counts,
    underflowCount: source.underflowCount,
    positiveOverflowCount: source.positiveOverflowCount,
    unknownCount: source.unknownCount ?? props.result.diagnostics?.rebarComparison?.unknownVertexCount ?? 0,
    edges: source.binEdges,
    min,
    max,
    peak: Math.max(...counts),
    overflowCount: Number.isFinite(reportedOverflow)
      ? Math.max(0, reportedOverflow)
      : derivedOverflow,
  }
})

const bars = computed(() => {
  const source = histogram.value
  if (!source?.peak) return []
  return source.counts.map((count, index) => ({
    x: (index / source.counts.length) * 100,
    width: 100 / source.counts.length,
    height: (count / source.peak) * 48,
    count,
    color: colorAt((source.edges[index] + source.edges[index + 1]) / 2),
    min: source.edges[index],
    max: source.edges[index + 1],
  }))
})

const toleranceBand = computed(() => {
  const source = histogram.value
  if (!source) return null
  const tolerance = visualization.value.toleranceLimit
  const start = valuePosition(Math.max(source.min, -tolerance), source.min, source.max)
  const end = valuePosition(Math.min(source.max, tolerance), source.min, source.max)
  if (end <= start) return null
  return { x: start, width: end - start }
})

const zeroPosition = computed(() => {
  const source = histogram.value
  if (!source || source.min > 0 || source.max < 0) return null
  return valuePosition(0, source.min, source.max)
})

const inRangeCount = computed(() => histogram.value?.counts.reduce((sum, count) => sum + count, 0) ?? 0)
const histogramDescription = computed(() => {
  const source = histogram.value
  if (!source) return '暂无偏差分布数据'
  const overflow = source.overflowCount
    ? `，区间外 ${formatCount(source.overflowCount)} 个顶点`
    : ''
  return `偏差范围 ${formatDistance(source.min)} 至 ${formatDistance(source.max)}，区间内 ${formatCount(inRangeCount.value)} 个顶点${overflow}`
})

function colorAt(distance: number) {
  return c2mColorCss(distance, visualization.value.toleranceLimit, visualization.value.maxColormapDistance, props.colorMode === 'discrete', props.bandCount)
}

// The legend is linear in millimetres, just like the histogram, including the tolerance boundaries.
const ramp = computed(() => {
  const limit = visualization.value.maxColormapDistance
  const tol = visualization.value.toleranceLimit
  const distances = Array.from({ length: 201 }, (_, i) => -limit + i / 200 * 2 * limit)
  distances.push(-tol - limit * 1e-7, -tol, tol, tol + limit * 1e-7)
  return `linear-gradient(90deg, ${distances.sort((a, b) => a - b).filter(d => Math.abs(d) <= limit).map(d => `${colorAt(d)} ${(d + limit) / (2 * limit) * 100}%`).join(', ')})`
})
const toleranceRatio = computed(() => {
  const value = props.result.stats?.withinToleranceRatio
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
})

function positiveOr(value: number | undefined, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback
}

function valuePosition(value: number, min: number, max: number) {
  return ((value - min) / (max - min)) * 100
}

function formatDistance(value: number) {
  const millimeters = value * 1000
  const absolute = Math.abs(millimeters)
  const digits = absolute >= 10 ? 0 : absolute >= 1 ? 1 : 2
  return `${millimeters > 0 ? '+' : ''}${millimeters.toFixed(digits)} mm`
}

function formatCount(value: number) {
  return Math.round(value).toLocaleString()
}
</script>

<template>
  <section class="c2m-histogram" :class="{ 'is-compact': compact }" aria-label="C2M 偏差分布与色标">
    <template v-if="histogram">
      <div class="c2m-histogram__header">
        <span>整体偏差分布</span>
        <span class="c2m-histogram__meta">
          已覆盖 {{ formatCount(inRangeCount + histogram.overflowCount) }} 顶点
          <template v-if="histogram.overflowCount"> · 区间外 {{ formatCount(histogram.overflowCount) }} 顶点</template>
        </span>
      </div>

      <div class="c2m-histogram__plot">
        <svg
          viewBox="0 0 100 58"
          preserveAspectRatio="none"
          role="img"
          :aria-label="histogramDescription"
        >
          <rect
            v-if="toleranceBand"
            :x="toleranceBand.x"
            y="4"
            :width="toleranceBand.width"
            height="50"
            class="c2m-histogram__tolerance"
          />
          <rect
            v-for="(bar, index) in bars"
            :key="index"
            :x="bar.x + Math.min(0.12, bar.width * 0.08)"
            :y="54 - bar.height"
            :width="Math.max(0.15, bar.width - Math.min(0.24, bar.width * 0.16))"
            :height="bar.height"
            class="c2m-histogram__bar"
            :style="{ fill: bar.color }"
          >
            <title>{{ formatDistance(bar.min) }} 至 {{ formatDistance(bar.max) }}：{{ formatCount(bar.count) }} 顶点</title>
          </rect>
          <template v-if="toleranceBand">
            <line v-for="x in [toleranceBand.x, toleranceBand.x + toleranceBand.width]" :key="x" :x1="x" y1="2" :x2="x" y2="56" class="c2m-histogram__threshold" />
          </template>
          <line v-if="zeroPosition !== null" :x1="zeroPosition" y1="2" :x2="zeroPosition" y2="56" class="c2m-histogram__zero" />
          <line x1="0" y1="54.5" x2="100" y2="54.5" class="c2m-histogram__axis" />
        </svg>
        <div class="c2m-histogram__range-labels">
          <span>{{ formatDistance(histogram.min) }}</span>
          <span v-if="histogram.min <= 0 && histogram.max >= 0">0 mm</span>
          <span>{{ formatDistance(histogram.max) }}</span>
        </div>
      </div>

    </template>
    <div v-else class="c2m-histogram__empty">直方图待更新，保存设置后获取分布。</div>
    <div class="c2m-histogram__legend-title">
      <span>容差 ±{{ (visualization.toleranceLimit * 1000).toLocaleString() }} mm</span>
      <span v-if="typeof toleranceRatio === 'number'">容差内 {{ (toleranceRatio * 100).toFixed(1) }}%</span>
    </div>
    <div class="c2m-histogram__ramp" :style="{ background: ramp }" />
    <div class="c2m-histogram__range-labels">
      <span>≤ {{ formatDistance(-visualization.maxColormapDistance) }}</span>
      <span>0 mm</span>
      <span>≥ {{ formatDistance(visualization.maxColormapDistance) }}</span>
    </div>
    <div class="c2m-histogram__key">
      <span><i style="background: #3b82f6" />负向超差</span>
      <span><i style="background: #22c55e" />容差内</span>
      <span><i style="background: #ff5252" />正向超差</span>
      <span><i style="background: #a8b2c1" />缺测{{ histogram?.unknownCount ? ` ${formatCount(histogram.unknownCount)}` : '' }}</span>
    </div>
    <div v-if="histogram?.overflowCount" class="c2m-histogram__tails">
      <template v-if="histogram.underflowCount !== undefined && histogram.positiveOverflowCount !== undefined">
        ← {{ formatCount(histogram.underflowCount) }} 低于图表下界 · {{ formatCount(histogram.positiveOverflowCount) }} 高于上界 →
      </template>
      <template v-else>图表外 {{ formatCount(histogram.overflowCount) }} 个顶点</template>
      <span>图表外顶点仍按色标着色，并计入容差统计。</span>
    </div>
  </section>
</template>

<style scoped>
.c2m-histogram {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 10px;
  border: 1px solid rgb(148 163 184 / 24%);
  border-radius: 6px;
  color: #e5edf8;
  background: rgb(10 18 31 / 88%);
  box-shadow: 0 10px 28px rgb(0 0 0 / 24%);
  backdrop-filter: blur(8px);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.c2m-histogram__header,
.c2m-histogram__legend-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #f8fafc;
  font-weight: 600;
  flex-wrap: wrap;
}

.c2m-histogram__meta {
  min-width: 0;
  color: #9aa9bd;
  font-size: 10px;
  font-weight: 400;
  overflow-wrap: anywhere;
}

.c2m-histogram__plot {
  margin-top: 7px;
}

.c2m-histogram__plot svg {
  display: block;
  width: 100%;
  height: 64px;
  overflow: visible;
}

.c2m-histogram__tolerance {
  fill: rgb(0 200 83 / 12%);
}

.c2m-histogram__bar {
  opacity: 0.9;
}

.c2m-histogram__bar:hover {
  opacity: 1;
  stroke: #fff;
  stroke-width: 0.25;
}

.c2m-histogram__zero {
  stroke: #ffffff;
  stroke-width: 1;
  stroke-dasharray: 2 2;
  vector-effect: non-scaling-stroke;
}

.c2m-histogram__axis {
  stroke: rgb(226 232 240 / 32%);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.c2m-histogram__range-labels {
  display: grid;
  color: #9aa9bd;
  font-size: 9px;
  line-height: 1.25;
}

.c2m-histogram__range-labels {
  grid-template-columns: repeat(3, 1fr);
  margin-top: 2px;
}

.c2m-histogram__range-labels span:nth-child(2) {
  text-align: center;
}

.c2m-histogram__range-labels span:last-child {
  text-align: right;
}

.c2m-histogram__range-labels span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.c2m-histogram__legend-title {
  margin-top: 10px;
}

.c2m-histogram__ramp {
  height: 12px;
  margin-top: 6px;
  border: 1px solid rgb(255 255 255 / 20%);
  border-radius: 3px;
}

.c2m-histogram__empty {
  padding: 12px 0;
  color: #94a3b8;
  text-align: center;
}

.c2m-histogram.is-compact {
  padding: 8px;
}

.c2m-histogram.is-compact .c2m-histogram__plot svg {
  height: 52px;
}
.c2m-histogram__threshold {
  stroke: #86efac;
  stroke-width: 1;
  stroke-dasharray: 3 2;
  vector-effect: non-scaling-stroke;
}
.c2m-histogram__key { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 10px; color: #cbd5e1; }
.c2m-histogram__key span { display: inline-flex; align-items: center; gap: 4px; }
.c2m-histogram__key i { width: 8px; height: 8px; border-radius: 2px; }
.c2m-histogram__tails { margin-top: 8px; padding: 6px 8px; border-radius: 4px; background: #ffffff0a; color: #cbd5e1; line-height: 1.6; }
.c2m-histogram__tails span { display: block; color: #94a3b8; }
</style>
