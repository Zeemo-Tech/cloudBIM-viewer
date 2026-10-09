<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Aim, ArrowLeft, DArrowRight } from '@element-plus/icons-vue'
import {
  BimPreviewPanel,
  DEFAULT_REBAR_SWEEP_PARAMS,
  MeasurementToolbar,
  REBAR_SWEEP_ALGORITHM,
  ViewerAnalysisOverlay,
  getRemeshStatus,
  remeshBimAsset,
  useViewerMeasurements,
  type RemeshStatus,
  type ViewerPageEmits,
  type ViewerSingleAssetProps,
} from '@cloudbim/viewer-core'
import { useBimRemeshDisplay } from './bimRemeshDisplay'

type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'

const props = defineProps<ViewerSingleAssetProps>()
const emit = defineEmits<ViewerPageEmits>()

const bimPanelRef = ref<any>(null)
const bimLoaded = ref(false)
const bimRemeshStatus = ref<RemeshStatus | null>(null)
const bimRemeshSubmitting = ref(false)
const bimRemeshError = ref('')
let bimRemeshGeneration = 0
let bimRemeshTimer: ReturnType<typeof setTimeout> | undefined

const assetId = computed(() => props.assetId ?? null)

const {
  analysisMode,
  analysisPoint,
  analysisDistance,
  analysisAreas,
  analysisPoints,
  analysisDistances,
  selectAnalysisMode,
  handleAnalysisModeExit,
  clearAnalysis,
  removeAnalysisById,
  handleAnalysisPoint,
  handleAnalysisDistance,
  handleAnalysisArea,
  loadMeasurements,
  resetAnalysisState,
} = useViewerMeasurements({ assetId, panelRef: bimPanelRef })

const bimRemeshReady = computed(() =>
  bimRemeshStatus.value?.status === 'succeeded' &&
  bimRemeshStatus.value.algorithm === REBAR_SWEEP_ALGORITHM &&
  Boolean(bimRemeshStatus.value.resultFileId),
)
const bimRemeshArtifactKey = computed(() => bimRemeshReady.value
  ? `${props.assetId}:${bimRemeshStatus.value?.contentHash}:${bimRemeshStatus.value?.finishedAt}`
  : '')
const bimRemeshDisplay = useBimRemeshDisplay(bimLoaded, bimRemeshArtifactKey, async (visible) => {
  if (visible && !bimPanelRef.value) throw new Error('BIM 预览尚未准备好')
  await bimPanelRef.value?.setBimRemeshVisible(visible)
})
const { visible: bimRemeshVisible, error: bimRemeshDisplayError } = bimRemeshDisplay
const bimRemeshBusy = computed(() => bimRemeshSubmitting.value || bimRemeshDisplay.busy.value)
const bimRemeshRunning = computed(() => ['queued', 'processing'].includes(bimRemeshStatus.value?.status || ''))
const bimRemeshCanRun = computed(() => Boolean(
  bimRemeshStatus.value?.supported && !bimRemeshRunning.value &&
  (bimRemeshStatus.value.status === 'succeeded' || bimRemeshStatus.value.canManualRetry),
))
const bimRemeshActionText = computed(() => {
  if (bimRemeshBusy.value) return '请稍候…'
  if (bimRemeshRunning.value) return '正在生成网格…'
  if (bimRemeshStatus.value?.status === 'succeeded') return '重新生成网格'
  if (bimRemeshStatus.value?.status === 'failed') return '重试生成网格'
  return '生成网格'
})
const bimRemeshStatusText = computed(() => {
  if (!bimRemeshStatus.value) return '正在查询均匀化状态…'
  if (!bimRemeshStatus.value.supported) return '当前模型不支持网格均匀化'
  switch (bimRemeshStatus.value.status) {
    case 'queued': return '任务已排队，完成后自动显示保形网格'
    case 'processing': return '正在生成保形网格，完成后自动显示'
    case 'succeeded': return !bimRemeshReady.value ? '已有结果版本不匹配，请重新生成网格' : bimRemeshVisible.value ? '当前显示：保形网格' : '当前显示：原始模型；可开启保形网格'
    case 'failed': return '均匀化失败，可重试'
    default: return '尚未生成均匀化网格'
  }
})

