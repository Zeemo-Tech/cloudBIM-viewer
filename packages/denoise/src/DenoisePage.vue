<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { Aim, ArrowLeft, FullScreen } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { BufferGeometry } from 'three'
import {
  PointcloudPreviewPanel,
  backendRequest,
  computeDenoise,
  computePointcloudPreprocess,
  denoiseArtifactUrl,
  getAssetDetail,
  getAssetRepresentations,
  getBimAlignment,
  getLatestDenoise,
  getPointcloudPreprocess,
  selectPointcloudRepresentation,
  type DenoiseResult,
  type PointcloudPreprocessResult,
  type ViewerAssetPairProps,
  type ViewerPageEmits,
} from '@cloudbim/viewer-core'
import DenoisePanel from './DenoisePanel.vue'
import {
  applyDenoisePreviewAppearance,
  parseDenoisePreview,
  type DenoiseColorMode,
} from './denoisePreview'

type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'
type DenoiseView = 'source' | DenoiseColorMode

const props = withDefaults(
  defineProps<
    ViewerAssetPairProps & {
      /** 所属项目名称，用于页头副标题。 */
      projectName?: string
      /** 返回入口文案。 */
      backLabel?: string
      /** 视口背景主题。 */
      backgroundTheme?: PreviewBackgroundTheme
    }
  >(),
  {
    projectName: '',
    backLabel: '返回扫描点云',
    backgroundTheme: 'deep',
  },
)

const emit = defineEmits<ViewerPageEmits>()

const backgroundOptions: Array<{ label: string; value: PreviewBackgroundTheme }> = [
  { label: '深色', value: 'deep' },
  { label: '蓝色', value: 'gradient' },
  { label: '浅色', value: 'light' },
  { label: '纯黑', value: 'black' },
]

const panelRef = ref<any>(null)
const stageRef = ref<HTMLElement | null>(null)

const backgroundTheme = ref<PreviewBackgroundTheme>(props.backgroundTheme)
const pointcloudSize = ref(2.5)
const pointcloudEdlEnabled = ref(true)
const pointcloudFullscreen = ref(false)
const pointcloudLoaded = ref(false)

// ── 前置条件：无台面点云表示 + 已保存的配准矩阵 ──────────────────────────────
const resourcesLoading = ref(false)
const resourcesResolved = ref(false)
const resourceError = ref('')
const pointcloudSourceUrl = ref('')
const pointcloudPreprocessResult = ref<PointcloudPreprocessResult | null>(null)
const pointcloudPreprocessRequired = ref(false)
const pointcloudPreprocessRunning = ref(false)
const pointcloudPreprocessError = ref('')
const alignmentMatrix = ref<number[] | null>(null)
const alignmentLoading = ref(false)

// ── 去噪结果与预览 ──────────────────────────────────────────────────────────
const denoiseResult = ref<DenoiseResult | null>(null)
const denoiseRunning = ref(false)
const denoiseLoading = ref(false)
const denoiseError = ref('')
const denoiseView = ref<DenoiseView>('source')
const denoiseColorMode = ref<DenoiseColorMode>('cleaned')
const denoiseVisibleClasses = ref<number[]>([3])
const denoiseVisiblePointCount = ref(0)
const denoisePreviewLoading = ref(false)
const denoisePreviewGeometry = shallowRef<BufferGeometry | null>(null)
const denoisePreviewOrigin = ref<[number, number, number]>([0, 0, 0])

let resourcesToken = 0
let denoiseRequestId = 0
let denoisePreviewRequestId = 0

const missingSelection = computed(() => !props.pointcloudAssetId || !props.bimAssetId)
const hasSavedAlignment = computed(() => Array.isArray(alignmentMatrix.value))
const canRunDenoise = computed(
  () =>
    !missingSelection.value &&
    pointcloudLoaded.value &&
    !pointcloudPreprocessRequired.value &&
    hasSavedAlignment.value &&
    !denoiseRunning.value &&
    !denoiseLoading.value,
)
const canInspect = computed(
  () => Boolean(denoiseResult.value?.fresh) && !denoiseRunning.value && !denoiseLoading.value,
)
const runBlockedReason = computed(() => {
  if (missingSelection.value) return '缺少 BIM 设计模型或扫描点云'
  if (pointcloudPreprocessRequired.value) return '当前点云尚未生成无台面切片，请先补充预处理'
  if (!hasSavedAlignment.value) return '该点云与设计模型尚未保存配准矩阵，请先完成配准'
  if (!pointcloudLoaded.value) return '点云仍在加载'
  return ''
})

