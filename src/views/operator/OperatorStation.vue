<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Aim, Camera, Check, CircleCheck, FullScreen, Minus, Plus, Refresh, RefreshLeft, RefreshRight, SwitchButton, Warning } from '@element-plus/icons-vue'
import type { AuthSession } from '@/features/auth/auth.service'
import type { C2MResult, RebarComparisonBar } from '@cloudbim/viewer-core'
import { coverage, millimetres, problemKind, type InspectionAction, type InspectionActionRecord } from '@/features/inspection/inspection-data'
import { getOperatorActions, getOperatorGeometry, getOperatorResult, listOperatorTasks, postOperatorAction, type OperatorTask } from '@/features/operator/operator-api'
import OperatorRebarViewer from '@/features/operator/OperatorRebarViewer.vue'

const props = defineProps<{ session: AuthSession }>(); const emit = defineEmits<{ logout: [] }>()
const route = useRoute(); const router = useRouter()
const stage = ref<'ready' | 'scan' | 'result'>('ready')
const scanState = ref<'idle' | 'running' | 'complete'>('idle')
const scanProgress = ref(0)
const scanCompletedVersion = ref('')
const scanDemoUsed = ref(false)
const reducedMotion = ref(false)
const following = ref(false)
const scanning = computed(() => scanState.value === 'running')
const canShowResult = computed(() => scanState.value === 'complete' && scanCompletedVersion.value === version.value && fresh.value && geometryReady.value && !loading.value)
let scanFrame = 0
let scanEpoch = 0
let scanElapsed = 0
let previousFrame = 0
let advanceScan: ((now: number) => void) | null = null
let motionPreference: MediaQueryList | null = null
const scanDuration = 8000

const tasks = ref<OperatorTask[]>([]); const chosen = ref(''); const task = computed(() => tasks.value.find(t => String(t.scanId) === chosen.value))
const loading = ref(false); const error = ref(''); const result = ref<C2MResult | null>(null); const records = ref<InspectionActionRecord[]>([])
const recordsError = ref(''); const recordsLoading = ref(false); const saving = ref(false); const note = ref(''); const message = ref('')
const selectedId = ref(''); const viewer = ref<InstanceType<typeof OperatorRebarViewer> | null>(null); const geometryReady = ref(false)
const demo = computed(() => route.query.demo === '1' || scanDemoUsed.value); const isWorker = computed(() => props.session.role === 'operator')
let generation = 0; let recordGeneration = 0
const bars = computed(() => result.value?.diagnostics?.rebarComparison?.bars || [])
const threshold = computed(() => { const n = result.value?.visualization?.toleranceLimit; return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null })
const kind = (bar: RebarComparisonBar) => problemKind(bar, threshold.value)
const issues = computed(() => bars.value.filter(b => kind(b) !== 'observed'))
const outliers = computed(() => issues.value.filter(b => kind(b) === 'outlier').length)
const missing = computed(() => issues.value.filter(b => kind(b) === 'missing').length)
const selected = computed(() => bars.value.find(b => b.ifcGlobalId === selectedId.value))
const issueIndex = computed(() => issues.value.findIndex(b => b.ifcGlobalId === selectedId.value))
const selectedNumber = computed(() => selected.value ? String(bars.value.indexOf(selected.value) + 1).padStart(2, '0') : '')
const fresh = computed(() => result.value?.fresh === true)
const version = computed(() => result.value?.resultVersion || '')
const currentActions = computed(() => records.value.filter(r => r.resultVersion === version.value && r.ifcGlobalId === selectedId.value && r.demonstration === demo.value))
const confirmed = computed(() => currentActions.value.some(r => r.action === 'acknowledge'))
const adjusted = computed(() => currentActions.value.find(r => r.action === 'record_adjustment'))
const editingAdjustment = computed(() => stage.value === 'result' && confirmed.value && !adjusted.value)
const requested = computed(() => currentActions.value.some(r => r.action === 'request_recheck'))
const canAct = computed(() => stage.value === 'result' && !loading.value && !saving.value && !recordsLoading.value && !recordsError.value && fresh.value && !!selected.value && kind(selected.value) !== 'observed')
const selectionTitle = computed(() => !selected.value ? '选择要检查的钢筋' : kind(selected.value) === 'missing' ? '此处需要补扫' : kind(selected.value) === 'observed' ? '此处已有测量' : '核对这根钢筋的位置')
const instruction = computed(() => !selected.value ? '点击模型中的钢筋，或按“下一处问题”。' : kind(selected.value) === 'missing' ? '检查遮挡并安排补扫，暂不判断是否需要调整。' : kind(selected.value) === 'observed' ? '尚未列入问题清单；是否放行由质量人员复核。' : '核对高亮位置和绑扎点，确认后调整，再申请复检。')
const computedAt = computed(() => { const d = result.value?.updatedAt || result.value?.createdAt; return d ? new Date(d).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) : '暂无检测记录' })
const componentName = computed(() => task.value?.bimName?.replace(/\.ifc$/i, '') || task.value?.scanName || '未分配检测任务')

