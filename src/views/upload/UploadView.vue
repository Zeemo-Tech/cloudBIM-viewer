<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { SwitchButton, View } from '@element-plus/icons-vue'
import {
  type AssetDetail,
  type AssetStatus,
  type AssetSummary,
  getAssetDetail,
  listAssets,
} from '@/api/backend-file'
import BimPreviewPanel from '@/components/preview/BimPreviewPanel.vue'
import PointcloudPreviewPanel from '@/components/preview/PointcloudPreviewPanel.vue'
import UploadDropCard from '@/components/upload/UploadDropCard.vue'
import type { AuthSession } from '@/features/auth/auth.service'
import {
  BIM_UPLOAD_CONFIG,
  POINT_CLOUD_UPLOAD_CONFIG,
} from '@/features/upload/upload.config'
import { cancelFileUpload, isAssetReady, uploadFile } from '@/features/upload/upload.service'
import { formatFileSize } from '@/features/upload/upload.utils'

type UploadTaskState = {
  uploadId: string | null
  progress: number
  chunkCurrent: number
  chunkTotal: number
  status: 'idle' | 'uploading' | 'success' | 'error'
  errorMessage: string
  result: AssetDetail | null
}

type PreviewMode = 'bim' | 'pointcloud' | 'split' | null
type UploadKind = 'bim' | 'pointcloud'

const props = defineProps<{
  session: AuthSession
}>()

defineEmits<{
  logout: []
}>()

const bimFile = ref<File | null>(null)
const pointCloudFile = ref<File | null>(null)
const previewMode = ref<PreviewMode>(null)
const activeUploadKind = ref<UploadKind | null>(null)
const cancelRequested = ref(false)
const loadingAssets = ref(false)
const refreshingState = reactive<Record<UploadKind, boolean>>({
  bim: false,
  pointcloud: false,
})
const assetCollections = reactive<Record<UploadKind, AssetSummary[]>>({
  bim: [],
  pointcloud: [],
})

const uploadTasks = reactive<Record<UploadKind, UploadTaskState>>({
  bim: createInitialTaskState(),
  pointcloud: createInitialTaskState(),
})

const canPreviewBim = computed(() => !!getPreviewTarget('bim'))
const canPreviewPointcloud = computed(() => !!getPreviewTarget('pointcloud'))
const canPreviewSplit = computed(() => canPreviewBim.value && canPreviewPointcloud.value)
const bimUploadedFiles = computed(() => assetCollections.bim)
const pointcloudUploadedFiles = computed(() => assetCollections.pointcloud)
const userName = computed(() => props.session.username)

const previewTitle = computed(() => {
  if (previewMode.value === 'bim') {
    return 'BIM 模型预览'
  }

  if (previewMode.value === 'pointcloud') {
    return '点云预览'
  }

  if (previewMode.value === 'split') {
    return '二分屏预览'
  }

  return '预览窗口'
})

function createInitialTaskState(): UploadTaskState {
  return {
    uploadId: null,
    progress: 0,
    chunkCurrent: 0,
    chunkTotal: 0,
    status: 'idle',
    errorMessage: '',
    result: null,
  }
}

function formatDateTime(timestamp?: number) {
  if (!timestamp) return '时间未记录'

  const date = new Date(timestamp * 1000)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')

  return `${year}-${month}-${day} ${hours}:${minutes}`
}

function isPreviewReady(asset: AssetDetail | AssetSummary | null) {
  return !!asset && isAssetReady(asset.status)
}

function getStatusText(status?: AssetStatus) {
  switch (status) {
    case 'uploading':
      return '文件上传中'
    case 'queued':
      return '已上传完成，等待解析'
    case 'processing':
      return '处理中，可刷新状态'
    case 'ready':
      return '文件已就绪，可预览'
    case 'failed':
      return '处理失败，请重新上传'
    case 'terminated':
      return '上传已终止'
    default:
      return '状态待更新'
  }
}

function getStatusTagType(status?: AssetStatus) {
  switch (status) {
    case 'ready':
      return 'success'
    case 'uploading':
    case 'queued':
    case 'processing':
      return 'warning'
    case 'failed':
    case 'terminated':
      return 'danger'
    default:
      return 'info'
  }
}