function closePage() {
  if (props.navigation?.back) {
    props.navigation.back()
    return
  }
  emit('back')
}

function resetView() {
  panelRef.value?.resetPointcloudView?.()
}

async function toggleFullscreen() {
  const stage = stageRef.value
  if (!stage) return
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await stage.requestFullscreen()
  } catch (error) {
    console.warn('[Denoise] 切换全屏失败', error)
  }
}

function syncFullscreenState() {
  pointcloudFullscreen.value = document.fullscreenElement === stageRef.value
}

function disposePreviewGeometry() {
  denoisePreviewGeometry.value?.dispose()
  denoisePreviewGeometry.value = null
  denoiseVisiblePointCount.value = 0
}

/** 原始点云与去噪结果二选一展示，与校准页第二步的显隐语义一致。 */
function applyViewVisibility() {
  const showResult = denoiseView.value !== 'source'
  panelRef.value?.setPointcloudSourceVisible?.(!showResult)
  panelRef.value?.setPointcloudOverlayVisible?.(showResult)
}

function clearPreview(keepView = false) {
  denoisePreviewRequestId++
  panelRef.value?.clearPointcloudOverlay?.()
  disposePreviewGeometry()
  if (!keepView) denoiseView.value = 'source'
  denoiseVisibleClasses.value = [3]
  denoiseColorMode.value = 'cleaned'
  denoisePreviewLoading.value = false
  applyViewVisibility()
}

async function loadLatestDenoise() {
  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  if (!scanId || !bimId) return
  const id = ++denoiseRequestId
  denoiseLoading.value = true
  denoiseError.value = ''
  try {
    const response = await getLatestDenoise(scanId, bimId)
    if (id !== denoiseRequestId) return
    if (response.data?.version !== denoiseResult.value?.version || !response.data?.fresh) clearPreview()
    denoiseResult.value = response.data ?? null
    if (response.data?.fresh && pointcloudLoaded.value) await showDenoisePreview('cleaned')
  } catch (error) {
    if (id !== denoiseRequestId) return
    denoiseResult.value = null
    clearPreview()
    denoiseError.value = error instanceof Error ? error.message : '读取去噪结果失败'
  } finally {
    if (id === denoiseRequestId) denoiseLoading.value = false
  }
}

async function loadAlignment() {
  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  alignmentMatrix.value = null
  if (!scanId || !bimId) return
  alignmentLoading.value = true
  try {
    const response = await getBimAlignment({ modelScanFileId: scanId, modelBimFileId: bimId })
    const matrix = response.data?.modelMatrix
    alignmentMatrix.value = Array.isArray(matrix) && matrix.length === 16 ? matrix : null
  } catch {
    // 未配准是正常前置缺失，不作为错误上报给用户。
    alignmentMatrix.value = null
  } finally {
    alignmentLoading.value = false
  }
}

async function loadResources() {
  const scanId = props.pointcloudAssetId
  const token = ++resourcesToken
  resourcesResolved.value = false
  resourceError.value = ''
  pointcloudSourceUrl.value = ''
  pointcloudPreprocessResult.value = null
  pointcloudPreprocessRequired.value = false
  pointcloudPreprocessError.value = ''
  pointcloudLoaded.value = false
  denoiseResult.value = null
  clearPreview()

  if (!scanId) {
    resourcesResolved.value = true
    return
  }

  resourcesLoading.value = true
  try {
    const detail = (await getAssetDetail(scanId)).data
    if (token !== resourcesToken) return
    if (detail?.type !== 'pointcloud' || detail.status !== 'ready') {
      resourceError.value = '点云资源尚未就绪，暂时无法去噪'
      return
    }

    const [representations, preprocess] = await Promise.allSettled([
      getAssetRepresentations(scanId),
      getPointcloudPreprocess(scanId),
    ])
    if (token !== resourcesToken) return

    if (preprocess.status === 'fulfilled') pointcloudPreprocessResult.value = preprocess.value.data
    if (representations.status === 'rejected') throw representations.reason

    if (preprocess.status === 'rejected' || !preprocess.value.data) {
      pointcloudPreprocessRequired.value = true
      resourceError.value =
        preprocess.status === 'rejected' && preprocess.reason instanceof Error
          ? preprocess.reason.message
          : '分析需要先生成无台面点云'
      return
    }

    const selected = selectPointcloudRepresentation(
      detail.tilesetUrl || '',
      representations.value.data?.list || [],
      true,
      preprocess.value.data.version,
    )
    if (selected.kind !== 'table-free') {
      pointcloudPreprocessRequired.value = true
      resourceError.value = '分析需要先生成无台面点云'
      return
    }
    pointcloudSourceUrl.value = selected.url
  } catch (error) {
    if (token !== resourcesToken) return
    resourceError.value = error instanceof Error ? error.message : '读取点云资源失败'
  } finally {
    if (token === resourcesToken) {
      resourcesLoading.value = false
      resourcesResolved.value = true
    }
  }

  if (token !== resourcesToken) return
  await loadAlignment()
  if (token !== resourcesToken) return
  await loadLatestDenoise()
}

