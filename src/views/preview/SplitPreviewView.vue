<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowLeft, RefreshRight, 
Connection } from '@element-plus/icons-vue'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'

type CameraPose = {
  camera: any
  target: any
}

const props = defineProps<{
  bimAssetId: number | null
  pointcloudAssetId: number | null
  bimDisplayName?: string
}>()

const isReady = computed(() => {
  return !!props.bimAssetId && !!props.pointcloudAssetId
})

const syncActive = ref(true)
const bimLoaded = ref(false)
const pointcloudLoaded = ref(false)
const pointcloudPanelRef = ref<any>(null)
const bimPanelRef = ref<any>(null)
const syncLock = ref<'bim' | 'pointcloud' | null>(null)

const canSync = computed(() => {
  return isReady.value && bimLoaded.value && pointcloudLoaded.value
})

function closePage() {
  if (window.opener) {
    window.close()
    return
  }

  window.history.pushState({}, '', window.location.origin)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function releaseSyncLock() {
  requestAnimationFrame(() => {
    syncLock.value = null
  })
}

function syncPointcloudFromBim() {
  if (!syncActive.value || !canSync.value || syncLock.value) {
    return
  }

  const pose = bimPanelRef.value?.getCameraPose?.() as CameraPose | null
  if (!pose) {
    return
  }

  syncLock.value = 'bim'
  pointcloudPanelRef.value?.syncFromExternalPose?.(pose)
  releaseSyncLock()
}

function syncBimFromPointcloud() {
  if (!syncActive.value || !canSync.value || syncLock.value) {
    return
  }

  const pose = pointcloudPanelRef.value?.getCameraPose?.() as CameraPose | null
  if (!pose) {
    return
  }

  syncLock.value = 'pointcloud'
  bimPanelRef.value?.setCameraPose?.(pose)
  releaseSyncLock()
}

function handleSync() {
  syncActive.value = !syncActive.value
  if (syncActive.value) {
    syncPointcloudFromBim()
  }
}

function handleResetView() {
  bimPanelRef.value?.resetView?.()
  pointcloudPanelRef.value?.resetPointcloudView?.()

  if (syncActive.value) {
    requestAnimationFrame(() => {
      syncPointcloudFromBim()
    })
  }
}

function handleBimLoadedChange(value: boolean) {
  bimLoaded.value = value
  if (value) {
    requestAnimationFrame(() => {
      syncPointcloudFromBim()
    })
  }
}

function handlePointcloudLoadedChange(value: boolean) {
  pointcloudLoaded.value = value
  if (value) {
    requestAnimationFrame(() => {
      syncPointcloudFromBim()
    })
  }
}

function handleBimCameraChange() {
  if (syncLock.value === 'pointcloud') {
    return
  }

  syncPointcloudFromBim()
}

function handlePointcloudCameraChange() {
  if (syncLock.value === 'bim') {
    return
  }

  syncBimFromPointcloud()
}
</script>

<template>
  <section class="split-preview-page">
    <div class="floating-controls">
      <button class="floating-btn" type="button" @click="closePage">
        <el-icon><ArrowLeft /></el-icon>
        <span>返回</span>
      </button>

      <button class="floating-btn" type="button" @click="handleResetView">
        <el-icon><RefreshRight /></el-icon>
        <span>重置</span>
      </button>

      <button
        class="floating-btn is-sync"
        :class="{ 'is-active': syncActive }"
        type="button"
        @click="handleSync"
      >
        <el-icon><Connection /></el-icon>
        <span>同步</span>
      </button>
    </div>

    <div v-if="!isReady" class="empty-state">
      <h2>缺少预览参数</h2>
      <p>请从上传页重新点击“二分屏预览”打开当前页面。</p>
    </div>

    <div v-else class="viewer-shell">
       <div class="viewer-slot">
        <BimPreviewPanel
          ref="bimPanelRef"
          :asset-id="bimAssetId"
          :display-name="bimDisplayName"
          minimal
          @loaded-change="handleBimLoadedChange"
          @camera-change="handleBimCameraChange"
        />
      </div>
      <div class="viewer-slot">
        <PointcloudPreviewPanel
          ref="pointcloudPanelRef"
          :asset-id="pointcloudAssetId"
          minimal
          @loaded-change="handlePointcloudLoadedChange"
          @camera-change="handlePointcloudCameraChange"
        />
      </div>

     
    </div>
  </section>
</template>

<style scoped>
.split-preview-page {
  min-height: 100vh;
  padding: 18px;
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.14), transparent 22%),
    radial-gradient(circle at 15% 20%, rgba(59, 130, 246, 0.12), transparent 24%),
    linear-gradient(180deg, #06111f 0%, #09172a 42%, #070d18 100%);
  display: flex;
  flex-direction: column;
  gap: 16px;
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
    box-shadow 0.2s ease,
    color 0.2s ease;
}

.floating-btn:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 0 0 1px rgba(103, 232, 249, 0.26),
    0 0 28px rgba(34, 211, 238, 0.18),
    0 18px 36px rgba(2, 6, 23, 0.4);
}

.floating-btn.is-active {
  color: #ecfeff;
  box-shadow:
    inset 0 0 0 1px rgba(34, 211, 238, 0.36),
    0 0 34px rgba(34, 211, 238, 0.28),
    0 20px 40px rgba(2, 6, 23, 0.44);
}

.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  border-radius: 28px;
  text-align: center;
  background: rgba(8, 15, 30, 0.52);
  color: #cbd5e1;
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.12),
    0 18px 48px rgba(2, 6, 23, 0.34);
}

.empty-state h2 {
  margin: 0 0 10px;
  color: #f8fafc;
}

.empty-state p {
  margin: 0;
  color: #94a3b8;
}

.viewer-shell {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
  min-height: calc(100vh - 36px);
}

.viewer-slot {
  min-width: 0;
  min-height: 0;
}

@media (max-width: 1080px) {
  .split-preview-page {
    padding-top: 72px;
  }

  .viewer-shell {
    grid-template-columns: 1fr;
    min-height: auto;
  }
}
</style>
