<script setup lang="ts">
import { computed } from 'vue'
import type { DenoiseResult } from '@/api/backend-denoise'
import { DENOISE_CLASSES, type DenoiseColorMode } from './denoisePreview'

const props = defineProps<{
  result: DenoiseResult | null
  running: boolean
  loading: boolean
  error: string
  canRun: boolean
  canInspect: boolean
  view: 'source' | DenoiseColorMode
  colorMode: DenoiseColorMode
  previewLoading: boolean
  visibleClasses: number[]
  visiblePointCount: number
}>()
const emit = defineEmits<{
  run: []
  preview: [mode: 'source' | DenoiseColorMode]
  visibility: [classes: number[]]
  download: []
}>()
const controlsDisabled = computed(() => !props.canInspect || props.previewLoading || props.view === 'source')
const status = computed(() => props.running ? '处理中' : props.loading ? '读取中'
  : props.result && !props.result.fresh ? '需重新处理' : props.canInspect ? '已完成' : '待处理')
const retainedPercent = computed(() => {
  const result = props.result?.result
  return result?.pointsBefore ? (result.pointsAfter / result.pointsBefore * 100).toFixed(1) : '0.0'
})
function toggleClass(id: number) {
  emit('visibility', props.visibleClasses.includes(id)
    ? props.visibleClasses.filter(value => value !== id) : [...props.visibleClasses, id])
}
function showAll(includeNoise: boolean) {
  emit('visibility', DENOISE_CLASSES.filter(item => includeNoise || item.key !== 'noise').map(item => item.id))
}
</script>