function buildAssetDetailFromSummary(asset: AssetSummary): AssetDetail {
  return {
    id: asset.id,
    type: asset.type,
    sourceName: asset.sourceName,
    sourceSize: asset.sourceSize,
    status: asset.status,
    errorMessage: asset.errorMessage,
    createdAt: asset.createdAt,
  }
}

function getLatestPreviewableAsset(type: UploadKind) {
  return assetCollections[type].find((asset) => asset.status === 'ready') || null
}

function getPreviewTarget(kind: UploadKind): AssetDetail | null {
  const currentResult = uploadTasks[kind].result
  if (isPreviewReady(currentResult)) {
    return currentResult
  }

  const fallbackAsset = getLatestPreviewableAsset(kind)
  return fallbackAsset ? buildAssetDetailFromSummary(fallbackAsset) : null
}

function ensurePreviewSelection(kind: UploadKind) {
  const target = getPreviewTarget(kind)
  if (!target) {
    return null
  }

  uploadTasks[kind].result = target
  if (isPreviewReady(target)) {
    uploadTasks[kind].status = 'success'
  }

  return target
}

function syncTaskResultsWithCollections() {
  ;(['bim', 'pointcloud'] as UploadKind[]).forEach((kind) => {
    const currentResult = uploadTasks[kind].result
    if (!currentResult) return

    const matchedAsset = assetCollections[kind].find((asset) => asset.id === currentResult.id)
    if (!matchedAsset) {
      resetTask(kind)
      if (previewMode.value === kind || previewMode.value === 'split') {
        previewMode.value = null
      }
      return
    }

    uploadTasks[kind].result = {
      ...currentResult,
      ...buildAssetDetailFromSummary(matchedAsset),
    }
  })
}

function resetTask(kind: UploadKind) {
  uploadTasks[kind] = createInitialTaskState()
}

watch(bimFile, () => {
  resetTask('bim')
})

watch(pointCloudFile, () => {
  resetTask('pointcloud')
})

watch([canPreviewBim, canPreviewPointcloud], ([bimReady, pointcloudReady]) => {
  if (previewMode.value === 'bim' && !bimReady) {
    previewMode.value = null
  }

  if (previewMode.value === 'pointcloud' && !pointcloudReady) {
    previewMode.value = null
  }

  if (previewMode.value === 'split' && !(bimReady && pointcloudReady)) {
    previewMode.value = null
  }
})

onMounted(() => {
  void loadAssets()
})

async function loadAssets(silent = false) {
  loadingAssets.value = true

  try {
    const [bimResponse, pointcloudResponse] = await Promise.all([
      listAssets({ page: 1, pageSize: 100, type: 'bim' }),
      listAssets({ page: 1, pageSize: 100, type: 'pointcloud' }),
    ])

    assetCollections.bim = [...(bimResponse.data.list || [])].sort(
      (a, b) => b.createdAt - a.createdAt,
    )
    assetCollections.pointcloud = [...(pointcloudResponse.data.list || [])].sort(
      (a, b) => b.createdAt - a.createdAt,
    )
    syncTaskResultsWithCollections()
  } catch (error) {
    if (!silent) {
      ElMessage({
        type: 'error',
        grouping: true,
        message: error instanceof Error ? error.message : '加载资产列表失败',
      })
    }
  } finally {
    loadingAssets.value = false
  }
}

async function refreshUploadedFile(kind: UploadKind, silent = false) {
  const currentResult = uploadTasks[kind].result || ensurePreviewSelection(kind)
  if (!currentResult) {
    if (!silent) {
      ElMessage.warning('当前暂无可刷新的文件')
    }
    return null
  }

  refreshingState[kind] = true

  try {
    const detailResponse = await getAssetDetail(currentResult.id)
    uploadTasks[kind].result = detailResponse.data

    if (detailResponse.data.status === 'failed') {
      uploadTasks[kind].status = 'error'
      uploadTasks[kind].errorMessage =
        detailResponse.data.errorMessage || '文件处理失败，请重新上传'
    }

    await loadAssets(true)

    if (!silent) {
      ElMessage.success('状态已刷新')
    }

    return uploadTasks[kind].result
  } catch (error) {
    if (!silent) {
      ElMessage({
        type: 'error',
        grouping: true,
        message: error instanceof Error ? error.message : '刷新文件状态失败',
      })
    }
    return null
  } finally {
    refreshingState[kind] = false
  }
}