async function runPointcloudPreprocess() {
  const scanId = props.pointcloudAssetId
  if (!scanId || pointcloudPreprocessRunning.value) return
  pointcloudPreprocessRunning.value = true
  pointcloudPreprocessError.value = ''
  try {
    await computePointcloudPreprocess(scanId)
    await loadResources()
    if (!pointcloudPreprocessRequired.value) ElMessage.success('台面识别与点云预处理完成')
  } catch (error) {
    pointcloudPreprocessError.value = error instanceof Error ? error.message : '点云预处理失败'
  } finally {
    pointcloudPreprocessRunning.value = false
  }
}

async function showDenoisePreview(mode: DenoiseView) {
  if (mode === 'source') {
    denoisePreviewRequestId++
    denoisePreviewLoading.value = false
    denoiseView.value = 'source'
    applyViewVisibility()
    return
  }

  const result = denoiseResult.value
  if (!result?.fresh) return

  const existing = denoisePreviewGeometry.value
  if (existing) {
    try {
      denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(
        existing,
        mode,
        denoiseVisibleClasses.value,
      )
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : '配色切换失败')
      return
    }
    denoiseColorMode.value = mode
    denoiseView.value = mode
    panelRef.value?.refreshPointcloudOverlay?.()
    applyViewVisibility()
    return
  }

  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  if (!scanId || !bimId) return

  const id = ++denoisePreviewRequestId
  denoisePreviewLoading.value = true
  try {
    const buffer = await backendRequest<ArrayBuffer>(
      denoiseArtifactUrl(scanId, bimId, result.version, 'preview.ply'),
      { responseType: 'arraybuffer' },
    )
    if (id !== denoisePreviewRequestId || result.version !== denoiseResult.value?.version) return
    const geometry = parseDenoisePreview(buffer, mode)
    denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(
      geometry,
      mode,
      denoiseVisibleClasses.value,
    )
    disposePreviewGeometry()
    denoisePreviewGeometry.value = geometry
    denoisePreviewOrigin.value = [...result.result.previewOrigin] as [number, number, number]
    denoiseColorMode.value = mode
    denoiseView.value = mode
    panelRef.value?.setPointcloudOverlay?.({
      geometry,
      size: pointcloudSize.value,
      origin: denoisePreviewOrigin.value,
      visible: true,
    })
    applyViewVisibility()
  } catch (error) {
    if (id === denoisePreviewRequestId) {
      ElMessage.error(error instanceof Error ? error.message : '去噪预览加载失败')
    }
  } finally {
    if (id === denoisePreviewRequestId) denoisePreviewLoading.value = false
  }
}

function setDenoiseVisibleClasses(classes: number[]) {
  const geometry = denoisePreviewGeometry.value
  if (!geometry || denoiseView.value === 'source') {
    denoiseVisibleClasses.value = classes
    return
  }
  denoiseVisiblePointCount.value = applyDenoisePreviewAppearance(
    geometry,
    denoiseColorMode.value,
    classes,
  )
  denoiseVisibleClasses.value = classes
  panelRef.value?.refreshPointcloudOverlay?.()
}

async function runDenoise() {
  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  if (!canRunDenoise.value || !scanId || !bimId) return
  const id = ++denoiseRequestId
  denoiseRunning.value = true
  denoiseError.value = ''
  clearPreview()
  try {
    const response = await computeDenoise(scanId, bimId)
    if (id !== denoiseRequestId) return
    denoiseResult.value = response.data
    if (response.data.fresh) await showDenoisePreview('cleaned')
    ElMessage.success('点云分类与去噪完成')
  } catch (error) {
    if (id === denoiseRequestId) {
      denoiseError.value = error instanceof Error ? error.message : '点云去噪失败'
    }
  } finally {
    if (id === denoiseRequestId) denoiseRunning.value = false
  }
}