<template>
  <div class="denoise-workspace" aria-label="点云分类与去噪控制">
    <section class="denoise-card denoise-run-card">
      <div class="denoise-heading">
        <h2>设计辅助去噪</h2>
        <span class="denoise-status" :class="{ 'is-ready': canInspect, 'is-stale': result && !result.fresh }" role="status">{{ status }}</span>
      </div>
      <p class="denoise-copy">根据 BIM 设计识别钢筋、夹具与噪声，保留钢筋点用于后续偏差分析。</p>
      <el-button class="denoise-primary-action" type="primary" :loading="running" :disabled="!canRun || loading" @click="emit('run')">
        {{ result ? '重新分类与去噪' : '开始分类与去噪' }}
      </el-button>
      <p v-if="running" class="denoise-copy" role="status">正在处理全量点云，大点云可能需要数分钟。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-alert v-if="result && !result.fresh" :title="result.staleReason || '去噪结果已失效，请重新运行'" type="warning" :closable="false" show-icon />
    </section>

    <template v-if="result">
      <section class="denoise-card" aria-label="去噪结果概览">
        <div class="denoise-heading"><h3>结果概览</h3><span class="denoise-caption">{{ result.fresh ? '钢筋保留率' : '上次保留率' }} {{ retainedPercent }}%</span></div>
        <div class="denoise-metrics">
          <div><span>处理前 · 无台面点</span><strong>{{ result.result.pointsBefore.toLocaleString() }}</strong></div>
          <div class="denoise-metric-kept"><span>保留钢筋点</span><strong>{{ result.result.pointsAfter.toLocaleString() }}</strong></div>
        </div>
        <div class="denoise-result-meta"><span>钢筋实例 <b>{{ result.result.instanceCount.toLocaleString() }}</b></span><span>耗时 {{ result.result.elapsedSeconds.toFixed(1) }} 秒</span></div>
      </section>

      <section class="denoise-card denoise-display-card" aria-label="配色与类别">
        <div class="denoise-heading"><h3>配色与类别</h3><span class="denoise-caption">快速查看</span></div>
        <div class="denoise-segments" role="group" aria-label="点云预览">
          <button type="button" :aria-pressed="view === 'source'" @click="emit('preview', 'source')">无台面点云</button>
          <button type="button" :aria-pressed="view !== 'source'" :disabled="!canInspect || previewLoading" @click="emit('preview', colorMode)">去噪结果</button>
        </div>
        <p v-if="previewLoading" class="denoise-copy" role="status">正在加载结果预览…</p>
        <p v-else-if="view === 'source'" class="denoise-copy">切换到「去噪结果」即可按类别查看点云。</p>
        <div class="denoise-color-control">
          <span>点云配色</span>
          <div class="denoise-segments" role="group" aria-label="点云配色">
            <button type="button" :aria-pressed="colorMode === 'classes'" :disabled="!canInspect || previewLoading" @click="emit('preview', 'classes')">按类别</button>
            <button type="button" :aria-pressed="colorMode === 'cleaned'" :disabled="!canInspect || previewLoading" @click="emit('preview', 'cleaned')">按实例</button>
          </div>
        </div>
        <div class="denoise-filter-toolbar">
          <span>类别显隐</span>
          <div>
            <button type="button" :disabled="controlsDisabled" @click="showAll(true)">全显</button>
            <button type="button" :disabled="controlsDisabled" @click="showAll(false)">不含噪声</button>
            <button type="button" :disabled="controlsDisabled" @click="emit('visibility', [])">全隐</button>
          </div>
        </div>
        <div class="denoise-class-list" role="group" aria-label="类别显隐">
          <div v-for="item in DENOISE_CLASSES" :key="item.id" class="denoise-class-row" :class="{ 'is-hidden': !visibleClasses.includes(item.id) }">
            <button type="button" class="denoise-class-toggle" role="switch" :aria-checked="visibleClasses.includes(item.id)" :aria-label="`显示${item.name}`" :disabled="controlsDisabled" @click="toggleClass(item.id)">
              <span class="denoise-switch" aria-hidden="true"><i /></span>
              <i class="denoise-swatch" :class="{ 'is-instances': item.id === 3 && colorMode === 'cleaned' }" :style="{ backgroundColor: item.color }" aria-hidden="true" />
              <span>{{ item.name }}</span>
            </button>
            <span class="denoise-class-count" :title="`${item.name}全量点数`">{{ result.result.counts[item.key].toLocaleString() }}</span>
            <button type="button" class="denoise-only-button" :disabled="controlsDisabled" :aria-label="`只看${item.name}`" @click="emit('visibility', [item.id])">只看</button>
          </div>
        </div>
        <p v-if="view !== 'source' && !previewLoading" class="denoise-visible-count" role="status">
          {{ visibleClasses.length === 0 ? '所有类别已隐藏，可打开开关或点击「全显」。' : visiblePointCount === 0 ? '当前类别没有可显示的采样点。' : `当前显示 ${visiblePointCount.toLocaleString()} 个预览点` }}
        </p>
        <p class="denoise-copy denoise-display-hint">{{ colorMode === 'cleaned' ? '同一根钢筋保持同色，浅灰钢筋点表示归属未定。' : '按类别统一着色，颜色与上方图例一致。' }}类别点数为全量统计，预览最多采样 50 万点。</p>
      </section>
      <el-button class="denoise-download" :disabled="!canInspect" @click="emit('download')">下载去噪点云（LAS）</el-button>
      <p class="denoise-copy denoise-export-hint">显隐与配色仅影响预览；下载和偏差计算使用全量保留钢筋点。</p>
    </template>
    <p v-else-if="!running && !loading" class="denoise-copy denoise-empty-hint">处理完成后，可在这里切换配色、单独查看类别并下载去噪点云。</p>
  </div>
</template>