function select(id: string) { if (saving.value || stage.value !== 'result') return; selectedId.value = id; note.value = ''; message.value = '' }
function move(delta: number) { if (!issues.value.length || saving.value) return; const start = issueIndex.value < 0 ? (delta > 0 ? -1 : 0) : issueIndex.value; const row = issues.value[(start + delta + issues.value.length) % issues.value.length]; if (row) select(row.ifcGlobalId) }
function showResult() { if (!canShowResult.value || saving.value) return; stage.value = 'result'; if (!selectedId.value) selectedId.value = issues.value[0]?.ifcGlobalId || '' }
function stopScan() {
  cancelAnimationFrame(scanFrame); scanFrame = 0; advanceScan = null; scanEpoch++
  scanState.value = 'idle'; scanProgress.value = 0; scanCompletedVersion.value = ''
}
function cancelScan() { stopScan(); stage.value = 'ready'; scanDemoUsed.value = false }
function prepare() { if (saving.value) return; stopScan(); stage.value = 'ready'; scanDemoUsed.value = false }
function startScan() {
  if (scanning.value || saving.value || loading.value || stage.value !== 'ready' || !task.value || !fresh.value || !geometryReady.value || !version.value) return
  stopScan(); stage.value = 'scan'; scanDemoUsed.value = true; scanState.value = 'running'
  const epoch = scanEpoch; const request = generation; const snapshot = version.value
  scanElapsed = 0; previousFrame = performance.now()
  advanceScan = (now: number) => {
    if (epoch !== scanEpoch) return
    if (request !== generation || snapshot !== version.value || !fresh.value || !geometryReady.value) { cancelScan(); return }
    if (document.hidden) { scanFrame = 0; return }
    scanElapsed += now - previousFrame; previousFrame = now
    scanProgress.value = Math.min(scanElapsed / scanDuration, 1)
    if (scanProgress.value >= 1) {
      scanState.value = 'complete'; scanCompletedVersion.value = snapshot; scanFrame = 0; advanceScan = null
      showResult()
      return
    }
    scanFrame = requestAnimationFrame(advanceScan!)
  }
  scanFrame = requestAnimationFrame(advanceScan)
}
function scanVisibilityChanged() {
  if (!scanning.value || !advanceScan) return
  if (document.hidden) { cancelAnimationFrame(scanFrame); scanFrame = 0 }
  else if (!scanFrame) { previousFrame = performance.now(); scanFrame = requestAnimationFrame(advanceScan) }
}
function motionPreferenceChanged(event: MediaQueryListEvent) { reducedMotion.value = event.matches }
function geometryLoaded(value: boolean) { geometryReady.value = value; if (!value) { stopScan(); if (stage.value === 'scan') stage.value = 'ready' } }
function loadGeometry(r: C2MResult) { const t = task.value; if (!t || t.scanId !== r.modelScanFileId || t.bimId !== r.modelBimFileId) return Promise.reject(new Error('检测结果与构件不一致，请刷新。')); return getOperatorGeometry(t,r) }
async function readRecords(request = generation) {
  const seq=++recordGeneration; recordsLoading.value=true; recordsError.value=''
  if (!version.value) { records.value=[]; recordsLoading.value=false; return }
  try { const data=await getOperatorActions(version.value); if (request===generation && seq===recordGeneration) records.value=data.data.items }
  catch(e) { if(request===generation && seq===recordGeneration) recordsError.value=e instanceof Error?e.message:'记录读取失败，请重试。' }
  finally { if(request===generation && seq===recordGeneration) recordsLoading.value=false }
}
async function loadTask() {
  stopScan(); stage.value = 'ready'; scanDemoUsed.value = false
  const request=++generation; ++recordGeneration; loading.value=true; error.value=''; result.value=null; records.value=[]; recordsError.value=''; selectedId.value=''; geometryReady.value=false; note.value=''; message.value=''
  const current=task.value
  try {
    if (!current?.bimId || !current.hasResult) return
    const r=await getOperatorResult(current); if(request!==generation)return
    if(r.data && (r.data.modelScanFileId!==current.scanId || r.data.modelBimFileId!==current.bimId))throw Error('任务与检测结果不一致，请联系技术人员。')
    result.value=r.data; selectedId.value=issues.value[0]?.ifcGlobalId||''; await readRecords(request)
  } catch(e) { if(request===generation)error.value=e instanceof Error?e.message:'检测结果读取失败，请刷新。' }
  finally {if(request===generation)loading.value=false}
}
async function refresh() {
  if (saving.value || scanning.value) return
  stopScan(); stage.value = 'ready'; scanDemoUsed.value = false
  const request=++generation; ++recordGeneration; loading.value=true; error.value=''
  try {
    const r=await listOperatorTasks(); if(request!==generation)return; tasks.value=r.data.items
    const requestedScan=String(route.query.scanId||''); const wanted=chosen.value||requestedScan
    const next=tasks.value.find(t=>String(t.scanId)===wanted)||tasks.value.find(t=>t.hasResult)||tasks.value[0]
    if(chosen.value!==String(next?.scanId||''))chosen.value=String(next?.scanId||'')
    else await loadTask()
    if(!next){result.value=null;records.value=[];selectedId.value=''}
  } catch(e) {if(request===generation){error.value=e instanceof Error?e.message:'任务读取失败，请重试。';tasks.value=[];result.value=null}}
  finally {if(request===generation)loading.value=false}
}
async function save(action:InspectionAction) {
  if(!canAct.value||!selected.value)return
  if(action==='acknowledge'&&confirmed.value||action==='record_adjustment'&&(!confirmed.value||adjusted.value||!note.value.trim())||action==='request_recheck'&&(!adjusted.value||requested.value))return
  const request=generation; const id=selectedId.value;const isDemo=demo.value; saving.value=true;message.value=''
  try {await postOperatorAction(version.value,{ifcGlobalId:id,action,note:action==='record_adjustment'?note.value.trim():'',demonstration:isDemo});if(request!==generation)return;await readRecords(request);if(!recordsError.value)message.value=action==='request_recheck'?'已申请复检，等待补扫和新检测结果。':'记录已保存。'}
  catch(e){if(request===generation)recordsError.value=e instanceof Error?e.message:'保存失败，请重试。'}
  finally{saving.value=false}
}
function changeTask(){prepare()}
watch(chosen,()=>{void loadTask()})
watch(demo,()=>{note.value='';message.value=''})
onMounted(() => {
  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  reducedMotion.value = motionPreference.matches
  motionPreference.addEventListener('change', motionPreferenceChanged)
  document.addEventListener('visibilitychange', scanVisibilityChanged)
  void refresh()
})
onBeforeUnmount(() => {
  stopScan(); generation++; recordGeneration++
  motionPreference?.removeEventListener('change', motionPreferenceChanged)
  document.removeEventListener('visibilitychange', scanVisibilityChanged)
})
</script>