function bindTaskCallbacks(kind: UploadKind) {
  return {
    onProgress(progress: number) {
      uploadTasks[kind].progress = progress
      uploadTasks[kind].status = 'uploading'
    },
    onChunkProgress(current: number, total: number) {
      uploadTasks[kind].chunkCurrent = current
      uploadTasks[kind].chunkTotal = total
    },
    onUploadIdCreated(uploadId: string) {
      uploadTasks[kind].uploadId = uploadId
    },
    onCancelCheck() {
      return cancelRequested.value && activeUploadKind.value === kind
    },
  }
}

async function handleUpload(kind: UploadKind) {
  const file = kind === 'bim' ? bimFile.value : pointCloudFile.value

  if (!file) {
    ElMessage.warning(kind === 'bim' ? '请先选择 BIM 文件' : '请先选择点云 LAS 文件')
    return
  }

  if (activeUploadKind.value) {
    ElMessage.info('当前已有上传任务在进行，请稍后再试')
    return
  }

  cancelRequested.value = false
  activeUploadKind.value = kind
  uploadTasks[kind].status = 'uploading'
  uploadTasks[kind].errorMessage = ''
  uploadTasks[kind].progress = 0

  try {
    const result = await uploadFile({
      type: kind === 'bim' ? 'bim' : 'pointcloud',
      file,
      resumeFromState: true,
      ...bindTaskCallbacks(kind),
    })

    uploadTasks[kind].result = result
    uploadTasks[kind].status = 'success'
    uploadTasks[kind].progress = 100

    ElMessage.success(kind === 'bim' ? 'BIM 文件上传并解析完成' : '点云文件上传并解析完成')
    await loadAssets(true)
  } catch (error) {
    uploadTasks[kind].status = 'error'
    uploadTasks[kind].errorMessage =
      error instanceof Error ? error.message : '上传失败，请稍后重试'

    ElMessage({
      type: 'error',
      grouping: true,
      message: uploadTasks[kind].errorMessage,
    })
  } finally {
    activeUploadKind.value = null
    cancelRequested.value = false
  }
}

async function handleCancelUpload(kind: UploadKind) {
  if (activeUploadKind.value !== kind) {
    return
  }

  const uploadId = uploadTasks[kind].uploadId
  if (!uploadId) {
    return
  }

  cancelRequested.value = true

  try {
    await cancelFileUpload(uploadId)
    ElMessage.info('已停止当前上传任务')
  } catch (error) {
    ElMessage({
      type: 'error',
      grouping: true,
      message: error instanceof Error ? error.message : '停止上传失败',
    })
  }
}

async function handlePreviewFromList(kind: UploadKind, asset: AssetSummary) {
  uploadTasks[kind].result = buildAssetDetailFromSummary(asset)
  uploadTasks[kind].status = isPreviewReady(uploadTasks[kind].result) ? 'success' : 'idle'
  await openPreview(kind)
}

function getTaskTitle(kind: UploadKind) {
  return kind === 'bim' ? 'BIM 模型' : '点云文件'
}

function getTaskMeta(kind: UploadKind) {
  const task = uploadTasks[kind]
  const currentFile = kind === 'bim' ? bimFile.value : pointCloudFile.value

  if (task.status === 'success' && task.result) {
    return getStatusText(task.result.status)
  }

  if (task.status === 'error' && task.errorMessage) {
    return task.errorMessage
  }

  if (task.status === 'uploading') {
    if (task.progress >= 90) {
      return '上传完成，正在等待后端解析'
    }

    return task.progress > 0 ? `上传中 ${task.progress}%` : '正在准备文件'
  }

  if (currentFile) {
    return '文件已就绪，点击上传'
  }

  return '请选择文件'
}

function getProgressStatus(task: UploadTaskState) {
  if (task.status === 'success') {
    return 'success'
  }

  if (task.status === 'error') {
    return 'exception'
  }

  return undefined
}

function buildSplitPreviewUrl(bimAsset: AssetDetail, pointcloudAsset: AssetDetail) {
  const url = new URL(window.location.origin)
  url.searchParams.set('view', 'split-preview')
  url.searchParams.set('bimAssetId', String(bimAsset.id))
  url.searchParams.set('pointcloudAssetId', String(pointcloudAsset.id))
  url.searchParams.set('bimDisplayName', bimAsset.sourceName || 'BIM 模型')
  return url.toString()
}