async function downloadDenoise() {
  const result = denoiseResult.value
  const scanId = props.pointcloudAssetId
  const bimId = props.bimAssetId
  if (!result?.fresh || !scanId || !bimId) return
  try {
    const download = (version: string) =>
      backendRequest<Blob>(denoiseArtifactUrl(scanId, bimId, version, 'cleaned.las'), {
        responseType: 'blob',
      })
    let blob: Blob
    try {
      blob = await download(result.version)
    } catch (error) {
      // 后台重算可能替换不可变产物，渲染完面板到点击下载之间需要重试一次。
      if ((error as any)?.response?.status !== 409) throw error
      const latest = (await getLatestDenoise(scanId, bimId)).data
      if (!latest?.fresh) throw error
      denoiseResult.value = latest
      blob = await download(latest.version)
    }
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'denoised-steel.las'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '下载去噪点云失败')
  }
}

function handleViewerLoaded(loaded: boolean) {
  pointcloudLoaded.value = loaded
  if (!loaded) return
  // 点云变换就绪后覆盖层才能对齐到切片内容的坐标系。
  if (denoiseView.value !== 'source' && denoiseResult.value?.fresh) {
    void showDenoisePreview(denoiseColorMode.value)
    return
  }
  panelRef.value?.refreshPointcloudOverlay?.()
}

function applyStageTheme() {
  panelRef.value?.setBackgroundTheme?.(backgroundTheme.value)
}

watch(
  () => [props.pointcloudAssetId, props.bimAssetId] as const,
  () => {
    void loadResources()
  },
)

watch(pointcloudSize, (size) => panelRef.value?.setPointSize?.(size))
watch(pointcloudEdlEnabled, (enabled) => panelRef.value?.setEdlEnabled?.(enabled))
watch(backgroundTheme, () => applyStageTheme())
watch(denoiseView, () => applyViewVisibility())

onMounted(async () => {
  document.addEventListener('fullscreenchange', syncFullscreenState)
  await loadResources()
  applyStageTheme()
  panelRef.value?.setPointSize?.(pointcloudSize.value)
  panelRef.value?.setEdlEnabled?.(pointcloudEdlEnabled.value)
  panelRef.value?.setPointcloudColorDisplay?.('rgb', 'grayscale', { min: 0, max: 1 })
})

onBeforeUnmount(() => {
  resourcesToken++
  denoiseRequestId++
  denoisePreviewRequestId++
  document.removeEventListener('fullscreenchange', syncFullscreenState)
  cleanupPreview()
})

// 卸载时释放覆盖层与预览几何，避免查看器残留几何被宿主复用。
function cleanupPreview() {
  panelRef.value?.clearPointcloudOverlay?.()
  disposePreviewGeometry()
}
</script>