<template>
  <div class="operator-station">
    <header class="operator-header">
      <div class="operator-brand"><span>CloudBIM</span><h1>工人操作</h1></div>
      <div class="operator-account"><span>{{ session.displayName || session.username }}<small>{{ isWorker ? '现场工人' : '工人视图' }}</small></span><button v-if="!isWorker" class="op-button subtle" @click="router.push('/projects')">返回管理平台</button><button class="op-button subtle" @click="emit('logout')"><el-icon><SwitchButton /></el-icon>退出</button></div>
    </header>
    <div class="operator-demo"><strong>{{ stage === 'result' ? '演示结果' : '扫描演示' }}</strong><span>{{ stage === 'result' ? '展示该构件已有检测结果；演示处理单独记录，不代表新采集或现场已调整。' : '当前未连接扫描设备，动画结束后自动展示该构件已有检测结果。' }}</span></div>
    <main>
      <div class="operator-context"><label>当前构件<select v-model="chosen" :disabled="loading || saving || scanning || !tasks.length" @change="changeTask"><option v-if="!tasks.length" value="">{{ loading ? '正在读取任务…' : '暂无分配任务' }}</option><option v-for="item in tasks" :key="item.scanId" :value="String(item.scanId)">{{ item.bimName.replace(/\.ifc$/i,'') || item.scanName }} · {{ item.projectName }}</option></select></label><ol class="operator-steps" aria-label="检测步骤">
          <li :class="{active:stage==='ready',done:stage!=='ready'}" :aria-current="stage==='ready'?'step':undefined"><span class="step-number">1</span><div><strong>准备检测</strong><small>构件就位</small></div></li>
          <li :class="{active:stage==='scan',done:stage==='result'}" :aria-current="stage==='scan'?'step':undefined"><span class="step-number">2</span><div><strong>扫描</strong><small>自动扫描</small></div></li>
          <li :class="{active:stage==='result'}" :aria-current="stage==='result'?'step':undefined"><span class="step-number">3</span><div><strong>查看结果</strong><small>定位需调整钢筋</small></div></li>
        </ol><button v-if="stage==='result'" class="op-button" :disabled="saving" @click="prepare">重新准备</button><button class="op-button subtle" :disabled="loading || saving || scanning" @click="refresh"><el-icon><Refresh /></el-icon>刷新</button></div>
      <div v-if="error" class="operator-error" role="alert"><strong>暂时无法读取检测任务</strong><p>{{ error }}</p><button class="op-button primary" @click="refresh">重新读取</button></div>
      <div v-else-if="!task" class="operator-empty"><el-icon :size="56"><Camera /></el-icon><h2>{{ loading ? '正在读取任务' : '还没有分配检测任务' }}</h2><p>{{ loading ? '请稍候…' : '请联系管理员分配项目和检测构件。' }}</p><button class="op-button" :disabled="loading" @click="refresh">刷新任务</button></div>
      <div v-else class="operator-workspace">
        <section class="operator-scene">
          <div class="scene-title"><div><h2>{{ stage==='result' ? '三维定位' : scanning ? '激光扫描演示' : '构件预览' }}</h2><p>{{ stage==='result' ? (following ? '视角跟随已开启 · 切换问题自动定位' : '拖动旋转 · 点击钢筋定位') : scanning ? (reducedMotion ? '保留扫描进度提示' : '上方光幕平移，沿钢筋表面扫描') : '设计模型 · 核对构件外形与朝向' }}</p></div><span v-if="result && stage==='result'" class="data-date">已有结果 {{ computedAt }}</span></div>
          <div class="scene-canvas"><OperatorRebarViewer ref="viewer" :result="result" :selected-id="stage==='result'?selectedId:''" :tolerance="threshold" :load-geometry="loadGeometry" :display-mode="stage==='result'?'result':scanning?'scan':'neutral'" :scan-progress="scanProgress" :reduced-motion="reducedMotion" @select="select" @loaded="geometryLoaded" @follow-change="following=$event" />
            <div v-if="!result && !loading" class="scene-unavailable"><el-icon :size="42"><Camera /></el-icon><strong>暂无三维检测结果</strong><span>请联系技术人员完成采集和分析。</span></div>
            <div v-if="result && !fresh" class="scene-unavailable"><el-icon :size="42"><Warning /></el-icon><strong>检测结果需要更新</strong><span>请联系技术人员重新计算。</span></div>
          </div>
          <div class="scene-toolbar"><div class="view-controls"><button class="op-button" :disabled="!geometryReady" @click="viewer?.resetView()"><el-icon><FullScreen /></el-icon>看全图</button><button class="op-button" :disabled="!geometryReady" @click="viewer?.topView()">俯视</button><button class="op-button icon" aria-label="向左旋转" :disabled="!geometryReady" @click="viewer?.rotateLeft()"><el-icon><RefreshLeft /></el-icon></button><button class="op-button icon" aria-label="向右旋转" :disabled="!geometryReady" @click="viewer?.rotateRight()"><el-icon><RefreshRight /></el-icon></button><button class="op-button icon" aria-label="放大模型" :disabled="!geometryReady" @click="viewer?.zoomIn()"><el-icon><Plus /></el-icon></button><button class="op-button icon" aria-label="缩小模型" :disabled="!geometryReady" @click="viewer?.zoomOut()"><el-icon><Minus /></el-icon></button></div><div v-if="stage==='result'" class="scene-legend"><span class="selected-key">选中</span><span class="problem-key">需复核调整</span><span class="missing-key">需补扫</span><span class="normal-key">其他钢筋</span></div><span v-else class="scene-hint">{{ scanning ? '蓝色光束为扫描演示效果' : '拖动模型可旋转查看' }}</span></div>
        </section>
        <aside class="operator-controls" :class="{'editing-adjustment':editingAdjustment}">
          <template v-if="stage==='ready'">
            <div class="stage-symbol ready-symbol"><el-icon><CircleCheck /></el-icon></div><h2>构件已到位</h2>
            <p class="stage-description">可以开始扫描</p>
            <div class="workpiece-name"><span>当前构件</span><strong>{{ componentName }}</strong></div>
            <p class="ready-instruction">确认网片放稳、扫描区域内无人后，点击开始。</p>
            <button class="op-button primary main-action" :disabled="loading || !task || !geometryReady || !fresh" @click="startScan">开始扫描<el-icon><ArrowRight /></el-icon></button>
            <p class="operator-note">{{ !fresh || !geometryReady ? '等待可用的构件模型加载完成。' : '扫描结束后自动显示结果。' }}</p>
          </template>
          <template v-else-if="stage==='scan'">
            <div class="stage-symbol scan-symbol"><el-icon><Camera /></el-icon></div>
            <h2>正在扫描</h2>
            <p class="stage-description">{{ reducedMotion ? '扫描演示进行中，请稍候。' : '激光光幕正平移扫过钢筋网片。' }}</p>
            <div class="scan-progress-panel">
              <div class="scan-progress-caption"><span>演示进度</span><strong>{{ Math.round(scanProgress*100) }}<small>%</small></strong></div>
              <div class="scan-progress-track" role="progressbar" aria-label="扫描演示进度" :aria-valuenow="Math.round(scanProgress*100)" aria-valuemin="0" aria-valuemax="100"><span :style="{transform:`scaleX(${scanProgress})`}"/></div>
              <p class="scan-step-status" role="status">{{ scanProgress<.85 ? '扫描演示进行中' : '即将完成，随后自动查看结果' }}</p>
              <p v-if="reducedMotion" class="operator-note">已启用减少动态效果，保留进度提示。</p>
            </div>
            <button class="op-button secondary-action main-action" @click="cancelScan">取消扫描演示</button>
            <p class="operator-note">扫描结束后自动显示结果，无需再次点击。</p>
          </template>
          <template v-else>
            <div class="result-heading"><el-icon><Warning /></el-icon><h2>{{ issues.length ? `${issues.length} 处需要检查` : '等待质量复核' }}</h2></div><p class="result-counts">{{ outliers }} 根超复核阈值<span>·</span>{{ missing }} 根需补扫</p>
            <div class="problem-navigation"><button class="op-button icon" aria-label="上一处问题" :disabled="!issues.length || saving" @click="move(-1)"><el-icon><ArrowLeft /></el-icon></button><strong>{{ issueIndex>=0 ? `问题 ${issueIndex+1} / ${issues.length}` : '模型中选中' }}</strong><button class="op-button icon" aria-label="下一处问题" :disabled="!issues.length || saving" @click="move(1)"><el-icon><ArrowRight /></el-icon></button></div>
            <div class="selected-problem"><div class="selected-problem-heading"><h3>{{ selected ? `钢筋 ${selectedNumber}` : '尚未选择' }}</h3><button class="op-button compact" :disabled="!selected || !geometryReady" @click="viewer?.focusSelected()"><el-icon><Aim /></el-icon>放大位置</button></div><strong v-if="!editingAdjustment" class="issue-instruction">{{ selectionTitle }}</strong><p v-if="!editingAdjustment">{{ instruction }}</p><div v-if="selected" class="selected-measure"><span>{{ kind(selected)==='missing' ? '有效测量' : '偏差参考值' }}</span><strong>{{ kind(selected)==='missing' ? '缺测' : millimetres(selected.stats?.p95Abs) }}</strong></div><p v-if="selected && kind(selected)!=='missing'" class="measurement-caption">95%绝对表面偏差 · 复核阈值 {{ millimetres(threshold) }}</p></div>
            <div v-if="selected && kind(selected)!=='observed'" class="operator-action-flow">
              <div v-if="requested" class="waiting-state"><el-icon><CircleCheck /></el-icon><div><strong>已申请复检</strong><p>等待补扫和新结果，尚未放行。</p></div></div>
              <template v-else-if="adjusted"><div class="saved-note"><el-icon><Check /></el-icon><strong>处理记录已保存</strong><p>{{ adjusted.note }}</p></div><button class="op-button primary main-action" :disabled="!canAct" @click="save('request_recheck')">{{ saving ? '正在提交…' : '申请复检' }}</button></template>
              <template v-else-if="confirmed"><label class="adjustment-label">做了哪些处理？</label><div class="quick-notes"><button v-for="text in (kind(selected)==='missing'?['安排补扫缺测区域','移除遮挡后补扫']:['调整钢筋位置','重新绑扎固定','安排局部补扫'])" :key="text" class="op-button" :class="{chosen:note===text}" :disabled="saving" @click="note=text">{{ text }}</button></div><textarea v-model="note" aria-label="处理说明" rows="2" maxlength="2000" placeholder="可补充处理说明" :disabled="saving"/><button class="op-button primary main-action" :disabled="!canAct || !note.trim()" @click="save('record_adjustment')">{{ saving ? '正在保存…' : '保存处理记录' }}</button></template>
              <button v-else class="op-button primary main-action" :disabled="!canAct" @click="save('acknowledge')">{{ saving ? '正在保存…' : '确认这处问题' }}</button>
              <p v-if="demo" class="operator-note">当前操作为演示记录</p>
            </div>
            <p v-if="message" class="save-feedback" role="status">{{ message }}</p><div v-if="recordsError" class="record-error" role="alert">{{ recordsError }}<button class="op-button" :disabled="recordsLoading || saving" @click="readRecords()">重新读取记录</button></div>
            <details v-if="selected" class="operator-details"><summary>构件与钢筋信息</summary><p>{{ selected.name }}</p><p>测量覆盖 {{ coverage(selected) }} · 编号与当前报告一致</p><p>构件 {{ componentName }}</p><p>缺测保留未知状态；处理记录不会改写检测结论。</p></details>
          </template>
        </aside>
      </div>
    </main>
  </div>
