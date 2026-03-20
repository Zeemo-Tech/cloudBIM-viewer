<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowLeft, RefreshRight } from '@element-plus/icons-vue'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'

const props = defineProps<{
  previewType: 'bim' | 'pointcloud'
  assetId: number | null
  displayName?: string
}>()

const bimPanelRef = ref<any>(null)
const pointcloudPanelRef = ref<any>(null)

const pageTitle = computed(() => {
  return props.previewType === 'bim' ? 'BIM 全屏预览' : '点云全屏预览'
})

const emptyText = computed(() => {
  return props.previewType === 'bim'
    ? '请从上传页重新点击“预览”打开 BIM 全屏页。'
    : '请从上传页重新点击“预览”打开点云全屏页。'
})

function closePage() {
  if (window.opener) {
    window.close()
    return
  }

  window.history.pushState({}, '', window.location.origin)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function resetView() {
  if (props.previewType === 'bim') {
    bimPanelRef.value?.resetView?.()
    return
  }

  pointcloudPanelRef.value?.resetPointcloudView?.()
}
</script>

<template>
  <section class="asset-preview-page">
    <div class="floating-controls">
      <button class="floating-btn" type="button" @click="closePage">
        <el-icon><ArrowLeft /></el-icon>
        <span>关闭</span>
      </button>

      <button class="floating-btn" type="button" @click="resetView">
        <el-icon><RefreshRight /></el-icon>
        <span>重置视角</span>
      </button>
    </div>

    <div v-if="!assetId" class="empty-state">
      <h2>{{ pageTitle }}</h2>
      <p>{{ emptyText }}</p>
    </div>

    <div v-else class="viewer-shell">
      <BimPreviewPanel
        v-if="previewType === 'bim'"
        ref="bimPanelRef"
        class="viewer-panel"
        :asset-id="assetId"
        :display-name="displayName"
      />

      <PointcloudPreviewPanel
        v-else
        ref="pointcloudPanelRef"
        class="viewer-panel"
        :asset-id="assetId"
      />
    </div>
  </section>
</template>

<style scoped>
.asset-preview-page {
  min-height: 100vh;
  padding: 18px;
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 22%),
    radial-gradient(circle at 15% 20%, rgba(59, 130, 246, 0.12), transparent 24%),
    linear-gradient(180deg, #06111f 0%, #09172a 42%, #070d18 100%);
  display: flex;
  flex-direction: column;
}

.floating-controls {
  position: fixed;
  top: 18px;
  left: 18px;
  z-index: 20;
  display: flex;
  gap: 10px;
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
  cursor: pointer;
  backdrop-filter: blur(18px);
  background:
    linear-gradient(180deg, rgba(10, 20, 38, 0.82), rgba(7, 14, 28, 0.64));
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.18),
    0 0 22px rgba(56, 189, 248, 0.12),
    0 16px 32px rgba(2, 6, 23, 0.34);
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.floating-btn:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 0 0 1px rgba(103, 232, 249, 0.26),
    0 0 28px rgba(34, 211, 238, 0.18),
    0 18px 36px rgba(2, 6, 23, 0.4);
}

.viewer-shell,
.empty-state {
  flex: 1;
  margin-top: 54px;
}

.viewer-shell {
  min-height: calc(100vh - 90px);
}

.viewer-panel {
  height: 100%;
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
</style>