function navigateInCurrentWindow(url: string) {
  window.history.pushState({}, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

async function openPreview(mode: PreviewMode) {
  if (mode === 'bim') {
    const selectedAsset = ensurePreviewSelection('bim')
    if (!selectedAsset) {
      ElMessage.warning('请先上传或选择可预览的 BIM 文件')
      return
    }

    const refreshed = (await refreshUploadedFile('bim', true)) || selectedAsset
    if (!isPreviewReady(refreshed)) {
      ElMessage.info('BIM 文件仍在处理中，请点击刷新状态后重试')
      return
    }
  }

  if (mode === 'pointcloud') {
    const selectedAsset = ensurePreviewSelection('pointcloud')
    if (!selectedAsset) {
      ElMessage.warning('请先上传或选择可预览的点云文件')
      return
    }

    const refreshed = (await refreshUploadedFile('pointcloud', true)) || selectedAsset
    if (!isPreviewReady(refreshed)) {
      ElMessage.info('点云文件仍在处理中，请点击刷新状态后重试')
      return
    }
  }

  if (mode === 'split') {
    const selectedPointcloud = ensurePreviewSelection('pointcloud')
    const selectedBim = ensurePreviewSelection('bim')
    if (!selectedPointcloud || !selectedBim) {
      ElMessage.warning('请先确保 BIM 文件和点云文件列表中都有可预览文件')
      return
    }

    const [nextPointcloud, nextBim] = await Promise.all([
      refreshUploadedFile('pointcloud', true),
      refreshUploadedFile('bim', true),
    ])

    const pointcloudReady = isPreviewReady(nextPointcloud || selectedPointcloud)
    const bimReady = isPreviewReady(nextBim || selectedBim)

    if (!pointcloudReady || !bimReady) {
      ElMessage.info('当前仍有文件处理中，请点击刷新状态后重试')
      return
    }

    const previewUrl = buildSplitPreviewUrl(
      nextBim || selectedBim,
      nextPointcloud || selectedPointcloud,
    )
    navigateInCurrentWindow(previewUrl)
    return
  }

  previewMode.value = mode
}

function closePreview() {
  previewMode.value = null
}
</script>

<template>
  <section class="upload-view">
    <div class="page-shell">
      <header class="page-header card-surface">
        <div class="header-copy">
          <p class="eyebrow">CloudBIM Viewer</p>
          <h1>文件上传与预览</h1>
          <p class="summary">
            当前已切换为真实后端：登录使用 JWT，上传使用 TUS，资产状态从后端实时拉取。
          </p>
          <p class="account-line">当前登录账号：{{ userName }}</p>
        </div>

        <div class="header-actions">
          <el-button
            type="primary"
            :icon="View"
            :disabled="!canPreviewSplit"
            @click="openPreview('split')"
          >
            二分屏预览
          </el-button>
          <el-button plain :icon="SwitchButton" @click="$emit('logout')">
            退出登录
          </el-button>
        </div>
      </header>

      <section class="upload-grid">
        <UploadDropCard v-model:file="pointCloudFile" :config="POINT_CLOUD_UPLOAD_CONFIG">
          <template #actions>
            <div class="task-summary">
              <strong>{{ getTaskTitle('pointcloud') }}</strong>
              <span>{{ getTaskMeta('pointcloud') }}</span>
            </div>

            <el-progress
              v-if="uploadTasks.pointcloud.status !== 'idle'"
              :percentage="
                uploadTasks.pointcloud.status === 'success'
                  ? 100
                  : uploadTasks.pointcloud.progress
              "
              :status="getProgressStatus(uploadTasks.pointcloud)"
              :stroke-width="8"
              :show-text="false"
            />

            <div class="task-actions">
              <el-button
                plain
                :loading="refreshingState.pointcloud"
                :disabled="!uploadTasks.pointcloud.result || activeUploadKind === 'pointcloud'"
                @click="refreshUploadedFile('pointcloud')"
              >
                刷新状态
              </el-button>
              <el-button
                v-if="activeUploadKind === 'pointcloud'"
                plain
                @click="handleCancelUpload('pointcloud')"
              >
                停止
              </el-button>
              <el-button
                type="primary"
                :loading="activeUploadKind === 'pointcloud'"
                :disabled="
                  !pointCloudFile ||
                  (!!activeUploadKind && activeUploadKind !== 'pointcloud')
                "
                @click="handleUpload('pointcloud')"
              >
                {{ uploadTasks.pointcloud.status === 'success' ? '重新上传' : '上传文件' }}
              </el-button>
              <el-button plain :disabled="!canPreviewPointcloud" @click="openPreview('pointcloud')">
                预览
              </el-button>
            </div>
          </template>
        </UploadDropCard>

        <UploadDropCard v-model:file="bimFile" :config="BIM_UPLOAD_CONFIG">
          <template #actions>
            <div class="task-summary">
              <strong>{{ getTaskTitle('bim') }}</strong>
              <span>{{ getTaskMeta('bim') }}</span>
            </div>

            <el-progress
              v-if="uploadTasks.bim.status !== 'idle'"
              :percentage="uploadTasks.bim.status === 'success' ? 100 : uploadTasks.bim.progress"
              :status="getProgressStatus(uploadTasks.bim)"
              :stroke-width="8"
              :show-text="false"
            />

            <div class="task-actions">
              <el-button
                plain
                :loading="refreshingState.bim"
                :disabled="!uploadTasks.bim.result || activeUploadKind === 'bim'"
                @click="refreshUploadedFile('bim')"
              >
                刷新状态
              </el-button>
              <el-button
                v-if="activeUploadKind === 'bim'"
                plain
                @click="handleCancelUpload('bim')"
              >
                停止
              </el-button>
              <el-button
                type="primary"
                :loading="activeUploadKind === 'bim'"
                :disabled="!bimFile || (!!activeUploadKind && activeUploadKind !== 'bim')"
                @click="handleUpload('bim')"
              >
                {{ uploadTasks.bim.status === 'success' ? '重新上传' : '上传文件' }}
              </el-button>
              <el-button plain :disabled="!canPreviewBim" @click="openPreview('bim')">
                预览
              </el-button>
            </div>
          </template>
        </UploadDropCard>
      </section>

      <section class="file-library card-surface">
        <div class="library-head">
          <div>
            <h2>资产列表</h2>
            <p>后端通过 `GET /assets` 返回 BIM 与点云资产，列表按创建时间倒序展示。</p>
          </div>
          <el-button plain :loading="loadingAssets" @click="loadAssets()">
            刷新列表
          </el-button>
        </div>

        <div class="library-grid">
          <div class="library-panel">
            <div class="library-panel-head">
              <h3>BIM 模型</h3>
              <span>{{ bimUploadedFiles.length }} 个文件</span>
            </div>

            <div v-if="!bimUploadedFiles.length" class="library-empty">
              暂无 BIM 文件
            </div>

            <div v-else class="library-list">
              <article
                v-for="asset in bimUploadedFiles"
                :key="`bim-${asset.id}`"
                class="library-item"
              >
                <div class="library-item-main">
                  <strong>{{ asset.sourceName }}</strong>
                  <div class="library-meta">
                    <span>{{ formatFileSize(asset.sourceSize) }}</span>
                    <span>{{ formatDateTime(asset.createdAt) }}</span>
                  </div>
                </div>

                <div class="library-item-side">
                  <el-tag :type="getStatusTagType(asset.status)" effect="light">
                    {{ getStatusText(asset.status) }}
                  </el-tag>
                  <div class="library-actions">
                    <el-button plain size="small" @click="handlePreviewFromList('bim', asset)">
                      预览
                    </el-button>
                  </div>
                </div>
              </article>
            </div>
          </div>

          <div class="library-panel">
            <div class="library-panel-head">
              <h3>点云文件</h3>
              <span>{{ pointcloudUploadedFiles.length }} 个文件</span>
            </div>

            <div v-if="!pointcloudUploadedFiles.length" class="library-empty">
              暂无点云文件
            </div>

            <div v-else class="library-list">
              <article
                v-for="asset in pointcloudUploadedFiles"
                :key="`pointcloud-${asset.id}`"
                class="library-item"
              >
                <div class="library-item-main">
                  <strong>{{ asset.sourceName }}</strong>
                  <div class="library-meta">
                    <span>{{ formatFileSize(asset.sourceSize) }}</span>
                    <span>{{ formatDateTime(asset.createdAt) }}</span>
                  </div>
                </div>

                <div class="library-item-side">
                  <el-tag :type="getStatusTagType(asset.status)" effect="light">
                    {{ getStatusText(asset.status) }}
                  </el-tag>
                  <div class="library-actions">
                    <el-button plain size="small" @click="handlePreviewFromList('pointcloud', asset)">
                      预览
                    </el-button>
                  </div>
                </div>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section class="preview-workspace card-surface">
        <div class="workspace-head">
          <div>
            <h2>{{ previewTitle }}</h2>
          </div>

          <el-button v-if="previewMode" plain @click="closePreview">
            收起预览
          </el-button>
        </div>

        <div v-if="!previewMode" class="workspace-empty">
          <el-icon><View /></el-icon>
          <h3>上传完成后开始预览</h3>
          <p>支持单独查看点云或 BIM 模型，两个文件均上传完成后可切换为二分屏展示。</p>
        </div>

        <PointcloudPreviewPanel
          v-else-if="previewMode === 'pointcloud'"
          :asset-id="uploadTasks.pointcloud.result?.id || null"
        />

        <BimPreviewPanel
          v-else-if="previewMode === 'bim'"
          :asset-id="uploadTasks.bim.result?.id || null"
          :display-name="uploadTasks.bim.result?.sourceName"
        />

        <div v-else class="split-preview">
          <PointcloudPreviewPanel :asset-id="uploadTasks.pointcloud.result?.id || null" />
          <BimPreviewPanel
            :asset-id="uploadTasks.bim.result?.id || null"
            :display-name="uploadTasks.bim.result?.sourceName"
          />
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.upload-view {
  min-height: 100vh;
  padding: 28px;
  background: linear-gradient(180deg, #20242b 0%, #666768 100%);
}

.page-shell {
  width: min(1280px, 100%);
  margin: 0 auto;
}

.card-surface {
  background: #fff;
  border: 1px solid #e5eaf1;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.06);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 32px;
  border-radius: 24px;
}

.eyebrow {
  margin: 0 0 10px;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #3b82f6;
}

.header-copy h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 2.7rem);
  line-height: 1.1;
  letter-spacing: -0.03em;
  color: #0f172a;
}

.summary {
  margin: 14px 0 0;
  max-width: 720px;
  line-height: 1.7;
  color: #64748b;
}

.account-line {
  margin: 10px 0 0;
  color: #334155;
  font-size: 0.92rem;
}

.header-actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.upload-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  margin-top: 20px;
}