async function refreshBimRemeshStatus() {
  clearTimeout(bimRemeshTimer)
  const currentAssetId = props.assetId
  const generation = bimRemeshGeneration
  if (!currentAssetId) return
  try {
    const { data } = await getRemeshStatus(currentAssetId)
    if (generation !== bimRemeshGeneration) return
    bimRemeshStatus.value = data
    bimRemeshError.value = data.status === 'failed' ? data.lastError || '网格均匀化失败' : ''
    if (data.supported && ['idle', 'queued', 'processing'].includes(data.status || 'idle')) {
      bimRemeshTimer = setTimeout(() => void refreshBimRemeshStatus(), 4000)
    }
  } catch (error) {
    if (generation === bimRemeshGeneration) bimRemeshError.value = error instanceof Error ? error.message : '查询均匀化状态失败'
  }
}

function toggleBimRemesh(visible = !bimRemeshVisible.value) {
  if (bimRemeshBusy.value || !bimLoaded.value || !bimRemeshReady.value) return
  if (visible === bimRemeshVisible.value) return
  bimRemeshError.value = ''
  bimRemeshDisplay.toggle()
}

async function retryBimRemesh() {
  if (!props.assetId || !bimRemeshCanRun.value || bimRemeshBusy.value) return
  // Retire in-flight status reads so they cannot restore the previous result.
  const generation = ++bimRemeshGeneration
  clearTimeout(bimRemeshTimer)
  const force = bimRemeshStatus.value?.status === 'succeeded'
  bimRemeshSubmitting.value = true
  bimRemeshError.value = ''
  try {
    await remeshBimAsset(props.assetId, {
      algorithm: REBAR_SWEEP_ALGORITHM,
      params: { ...DEFAULT_REBAR_SWEEP_PARAMS },
      ...(force ? { force: true } : {}),
    })
    if (generation !== bimRemeshGeneration) return
    // A fast rerun may finish before the next read and retain the same hash.
    // Always retire the displayed PLY after an accepted submission.
    bimRemeshStatus.value = { ...bimRemeshStatus.value!, status: 'queued', canManualRetry: false }
    await nextTick() // Let the display watcher retire the old artifact, even for a fast rerun.
    if (generation !== bimRemeshGeneration) return
    await refreshBimRemeshStatus()
  } catch (error) {
    if (generation === bimRemeshGeneration) bimRemeshError.value = error instanceof Error ? error.message : '提交均匀化任务失败'
  } finally {
    if (generation === bimRemeshGeneration) bimRemeshSubmitting.value = false
  }
}

function resetBimRemeshState() {
  bimRemeshGeneration++
  clearTimeout(bimRemeshTimer)
  bimLoaded.value = false
  bimRemeshStatus.value = null
  bimRemeshDisplay.reset()
  bimRemeshSubmitting.value = false
  bimRemeshError.value = ''
}

const backgroundTheme = ref<PreviewBackgroundTheme>('deep')
const sidebarCollapsed = ref(false)
const analysisToolbarCollapsed = ref(false)

const bimControls = reactive({
  showAxes: true,
  showGrid: true,
  wireframe: false,
  sectionEnabled: false,
})

const backgroundOptions: Array<{ label: string; value: PreviewBackgroundTheme }> = [
  { label: '蓝色', value: 'gradient' },
  { label: '深色', value: 'deep' },
  { label: '浅色', value: 'light' },
  { label: '纯黑', value: 'black' },
]

const returnLabel = computed(() => props.backLabel?.trim() || '返回')
const pageTitle = 'BIM 全屏预览'
const emptyText = '请返回项目的 IFC 模型列表，选择已就绪的模型进行预览。'

// 宿主可以用 `navigation.back` 回调，也可以监听 `back` 事件。
function closePage() {
  if (props.navigation?.back) {
    props.navigation.back()
    return
  }

  emit('back')
}

function resetView() {
  bimPanelRef.value?.resetView?.()
}