<template>
  <section class="denoise-page">
    <header class="denoise-header">
      <div class="denoise-header-left">
        <el-button text :icon="ArrowLeft" :aria-label="backLabel" :title="backLabel" @click="closePage" />
        <div class="denoise-title-block">
          <strong>点云分类与去噪</strong>
          <small :title="pointcloudDisplayName">
            {{ pointcloudDisplayName || '未选择点云' }} · {{ bimDisplayName || '未选择设计模型' }}
          </small>
        </div>
      </div>

      <div class="denoise-header-right" role="group" aria-label="预览背景">
        <span class="denoise-header-label">背景</span>
        <div class="denoise-segmented">
          <button
            v-for="option in backgroundOptions"
            :key="option.value"
            type="button"
            :class="{ on: backgroundTheme === option.value }"
            :aria-pressed="backgroundTheme === option.value"
            @click="backgroundTheme = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
    </header>

    <main ref="stageRef" class="denoise-body" :class="`theme-${backgroundTheme}`">
      <div class="denoise-stage">
        <div v-if="missingSelection" class="denoise-placeholder">
          <h2>点云分类与去噪</h2>
          <p>请在宿主页面选择一条扫描点云与对应的 BIM 设计模型后再进入本页。</p>
        </div>

        <template v-else>
          <PointcloudPreviewPanel
            v-if="pointcloudSourceUrl"
            ref="panelRef"
            class="denoise-viewer-panel"
            :asset-id="pointcloudAssetId"
            :tileset-url="pointcloudSourceUrl"
            :show-edl-control="false"
            minimal
            @loaded-change="handleViewerLoaded"
          />

          <div v-if="!pointcloudSourceUrl" class="denoise-placeholder">
            <h2>{{ resourcesLoading ? '正在读取点云资源' : '点云暂不可预览' }}</h2>
            <p>{{ resourceError || '正在解析无台面切片…' }}</p>
            <el-button
              v-if="pointcloudPreprocessRequired"
              type="primary"
              :loading="pointcloudPreprocessRunning"
              @click="runPointcloudPreprocess"
            >
              补充预处理并生成无台面切片
            </el-button>
            <el-alert
              v-if="pointcloudPreprocessError"
              class="denoise-placeholder-alert"
              :title="pointcloudPreprocessError"
              type="warning"
              :closable="false"
              show-icon
            />
          </div>

          <div class="denoise-viewport-toolbar">
            <button type="button" aria-label="重置视角" title="重置视角" @click="resetView">
              <el-icon><Aim /></el-icon>
            </button>
            <button
              type="button"
              :class="{ 'is-active': pointcloudFullscreen }"
              :aria-label="pointcloudFullscreen ? '退出全屏' : '进入全屏'"
              :title="pointcloudFullscreen ? '退出全屏' : '进入全屏'"
              @click="toggleFullscreen"
            >
              <el-icon><FullScreen /></el-icon>
            </button>
            <div class="denoise-segmented denoise-toolbar-toggle">
              <button
                type="button"
                :class="{ on: pointcloudEdlEnabled }"
                :aria-pressed="pointcloudEdlEnabled"
                @click="pointcloudEdlEnabled = !pointcloudEdlEnabled"
              >
                显示增强
              </button>
            </div>
            <label class="denoise-size-control" title="点大小">
              <span>点大小</span>
              <input
                v-model.number="pointcloudSize"
                aria-label="点大小"
                type="range"
                min="1"
                max="5"
                step="0.1"
              />
              <output>{{ pointcloudSize.toFixed(1) }}</output>
            </label>
          </div>

          <div class="denoise-viewer-status" role="status">
            <i :class="{ loading: !pointcloudLoaded }" aria-hidden="true"></i>
            {{ pointcloudLoaded ? '点云已加载' : '正在加载点云' }}
            <span v-if="denoiseView !== 'source'">去噪结果 · {{ denoiseColorMode === 'cleaned' ? '按实例' : '按类别' }}</span>
          </div>
        </template>
      </div>

      <aside class="denoise-side-panel" aria-label="去噪设置">
        <el-alert
          v-if="runBlockedReason"
          class="denoise-side-alert"
          :title="runBlockedReason"
          type="warning"
          :closable="false"
          show-icon
        />
        <DenoisePanel
          :result="denoiseResult"
          :running="denoiseRunning"
          :loading="denoiseLoading || resourcesLoading || alignmentLoading"
          :error="denoiseError"
          :can-run="canRunDenoise"
          :can-inspect="canInspect"
          :view="denoiseView"
          :color-mode="denoiseColorMode"
          :preview-loading="denoisePreviewLoading"
          :visible-classes="denoiseVisibleClasses"
          :visible-point-count="denoiseVisiblePointCount"
          @run="runDenoise"
          @preview="showDenoisePreview"
          @visibility="setDenoiseVisibleClasses"
          @download="downloadDenoise"
        />
      </aside>
    </main>
  </section>
</template>

<style scoped>
.denoise-page {
  --viewer-stage: #0c1224;
  --viewer-chrome: rgb(12 18 36 / 88%);
  --viewer-ink: #e8ecf8;
  --viewer-muted: #9aa8c7;
  --viewer-accent: #9ec1ff;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--viewer-stage);
}

.denoise-header {
  flex: 0 0 auto;
  height: 64px;
  padding: 8px 20px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #cfd7e8;
  background: #e6ebf5;
}