<style scoped>
.denoise-workspace { display: flex; flex-direction: column; gap: 12px; min-width: 0; padding: 12px; color: var(--text-primary); background: var(--bg-page, #f8fafc); }
.denoise-card { display: flex; flex-direction: column; gap: 12px; padding: 12px; background: var(--bg-card, #fff); border: 1px solid var(--border-color-light, #e2e8f0); border-radius: 10px; }
.denoise-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.denoise-heading h2, .denoise-heading h3 { margin: 0; font-size: 14px; line-height: 1.5; font-weight: 650; color: var(--text-primary); }
.denoise-status { padding: 3px 8px; border-radius: 6px; color: var(--text-secondary); background: var(--bg-page, #f1f5f9); font-size: 11px; }
.denoise-status.is-ready { color: #047857; background: #ecfdf5; }
.denoise-status.is-stale { color: #92400e; background: #fffbeb; }
.denoise-copy { margin: 0; color: var(--text-secondary, #64748b); font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.denoise-primary-action { width: 100%; min-height: 36px; }
.denoise-caption { color: var(--text-secondary); font-size: 11px; }
.denoise-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.denoise-metrics > div { padding: 12px 10px; background: var(--bg-page, #f8fafc); border-radius: 8px; }
.denoise-metrics span { display: block; color: var(--text-secondary); font-size: 11px; }
.denoise-metrics strong { display: block; margin-top: 6px; font-size: clamp(15px, 1.2vw, 20px); font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.denoise-metrics .denoise-metric-kept { background: #ecfdf5; }
.denoise-metric-kept strong { color: #047857; }
.denoise-result-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; color: var(--text-secondary); font-size: 11px; }
.denoise-result-meta b { color: var(--text-primary); font-weight: 600; }
.denoise-segments { display: flex; min-width: 0; padding: 3px; gap: 3px; background: var(--bg-page, #f1f5f9); border: 1px solid var(--border-color-light, #e2e8f0); border-radius: 8px; }
.denoise-segments button { flex: 1; min-width: 0; min-height: 30px; padding: 5px 10px; border: 0; border-radius: 5px; background: transparent; color: var(--text-secondary); font: inherit; font-size: 12px; cursor: pointer; }
.denoise-segments button[aria-pressed="true"] { background: var(--bg-card, #fff); color: var(--color-primary, #2563eb); box-shadow: 0 1px 4px #0f172a14; font-weight: 600; }
.denoise-color-control, .denoise-filter-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; }
.denoise-color-control .denoise-segments { flex: 0 1 180px; }
.denoise-filter-toolbar { margin-top: 4px; }
.denoise-filter-toolbar > div { display: flex; gap: 2px; }
.denoise-filter-toolbar button, .denoise-only-button { padding: 5px 6px; min-height: 28px; border: 0; border-radius: 4px; background: transparent; color: var(--color-primary, #2563eb); font: inherit; font-size: 11px; white-space: nowrap; cursor: pointer; }
.denoise-class-list { border: 1px solid var(--border-color-light, #e2e8f0); border-radius: 8px; overflow: hidden; }
.denoise-class-row { display: flex; align-items: center; gap: 6px; min-height: 43px; padding: 4px 8px; }
.denoise-class-row + .denoise-class-row { border-top: 1px solid var(--border-color-light, #e2e8f0); }
.denoise-class-toggle { display: flex; flex: 1; align-items: center; gap: 7px; min-width: 0; min-height: 32px; padding: 0; border: 0; color: inherit; background: transparent; font: inherit; font-size: 12px; cursor: pointer; text-align: left; }
.denoise-class-toggle > span:last-child { white-space: nowrap; }
.denoise-switch { display: flex; align-items: center; flex-shrink: 0; width: 26px; height: 15px; padding: 2px; box-sizing: border-box; border-radius: 12px; background: var(--color-primary, #2563eb); }
.denoise-switch i { width: 11px; height: 11px; border-radius: 50%; background: white; transform: translateX(11px); transition: transform 120ms ease; }
.denoise-swatch { width: 8px; height: 8px; flex-shrink: 0; border-radius: 2px; }
.denoise-swatch.is-instances { background-image: linear-gradient(135deg, #38bdf8 0% 33%, #f472b6 33% 66%, #facc15 66%); }
.denoise-class-count { color: var(--text-secondary); font-size: 11px; font-variant-numeric: tabular-nums; }
.is-hidden .denoise-switch { background: #94a3b8; }
.is-hidden .denoise-switch i { transform: translateX(0); }
.is-hidden .denoise-swatch { opacity: .4; }
.is-hidden .denoise-class-toggle { color: var(--text-secondary); }
.denoise-visible-count { margin: 0; color: var(--color-primary, #2563eb); font-size: 11px; line-height: 1.6; }
.denoise-display-hint { padding-top: 10px; border-top: 1px solid var(--border-color-light, #e2e8f0); font-size: 11px; }
.denoise-download { width: 100%; margin: 0; min-height: 36px; }
.denoise-export-hint, .denoise-empty-hint { padding: 0 4px; font-size: 11px; }
.denoise-workspace button:disabled { opacity: .45; cursor: not-allowed; }
.denoise-workspace button:focus-visible { outline: 2px solid var(--color-primary, #2563eb); outline-offset: -2px; }
.denoise-filter-toolbar button:hover:not(:disabled), .denoise-only-button:hover:not(:disabled) { background: var(--bg-page, #eff6ff); }
@media (prefers-reduced-motion: reduce) { .denoise-switch i { transition: none; } }
</style>
