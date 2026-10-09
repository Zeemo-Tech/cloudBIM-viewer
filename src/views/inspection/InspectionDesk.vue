<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Aim, ArrowRight, CircleCheck, Refresh, Warning, View } from '@element-plus/icons-vue'
import { getAssetDetail, getC2MReport, getLatestC2M, listAssets, listC2MReports, type AssetSummary, type C2MResult, type RebarComparisonBar } from '@cloudbim/viewer-core'
import type { AuthSession } from '@/features/auth/auth.service'
import RebarLocationMap from '@/features/inspection/RebarLocationMap.vue'
import { actionLabels, coverage, listInspectionActions, millimetres, problemKind, problemLabels, saveInspectionAction, type InspectionAction, type InspectionActionRecord, type ProblemKind } from '@/features/inspection/inspection-data'

const props = defineProps<{ session: AuthSession; projectId: number; projectName: string }>()
const route = useRoute(); const router = useRouter()
type DeskTab = 'operation' | 'alarms' | 'correction'
const tabs: { id: DeskTab; label: string }[] = [{ id: 'operation', label: '工人操作' }, { id: 'alarms', label: '报警展示' }, { id: 'correction', label: '修正操作' }]
const tab = computed<DeskTab>(() => ['alarms', 'correction'].includes(String(route.query.tab)) ? route.query.tab as DeskTab : 'operation')
const demonstration = computed(() => route.query.demo === '1')
function queryId(value: unknown) { const str = Array.isArray(value) ? value[0] : value; const n = Number(str); return typeof str === 'string' && /^\d+$/.test(str) && Number.isSafeInteger(n) && n > 0 ? n : null }
const scanId = computed(() => queryId(route.query.scanId)); const bimId = computed(() => queryId(route.query.bimId))
const chooserOpen = ref(!scanId.value || !bimId.value)
const scans = ref<AssetSummary[]>([]); const models = ref<AssetSummary[]>([])
const chosenScan = ref(''); const chosenBim = ref('')
const scan = computed(() => scans.value.find(row => row.id === scanId.value)); const model = computed(() => models.value.find(row => row.id === bimId.value))
const result = ref<C2MResult | null>(null); const bars = ref<RebarComparisonBar[]>([])
const selectedId = ref(''); const selected = computed(() => bars.value.find(row => row.ifcGlobalId === selectedId.value))
const loading = ref(false); const error = ref(''); const historyNote = ref(''); const loadedAt = ref('')
const actions = ref<InspectionActionRecord[]>([]); const actionsLoading = ref(false); const actionsError = ref(''); const saving = ref(false); const saveMessage = ref(''); const adjustmentNote = ref('')
const filter = ref<ProblemKind | 'all'>('all'); const keyword = ref('')
let generation = 0; let actionsGeneration = 0
const tolerance = computed(() => {
  const n = result.value?.diagnostics?.rebarComparison?.inspection?.summary.toleranceM ?? result.value?.visualization?.toleranceLimit
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null
})
const kindOf = (bar: RebarComparisonBar) => problemKind(bar, tolerance.value)
const problems = computed(() => bars.value.filter(bar => kindOf(bar) !== 'observed'))
const counts = computed(() => bars.value.reduce((sum, bar) => { sum[kindOf(bar)]++; return sum }, { missing: 0, review: 0, outlier: 0, observed: 0 }))
const totalVertices = computed(() => bars.value.reduce((n, bar) => n + bar.knownCount + bar.unknownCount, 0))
const knownVertices = computed(() => bars.value.reduce((n, bar) => n + bar.knownCount, 0))
const unknownVertices = computed(() => totalVertices.value - knownVertices.value)
const knownRatio = computed(() => totalVertices.value ? `${(knownVertices.value / totalVertices.value * 100).toFixed(2)}%` : '不可评估')
const filtered = computed(() => bars.value.filter(bar => (filter.value === 'all' || kindOf(bar) === filter.value) && `${bar.name} ${bar.designBarId} ${bar.ifcGlobalId}`.toLowerCase().includes(keyword.value.toLowerCase().trim())))
const fresh = computed(() => result.value?.fresh === true)
const dataReady = computed(() => fresh.value && !!bars.value.length && !loading.value && !error.value)
const version = computed(() => result.value?.resultVersion || '')
const currentRecords = computed(() => actions.value.filter(row => row.demonstration === demonstration.value && row.resultVersion === version.value && row.ifcGlobalId === selectedId.value))
const acknowledged = computed(() => currentRecords.value.some(row => row.action === 'acknowledge'))
const adjusted = computed(() => currentRecords.value.some(row => row.action === 'record_adjustment'))
const savedAdjustmentNote = computed(() => currentRecords.value.find(row => row.action === 'record_adjustment')?.note || '')
const requested = computed(() => currentRecords.value.some(row => row.action === 'request_recheck'))
const canAct = computed(() => dataReady.value && !!selected.value && kindOf(selected.value) !== 'observed' && !!version.value && !saving.value && !actionsLoading.value && !actionsError.value)
const correctionStep = computed(() => requested.value ? '等待复检数据' : adjusted.value ? '提交复检申请' : acknowledged.value ? '现场处理并记录' : '确认问题位置')
const statusText = computed(() => loading.value ? '正在读取检测数据' : error.value ? '数据读取失败' : !scanId.value || !bimId.value ? '待选择检测任务' : !result.value ? '尚无检测结果' : !fresh.value ? '结果需重新计算' : bars.value.length ? '检测数据已就绪 · 需要复核' : '结果缺少逐根数据')
function date(value: string | undefined) { if (!value) return '未提供'; const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) : '未提供' }
function setTab(id: DeskTab) { void router.replace({ query: { ...route.query, tab: id } }) }
function selectBar(id: string) { if (saving.value) return; selectedId.value = id; adjustmentNote.value = ''; saveMessage.value = '' }
function viewProblem(bar: RebarComparisonBar) { selectBar(bar.ifcGlobalId); setTab('correction') }
function chooseTask() {
  if (!chosenScan.value || !chosenBim.value) return
  chooserOpen.value = false
  void router.replace({ query: { ...route.query, scanId: chosenScan.value, bimId: chosenBim.value } })
}
function scanChanged() {
  const linked = scans.value.find(row => row.id === Number(chosenScan.value))?.linkedBimId
  chosenBim.value = linked && models.value.some(row => row.id === linked) ? String(linked) : ''
}
function openAnalysis() {
  if (!scan.value || !model.value) return
  void router.push({ path: '/alignment', query: { projectId: props.projectId, projectName: props.projectName, pointcloudAssetId: scan.value.id, bimAssetId: model.value.id, pointcloudDisplayName: scan.value.sourceName, displayName: model.value.sourceName, step: '3', returnTo: route.fullPath } })
}
async function allAssets(type: 'pointcloud' | 'bim') {
  const items: AssetSummary[] = []; let page = 1
  while (true) {
    const response = await listAssets({ projectId: props.projectId, type, page, pageSize: 100 })
    const batch = response.data?.list ?? []; items.push(...batch)
    if (!batch.length || items.length >= response.data.total) break
    page++
  }
  return items.filter(row => row.projectId === undefined || row.projectId === props.projectId)
}
async function loadRecords(request = generation) {
  const resultVersion = version.value; const actionRequest = ++actionsGeneration
  actions.value = []; actionsError.value = ''; actionsLoading.value = false
  if (!resultVersion) return
  actionsLoading.value = true
  try {
    const response = await listInspectionActions(resultVersion)
    if (request !== generation || actionRequest !== actionsGeneration) return
    actions.value = response.data.items
  } catch (cause) { if (request === generation && actionRequest === actionsGeneration) actionsError.value = cause instanceof Error ? cause.message : '处理记录读取失败，请重试。' }
  finally { if (request === generation && actionRequest === actionsGeneration) actionsLoading.value = false }
}
async function load() {
  const request = ++generation; actionsGeneration++
  loading.value = true; error.value = ''; historyNote.value = ''; result.value = null; bars.value = []; actions.value = []; actionsError.value = ''; actionsLoading.value = false; saveMessage.value = ''; adjustmentNote.value = ''; selectedId.value = ''; loadedAt.value = ''
  try {
    const [scanRows, bimRows] = await Promise.all([allAssets('pointcloud'), allAssets('bim')])
    if (request !== generation) return
    scans.value = scanRows; models.value = bimRows
    chosenScan.value = scanId.value ? String(scanId.value) : ''; chosenBim.value = bimId.value ? String(bimId.value) : ''
    if (!scanId.value || !bimId.value) return
    if (!scan.value || !model.value) throw new Error('所选点云或设计模型不属于当前项目，请重新选择检测任务。')
    const [scanDetail, bimDetail] = await Promise.all([getAssetDetail(scanId.value), getAssetDetail(bimId.value)])
    if (request !== generation) return
    if (scanDetail.data.type !== 'pointcloud' || bimDetail.data.type !== 'bim' || [scanDetail.data, bimDetail.data].some(row => row.projectId !== undefined && row.projectId !== props.projectId)) throw new Error('检测文件与当前项目不一致，请重新选择。')
    const response = await getLatestC2M(scanId.value, bimId.value)
    if (request !== generation) return
    result.value = response.data || null
    if (!result.value) return
    if (result.value.modelScanFileId !== scanId.value || result.value.modelBimFileId !== bimId.value) throw new Error('检测结果与所选文件不一致，请刷新或联系管理员。')
    bars.value = result.value.diagnostics?.rebarComparison?.bars ?? []
    // The immutable report may contain all bars when the latest response is abbreviated.
    if (!bars.value.length) {
      try {
        let reportVersion = result.value.resultVersion
        if (!reportVersion) { const runs = await listC2MReports(scanId.value, bimId.value); reportVersion = runs.data.items[0]?.resultVersion }
        if (request !== generation) return
        if (reportVersion) {
          const all: RebarComparisonBar[] = []; let page = 1
          while (true) { const report = await getC2MReport(reportVersion, page); if (request !== generation) return; all.push(...report.data.bars.map(row => JSON.parse(row.rawJson) as RebarComparisonBar)); if (!report.data.bars.length || all.length >= report.data.total) break; page++ }
          // A different archived version is not a fresh latest result.
          if (reportVersion !== result.value.resultVersion) { result.value = { ...result.value, resultVersion: reportVersion, fresh: false }; historyNote.value = '逐根数据来自归档批次，需在分析工作区确认并重新计算。' }
          bars.value = all
        }
      } catch (cause) { historyNote.value = cause instanceof Error ? `逐根报告读取失败：${cause.message}` : '逐根报告读取失败，请刷新。' }
    }
    selectedId.value = problems.value[0]?.ifcGlobalId ?? bars.value[0]?.ifcGlobalId ?? ''
    loadedAt.value = new Date().toISOString()
    await loadRecords(request)
  } catch (cause) { if (request === generation) { error.value = cause instanceof Error ? cause.message : '检测数据读取失败，请刷新重试。'; result.value = null; bars.value = [] } }
  finally { if (request === generation) loading.value = false }
}
async function submitAction(action: InspectionAction) {
  if (!canAct.value || !selected.value) return
  if (action === 'acknowledge' && acknowledged.value || action === 'record_adjustment' && (!acknowledged.value || adjusted.value || !adjustmentNote.value.trim()) || action === 'request_recheck' && (!adjusted.value || requested.value)) return
  const request = generation; const resultVersion = version.value; const id = selected.value.ifcGlobalId; const demo = demonstration.value
  saving.value = true; saveMessage.value = ''; actionsError.value = ''
  try {
    await saveInspectionAction(resultVersion, { ifcGlobalId: id, action, note: action === 'record_adjustment' ? adjustmentNote.value.trim() : '', demonstration: demo })
    if (request !== generation) return
    await loadRecords(request)
    if (request === generation && demo === demonstration.value && id === selectedId.value && !actionsError.value) saveMessage.value = `${demo ? '演示' : ''}${actionLabels[action]}已保存${action === 'request_recheck' ? '，等待补扫和新检测结果。' : '。'}`
  } catch (cause) { if (request === generation && demo === demonstration.value) actionsError.value = cause instanceof Error ? cause.message : '记录保存失败，请重试。' }
  finally { saving.value = false }
}
watch(() => [props.projectId, scanId.value, bimId.value], () => { void load() }, { immediate: true })
watch(demonstration, () => { adjustmentNote.value = ''; saveMessage.value = '' })
onBeforeUnmount(() => { generation++; actionsGeneration++ })
</script>

