<script setup lang="ts">
import { computed } from 'vue'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'
import { projectRebarProfile } from './rebarProjection'
import { rebarStatusLabel } from './rebarComparison'

const props = defineProps<{ bar: RebarComparisonBar; compact?: boolean; report?: boolean }>()
const views = computed(() => ([
  { label: '俯视 · XZ', axes: [0, 2] as [number, number] },
  { label: '侧视 · XY', axes: [0, 1] as [number, number] },
]).map(view => ({ ...view, plot: projectRebarProfile(props.bar.measurement?.longitudinalProfile ?? [], view.axes) })))
const mm = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? `${(value * 1000).toFixed(2)} mm` : '不可评估'
const maximum = computed(() => props.bar.measurement?.surface.maxAbs ?? (props.bar.stats ? Math.max(Math.abs(props.bar.stats.min), Math.abs(props.bar.stats.max)) : null))
const coverage = computed(() => props.bar.vertexCount ? `${(props.bar.knownCount / props.bar.vertexCount * 100).toFixed(1)}%` : '无设计网格')
</script>

<template>
  <section class="rebar-detail" :class="{ 'rebar-detail--compact': compact, 'rebar-detail--report': report }" :aria-label="`${bar.name || bar.designBarId} 偏差详情`">
    <header>
      <strong>{{ bar.name || bar.designBarId }}</strong>
      <span>{{ rebarStatusLabel(bar.status) }} · 覆盖 {{ coverage }}</span>
    </header>
    <p class="rebar-detail__identity">IFC {{ bar.ifcGlobalId }} · 实例 {{ bar.instanceIds.join('、') || '无可靠对应' }}</p>
    <dl class="rebar-detail__metrics">
      <div><dt>最大表面偏差</dt><dd>{{ mm(maximum) }}</dd></div>
      <div><dt>中心线最大偏移</dt><dd>{{ mm(bar.measurement?.bending.maxCentrelineDepartureM) }}</dd></div>
      <div><dt>弯曲估计（残余弓高）</dt><dd>{{ mm(bar.measurement?.bending.residualBowM) }}</dd></div>
      <div v-if="bar.measurement?.crossSection"><dt>截面半径最大变化</dt><dd>{{ mm(bar.measurement.crossSection.maxAbsRadiusDeltaM) }}</dd></div>
      <div v-if="bar.measurement?.crossSection"><dt>可靠截面</dt><dd>{{ bar.measurement.crossSection.supportedSectionCount }} / {{ bar.measurement.crossSection.sectionCount }}</dd></div>
    </dl>
    <div class="rebar-detail__views">
      <figure v-for="view in views" :key="view.label">
        <figcaption>{{ view.label }}</figcaption>
        <svg v-if="view.plot" viewBox="0 0 600 168" role="img" :aria-label="`${bar.name || bar.designBarId} ${view.label}，设计中心线与实测拟合中心线，等比例投影`">
          <path d="M50 28V158H550" class="rebar-detail__axis" />
          <path v-for="(path, i) in view.plot.designPaths" :key="`d${i}`" :d="path" class="rebar-detail__design" />
          <path v-for="(path, i) in view.plot.observedPaths" :key="`o${i}`" :d="path" class="rebar-detail__observed" />
          <circle v-for="(point, i) in view.plot.observedPoints" :key="i" :cx="point.x" :cy="point.y" r="2.5" class="rebar-detail__point" />
        </svg>
        <div v-else class="rebar-detail__empty">{{ bar.measurement ? '轴线或扫描截面证据不足，无法生成投影。' : '本批次尚无轴线剖面，请启用法向约束后重新计算。' }}</div>
        <p v-if="view.plot" class="rebar-detail__extent">图幅 {{ view.plot.horizontalSpanMm.toFixed(0) }} × {{ view.plot.verticalSpanMm.toFixed(0) }} mm</p>
      </figure>
    </div>
    <p v-if="!report" class="rebar-detail__legend"><span class="rebar-detail__key rebar-detail__key--design"></span>设计中心线 <span class="rebar-detail__key rebar-detail__key--observed"></span>实测拟合中心线 · 断开处为缺测</p>
    <p v-if="!report && bar.measurement?.surface.maxLocationM" class="rebar-detail__note">最大偏差位置（模型 XYZ）：{{ bar.measurement.surface.maxLocationM.map(value => (value * 1000).toFixed(1)).join('，') }} mm</p>
    <p v-if="!report" class="rebar-detail__note">表面偏差与中心线偏移分别统计；弯曲为扣除整体偏移与倾斜后的残余弓高估计。覆盖不足不出具弯曲结论。</p>
    <p v-if="bar.scanSurface" class="rebar-detail__note">按实测轴线的朝外方向匹配同侧扫描表面。缺测背面不补值；截面半径为宏观拟合，包含表面肋纹与扫描噪声的影响。</p>
  </section>
</template>

<style scoped>
.rebar-detail { min-width: 0; color: var(--text-primary); font-size: 14px; }
.rebar-detail header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.rebar-detail header strong { overflow-wrap: anywhere; }
.rebar-detail header span, .rebar-detail__identity, .rebar-detail__note, .rebar-detail__legend { font-size: 12px; color: var(--text-secondary); }
.rebar-detail__identity { overflow-wrap: anywhere; margin: 4px 0 12px; }
.rebar-detail__metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }
.rebar-detail__metrics dt { font-size: 12px; color: var(--text-secondary); }
.rebar-detail__metrics dd { margin: 4px 0 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.rebar-detail__views { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.rebar-detail figure { margin: 0; min-width: 0; border-block: 1px solid var(--border-color-light); padding-block: 8px; }
.rebar-detail figcaption { font-size: 12px; font-weight: 600; }
.rebar-detail svg { display: block; width: 100%; overflow: visible; }
.rebar-detail__extent { margin: 0; font-size: 12px; color: var(--text-secondary); text-align: center; font-variant-numeric: tabular-nums; }
.rebar-detail__axis { fill: none; stroke: var(--border-color); stroke-width: 1; }
.rebar-detail__design { fill: none; stroke: var(--text-secondary); stroke-width: 2; stroke-dasharray: 7 4; }
.rebar-detail__observed { fill: none; stroke: var(--color-primary); stroke-width: 2; }
.rebar-detail__point { fill: var(--color-primary); }
.rebar-detail__empty { display: grid; place-content: center; min-height: 90px; font-size: 12px; line-height: 1.6; color: var(--text-secondary); }
.rebar-detail__legend { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.rebar-detail__key { display: inline-block; width: 18px; height: 0; border-top: 2px dashed var(--text-secondary); }
.rebar-detail__key--observed { border-top: 2px solid var(--color-primary); }
.rebar-detail__note { line-height: 1.6; margin: 8px 0 0; }
.rebar-detail--compact .rebar-detail__views { grid-template-columns: 1fr; }
.rebar-detail--compact .rebar-detail__metrics { grid-template-columns: 1fr; }
.rebar-detail--compact .rebar-detail__metrics div { display: flex; justify-content: space-between; gap: 8px; }
.rebar-detail--compact .rebar-detail__metrics dd { margin: 0; }
.rebar-detail--report .rebar-detail__metrics { margin-block: 8px; }
@media print { .rebar-detail { break-inside: avoid; } }
</style>
