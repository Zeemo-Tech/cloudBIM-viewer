<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ArrowLeft, ArrowRightBold, DArrowRight } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'

type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'

const DEFAULT_POINT_COLOR = '#86898D'

const props = defineProps<{
  previewType: 'bim' | 'pointcloud'
  assetId: number | null
  displayName?: string
}>()

const router = useRouter()
const bimPanelRef = ref<any>(null)
const pointcloudPanelRef = ref<any>(null)

const backgroundTheme = ref<PreviewBackgroundTheme>('black')
const sidebarCollapsed = ref(false)

const bimControls = reactive({
  showAxes: true,
  showGrid: true,
  wireframe: false,
  sectionEnabled: false,
  sectionRatio: 52,
})

const pointcloudControls = reactive({
  showAxes: false,
  showGrid: true,
  colorMode: 'original' as 'original' | 'custom',
  pointColor: DEFAULT_POINT_COLOR,
})

const pointColorPresets = [
  { label: '白色', value: '#f8fafc' },
  { label: '青色', value: '#67e8f9' },
  { label: '橙色', value: '#fb923c' },
  { label: '绿色', value: '#4ade80' },
  { label: '灰色', value: '#86898D' },
]

const backgroundOptions: Array<{ label: string; value: PreviewBackgroundTheme }> = [
  { label: '蓝色', value: 'gradient' },
  { label: '深色', value: 'deep' },
  { label: '浅色', value: 'light' },
  { label: '纯黑', value: 'black' },
]

const pageTitle = computed(() => {
  return props.previewType === 'bim' ? 'BIM 全屏预览' : '点云全屏预览'
})

const emptyText = computed(() => {
  return props.previewType === 'bim'
    ? '请从上传页重新点击“预览”打开 BIM 全屏页。'
    : '请从上传页重新点击“预览”打开点云全屏页。'
})

const currentPanelRef = computed(() => {
  return props.previewType === 'bim' ? bimPanelRef.value : pointcloudPanelRef.value
})

function closePage() {
  if (window.opener) {
    window.close()
    return
  }

  void router.push('/upload')
}

function resetView() {
  if (props.previewType === 'bim') {
    bimPanelRef.value?.resetView?.()
    return
  }

  pointcloudPanelRef.value?.resetPointcloudView?.()
}

function applyPanelSettings() {
  const panel = currentPanelRef.value
  if (!panel) {
    return
  }

  panel.setBackgroundTheme?.(backgroundTheme.value)

  if (props.previewType === 'bim') {
    panel.setShowAxes?.(bimControls.showAxes)
    panel.setShowGrid?.(bimControls.showGrid)
    panel.setWireframe?.(bimControls.wireframe)
    panel.setSectionState?.(bimControls.sectionEnabled, bimControls.sectionRatio)
    return
  }

  panel.setShowAxes?.(pointcloudControls.showAxes)
  panel.setShowGrid?.(pointcloudControls.showGrid)
  panel.setPointColor?.(
    pointcloudControls.colorMode === 'custom' ? pointcloudControls.pointColor : null,
  )
}