<template>
  <section class="inspection-desk" aria-label="钢筋网片检测工作台">
    <header class="desk-heading"><div><h1>钢筋网片检测</h1><p>浇筑前检查、问题定位与现场修正</p></div><div class="heading-actions"><button class="desk-button" :aria-expanded="chooserOpen" @click="chooserOpen = !chooserOpen">{{ chooserOpen ? '收起任务选择' : '更换检测任务' }}</button><button class="desk-button" :disabled="loading || saving" @click="load"><el-icon><Refresh /></el-icon>{{ loading ? '读取中…' : '刷新数据' }}</button></div></header>
    <div v-if="demonstration" class="demo-notice" role="status"><strong>流程演示</strong><span>展示真实历史检测数据；操作记录单独保存，不计入现场处理记录。</span></div>
    <nav class="desk-tabs" aria-label="检测操作页面"><button v-for="item in tabs" :key="item.id" :class="{ active: tab === item.id }" :aria-current="tab === item.id ? 'page' : undefined" @click="setTab(item.id)">{{ item.label }}<span v-if="item.id === 'alarms' && bars.length" class="tab-count">{{ problems.length }}</span></button></nav>
    <div v-if="chooserOpen" class="task-selector"><label>扫描点云<select v-model="chosenScan" :disabled="loading || saving" @change="scanChanged"><option value="">选择当前项目点云</option><option v-for="item in scans" :key="item.id" :value="String(item.id)">{{ item.archiveCode || item.sourceName }}</option></select></label><label>设计模型<select v-model="chosenBim" :disabled="loading || saving"><option value="">选择设计模型</option><option v-for="item in models" :key="item.id" :value="String(item.id)">{{ item.sourceName }}</option></select></label><button class="desk-button" :disabled="!chosenScan || !chosenBim || loading || saving" @click="chooseTask">载入检测任务</button></div>
    <div v-if="error" class="notice danger" role="alert"><el-icon><Warning /></el-icon><div><strong>检测数据读取失败</strong><p>{{ error }}</p></div><button class="desk-button" :disabled="loading" @click="load">重试</button></div>
    <div v-if="!scanId || !bimId" class="empty-workspace"><el-icon :size="36"><Aim /></el-icon><h2>选择要检查的钢筋网片</h2><p>{{ scans.length ? '选择当前项目的扫描点云与设计模型，再载入已有检测结果。' : loading ? '正在读取当前项目文件…' : '当前项目暂无扫描点云。请先在扫描点云页面上传和归档。' }}</p><button class="desk-button" @click="router.push({ path: '/survey', query: { projectId, projectName } })">前往扫描点云</button></div>
    <template v-else-if="!error">
      <div class="task-strip"><div><span>当前检测任务</span><strong :title="scan?.sourceName">{{ scan?.archiveCode || scan?.sourceName || '读取文件信息…' }}</strong></div><div><span>计算更新时间</span><strong>{{ date(result?.updatedAt || result?.createdAt) }}</strong></div><div><span>复核阈值 · 非验收阈值</span><strong>{{ millimetres(tolerance) }}</strong></div><div class="readiness" :class="{ ready: dataReady, stale: result && !fresh }"><i />{{ statusText }}</div></div>
      <div v-if="result && !fresh" class="notice warning" role="status"><el-icon><Warning /></el-icon><div><strong>结果需重新计算</strong><p>{{ result.staleReason || '结果有效性未通过当前文件校验。请在分析工作区完成重新计算，再处理检测问题。' }}</p></div><button class="desk-button" :disabled="!scan || !model" @click="openAnalysis">打开分析工作区</button></div>
      <p v-if="historyNote" class="history-note" role="status">{{ historyNote }}</p>
      <div v-if="loading && !bars.length" class="empty-workspace" aria-busy="true"><h2>正在读取检测结果</h2><p>核对当前项目、检测文件和逐根钢筋数据…</p></div>
      <div v-else-if="!bars.length" class="empty-workspace"><h2>{{ result ? '本批次缺少逐根钢筋数据' : '该任务尚无检测结果' }}</h2><p>进入分析工作区检查配准、点云分类和偏差计算，再返回刷新。</p><button class="desk-button primary" :disabled="!scan || !model" @click="openAnalysis">打开分析工作区</button></div>
      <template v-else>
        <div class="result-summary"><div><span>设计钢筋</span><strong>{{ bars.length }} <small>根</small></strong></div><div><span>有效测量覆盖率</span><strong>{{ knownRatio }}</strong><small>未测采样点 {{ unknownVertices.toLocaleString('zh-CN') }}</small></div><button :class="{ selected: filter === 'missing' && tab === 'alarms' }" @click="filter = 'missing'; setTab('alarms')"><span>整根缺测</span><strong class="info">{{ counts.missing }} <small>根</small></strong></button><button :class="{ selected: filter === 'review' && tab === 'alarms' }" @click="filter = 'review'; setTab('alarms')"><span>待复核</span><strong class="warning-text">{{ counts.review }} <small>根</small></strong></button><button :class="{ selected: filter === 'outlier' && tab === 'alarms' }" @click="filter = 'outlier'; setTab('alarms')"><span>偏差超阈值</span><strong class="danger-text">{{ counts.outlier }} <small>根</small></strong></button></div>
        <div v-if="tab === 'operation'" class="operation-grid">
          <section class="geometry-panel"><header class="section-heading"><h2>网片定位图</h2><button class="desk-button" @click="openAnalysis"><el-icon><View /></el-icon>打开三维分析</button></header><RebarLocationMap :bars="bars" :selected-id="selectedId" :tolerance="tolerance" @select="selectBar" /></section>
          <aside class="operation-panel"><h2>当前任务</h2><div class="task-status"><el-icon><Warning /></el-icon><strong>{{ problems.length ? `${problems.length} 根钢筋需要检查` : '已有测量结果，等待质量复核' }}</strong></div><p class="instructions">{{ counts.missing ? '先检查缺测位置与遮挡，补扫后再判断偏差。' : '按报警位置核对钢筋，记录现场处理，并申请复检。' }}</p><ol class="worker-steps"><li><strong>核对构件与检测数据</strong><span>确认点云、设计模型和数据时间。</span></li><li><strong>查看报警并定位钢筋</strong><span>缺测安排补扫；偏差问题现场复核。</span></li><li><strong>记录处理，等待复检</strong><span>新检测结果产生前，不放行浇筑。</span></li></ol><button class="desk-button primary big" :disabled="!dataReady" @click="filter = 'all'; setTab('alarms')">查看问题并定位 <el-icon><ArrowRight /></el-icon></button><button class="desk-button big" :disabled="!selected || !dataReady" @click="setTab('correction')">进入修正操作</button><p class="gate-note">{{ dataReady ? '测量数据已就绪，不代表网片已通过验收。' : '当前结果需重新计算，处理按钮暂不可用。' }}</p></aside>
        </div>
        <div v-else-if="tab === 'alarms'" class="alarms-grid">
          <section class="alarm-list"><header class="section-heading"><h2>钢筋问题清单 <small>{{ filtered.length }} 根</small></h2></header><div class="list-filters"><label class="sr-only" for="alarm-filter">问题类型</label><select id="alarm-filter" v-model="filter"><option value="all">全部钢筋</option><option value="missing">缺测</option><option value="review">待复核</option><option value="outlier">偏差超阈值</option><option value="observed">已有测量</option></select><label class="sr-only" for="alarm-search">搜索钢筋</label><input id="alarm-search" v-model="keyword" placeholder="搜索钢筋编号" /></div><div class="alarm-table-wrap"><table class="alarm-table"><thead><tr><th>钢筋编号</th><th>问题状态</th><th>覆盖率</th><th>95% 绝对偏差</th><th>操作</th></tr></thead><tbody><tr v-for="bar in filtered" :key="bar.ifcGlobalId" :class="{ active: selectedId === bar.ifcGlobalId }"><td><button class="bar-name" :title="bar.ifcGlobalId" @click="selectBar(bar.ifcGlobalId)">{{ bar.name || bar.designBarId }}<small>{{ bar.designBarId }}</small></button></td><td><span class="status-tag" :class="kindOf(bar)">{{ problemLabels[kindOf(bar)] }}</span></td><td>{{ coverage(bar) }}</td><td>{{ millimetres(bar.stats?.p95Abs) }}</td><td><button class="desk-button compact" @click="selectBar(bar.ifcGlobalId)">定位</button></td></tr></tbody></table><div v-if="!filtered.length" class="list-empty">当前筛选下没有钢筋。<button class="text-button" @click="filter = 'all'; keyword = ''">清除筛选</button></div></div><p class="table-note">偏差超阈值按 95% 绝对表面偏差筛选。缺测与待复核单独列出，不能按零偏差处理。</p></section>
          <aside class="selected-panel"><header class="section-heading"><h2>报警位置</h2><div v-if="selected" class="heading-actions"><span class="status-tag" :class="kindOf(selected)">{{ problemLabels[kindOf(selected)] }}</span><button class="desk-button primary" :disabled="kindOf(selected) === 'observed' || !dataReady" @click="viewProblem(selected)">处理这根钢筋 <el-icon><ArrowRight /></el-icon></button></div></header><RebarLocationMap :bars="bars" :selected-id="selectedId" :tolerance="tolerance" @select="selectBar" /><template v-if="selected"><h3>{{ selected.name || selected.designBarId }}</h3><dl class="bar-values"><div><dt>测量覆盖率</dt><dd>{{ coverage(selected) }}</dd></div><div><dt>95% 绝对偏差</dt><dd>{{ millimetres(selected.stats?.p95Abs) }}</dd></div><div><dt>中心线最大偏移</dt><dd>{{ millimetres(selected.measurement?.bending.maxCentrelineDepartureM) }}</dd></div></dl></template></aside>
        </div>
        <div v-else class="correction-grid">
          <section class="correction-location"><header class="section-heading"><h2>修正位置</h2><button class="desk-button" @click="setTab('alarms')">返回问题清单</button></header><label class="bar-select">选择钢筋<select :value="selectedId" :disabled="saving" @change="selectBar(($event.target as HTMLSelectElement).value)"><option v-for="bar in bars" :key="bar.ifcGlobalId" :value="bar.ifcGlobalId">{{ bar.name || bar.designBarId }} · {{ problemLabels[kindOf(bar)] }}</option></select></label><RebarLocationMap :bars="bars" :selected-id="selectedId" :tolerance="tolerance" @select="selectBar" /><div v-if="selected" class="selected-context"><strong>{{ selected.name || selected.designBarId }}</strong><span class="status-tag" :class="kindOf(selected)">{{ problemLabels[kindOf(selected)] }}</span><span>测量覆盖 {{ coverage(selected) }}</span><span>95% 绝对偏差 {{ millimetres(selected.stats?.p95Abs) }}</span><small>IFC {{ selected.ifcGlobalId }}</small></div><p class="field-guide">{{ selected && kindOf(selected) === 'missing' ? '补扫指引：检查支撑、交叉筋和设备视线遮挡；补采缺测位置，并在分析工作区重新计算。' : '调整指引：核对设计位置和绑扎状态；由现场人员调整钢筋或绑扎点，再补扫对应区域。' }}</p></section>
          <aside class="correction-form"><header class="section-heading"><h2>{{ demonstration ? '演示处理记录' : '现场处理记录' }}</h2><span class="current-step">{{ correctionStep }}</span></header><p v-if="selected && kindOf(selected) === 'observed'" class="gate-note">这根钢筋没有进入问题清单。已有测量不等于验收合格。</p><div class="repair-step" :class="{ done: acknowledged }"><div class="step-title"><span class="step-number">1</span><h3>确认问题位置</h3><el-icon v-if="acknowledged"><CircleCheck /></el-icon></div><p v-if="!acknowledged">现场核对钢筋编号与图中位置，确认需要处理。</p><button class="desk-button" :disabled="!canAct || acknowledged" @click="submitAction('acknowledge')">{{ acknowledged ? '已确认问题' : '确认这根钢筋的问题' }}</button></div><div class="repair-step" :class="{ done: adjusted }"><div class="step-title"><span class="step-number">2</span><h3>现场处理并记录</h3><el-icon v-if="adjusted"><CircleCheck /></el-icon></div><p v-if="adjusted" class="saved-adjustment">{{ savedAdjustmentNote }}</p><label v-else for="adjustment-note">处理说明 <span>必填</span></label><textarea v-if="!adjusted" id="adjustment-note" v-model="adjustmentNote" :disabled="!canAct || !acknowledged || adjusted" rows="3" maxlength="2000" placeholder="填写调整部位、处理方式或补扫安排" /><button class="desk-button" :disabled="!canAct || !acknowledged || adjusted || !adjustmentNote.trim()" @click="submitAction('record_adjustment')">{{ adjusted ? '处理记录已保存' : '保存处理记录' }}</button></div><div class="repair-step" :class="{ done: requested }"><div class="step-title"><span class="step-number">3</span><h3>补扫并申请复检</h3><el-icon v-if="requested"><CircleCheck /></el-icon></div><p>提交复检需求，补扫与重新计算后查看新结果。</p><button class="desk-button primary" :disabled="!canAct || !adjusted || requested" @click="submitAction('request_recheck')">{{ requested ? '已申请 · 等待复检' : saving ? '正在保存…' : '提交复检申请' }}</button></div><p v-if="saveMessage" class="save-message" role="status">{{ saveMessage }}</p><p v-if="actionsError" class="action-error" role="alert">{{ actionsError }} <button class="text-button" :disabled="actionsLoading || saving" @click="loadRecords()">重试读取记录</button></p><p class="gate-note">处理记录不会修改检测结论。复检申请不直接启动设备或重新计算。</p><details class="record-history"><summary>{{ demonstration ? '演示' : '现场' }}记录（{{ currentRecords.length }}）{{ actionsLoading ? ' · 读取中…' : '' }}</summary><ol v-if="currentRecords.length"><li v-for="record in currentRecords" :key="record.id"><strong>{{ actionLabels[record.action] }}</strong><time>{{ date(record.createdAt) }}</time><p v-if="record.note">{{ record.note }}</p></li></ol><p v-else>当前钢筋尚无{{ demonstration ? '演示' : '现场' }}记录。</p></details></aside>
        </div>
        <footer class="data-footer"><span>批次 {{ version ? version.slice(0, 12) : '未提供' }}</span><span>读取于 {{ date(loadedAt) }} · 北京时间</span><span>未测设计网格采样点 {{ unknownVertices.toLocaleString('zh-CN') }} / {{ totalVertices.toLocaleString('zh-CN') }} · 缺测保留未知状态</span><span>浇筑放行由质量人员复核</span></footer>
      </template>
    </template>
  </section>