.task-summary {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.task-summary strong {
  font-size: 0.94rem;
  color: #0f172a;
}

.task-summary span {
  font-size: 0.84rem;
  color: #64748b;
}

.task-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.file-library {
  margin-top: 20px;
  padding: 24px;
  border-radius: 24px;
}

.library-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.library-head h2 {
  margin: 0;
  color: #0f172a;
}

.library-head p {
  margin: 8px 0 0;
  color: #64748b;
  line-height: 1.6;
}

.library-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.library-panel {
  border: 1px solid #e5eaf1;
  border-radius: 20px;
  background: #fbfcfe;
  overflow: hidden;
}

.library-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border-bottom: 1px solid #e5eaf1;
}

.library-panel-head h3 {
  margin: 0;
  font-size: 1rem;
  color: #0f172a;
}

.library-panel-head span {
  font-size: 0.84rem;
  color: #64748b;
}

.library-empty {
  padding: 28px 20px;
  color: #94a3b8;
}

.library-list {
  display: flex;
  flex-direction: column;
}

.library-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 20px;
  border-top: 1px solid #edf2f7;
}

.library-item:first-child {
  border-top: 0;
}

.library-item-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.library-item-main strong {
  color: #0f172a;
  word-break: break-all;
}

.library-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 0.84rem;
  color: #64748b;
}

.library-item-side {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.library-actions {
  display: flex;
  gap: 8px;
}

.preview-workspace {
  margin-top: 20px;
  padding: 24px;
  border-radius: 24px;
}

.workspace-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.workspace-head h2 {
  margin: 0;
  color: #0f172a;
}

.workspace-empty {
  min-height: 420px;
  border: 1px dashed #cbd5e1;
  border-radius: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #64748b;
}

.workspace-empty h3 {
  margin: 0;
  color: #0f172a;
}

.workspace-empty p {
  margin: 0;
  text-align: center;
  max-width: 560px;
  line-height: 1.7;
}

.split-preview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}

@media (max-width: 960px) {
  .page-header,
  .library-head {
    flex-direction: column;
    align-items: stretch;
  }

  .upload-grid,
  .library-grid,
  .split-preview {
    grid-template-columns: 1fr;
  }
}
</style>