.denoise-header-left {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.denoise-title-block {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.denoise-title-block strong,
.denoise-title-block small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.denoise-title-block strong {
  color: #1a1d24;
  font-size: 14px;
  line-height: 19px;
}

.denoise-title-block small {
  margin-top: 2px;
  color: #6b7280;
  font-size: 12px;
  line-height: 18px;
}

.denoise-header-right {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.denoise-header-label {
  color: #6b7280;
  font-size: 12px;
}

.denoise-segmented {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  border-radius: 8px;
  background: rgb(255 255 255 / 55%);
}

.denoise-segmented button {
  padding: 6px 12px;
  border: 0;
  border-radius: 6px;
  color: #3d4450;
  background: transparent;
  font-size: 12px;
  line-height: 20px;
  cursor: pointer;
}

.denoise-segmented button.on {
  color: #4e66cc;
  background: #fff;
  box-shadow: 0 0 0 1px #cfd7e8;
  font-weight: 600;
}

.denoise-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  background: var(--viewer-stage);
}

.denoise-stage {
  position: relative;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  background: var(--viewer-stage);
}

.denoise-stage:fullscreen {
  width: 100vw;
  height: 100vh;
}

.denoise-viewer-panel {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.denoise-stage :deep(.unified-viewer-3d),
.denoise-stage :deep(.preview-panel),
.denoise-stage :deep(.preview-panel.is-minimal) {
  min-height: 100%;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  background: var(--viewer-stage);
}

.denoise-body.theme-deep .denoise-stage,
.denoise-body.theme-deep :deep(.unified-viewer-3d) {
  background: #0c1224;
}

.denoise-body.theme-black .denoise-stage,
.denoise-body.theme-black :deep(.unified-viewer-3d) {
  background: #000;
}

.denoise-body.theme-light .denoise-stage,
.denoise-body.theme-light :deep(.unified-viewer-3d) {
  background: #e8eef6;
}

.denoise-body.theme-gradient .denoise-stage,
.denoise-body.theme-gradient :deep(.unified-viewer-3d) {
  background: #10213b;
}

.denoise-placeholder {
  position: absolute;
  inset: 0;
  display: grid;
  place-content: center;
  gap: 10px;
  padding: 24px;
  text-align: center;
  color: var(--viewer-ink);
}

.denoise-placeholder h2,
.denoise-placeholder p {
  margin: 0;
}

.denoise-placeholder p {
  color: var(--viewer-muted);
  font-size: 13px;
}

.denoise-placeholder-alert {
  max-width: 420px;
  text-align: left;
}

.denoise-viewport-toolbar {
  position: absolute;
  z-index: 30;
  top: 16px;
  left: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.denoise-viewport-toolbar > button {
  width: 36px;
  height: 36px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 6px;
  color: var(--viewer-ink);
  background: var(--viewer-chrome);
  cursor: pointer;
}

.denoise-viewport-toolbar > button:hover,
.denoise-viewport-toolbar > button.is-active {
  border-color: rgb(115 162 243 / 55%);
  color: var(--viewer-accent);
  background: rgb(24 42 72 / 88%);
}

.denoise-toolbar-toggle {
  background: var(--viewer-chrome);
  border: 1px solid rgb(255 255 255 / 14%);
}

.denoise-toolbar-toggle button {
  color: rgb(255 255 255 / 72%);
  padding: 5px 10px;
}

.denoise-toolbar-toggle button.on {
  color: var(--viewer-accent);
  background: rgb(255 255 255 / 14%);
  box-shadow: none;
}

.denoise-size-control {
  height: 36px;
  padding: 3px 10px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 6px;
  color: rgb(255 255 255 / 72%);
  background: var(--viewer-chrome);
  font-size: 12px;
}

.denoise-size-control input {
  width: 96px;
  height: 4px;
  accent-color: var(--viewer-accent);
  cursor: pointer;
}

.denoise-size-control output {
  min-width: 24px;
  color: var(--viewer-muted);
  font-variant-numeric: tabular-nums;
}

.denoise-viewer-status {
  position: absolute;
  z-index: 25;
  left: 18px;
  bottom: 16px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--viewer-muted);
  font-size: 11px;
  pointer-events: none;
}

.denoise-viewer-status > i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--brand-nano-cyan, #2dd4bf);
}

.denoise-viewer-status > i.loading {
  background: var(--brand-opto-trace, #f59e0b);
}

.denoise-viewer-status span {
  padding: 2px 6px;
  border-radius: 4px;
  color: #a9bee8;
  background: rgb(34 53 86 / 80%);
}

.denoise-side-panel {
  flex: 0 0 372px;
  width: 372px;
  min-width: 0;
  overflow-y: auto;
  border-left: 1px solid #dbe2ee;
  background: var(--bg-page, #f8fafc);
}

.denoise-side-alert {
  margin: 12px 12px 0;
}

@media (max-width: 1100px) {
  .denoise-side-panel {
    flex-basis: 320px;
    width: 320px;
  }
}
</style>