function applyPanelSettings() {
  const panel = bimPanelRef.value
  if (!panel) {
    return
  }

  panel.setBackgroundTheme?.(backgroundTheme.value)
  panel.setShowAxes?.(bimControls.showAxes)
  panel.setShowGrid?.(bimControls.showGrid)
  panel.setWireframe?.(bimControls.wireframe)
  panel.setSectionState?.({ enabled: bimControls.sectionEnabled })
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

watch(
  () => [
    backgroundTheme.value,
    bimControls.showAxes,
    bimControls.showGrid,
    bimControls.wireframe,
    bimControls.sectionEnabled,
    bimPanelRef.value,
  ] as const,
  () => {
    applyPanelSettings()
  },
  { immediate: true },
)

onMounted(() => {
  void refreshBimRemeshStatus()
  applyPanelSettings()
  void loadMeasurements()
})

onBeforeUnmount(() => {
  resetBimRemeshState()
})

watch(
  () => props.assetId,
  () => {
    resetBimRemeshState()
    void refreshBimRemeshStatus()
    resetAnalysisState()
    void loadMeasurements()
  },
)
</script>

<template>
  <section class="asset-preview-page" :class="`theme-${backgroundTheme}`">
    <header class="bim-preview-header">
      <button class="preview-button" type="button" @click="closePage">
        <el-icon><ArrowLeft /></el-icon>
        <span>{{ returnLabel }}</span>
      </button>
      <div class="bim-file-context">
        <strong :title="displayName">{{ displayName || 'BIM 模型预览' }}</strong>
        <span :title="projectName">{{ projectName || (projectId ? `项目 ${projectId}` : '模型预览') }}</span>
      </div>
      <div v-if="assetId" class="bim-header-tools">
        <span class="toolbar-label">测量</span>
        <MeasurementToolbar
          v-model:collapsed="analysisToolbarCollapsed"
          :mode="analysisMode"
          :disabled="!bimLoaded"
          position="static"
          @update:mode="selectAnalysisMode"
          @clear="clearAnalysis"
        />
        <button class="preview-button" type="button" :disabled="!bimLoaded" @click="resetView">
          <el-icon><Aim /></el-icon>
          <span>重置视角</span>
        </button>
      </div>
    </header>

    <div v-if="!assetId" class="empty-state">
      <h2>{{ pageTitle }}</h2>
      <p>{{ emptyText }}</p>
    </div>

    <div v-else class="layout-shell" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
      <div class="viewer-region" :class="`theme-${backgroundTheme}`">
        <BimPreviewPanel
          ref="bimPanelRef"
          class="viewer-panel"
          :asset-id="assetId"
          :display-name="displayName"
          :analysis-mode="analysisMode"
          :analysis-points="analysisPoints"
          :analysis-distances="analysisDistances"
          :analysis-areas="analysisAreas"
          @loaded-change="bimLoaded = $event"
          @analysis-point="handleAnalysisPoint"
          @analysis-distance="handleAnalysisDistance"
          @analysis-area="handleAnalysisArea"
          @analysis-delete="removeAnalysisById($event.kind, $event.id)"
          @analysis-mode-exit="handleAnalysisModeExit"
          minimal
        />

        <ViewerAnalysisOverlay
          :mode="analysisMode"
          :point="analysisPoint"
          :distance="analysisDistance"
          :points="analysisPoints"
          :distances="analysisDistances"
          :areas="analysisAreas"
          @clear="clearAnalysis"
        />
      </div>

      <aside class="sidebar" aria-label="模型工具" :class="{ 'is-collapsed': sidebarCollapsed }">
        <div class="sidebar-heading">
          <h2 v-if="!sidebarCollapsed">模型工具</h2>
          <button
            class="preview-button icon-btn"
            type="button"
            :aria-label="sidebarCollapsed ? '展开模型工具' : '收起模型工具'"
            :title="sidebarCollapsed ? '展开模型工具' : '收起模型工具'"
            :aria-expanded="!sidebarCollapsed"
            aria-controls="bim-tool-sections"
            @click="toggleSidebar"
          >
            <el-icon><ArrowLeft v-if="sidebarCollapsed" /><DArrowRight v-else /></el-icon>
          </button>
        </div>
        <div v-show="!sidebarCollapsed" id="bim-tool-sections" class="sidebar-sections">
          <section class="tool-section" aria-labelledby="mesh-heading">
            <h3 id="mesh-heading">模型与网格</h3>
            <div class="model-view-options" role="group" aria-label="模型显示内容">
              <button class="preview-button" :class="{ 'is-active': !bimRemeshVisible }" type="button"
                :aria-pressed="!bimRemeshVisible" :disabled="!bimLoaded || bimRemeshBusy"
                @click="toggleBimRemesh(false)">原始 IFC</button>
              <button class="preview-button" :class="{ 'is-active': bimRemeshVisible }" type="button"
                :aria-pressed="bimRemeshVisible" :disabled="!bimLoaded || !bimRemeshReady || bimRemeshBusy"
                @click="toggleBimRemesh(true)">网格结果</button>
            </div>
            <p class="mesh-status" role="status" :class="{ 'is-ready': bimRemeshReady, 'is-error': bimRemeshStatus?.status === 'failed' }">
              {{ bimRemeshStatusText }}
            </p>
            <table v-if="bimRemeshReady && bimRemeshStatus?.stats" class="mesh-stats" aria-label="网格均匀化前后统计">
              <thead><tr><th scope="col">几何统计</th><th scope="col">原始 IFC</th><th scope="col">网格结果</th></tr></thead>
              <tbody>
                <tr><th scope="row">顶点</th><td>{{ bimRemeshStatus.stats.vertexBefore.toLocaleString() }}</td><td>{{ bimRemeshStatus.stats.vertexAfter.toLocaleString() }}</td></tr>
                <tr><th scope="row">三角面</th><td>{{ bimRemeshStatus.stats.faceBefore.toLocaleString() }}</td><td>{{ bimRemeshStatus.stats.faceAfter.toLocaleString() }}</td></tr>
              </tbody>
            </table>
            <div class="mesh-actions">
              <button class="preview-button primary-button" type="button" :disabled="!bimRemeshCanRun || bimRemeshBusy" @click="retryBimRemesh">{{ bimRemeshActionText }}</button>
              <button class="preview-button" type="button" :disabled="bimRemeshBusy || bimRemeshRunning" @click="refreshBimRemeshStatus">刷新状态</button>
            </div>
            <p v-if="bimRemeshError || bimRemeshDisplayError" role="alert" class="error-message">{{ bimRemeshError || bimRemeshDisplayError }}</p>
            <p class="section-note">{{ bimRemeshVisible ? '橙色模型为均匀化结果；开启线框可检查三角网格。' : '切换网格结果后，可用线框检查网格化效果。' }}</p>
            <label class="toggle-row">
              <span>线框模式</span>
              <el-switch v-model="bimControls.wireframe" aria-label="线框模式" />
            </label>
          </section>
          <section class="tool-section" aria-labelledby="section-heading">
            <h3 id="section-heading">剖切</h3>
            <label class="toggle-row">
              <span>启用剖切</span>
              <el-switch v-model="bimControls.sectionEnabled" aria-label="启用剖切" />
            </label>
            <p class="section-note">开启后拖拽模型外侧的 6 个方向箭头，调整剖切范围。</p>
          </section>
          <section class="tool-section" aria-labelledby="scene-heading">
            <h3 id="scene-heading">辅助显示</h3>
            <label class="toggle-row"><span>坐标轴</span><el-switch v-model="bimControls.showAxes" aria-label="坐标轴" /></label>
            <label class="toggle-row"><span>参考网格</span><el-switch v-model="bimControls.showGrid" aria-label="参考网格" /></label>
          </section>
          <section class="tool-section" aria-labelledby="background-heading">
            <h3 id="background-heading">画布背景</h3>
            <div class="background-options" role="group" aria-label="画布背景">
              <button v-for="option in backgroundOptions" :key="option.value" class="preview-button theme-chip"
                :class="{ 'is-active': backgroundTheme === option.value }" type="button"
                :aria-pressed="backgroundTheme === option.value" @click="backgroundTheme = option.value">
                <span class="theme-swatch" :class="`theme-swatch-${option.value}`"></span>{{ option.label }}
              </button>
            </div>
          </section>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.asset-preview-page {
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: var(--text-primary);
  background: var(--bg-page);
  font-size: var(--font-size-sm);
}

.bim-preview-header {
  flex: 0 0 64px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-card);
}