</template>

<style scoped>
.heading-actions { display: flex; align-items: center; gap: 10px; }
.saved-adjustment { white-space: pre-wrap; overflow-wrap: anywhere; padding: 10px 12px; background: var(--bg-control); border-radius: 8px; }
@media (min-width: 1051px) {
  .inspection-desk .desk-heading { margin-bottom: 12px; }
  .inspection-desk .task-strip { padding-block: 10px; margin-bottom: 12px; }
  .inspection-desk .result-summary { margin-bottom: 16px; }
  .inspection-desk .result-summary > * { padding-block: 10px; }
  .inspection-desk :deep(.location-map svg) { max-height: 330px; }
  .inspection-desk .selected-panel :deep(.location-map svg) { max-height: 250px; }
  .inspection-desk .worker-steps { margin-block: 16px; }
  .inspection-desk .worker-steps li { margin-bottom: 12px; }
  .inspection-desk .repair-step { padding-block: 10px; }
  .inspection-desk .alarm-table-wrap { max-height: 385px; }
}

.inspection-desk { min-width: 0; color: var(--text-primary); font-family: var(--font-family-base); font-size: 16px; line-height: 1.5; font-variant-numeric: tabular-nums; }
.inspection-desk ::selection { color: white; background: var(--brand-aether); }.inspection-desk :is(input, textarea) { caret-color: var(--brand-aether); }.inspection-desk :is(button, input, select, textarea):focus-visible { outline: 3px solid var(--brand-aether); outline-offset: 3px; }
.inspection-desk button, .inspection-desk input, .inspection-desk select, .inspection-desk textarea { font: inherit; }.inspection-desk button { cursor: pointer; }.inspection-desk button:disabled { color: #657894; background: #edf0f5; border-color: #d6dfec; cursor: not-allowed; }.inspection-desk p { margin: 0; }.inspection-desk h1, .inspection-desk h2, .inspection-desk h3 { margin: 0; color: var(--brand-sapphire); }.inspection-desk h1 { font-size: 24px; font-weight: 650; }.inspection-desk h2 { font-size: 18px; font-weight: 650; }.inspection-desk h3 { font-size: 16px; font-weight: 650; }
.desk-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 18px; }.desk-heading p { margin-top: 4px; color: var(--text-secondary); }.desk-button { display: inline-flex; justify-content: center; align-items: center; gap: 8px; min-height: 42px; padding: 8px 16px; border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-secondary); background: white; font-weight: 550; white-space: nowrap; }.desk-button:hover:not(:disabled) { border-color: var(--brand-aether); background: var(--color-primary-soft); color: var(--brand-aether); }.desk-button.primary { border-color: var(--brand-aether); color: white; background: var(--brand-aether); }.desk-button.primary:hover:not(:disabled) { color: white; background: var(--color-primary-hover); }.desk-button.primary:disabled { color: #657894; background: #edf0f5; border-color: #d6dfec; }.desk-button.big { min-height: 54px; width: 100%; justify-content: space-between; padding-inline: 18px; }.desk-button.big + .desk-button.big { margin-top: 12px; }.desk-button.compact { min-height: 42px; padding-inline: 12px; }
.desk-tabs { display: flex; gap: 28px; border-bottom: 1px solid var(--border-color); margin-bottom: 16px; }.desk-tabs > button { display: inline-flex; align-items: center; gap: 8px; padding: 10px 2px 13px; min-height: 48px; border: 0; border-bottom: 3px solid transparent; background: transparent; color: var(--text-secondary); }.desk-tabs > button:hover, .desk-tabs > button.active { color: var(--brand-aether); }.desk-tabs > button.active { border-bottom-color: var(--brand-aether); font-weight: 650; }.tab-count { font-size: 13px; min-width: 24px; text-align: center; padding: 1px 6px; border-radius: 6px; background: var(--color-primary-soft); }
.demo-notice { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 10px 16px; margin-bottom: 12px; background: var(--color-warning-soft); color: var(--color-warning); border: 1px solid #e9dbc4; border-radius: 8px; }.demo-notice strong { font-weight: 700; }.demo-notice span { font-size: 14px; }
.task-selector { display: flex; gap: 12px; align-items: flex-end; margin-bottom: 16px; }.task-selector label { display: flex; flex-direction: column; gap: 5px; min-width: 0; max-width: 420px; flex: 1; font-size: 14px; color: var(--text-secondary); }.inspection-desk select, .inspection-desk input, .inspection-desk textarea { border: 1px solid var(--border-color); border-radius: 8px; background: white; color: var(--text-primary); }.inspection-desk select, .inspection-desk input { height: 42px; padding: 8px 12px; width: 100%; min-width: 0; }.inspection-desk select { padding-right: 24px; text-overflow: ellipsis; }.inspection-desk textarea { display: block; width: 100%; min-height: 88px; resize: vertical; padding: 10px 12px; line-height: 1.5; }.inspection-desk :is(select, input, textarea):disabled { background: #f3f5f8; color: #657894; cursor: not-allowed; }.inspection-desk ::placeholder { color: var(--text-placeholder); }
.task-strip { display: grid; grid-template-columns: minmax(200px, 1.25fr) minmax(170px, 1fr) minmax(170px, .85fr) auto; align-items: center; gap: 16px; padding: 16px 18px; background: var(--bg-control); border: 1px solid var(--border-color-light); border-radius: 10px; margin-bottom: 16px; }.task-strip > div { min-width: 0; }.task-strip span { display: block; color: var(--text-secondary); font-size: 13px; margin-bottom: 3px; }.task-strip strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; font-size: 15px; }.readiness { display: flex; align-items: center; gap: 8px; color: var(--text-info); font-size: 14px; font-weight: 600; }.readiness i { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: 0 0 8px; }.readiness.ready { color: var(--color-success); }.readiness.stale { color: var(--color-warning); }
.notice { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; padding: 14px 16px; border-radius: 8px; }.notice p { font-size: 14px; margin-top: 4px; }.notice > div { flex: 1; }.notice.warning { color: var(--color-warning); background: var(--color-warning-soft); }.notice.danger { color: var(--color-danger); background: var(--color-danger-soft); }.history-note { margin-bottom: 12px !important; color: var(--color-warning); font-size: 14px; }
.result-summary { display: grid; grid-template-columns: 1fr 1.2fr 1fr 1fr 1fr; border-top: 1px solid var(--border-color-light); border-bottom: 1px solid var(--border-color-light); margin-bottom: 20px; }.result-summary > div, .result-summary > button { padding: 14px 20px; text-align: left; border: 0; background: transparent; }.result-summary > * + * { border-left: 1px solid var(--border-color-light); }.result-summary > button:hover, .result-summary > button.selected { background: var(--bg-control); }.result-summary span { display: block; font-size: 14px; color: var(--text-secondary); }.result-summary strong { display: block; font-size: 24px; font-weight: 650; margin-top: 4px; }.result-summary small { font-size: 14px; font-weight: 400; color: var(--text-secondary); }.info { color: var(--color-info); }.warning-text { color: var(--color-warning); }.danger-text { color: var(--color-danger); }
.operation-grid, .correction-grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 24px; }.alarms-grid { display: grid; grid-template-columns: minmax(0, 1.16fr) minmax(350px, .84fr); gap: 24px; }.geometry-panel, .alarm-list, .correction-location { min-width: 0; }.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }.section-heading h2 small { font-size: 14px; font-weight: 400; margin-left: 8px; color: var(--text-secondary); }.operation-panel, .selected-panel, .correction-form { border-left: 1px solid var(--border-color-light); padding-left: 24px; min-width: 0; }.operation-panel h2 { margin-bottom: 16px; }.task-status { display: flex; align-items: center; gap: 8px; color: var(--color-warning); font-size: 18px; }.instructions { margin-top: 12px !important; color: var(--text-secondary); line-height: 1.7; }.worker-steps { padding: 0 0 0 22px; margin: 24px 0; }.worker-steps li { padding-left: 6px; margin-bottom: 18px; color: var(--brand-aether); }.worker-steps strong { display: block; color: var(--text-primary); }.worker-steps span { display: block; margin-top: 4px; color: var(--text-secondary); font-size: 14px; }.gate-note { margin-top: 14px !important; color: var(--text-secondary); font-size: 14px; line-height: 1.6; }
.list-filters { display: flex; gap: 12px; margin-bottom: 12px; }.list-filters select { width: 150px; flex: 0 0 150px; }.list-filters input { flex: 1; }.alarm-table-wrap { overflow: auto; max-height: 480px; border: 1px solid var(--border-color-light); border-radius: 8px; scrollbar-color: #b5c2d8 var(--bg-control); scrollbar-width: thin; }.alarm-table { border-collapse: collapse; width: 100%; font-size: 14px; white-space: nowrap; }.alarm-table th { position: sticky; top: 0; z-index: 1; background: var(--bg-control); font-weight: 600; color: var(--text-secondary); text-align: left; padding: 12px; }.alarm-table td { padding: 10px 12px; border-top: 1px solid var(--border-color-light); }.alarm-table tr.active { background: var(--color-primary-soft); }.bar-name { text-align: left; padding: 0; border: 0; background: none; color: var(--text-primary); font-weight: 600; }.bar-name small { display: block; font-size: 12px; font-weight: 400; margin-top: 3px; color: var(--text-secondary); }.status-tag { display: inline-block; white-space: nowrap; padding: 3px 8px; border-radius: 5px; font-size: 13px; font-weight: 550; color: var(--color-success); background: var(--color-success-soft); }.status-tag.missing { color: var(--color-info); background: var(--color-info-soft); }.status-tag.review { color: var(--color-warning); background: var(--color-warning-soft); }.status-tag.outlier { color: var(--color-danger); background: var(--color-danger-soft); }.table-note { color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin-top: 12px !important; }.selected-panel h3 { margin-top: 18px; }.bar-values { display: grid; gap: 8px; margin: 12px 0 20px; font-size: 14px; }.bar-values div { display: flex; justify-content: space-between; gap: 16px; }.bar-values dt { color: var(--text-secondary); }.bar-values dd { margin: 0; font-weight: 600; }.list-empty { padding: 24px; text-align: center; }.text-button { border: 0; background: none; color: var(--text-link); text-decoration: underline; text-underline-offset: 3px; padding: 4px; }
.bar-select { display: flex; align-items: center; gap: 12px; font-size: 14px; margin-bottom: 14px; }.bar-select select { flex: 1; }.selected-context { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-top: 16px; font-size: 14px; }.selected-context strong { color: var(--brand-sapphire); font-size: 18px; }.selected-context small { flex-basis: 100%; color: var(--text-secondary); overflow-wrap: anywhere; }.field-guide { margin-top: 18px !important; padding: 14px 16px; background: var(--color-info-soft); color: var(--text-info); border-radius: 8px; font-size: 14px; line-height: 1.7; }.current-step { font-size: 13px; color: var(--text-secondary); }.repair-step { padding: 14px 0; border-top: 1px solid var(--border-color-light); }.step-title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }.step-title .el-icon { margin-left: auto; color: var(--color-success); }.step-number { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 6px; color: var(--brand-aether); background: var(--color-primary-soft); font-size: 14px; font-weight: 650; }.repair-step p { font-size: 14px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 12px; }.repair-step label { display: block; color: var(--text-secondary); font-size: 14px; margin-bottom: 6px; }.repair-step label span { color: var(--color-warning); margin-left: 6px; }.repair-step textarea { margin-bottom: 10px; }.repair-step .desk-button { width: 100%; }.save-message { padding: 10px; background: var(--color-success-soft); color: var(--color-success); border-radius: 6px; font-size: 14px; }.action-error { margin-top: 10px !important; color: var(--color-danger); font-size: 14px; }.record-history { margin-top: 16px; font-size: 14px; border-top: 1px solid var(--border-color-light); padding-top: 12px; }.record-history summary { cursor: pointer; color: var(--text-secondary); }.record-history ol { padding-left: 18px; }.record-history li { padding: 8px 0; }.record-history time { display: block; color: var(--text-secondary); font-size: 12px; }.record-history p { padding-top: 6px; overflow-wrap: anywhere; }.data-footer { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: 20px; padding-top: 12px; border-top: 1px solid var(--border-color-light); color: var(--text-secondary); font-size: 12px; }.empty-workspace { min-height: 300px; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 16px; color: var(--text-secondary); text-align: center; padding: 24px; background: var(--bg-control); border-radius: 10px; }.empty-workspace p { max-width: 52ch; line-height: 1.7; }.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
@media (max-width: 1350px) { .task-strip { grid-template-columns: 1.1fr 1fr 1fr; }.readiness { grid-column: 1 / -1; }.operation-grid, .correction-grid { grid-template-columns: minmax(0, 1fr) 330px; gap: 18px; }.operation-panel, .selected-panel, .correction-form { padding-left: 18px; }.alarms-grid { grid-template-columns: minmax(0, 1.1fr) minmax(300px, .9fr); gap: 18px; }.result-summary > div, .result-summary > button { padding-inline: 12px; }.alarm-table th, .alarm-table td { padding-inline: 8px; }.alarm-table { font-size: 13px; } }
@media (max-width: 1050px) { .operation-grid, .alarms-grid, .correction-grid { grid-template-columns: minmax(0, 1fr); }.operation-panel, .selected-panel, .correction-form { border-left: 0; padding-left: 0; border-top: 1px solid var(--border-color-light); padding-top: 18px; }.task-selector { flex-wrap: wrap; }.task-selector label { flex-basis: calc(50% - 12px); max-width: none; }.task-strip { grid-template-columns: 1fr 1fr; }.task-strip > div:nth-child(3) { grid-column: 1; }.readiness { grid-column: auto; }.result-summary strong { font-size: 21px; }.operation-panel .desk-button.big { max-width: 520px; }.alarm-table-wrap { max-height: 400px; } }
@media (max-width: 650px) { .task-strip { grid-template-columns: 1fr; }.task-strip > div:nth-child(3), .readiness { grid-column: auto; }.result-summary { grid-template-columns: 1fr 1fr; }.result-summary > * + * { border-left: 0; }.result-summary > * { border-bottom: 1px solid var(--border-color-light); }.task-selector label { flex-basis: 100%; }.desk-heading { align-items: flex-start; }.desk-heading h1 { font-size: 22px; }.desk-tabs { gap: 16px; }.notice { align-items: flex-start; flex-wrap: wrap; }.section-heading { flex-wrap: wrap; }.bar-select { flex-wrap: wrap; }.bar-select select { flex-basis: 100%; } }
</style>