function applyPointColorPreset(color: string) {
  pointcloudControls.colorMode = 'custom'
  pointcloudControls.pointColor = color
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

watch(
  () => [
    props.previewType,
    backgroundTheme.value,
    bimControls.showAxes,
    bimControls.showGrid,
    bimControls.wireframe,
    bimControls.sectionEnabled,
    bimControls.sectionRatio,
    pointcloudControls.showAxes,
    pointcloudControls.showGrid,
    pointcloudControls.colorMode,
    pointcloudControls.pointColor,
    bimPanelRef.value,
    pointcloudPanelRef.value,
  ] as const,
  () => {
    applyPanelSettings()
  },
  { immediate: true },
)

onMounted(() => {
  applyPanelSettings()
})
</script>

<template>
  <section class="asset-preview-page" :class="`theme-${backgroundTheme}`">
    <div class="floating-controls">
      <button class="floating-btn" type="button" @click="closePage">
        <el-icon><ArrowLeft /></el-icon>
        <span>关闭</span>
      </button>
    </div>

    <div v-if="!assetId" class="empty-state">
      <h2>{{ pageTitle }}</h2>
      <p>{{ emptyText }}</p>
    </div>

    <div v-else class="layout-shell" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
      <div class="viewer-region" :class="`theme-${backgroundTheme}`">
        <BimPreviewPanel
          v-if="previewType === 'bim'"
          ref="bimPanelRef"
          class="viewer-panel"
          :asset-id="assetId"
          :display-name="displayName"
          minimal
        />

        <PointcloudPreviewPanel
          v-else
          ref="pointcloudPanelRef"
          class="viewer-panel"
          :asset-id="assetId"
          minimal
        />
      </div>

      <aside class="sidebar" :class="{ 'is-collapsed': sidebarCollapsed }">
        <el-scrollbar class="sidebar-scrollbar">
          <el-space direction="vertical" fill :size="14" class="sidebar-stack">
            <section class="sidebar-card sidebar-toolbar">
              <div class="card-head">
                <el-tooltip
                  :content="sidebarCollapsed ? '展开工具栏' : '收起工具栏'"
                  placement="left"
                >
                  <el-button
                    class="icon-btn"
                    circle
                    type="default"
                    @click="toggleSidebar"
                  >
                    <el-icon>
                      <ArrowRightBold v-if="sidebarCollapsed" />
                      <DArrowRight v-else />
                    </el-icon>
                  </el-button>
                </el-tooltip>
                <button
                  v-if="!sidebarCollapsed"
                  class="ghost-btn"
                  type="button"
                  @click="resetView"
                >
                  重置
                </button>
              </div>
            </section>

            <div v-show="!sidebarCollapsed" class="sidebar-sections">
              <section class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Environment</p>
                  <h3>背景切换</h3>
                  <p class="section-desc">切换观察环境，快速增强浅色模型和点云轮廓对比。</p>
                </div>
                <div class="option-row">
                  <button
                    v-for="option in backgroundOptions"
                    :key="option.value"
                    class="chip-btn theme-chip"
                    :class="{ 'is-active': backgroundTheme === option.value }"
                    type="button"
                    @click="backgroundTheme = option.value"
                  >
                    <span class="theme-swatch" :class="`theme-swatch-${option.value}`"></span>
                    <span>{{ option.label }}</span>
                  </button>
                </div>
              </section>

              <section class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Scene</p>
                  <h3>辅助显示</h3>
                  <p class="section-desc">保持方向感和尺度感，适合定位模型朝向与地平面。</p>
                </div>
                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>坐标轴</strong>
                    <small>显示更大的 XYZ 方向参考，便于判断朝向。</small>
                  </span>
                  <span class="switch">
                    <input
                      v-if="previewType === 'bim'"
                      v-model="bimControls.showAxes"
                      type="checkbox"
                    />
                    <input
                      v-else
                      v-model="pointcloudControls.showAxes"
                      type="checkbox"
                    />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>网格</strong>
                    <small>铺满视窗的参考地面，便于观察高度和投影关系。</small>
                  </span>
                  <span class="switch">
                    <input
                      v-if="previewType === 'bim'"
                      v-model="bimControls.showGrid"
                      type="checkbox"
                    />
                    <input
                      v-else
                      v-model="pointcloudControls.showGrid"
                      type="checkbox"
                    />
                    <span class="switch-track"></span>
                  </span>
                </label>
              </section>

              <section v-if="previewType === 'bim'" class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Model</p>
                  <h3>BIM 显示</h3>
                  <p class="section-desc">面向结构查看的显示控制，适合查看边界和剖切关系。</p>
                </div>
                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>线框模式</strong>
                    <small>突出结构边界和构件轮廓，便于快速检查层次。</small>
                  </span>
                  <span class="switch">
                    <input v-model="bimControls.wireframe" type="checkbox" />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <label class="toggle-row">
                  <span class="toggle-copy">
                    <strong>剖切启用</strong>
                    <small>按高度裁切模型，快速查看内部构造与楼层关系。</small>
                  </span>
                  <span class="switch">
                    <input v-model="bimControls.sectionEnabled" type="checkbox" />
                    <span class="switch-track"></span>
                  </span>
                </label>

                <div class="range-row" :class="{ 'is-disabled': !bimControls.sectionEnabled }">
                  <div class="range-head">
                    <span>剖切高度</span>
                    <strong>{{ bimControls.sectionRatio }}%</strong>
                  </div>
                  <input
                    v-model.number="bimControls.sectionRatio"
                    :disabled="!bimControls.sectionEnabled"
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                  />
                </div>
              </section>

              <section v-else class="sidebar-card">
                <div class="card-heading">
                  <p class="section-kicker">Point Cloud</p>
                  <h3>点云显示</h3>
                  <p class="section-desc">默认保留后端原始颜色，需要时可切换为统一覆盖色。</p>
                </div>

                <div class="option-row">
                  <button
                    class="chip-btn"
                    :class="{ 'is-active': pointcloudControls.colorMode === 'original' }"
                    type="button"
                    @click="pointcloudControls.colorMode = 'original'"
                  >
                    原始颜色
                  </button>
                  <button
                    class="chip-btn"
                    :class="{ 'is-active': pointcloudControls.colorMode === 'custom' }"
                    type="button"
                    @click="pointcloudControls.colorMode = 'custom'"
                  >
                    自定义颜色
                  </button>
                </div>

                <div class="color-block" :class="{ 'is-disabled': pointcloudControls.colorMode !== 'custom' }">
                  <div class="range-head">
                    <span>覆盖颜色</span>
                    <strong>{{ pointcloudControls.pointColor.toUpperCase() }}</strong>
                  </div>
                  <div class="color-row">
                    <input
                      v-model="pointcloudControls.pointColor"
                      class="color-input"
                      type="color"
                      :disabled="pointcloudControls.colorMode !== 'custom'"
                    />
                  </div>
                  <div class="option-row">
                    <button
                      v-for="preset in pointColorPresets"
                      :key="preset.value"
                      class="chip-btn color-chip"
                      :class="{ 'is-active': pointcloudControls.pointColor === preset.value && pointcloudControls.colorMode === 'custom' }"
                      type="button"
                      @click="applyPointColorPreset(preset.value)"
                    >
                      <span class="preset-swatch" :style="{ background: preset.value }"></span>
                      <span>{{ preset.label }}</span>
                    </button>
                  </div>
                </div>
              </section>
            </div>
          </el-space>
        </el-scrollbar>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.asset-preview-page {
  min-height: 100vh;
  padding: 18px;
  display: flex;
  flex-direction: column;
}

.asset-preview-page.theme-gradient {
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 22%),
    radial-gradient(circle at 15% 20%, rgba(59, 130, 246, 0.12), transparent 24%),
    linear-gradient(180deg, #06111f 0%, #09172a 42%, #070d18 100%);
}

.asset-preview-page.theme-deep {
  background: linear-gradient(180deg, #081221 0%, #0d1b2f 52%, #09111d 100%);
}

.asset-preview-page.theme-light {
  background: linear-gradient(180deg, #eef4fb 0%, #dde7f2 100%);
}

.asset-preview-page.theme-black {
  background: #000;
}

.floating-controls {
  position: fixed;
  top: 18px;
  left: 18px;
  z-index: 20;
  display: flex;
  gap: 10px;
}

.floating-btn,
.ghost-btn,
.chip-btn {

  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease,
    background-color 0.2s ease;
}

.floating-btn {
  height: 42px;
  padding: 0 16px;
  border: 0;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: #d8f3ff;
  font-size: 13px;
  letter-spacing: 0.04em;
  backdrop-filter: blur(18px);
  background:
    linear-gradient(180deg, rgba(10, 20, 38, 0.82), rgba(7, 14, 28, 0.64));
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.18),
    0 0 22px rgba(56, 189, 248, 0.12),
    0 16px 32px rgba(2, 6, 23, 0.34);
}

.floating-btn:hover,
.ghost-btn:hover,
.chip-btn:hover {
  transform: translateY(-1px);
}

.layout-shell,
.empty-state {
  flex: 1;
  margin-top: 54px;
}

.layout-shell {
  min-height: calc(100vh - 90px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 368px;
  gap: 20px;
  transition: grid-template-columns 0.24s ease;
}

.layout-shell.is-sidebar-collapsed {
  grid-template-columns: minmax(0, 1fr) 82px;
}

.viewer-region {
  min-height: calc(100vh - 90px);
  border-radius: 28px;
  overflow: hidden;
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.12),
    0 22px 60px rgba(2, 6, 23, 0.34);
}

.viewer-region.theme-gradient {
  background:
    radial-gradient(circle at 15% 15%, rgba(34, 211, 238, 0.18), transparent 22%),
    linear-gradient(180deg, #081425 0%, #11213a 100%);
}

.viewer-region.theme-deep {
  background: linear-gradient(180deg, #07111f 0%, #0c1728 100%);
}

.viewer-region.theme-light {
  background: linear-gradient(180deg, #f8fbff 0%, #e8eef6 100%);
}

.viewer-region.theme-black {
  background: #000;
}

.viewer-region.theme-gradient :deep(.preview-panel),
.viewer-region.theme-deep :deep(.preview-panel),
.viewer-region.theme-light :deep(.preview-panel),
.viewer-region.theme-black :deep(.preview-panel) {
  min-height: calc(100vh - 90px);
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.viewer-region.theme-gradient :deep(.preview-panel),
.viewer-region.theme-gradient :deep(.preview-panel.is-minimal) {
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 26%),
    linear-gradient(180deg, #071323 0%, #12233d 100%);
}

.viewer-region.theme-deep :deep(.preview-panel),
.viewer-region.theme-deep :deep(.preview-panel.is-minimal) {
  background: linear-gradient(180deg, #07111f 0%, #0c1728 100%);
}

.viewer-region.theme-light :deep(.preview-panel),
.viewer-region.theme-light :deep(.preview-panel.is-minimal) {
  background: linear-gradient(180deg, #f8fbff 0%, #e8eef6 100%);
}

.viewer-region.theme-black :deep(.preview-panel),
.viewer-region.theme-black :deep(.preview-panel.is-minimal) {
  background: #000;
}

.viewer-region.theme-light :deep(.panel-chip),
.viewer-region.theme-light :deep(.panel-title),
.viewer-region.theme-light :deep(.panel-status) {
  color: #0f172a;
}

.viewer-panel {
  height: 100%;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 90px);
  overflow: auto;
  padding-right: 4px;
}

.sidebar.is-collapsed {
  gap: 0;
}

.sidebar-scrollbar {
  height: 100%;
}

.sidebar-stack {
  width: 100%;
}

.sidebar-stack :deep(.el-space__item) {
  width: 100%;
}

.sidebar.is-collapsed .sidebar-scrollbar :deep(.el-scrollbar__view) {
  display: flex;
  justify-content: center;
}

.sidebar-sections {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sidebar-card {
  position: relative;
  padding: 18px 18px 20px;
  border-radius: 24px;
  color: #e2e8f0;
  background:
    linear-gradient(180deg, rgba(10, 18, 32, 0.96), rgba(6, 12, 22, 0.88));
  backdrop-filter: blur(22px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),
    inset 0 0 0 1px rgba(148, 163, 184, 0.14),
    0 20px 48px rgba(2, 6, 23, 0.28);
}

.asset-preview-page.theme-light .sidebar-card {
  color: #0f172a;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(244, 248, 252, 0.9));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    inset 0 0 0 1px rgba(148, 163, 184, 0.16),
    0 24px 54px rgba(15, 23, 42, 0.12);
}

.sidebar-toolbar {
  padding: 10px 12px;
}

.sidebar-toolbar::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background:
    radial-gradient(circle at top right, rgba(34, 211, 238, 0.18), transparent 34%),
    linear-gradient(135deg, rgba(14, 165, 233, 0.08), transparent 58%);
}

.card-heading,
.toggle-row,
.range-row,
.color-block {
  position: relative;
  z-index: 1;
}

.card-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  min-height: 0;
}

.sidebar.is-collapsed .card-head {
  justify-content: center;
}

.card-head h2,
.sidebar-card h3 {
  margin: 0;
}

.card-eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #67e8f9;
}
.section-kicker,
.range-head span {
  font-size: 12px;
  color: #94a3b8;
}

.asset-preview-page.theme-light .section-kicker,
.asset-preview-page.theme-light .range-head span {
  color: #64748b;
}

.range-head strong {
  font-size: 14px;
}

.card-heading {
  margin-bottom: 14px;
}

.section-kicker {
  margin: 0 0 8px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.section-desc {
  margin: 8px 0 0;
  line-height: 1.6;
  font-size: 13px;
  color: #8ea0b7;
}

.asset-preview-page.theme-light .section-desc {
  color: #5f7085;
}

.ghost-btn {
  width: 30%;
  height: 34px;
  padding: 0 14px;
  border: 1px solid rgba(103, 232, 249, 0.22);
  border-radius: 999px;
  color: inherit;
  background:
    linear-gradient(180deg, rgba(34, 211, 238, 0.18), rgba(56, 189, 248, 0.08));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 10px 24px rgba(8, 47, 73, 0.18);
  font-size: 12px;
  letter-spacing: 0.04em;
}

.asset-preview-page.theme-light .ghost-btn {
  background: rgba(255, 255, 255, 0.88);
}

.icon-btn {
  width: 34px;
  height: 34px;
  min-height: 34px;
  padding: 0;
  border: 1px solid rgba(148, 163, 184, 0.2);
  color: #d8f3ff;
  background: rgba(15, 23, 42, 0.22);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),
    0 8px 20px rgba(15, 23, 42, 0.14);
}

.icon-btn:hover,
.icon-btn:focus-visible {
  color: #d8f3ff;
  border-color: rgba(103, 232, 249, 0.22);
  background: rgba(21, 34, 54, 0.42);
}

.asset-preview-page.theme-light .icon-btn {
  color: #0f172a;
  background: rgba(241, 245, 249, 0.96);
}

.asset-preview-page.theme-light .icon-btn:hover,
.asset-preview-page.theme-light .icon-btn:focus-visible {
  color: #0f172a;
  background: rgba(255, 255, 255, 0.98);
}

.icon-btn :deep(.el-icon) {
  font-size: 14px;
}

.option-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.chip-btn {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 16px;
  color: inherit;
  background: rgba(15, 23, 42, 0.24);
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.chip-btn.is-active {
  border-color: rgba(34, 211, 238, 0.4);
  background: rgba(34, 211, 238, 0.14);
  box-shadow: inset 0 0 0 1px rgba(34, 211, 238, 0.16);
}

.theme-chip {
  flex: 1 1 calc(50% - 4px);
  justify-content: flex-start;
}

.theme-swatch,
.preset-swatch {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  flex: 0 0 auto;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
}

.theme-swatch-gradient {
  background: linear-gradient(135deg, #0f172a 15%, #0ea5e9 100%);
}

.theme-swatch-deep {
  background: linear-gradient(135deg, #081221 0%, #163256 100%);
}

.theme-swatch-light {
  background: linear-gradient(135deg, #f8fbff 0%, #cbd5e1 100%);
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.5);
}

.theme-swatch-black {
  background: #000;
}

.toggle-row {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 14px 14px 16px;
  border-radius: 18px;
  background: rgba(15, 23, 42, 0.22);
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.1);
}

.asset-preview-page.theme-light .toggle-row {
  background: rgba(248, 250, 252, 0.82);
}

.toggle-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.toggle-copy strong {
  font-size: 14px;
  font-weight: 600;
}

.toggle-copy small {
  line-height: 1.55;
  color: #8ea0b7;
}

.asset-preview-page.theme-light .toggle-copy small {
  color: #5f7085;
}

.switch {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
}

.switch input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.switch-track {
  width: 50px;
  height: 30px;
  border-radius: 999px;
  background: rgba(51, 65, 85, 0.92);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.16),
    inset 0 10px 18px rgba(15, 23, 42, 0.18);
  transition: background-color 0.2s ease;
}

.switch-track::after {
  content: '';
  position: absolute;
  top: 4px;
  left: 4px;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: linear-gradient(180deg, #f8fafc 0%, #dbe6f2 100%);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.24);
  transition: transform 0.2s ease;
}

.switch input:checked + .switch-track {
  background: linear-gradient(135deg, #0891b2 0%, #22d3ee 100%);
}

.switch input:checked + .switch-track::after {
  transform: translateX(20px);
}

.range-row,
.color-block {
  margin-top: 14px;
  padding: 14px 16px 16px;
  border-radius: 18px;
  background: rgba(15, 23, 42, 0.22);
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.1);
}

.asset-preview-page.theme-light .range-row,
.asset-preview-page.theme-light .color-block {
  background: rgba(248, 250, 252, 0.82);
}

.range-row.is-disabled {
  opacity: 0.5;
}

.range-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.range-row input[type='range'] {
  width: 100%;
  accent-color: #22d3ee;
  cursor: pointer;
}

.color-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(8, 15, 30, 0.36);
}

.asset-preview-page.theme-light .color-row {
  background: rgba(255, 255, 255, 0.9);
}

.color-input {
  width: 56px;
  height: 36px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  border-radius: 28px;
  text-align: center;
  color: #cbd5e1;
  background: rgba(8, 15, 30, 0.52);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.12),
    0 18px 48px rgba(2, 6, 23, 0.34);
}

.empty-state h2 {
  margin: 0 0 12px;
  font-size: 1.4rem;
  color: #f8fafc;
}

.empty-state p {
  margin: 0;
  color: #94a3b8;
}

@media (max-width: 1180px) {
  .layout-shell {
    grid-template-columns: 1fr;
  }

  .layout-shell.is-sidebar-collapsed {
    grid-template-columns: 1fr;
  }

  .sidebar {
    position: static;
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .viewer-region {
    min-height: 60vh;
  }
}
</style>