.bim-file-context {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--spacing-compact);
  padding-left: var(--spacing-md);
  border-left: 1px solid var(--border-color);
}

.bim-file-context strong,
.bim-file-context span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.bim-file-context strong { font-weight: 600; }
.bim-file-context span { color: var(--text-secondary); font-size: var(--font-size-xs); }
.bim-header-tools { display: flex; align-items: center; gap: var(--spacing-compact); }
.toolbar-label { color: var(--text-secondary); }

.preview-button {
  min-height: var(--control-height);
  padding: 0 var(--spacing-compact);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xs);
  background: var(--bg-card);
  color: var(--text-secondary);
  font: inherit;
  cursor: pointer;
  transition: background-color var(--transition-fast), border-color var(--transition-fast);
}

.preview-button:hover:not(:disabled) { background: var(--bg-control-hover); border-color: var(--border-color-hover); }
.preview-button.is-active { background: var(--color-primary-soft); border-color: var(--color-primary); color: var(--color-primary-active); font-weight: 600; }
.preview-button:active:not(:disabled) { background: var(--bg-control); }
.primary-button { background: var(--color-primary-hover); border-color: var(--color-primary-hover); color: var(--bg-card); }
.primary-button:hover:not(:disabled) { background: var(--color-primary-active); border-color: var(--color-primary-active); }
.primary-button:active:not(:disabled) { background: var(--color-primary-active); }
.preview-button:disabled { cursor: not-allowed; color: var(--text-disabled); background: var(--bg-muted); border-color: var(--border-color-light); }
.preview-button:focus-visible,
.bim-header-tools :deep(button:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }

