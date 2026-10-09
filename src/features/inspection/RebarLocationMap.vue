<script setup lang="ts">
import { computed } from 'vue'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'
import { projectMeshBars, problemKind } from './inspection-data'
const props = defineProps<{ bars: RebarComparisonBar[]; selectedId: string; tolerance: number | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const plot = computed(() => projectMeshBars(props.bars))
const kind = (id: string) => problemKind(props.bars.find(bar => bar.ifcGlobalId === id)!, props.tolerance)
</script>
<template>
  <div class="location-map">
    <div v-if="!plot" class="map-empty"><strong>暂无中心线坐标</strong><p>本批次没有可用于定位的纵向测量数据。请打开分析工作区检查配准和补扫范围。</p></div>
    <svg v-else viewBox="0 0 900 480" role="img" aria-label="钢筋中心线定位图，BIM坐标XZ俯视，设计与实测等比例投影">
      <path d="M32 24V446H870" class="axes" />
      <text x="38" y="20">Z</text><text x="877" y="451">X</text>
      <text x="40" y="470">X {{ plot.xRange[0]?.toFixed(2) }}～{{ plot.xRange[1]?.toFixed(2) }} m</text>
      <text x="620" y="470">Z {{ plot.zRange[0]?.toFixed(2) }}～{{ plot.zRange[1]?.toFixed(2) }} m</text>
      <g v-for="row in plot.rows" :key="row.id" :class="['bar', kind(row.id), { selected: row.id === selectedId }]" tabindex="0" role="button" :aria-label="`定位钢筋 ${bars.find(bar => bar.ifcGlobalId === row.id)?.name || row.id}`" :aria-pressed="row.id === selectedId" @click="emit('select', row.id)" @keydown.enter.prevent="emit('select', row.id)" @keydown.space.prevent="emit('select', row.id)">
        <title>{{ bars.find(bar => bar.ifcGlobalId === row.id)?.name || row.id }}</title>
        <path v-for="(d, i) in row.design" :key="`hit-${i}`" :d="d" class="hit" />
        <path v-for="(d, i) in row.design" :key="`design-${i}`" :d="d" class="design" />
        <path v-for="(d, i) in row.observed" :key="`observed-${i}`" :d="d" class="observed-line" />
        <circle v-for="(p, i) in row.points" :key="`point-${i}`" :cx="p.x" :cy="p.y" r="1.6" />
      </g>
    </svg>
    <div class="map-legend"><span class="design-key">设计中心线</span><span class="observed-key">实测中心线</span><span class="missing-key">缺测</span><span class="review-key">待复核</span><span class="outlier-key">偏差超阈值</span></div>
    <p class="map-caption">BIM 坐标 · XZ 俯视 · 点击钢筋定位。缺测区段不连线，图形按实际坐标等比例绘制。</p>
  </div>
</template>
<style scoped>
.location-map { display: flex; flex-direction: column; min-height: 0; }
svg { width: 100%; min-height: 200px; max-height: 470px; background: var(--bg-control); border-radius: 8px; }
svg text { font-family: var(--font-family-base); font-size: 14px; fill: var(--text-secondary); }
.axes { stroke: var(--border-color); fill: none; stroke-width: 1; }
.bar { cursor: pointer; color: var(--color-success); }
.bar.missing { color: var(--color-info); }.bar.review { color: var(--color-warning); }.bar.outlier { color: var(--color-danger); }
.design { fill: none; stroke: #a4b5c9; stroke-width: 1.6; stroke-dasharray: 5 4; }
.observed-line { fill: none; stroke: currentColor; stroke-width: 2.1; }.bar circle { fill: currentColor; }
.hit { fill: none; stroke: transparent; stroke-width: 14; }.selected .design { stroke: var(--color-primary); stroke-width: 3; }.selected .observed-line { stroke-width: 4; }
.bar:focus-visible { outline: none; }.bar:focus-visible .design { stroke: var(--brand-sapphire); stroke-width: 5; }
.map-legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-top: 14px; font-size: 14px; color: var(--text-secondary); }.map-legend span { display: flex; align-items: center; gap: 6px; }.map-legend span::before { content: ''; width: 20px; border-top: 3px solid var(--color-success); }.map-legend .design-key::before { border-color: #a4b5c9; border-top-style: dashed; }.map-legend .missing-key::before { border-color: var(--color-info); border-top-style: dashed; }.map-legend .review-key::before { border-color: var(--color-warning); }.map-legend .outlier-key::before { border-color: var(--color-danger); }
.map-caption { margin: 10px 0 0; color: var(--text-secondary); font-size: 14px; line-height: 1.6; }.map-empty { display: grid; align-content: center; min-height: 300px; padding: 24px; text-align: center; background: var(--bg-control); }.map-empty p { max-width: 50ch; margin: 12px auto; line-height: 1.6; }
</style>
