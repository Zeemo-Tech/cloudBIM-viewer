<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { downloadC2MReportJSON, getC2MReport, listC2MReports, type C2MReportRun, type RebarComparisonBar } from '@cloudbim/viewer-core'
import RebarDeviationDetail from './RebarDeviationDetail.vue'

const props = defineProps<{ scanId: number; bimId: number; resultVersion?: string }>()
const runs = ref<C2MReportRun[]>([])
const bars = ref<RebarComparisonBar[]>([])
const selectedVersion = ref('')
const selectedBar = ref('')
const page = ref(1)
const barPage = ref(1)
const total = ref(0)
const barTotal = ref(0)
const loading = ref(false)
const error = ref('')
const downloading = ref(false)
let generation = 0
const date = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
async function loadRuns() {
  const request = ++generation
  loading.value = true; error.value = ''; bars.value = []; selectedVersion.value = ''; runs.value = []
  try {
    const response = await listC2MReports(props.scanId, props.bimId, page.value)
    if (request !== generation) return
    runs.value = response.data.items
    total.value = response.data.total
  } catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '历史批次加载失败，请重试。' }
  finally { if (request === generation) loading.value = false }
}
async function loadBars() {
  if (!selectedVersion.value) { bars.value = []; return }
  const request = ++generation
  loading.value = true; error.value = ''; bars.value = []; selectedBar.value = ''
  try {
    const response = await getC2MReport(selectedVersion.value, barPage.value)
    if (request !== generation) return
    bars.value = response.data.bars.map(row => JSON.parse(row.rawJson) as RebarComparisonBar)
    barTotal.value = response.data.total
    selectedBar.value = bars.value[0]?.ifcGlobalId ?? ''
  } catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '批次明细加载失败，请重试。' }
  finally { if (request === generation) loading.value = false }
}
async function downloadJSON() {
  const version = selectedVersion.value
  if (!version || downloading.value) return
  const request = generation
  downloading.value = true; error.value = ''
  try {
    const blob = await downloadC2MReportJSON(version)
    if (request !== generation || selectedVersion.value !== version) return
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `钢筋检测-${version}.json`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (cause) {
    if (request === generation) error.value = cause instanceof Error ? cause.message : '检测 JSON 下载失败'
  } finally { downloading.value = false }
}
watch(() => [props.scanId, props.bimId, props.resultVersion], () => { generation++; runs.value = []; bars.value = []; selectedVersion.value = ''; total.value = 0; page.value = 1; loading.value = false; error.value = '' })
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <details class="rebar-history" @toggle="(event) => { if ((event.target as HTMLDetailsElement).open && !runs.length && !loading) loadRuns() }">
    <summary>历史计算批次</summary>
    <div class="rebar-history__body">
      <p>保存每次计算及容差调整后的统计快照。历史记录用于查询，三维场景仍显示当前结果。</p>
      <el-button size="small" :loading="loading" @click="loadRuns">刷新批次</el-button>
      <p v-if="error" role="alert">{{ error }} <el-button size="small" @click="selectedVersion ? loadBars() : loadRuns()">重试</el-button></p>
      <p v-else-if="!runs.length && !loading">还没有已归档的批次。完成一次新计算后自动记录。</p>
      <template v-if="runs.length">
        <el-select v-model="selectedVersion" aria-label="历史计算批次" placeholder="选择计算批次" :disabled="loading" @change="barPage = 1; loadBars()">
          <el-option v-for="run in runs" :key="run.resultVersion" :value="run.resultVersion" :label="`${date(run.createdAt)} · ${run.resultVersion.slice(0, 8)}`" />
        </el-select>
        <el-button :disabled="!selectedVersion || loading" :loading="downloading" @click="downloadJSON">导出该批次检测 JSON</el-button>
        <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" small @current-change="loadRuns" />
      </template>
      <template v-if="bars.length">
        <el-select v-model="selectedBar" filterable aria-label="历史批次钢筋">
          <el-option v-for="bar in bars" :key="bar.ifcGlobalId" :value="bar.ifcGlobalId" :label="`${bar.name || bar.designBarId} · ${bar.ifcGlobalId}`" />
        </el-select>
        <el-pagination v-if="barTotal > 100" v-model:current-page="barPage" :page-size="100" :total="barTotal" layout="prev, pager, next" small @current-change="loadBars" />
        <RebarDeviationDetail v-for="bar in bars.filter(row => row.ifcGlobalId === selectedBar)" :key="bar.ifcGlobalId" :bar="bar" compact />
      </template>
    </div>
  </details>
</template>

<style scoped>
.rebar-history { border-top: 1px solid var(--border-color-light); padding-top: 12px; margin-top: 16px; font-size: 14px; }
.rebar-history summary { cursor: pointer; font-weight: 600; }
.rebar-history summary:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 4px; }
.rebar-history__body { display: grid; gap: 12px; padding-block: 12px; min-width: 0; }
.rebar-history__body p { margin: 0; font-size: 12px; line-height: 1.6; color: var(--text-secondary); overflow-wrap: anywhere; }
.rebar-history__body :deep(.el-select) { width: 100%; }
</style>