.layout-shell {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--viewer-panel-width);
}
.layout-shell.is-sidebar-collapsed { grid-template-columns: minmax(0, 1fr) 56px; }
.viewer-region { position: relative; min-width: 0; min-height: 0; overflow: hidden; }
.viewer-panel { width: 100%; height: 100%; }
/* The renderer resizes its buffer without rewriting its initial inline CSS size. */
.viewer-region :deep(.unified-viewer-viewport > canvas) { width: 100% !important; height: 100% !important; }
.viewer-region :deep(.preview-panel),
.viewer-region :deep(.preview-panel.is-minimal) { height: 100%; min-height: 0; border: 0; border-radius: 0; box-shadow: none; }

.sidebar { min-height: 0; display: flex; flex-direction: column; border-left: 1px solid var(--border-color); background: var(--bg-card); }
.sidebar-heading { min-height: 56px; display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-sm); padding: var(--spacing-sm) var(--spacing-md); border-bottom: 1px solid var(--border-color-light); }
.sidebar-heading h2 { margin: 0; font-size: var(--font-size-sm); font-weight: 600; }
.icon-btn { width: var(--control-height); flex: 0 0 var(--control-height); padding: 0; }
.sidebar.is-collapsed .sidebar-heading { padding-inline: var(--spacing-sm); justify-content: center; }
.sidebar-sections { min-height: 0; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--border-color-hover) transparent; }
.tool-section { padding: var(--spacing-md); border-bottom: 1px solid var(--border-color-light); }
.tool-section h3 { margin: 0 0 var(--spacing-compact); color: var(--text-primary); font-size: var(--font-size-sm); font-weight: 600; }
.model-view-options { display: grid; grid-template-columns: 1fr 1fr; gap: var(--spacing-sm); }
.mesh-status { margin: var(--spacing-compact) 0; color: var(--text-secondary); font-size: var(--font-size-xs); line-height: var(--line-height-base); overflow-wrap: anywhere; }
.mesh-status.is-ready { color: var(--color-success); }
.mesh-status.is-error, .error-message { color: var(--text-danger); }
.mesh-stats { width: 100%; margin-bottom: var(--spacing-md); border-collapse: collapse; font-size: var(--font-size-xs); font-variant-numeric: tabular-nums; }
.mesh-stats th, .mesh-stats td { padding: var(--spacing-xs) 0; text-align: right; font-weight: 400; }
.mesh-stats th:first-child { text-align: left; color: var(--text-secondary); }
.mesh-stats thead { color: var(--text-secondary); border-bottom: 1px solid var(--border-color-light); }
.mesh-stats thead th { padding-bottom: var(--spacing-sm); }
.mesh-stats tbody tr:first-child th, .mesh-stats tbody tr:first-child td { padding-top: var(--spacing-sm); }
.mesh-actions { display: flex; gap: var(--spacing-sm); }
.mesh-actions .primary-button { flex: 1; }
.error-message { margin: var(--spacing-sm) 0 0; overflow-wrap: anywhere; font-size: var(--font-size-xs); }
.section-note { margin: var(--spacing-sm) 0 0; color: var(--text-secondary); font-size: var(--font-size-xs); line-height: var(--line-height-base); }
.toggle-row { min-height: var(--control-height); display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-compact); }
.tool-section > .section-note + .toggle-row { margin-top: var(--spacing-sm); }
.toggle-row :deep(.el-switch) { --el-switch-on-color: var(--color-primary); }
.background-options { display: grid; grid-template-columns: 1fr 1fr; gap: var(--spacing-sm); }
.theme-chip { justify-content: flex-start; }
.theme-swatch { width: 16px; height: 16px; flex: 0 0 auto; border: 1px solid var(--border-color); border-radius: var(--spacing-xs); }
.theme-swatch-gradient { background: #10213b; }
.theme-swatch-deep { background: #0c1224; }
.theme-swatch-light { background: #e8eef6; }
.theme-swatch-black { background: #000; }

.bim-header-tools :deep(.measurement-toolbar) { background: transparent; border-radius: 0; }
.bim-header-tools :deep(.measurement-toggle),
.bim-header-tools :deep(.measurement-action) { min-height: var(--control-height); color: var(--text-secondary); border: 1px solid var(--border-color); border-radius: var(--radius-xs); background: var(--bg-card); font-size: var(--font-size-sm); }
.bim-header-tools :deep(.measurement-toggle) { width: var(--control-height); height: var(--control-height); }
.bim-header-tools :deep(.measurement-toggle-icon) { filter: none; }
.bim-header-tools :deep(.measurement-toggle:hover),
.bim-header-tools :deep(.measurement-action:hover:not(:disabled)),
.bim-header-tools :deep(.measurement-action.is-active) { color: var(--color-primary-active); background: var(--color-primary-soft); border-color: var(--color-primary); }
.bim-header-tools :deep(.measurement-action--clear) { color: var(--text-danger); margin-left: var(--spacing-sm); }
.empty-state { flex: 1; display: grid; place-content: center; padding: var(--spacing-lg); text-align: center; }
.empty-state h2 { font-size: var(--font-size-lg); }
.empty-state p { color: var(--text-secondary); }

@media (max-width: 1600px) {
  .asset-preview-page { --viewer-panel-width: 320px; }
  .bim-file-context { flex-direction: column; align-items: flex-start; gap: 0; }
  .bim-file-context strong, .bim-file-context span { max-width: 100%; }
}
@media (max-width: 1000px) {
  .bim-header-tools { gap: var(--spacing-sm); }
  .toolbar-label { display: none; }
  .bim-header-tools :deep(.measurement-action span) { display: none; }
  .bim-header-tools :deep(.measurement-action) { width: var(--control-height); padding: 0; }
}
@media (max-width: 720px) {
  .bim-preview-header { flex-wrap: wrap; gap: var(--spacing-sm); }
  .bim-file-context { flex-basis: 40%; }
  .bim-header-tools { margin-left: auto; }
  .layout-shell { grid-template-columns: minmax(0, 1fr) min(280px, 48vw); }
  .mesh-actions { flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) {
  .asset-preview-page *, .asset-preview-page :deep(*) { transition: none !important; }
}
</style>