</template>

<style scoped>
.operator-station{--op-blue:var(--brand-aether,#4e66cc);--op-ink:var(--brand-sapphire,#102375);--op-border:#d7e0ed;min-height:100dvh;background:#f3f6fb;color:#263657;font-family:var(--font-family-base);font-size:18px;line-height:1.5;font-variant-numeric:tabular-nums;}
.operator-station *{box-sizing:border-box}.operator-station h1,.operator-station h2,.operator-station h3,.operator-station p{margin:0}.operator-station button,.operator-station select,.operator-station textarea{font:inherit}.operator-station button{cursor:pointer}.operator-station :is(button,select,textarea):focus-visible{outline:3px solid var(--op-blue);outline-offset:3px}.operator-station ::selection{background:#4e66cc;color:white}.operator-station textarea{caret-color:#4e66cc}.operator-header{height:80px;background:white;border-bottom:1px solid var(--op-border);display:flex;justify-content:space-between;align-items:center;padding:0 32px;gap:24px}.operator-brand,.operator-account{display:flex;align-items:center;gap:24px}.operator-brand>span{font-weight:750;font-size:27px;color:var(--op-ink)}.operator-brand h1{font-size:22px;font-weight:600;border-left:1px solid var(--op-border);padding-left:24px}.operator-account>span{font-weight:600;font-size:16px}.operator-account small{display:block;color:#5b6b84;font-size:14px;font-weight:400}.operator-demo{padding:10px 32px;background:#fbf3df;color:#75521b;display:flex;gap:18px;font-size:16px}.operator-station main{padding:20px 28px 24px}.operator-context{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:20px}.operator-context>label{display:flex;align-items:center;gap:12px;min-width:0;font-size:16px}.operator-context select{max-width:400px;min-width:180px;background:white;border:1px solid var(--op-border);border-radius:8px;padding:12px;color:var(--op-ink);font-weight:600;overflow:hidden;text-overflow:ellipsis}.operator-steps{display:flex;align-items:center;justify-content:space-between;flex:1;max-width:850px;list-style:none;margin:0;padding:0;gap:18px}.operator-steps li{display:flex;align-items:center;gap:12px;position:relative;color:#596b84;padding:14px 18px;flex:1;min-width:0;border-radius:10px}.operator-steps li:not(:last-child)::after{content:'';position:absolute;right:-12px;top:50%;width:8px;height:8px;border-top:2px solid #a8b8d0;border-right:2px solid #a8b8d0;transform:rotate(45deg)}.step-number{display:grid;place-items:center;flex-shrink:0;width:48px;height:48px;background:#e2e8f3;color:#4a5e7f;border-radius:50%;font-size:27px;font-weight:700}.operator-steps strong{display:block;white-space:nowrap;font-size:22px;line-height:1.3;font-weight:650}.operator-steps small{display:block;margin-top:5px;font-size:14px;white-space:nowrap}.operator-steps .active{background:#e7edfc;color:var(--op-ink)}.operator-steps .active .step-number{color:white;background:var(--op-blue)}.operator-steps .done .step-number{color:var(--op-blue);background:#e7edfc}.operator-workspace{display:grid;grid-template-columns:minmax(0,1fr) 400px;min-height:calc(100dvh - 195px);gap:24px}.operator-demo~main .operator-workspace{min-height:calc(100dvh - 239px)}.operator-scene{display:flex;flex-direction:column;min-width:0;border:1px solid var(--op-border);border-radius:12px;background:white;overflow:hidden}.scene-title{padding:18px 22px;display:flex;align-items:center;justify-content:space-between;gap:16px}.scene-title h2{font-size:21px;color:var(--op-ink)}.scene-title p{font-size:15px;color:#596b84;margin-top:4px}.data-date{font-size:14px;color:#596b84;text-align:right}.scene-canvas{position:relative;flex:1;min-height:360px;background:#edf2f8}.scene-toolbar{padding:16px;display:flex;flex-wrap:wrap;justify-content:space-between;gap:16px;background:white}.view-controls{display:flex;gap:8px;flex-wrap:wrap}.scene-legend{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:14px;color:#53647b}.scene-legend>span{display:flex;align-items:center;gap:6px}.scene-legend>span:before{content:'';width:12px;height:12px;border-radius:3px;background:#8292a6}.scene-legend .selected-key:before{background:#1674e8}.scene-legend .problem-key:before{background:#d74f55}.scene-legend .missing-key:before{background:#d99b23}.operator-controls{background:white;border:1px solid var(--op-border);border-radius:12px;padding:28px;min-width:0;align-self:stretch;overflow:auto;scrollbar-color:#b5c2d8 #f3f6fb}.operator-controls>h2{font-size:34px;font-weight:650;line-height:1.35;color:var(--op-ink);margin-top:22px}.stage-symbol{width:68px;height:68px;display:grid;place-items:center;background:#e9eefc;color:var(--op-blue);border-radius:14px;font-size:36px;margin-top:20px}.stage-symbol.ready-symbol{background:#e8f3ee;color:#356c58}.ready-instruction{font-size:18px;color:#596b84;margin-bottom:24px!important;line-height:1.7}.stage-description{margin-top:18px!important;color:#596b84;font-size:20px;line-height:1.7}.workpiece-name{padding:24px 0;margin:14px 0 20px;border-block:1px solid #e1e7f0}.workpiece-name>span{display:block;color:#596b84;font-size:16px}.workpiece-name strong{display:block;color:var(--op-ink);font-size:23px;overflow-wrap:anywhere;margin-top:8px}.op-button{display:inline-flex;align-items:center;justify-content:center;gap:10px;min-height:52px;padding:12px 18px;background:white;border:1px solid var(--op-border);border-radius:8px;color:var(--op-ink);font-weight:600;line-height:1.4;transition:background-color .15s,border-color .15s}.op-button:hover:not(:disabled){background:#eef2fc;border-color:var(--op-blue)}.op-button.primary{background:var(--op-blue);border-color:var(--op-blue);color:white}.op-button.primary:hover:not(:disabled){background:#3d53b9}.operator-station button:disabled{background:#e9edf3;color:#758399;border-color:#d8e0eb;cursor:not-allowed}.op-button.subtle{background:transparent;border-color:transparent;font-size:16px}.op-button.icon{width:52px;flex-shrink:0;padding:12px;font-size:22px}.op-button.compact{font-size:16px;min-height:46px;padding:10px 12px}.main-action{width:100%;min-height:78px;font-size:25px;justify-content:center;gap:24px}.secondary-action{margin-top:16px;font-size:22px;min-height:64px}.operator-note{color:#596b84;font-size:15px;margin-top:16px!important;line-height:1.65}.result-heading{display:flex;align-items:center;gap:10px;color:#9a6122}.result-heading>.el-icon{font-size:26px}.result-heading h2{font-size:25px;line-height:1.4;color:var(--op-ink)}.result-counts{margin-top:10px!important;color:#6a4d33;font-size:16px}.result-counts span{margin-inline:10px}.problem-navigation{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:22px 0}.problem-navigation strong{font-size:20px;color:var(--op-ink)}.selected-problem{border-block:1px solid #e1e7f0;padding:20px 0}.selected-problem-heading{display:flex;justify-content:space-between;align-items:center;gap:8px}.selected-problem h3{font-size:25px;color:var(--op-ink)}.issue-instruction{display:block;margin-top:18px;font-size:21px}.selected-problem>p{margin-top:8px;color:#53647b;font-size:17px;line-height:1.65}.selected-measure{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:18px}.selected-measure span{font-size:16px;color:#53647b}.selected-measure strong{font-size:25px;color:#a13d48}.selected-problem .measurement-caption{font-size:13px;line-height:1.4;margin-top:5px}.operator-action-flow{margin-top:22px}.operator-action-flow .main-action{min-height:65px;font-size:22px}.waiting-state{display:flex;gap:10px;color:#3e6769;align-items:flex-start;padding-block:14px}.waiting-state>.el-icon{font-size:26px}.waiting-state strong{font-size:21px}.waiting-state p{font-size:16px;margin-top:8px}.saved-note{font-size:16px;margin-bottom:18px}.saved-note>.el-icon{color:#3e6769;margin-right:8px}.saved-note p{color:#53647b;margin-top:8px;overflow-wrap:anywhere}.adjustment-label{display:block;font-size:18px;font-weight:600;margin-bottom:12px}.quick-notes{display:flex;flex-wrap:wrap;gap:8px}.quick-notes .op-button{font-size:16px;min-height:44px;padding:9px 12px}.quick-notes .chosen{color:var(--op-blue);background:#e9eefc;border-color:var(--op-blue)}.operator-action-flow textarea{width:100%;padding:10px 12px;background:white;border:1px solid var(--op-border);border-radius:8px;color:#263657;resize:vertical;font-size:16px;line-height:1.5;margin:12px 0;min-height:72px}.operator-details{font-size:14px;color:#65748b;margin-top:20px}.operator-details summary{cursor:pointer;padding-block:6px}.operator-details p{margin-top:8px;overflow-wrap:anywhere}.save-feedback{font-size:15px;color:#3e6769;margin-top:12px!important}.record-error{color:#a13d48;font-size:16px;margin-top:12px}.record-error button{margin-top:10px}.scene-unavailable{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;background:#edf2f8;color:#65748b;padding:24px;text-align:center}.scene-unavailable strong{font-size:24px;color:var(--op-ink)}.operator-empty,.operator-error{min-height:600px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:24px;text-align:center}.operator-empty h2{font-size:28px;color:var(--op-ink)}.operator-empty p,.operator-error p{color:#53647b}.operator-error strong{color:#a13d48;font-size:25px}
.scene-hint{color:#596b84;font-size:15px;align-self:center}.stage-symbol.scan-symbol{background:#e4f4f2;color:#146e70}.scan-progress-panel{margin:30px 0 28px}.scan-progress-caption{display:flex;align-items:baseline;justify-content:space-between;gap:16px;color:#47617a;font-size:18px}.scan-progress-caption strong{font-size:42px;line-height:1.2;color:#146e70}.scan-progress-caption small{font-size:22px;margin-left:5px}.scan-progress-track{height:10px;background:#e5edf1;border-radius:5px;overflow:hidden;margin-top:16px}.scan-progress-track>span{display:block;height:100%;background:#218b8b;transform-origin:left center}.scan-step-status{font-size:16px;color:#526b7b;margin-top:16px!important}
.editing-adjustment .selected-problem{padding:14px 0}.editing-adjustment .problem-navigation{margin:14px 0}
@media(min-width:1400px){.operator-station{height:100dvh;display:flex;flex-direction:column}.operator-header,.operator-demo{flex-shrink:0}.operator-station main{flex:1;min-height:0;display:flex;flex-direction:column}.operator-context{flex-shrink:0}.operator-workspace,.operator-demo~main .operator-workspace{flex:1;min-height:0}.operator-scene,.scene-canvas{min-height:0}.operator-controls{min-height:0;max-height:100%}}
@media(max-width:1399px){.operator-header{padding-inline:22px}.operator-station main{padding:16px}.operator-context{gap:12px}.operator-context select{max-width:270px}.operator-steps{gap:12px}.operator-steps li{padding:12px 8px;gap:8px}.operator-steps strong{font-size:19px}.step-number{width:40px;height:40px;font-size:24px}.operator-workspace{grid-template-columns:minmax(0,1fr) 350px;gap:16px}.operator-controls{padding:22px}.data-date{display:none}.operator-brand{gap:16px}.operator-account{gap:12px}}
@media(max-width:1050px){.operator-context{flex-wrap:wrap}.operator-steps{order:3;flex-basis:100%;max-width:none}.operator-steps li{justify-content:center}.operator-workspace{grid-template-columns:minmax(0,1fr)}.scene-canvas{height:440px;flex:none}.operator-controls{max-height:none}.operator-brand>span{font-size:22px}.operator-account>span{display:none}.operator-controls>h2 br{display:none}.operator-controls .stage-symbol{margin-top:0}.operator-header{height:auto;min-height:76px;flex-wrap:wrap;padding-block:12px}.operator-brand h1{font-size:19px}.operator-demo{padding-inline:18px;font-size:14px;flex-wrap:wrap;gap:4px 12px}.main-action{max-width:560px}.operator-context select{max-width:calc(100vw - 200px)}}
@media(max-width:600px){.operator-steps{gap:8px}.operator-steps li{flex-direction:column;gap:8px;padding:12px 4px;text-align:center}.operator-steps strong{font-size:17px}.operator-steps small{font-size:12px;white-space:normal}.operator-steps li:not(:last-child)::after{right:-7px}.step-number{width:42px;height:42px;font-size:25px}.operator-context>label{flex:1 1 100%;flex-direction:column;align-items:stretch;gap:6px}.operator-context select{min-width:0;max-width:100%;width:100%}}
@media(prefers-reduced-motion:reduce){.op-button{transition:none}}
</style>
