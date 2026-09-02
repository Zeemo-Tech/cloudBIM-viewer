<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { SwitchButton } from '@element-plus/icons-vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import { getBimAlignment, getScanCalibration } from '@/api/backend-alignment'
import {
  type AssetDetail,
  type AssetStatus,
  type AssetSummary,
  deleteAsset,
  getAssetDetail,
  listAssets,
} from '@/api/backend-file'
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

type PreviewMode = 'bim' | 'pointcloud' | 'split'
type UploadKind = 'bim' | 'pointcloud'
type CalibratedSplitPreviewOption = {
  key: string
  bimAssetId: number
  pointcloudAssetId: number
  bimDisplayName: string
  pointcloudDisplayName: string
  bimCreatedAt?: number
  pointcloudCreatedAt?: number
}

function isCalibratedSplitPreviewOption(
  item: CalibratedSplitPreviewOption | null,
): item is CalibratedSplitPreviewOption {
  return item !== null
}

const props = defineProps<{
  session: AuthSession
}>()

defineEmits<{
  logout: []
}>()

const router = useRouter()
const bimFile = ref<File | null>(null)
const pointCloudFile = ref<File | null>(null)
const activeUploadKind = ref<UploadKind | null>(null)
const cancelRequested = ref(false)
const loadingAssets = ref(false)
const assetLibraryExpanded = ref(false)
const alignmentDialogVisible = ref(false)
const splitPreviewDialogVisible = ref(false)
const loadingSplitPreviewOptions = ref(false)
const selectedAlignmentBimId = ref<number | null>(null)
const selectedAlignmentPointcloudId = ref<number | null>(null)
const selectedSplitPreviewKey = ref('')
const calibratedSplitPreviewOptions = ref<CalibratedSplitPreviewOption[]>([])
const refreshingState = reactive<Record<UploadKind, boolean>>({
  bim: false,
  pointcloud: false,
})
const deletingAssetIds = reactive<Record<number, boolean>>({})
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
const canOpenAlignmentStep = computed(
  () => !!getDefaultAlignmentAssetId('bim') && !!getDefaultAlignmentAssetId('pointcloud'),
)
const canOpenSplitPreviewStep = computed(() => calibratedSplitPreviewOptions.value.length > 0)
const headerWorkflowActive = computed(() => {
  if (canOpenSplitPreviewStep.value) {
    return 2
  }

  if (canOpenAlignmentStep.value) {
    return 1
  }

  return 0
})
const headerWorkflowSteps = computed(() => [
  { title: '上传文件', action: 'upload', disabled: false },
  { title: '校准页面', action: 'alignment', disabled: !canOpenAlignmentStep.value },
  { title: '实模对比', action: 'split-preview', disabled: !canOpenSplitPreviewStep.value },
] as const)
const bimUploadedFiles = computed(() => assetCollections.bim)
const pointcloudUploadedFiles = computed(() => assetCollections.pointcloud)
const alignmentBimOptions = computed(() =>
  assetCollections.bim.map((asset) => ({
    label: `${asset.sourceName} · ${getStatusText(asset.status)}`,
    value: asset.id,
    disabled: !isAssetReady(asset.status),
  })),
)
const alignmentPointcloudOptions = computed(() =>
  assetCollections.pointcloud.map((asset) => ({
    label: `${asset.sourceName} · ${getStatusText(asset.status)}`,
    value: asset.id,
    disabled: !isAssetReady(asset.status),
  })),
)
const selectedSplitPreviewOption = computed(() =>
  calibratedSplitPreviewOptions.value.find((item) => item.key === selectedSplitPreviewKey.value) ?? null,
)
const userName = computed(() => props.session.username)

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
      return '解析中'
    case 'processing':
      return '解析中'
    case 'ready':
      return '已解析'
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

function getRemeshStatusText(asset: AssetSummary) {
  switch (asset.meshRemesh?.status) {
    case 'queued':
      return '均匀化排队中'
    case 'processing':
      return '均匀化处理中'
    case 'succeeded':
      return '网格已均匀化'
    case 'failed':
      return '均匀化失败'
    default:
      return '等待均匀化'
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
    meshRemesh: asset.meshRemesh,
  }
}

function getLatestPreviewableAsset(type: UploadKind) {
  return assetCollections[type].find((asset) => asset.status === 'ready') || null
}

function getAssetById(kind: UploadKind, assetId: number | null) {
  if (!assetId) return null
  return assetCollections[kind].find((asset) => asset.id === assetId) || null
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

function getDefaultAlignmentAssetId(kind: UploadKind) {
  const selectedAssetId = uploadTasks[kind].result?.id ?? null
  const selectedAsset = getAssetById(kind, selectedAssetId)
  if (selectedAsset && isAssetReady(selectedAsset.status)) {
    return selectedAsset.id
  }

  return getLatestPreviewableAsset(kind)?.id ?? null
}

function syncTaskResultWithCollection(kind: UploadKind) {
  const currentResult = uploadTasks[kind].result
  if (!currentResult) return

  const matchedAsset = assetCollections[kind].find((asset) => asset.id === currentResult.id)
  if (matchedAsset) {
    uploadTasks[kind].result = {
      ...currentResult,
      ...buildAssetDetailFromSummary(matchedAsset),
    }
    return
  }

	const latestReady = getLatestPreviewableAsset(kind)
	const fallbackAsset = latestReady ? buildAssetDetailFromSummary(latestReady) : null
  if (fallbackAsset) {
    uploadTasks[kind].result = fallbackAsset
    uploadTasks[kind].status = isPreviewReady(fallbackAsset) ? 'success' : 'idle'
    uploadTasks[kind].errorMessage = ''
    return
  }

  resetTask(kind)
}

function syncTaskResultsWithCollections() {
  ;(['bim', 'pointcloud'] as UploadKind[]).forEach((kind) => {
    syncTaskResultWithCollection(kind)
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
    await loadCalibratedSplitPreviewOptions(true)
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

async function handleDeleteAsset(kind: UploadKind, asset: AssetSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除资产“${asset.sourceName || `#${asset.id}`}”吗？删除后不可恢复。`,
      '删除资产',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }

  deletingAssetIds[asset.id] = true

  try {
    await deleteAsset(asset.id)
    assetCollections[kind] = assetCollections[kind].filter((item) => item.id !== asset.id)
    syncTaskResultWithCollection(kind)
    ElMessage.success('资产已删除')
  } catch (error) {
    ElMessage({
      type: 'error',
      grouping: true,
      message: error instanceof Error ? error.message : '删除资产失败',
    })
  } finally {
    delete deletingAssetIds[asset.id]
  }
}

function openAlignmentSelector() {
  selectedAlignmentBimId.value = getDefaultAlignmentAssetId('bim')
  selectedAlignmentPointcloudId.value = getDefaultAlignmentAssetId('pointcloud')

  if (!selectedAlignmentBimId.value || !selectedAlignmentPointcloudId.value) {
    ElMessage.warning('请先确保 BIM 和点云列表中至少各有一个可用文件')
    return
  }

  alignmentDialogVisible.value = true
}

async function loadCalibratedSplitPreviewOptions(silent = false) {
  try {
    const readyPointclouds = assetCollections.pointcloud.filter((asset) => isAssetReady(asset.status))
    const readyBimMap = new Map(
      assetCollections.bim
        .filter((asset) => isAssetReady(asset.status))
        .map((asset) => [asset.id, asset] as const),
    )

    if (!readyPointclouds.length || !readyBimMap.size) {
      calibratedSplitPreviewOptions.value = []
      return []
    }

    const rawOptions: Array<CalibratedSplitPreviewOption | null> = await Promise.all(
      readyPointclouds.map(async (pointcloudAsset) => {
        try {
          const calibration = await getScanCalibration(pointcloudAsset.id)
          const data = calibration?.data
          const calibratedBimId = Number(data?.bimFileId)
          if (data?.hasBimAlignment && Number.isFinite(calibratedBimId) && calibratedBimId > 0) {
            const bimAsset = readyBimMap.get(calibratedBimId)
            if (bimAsset) {
              return {
                key: `${pointcloudAsset.id}:${bimAsset.id}`,
                bimAssetId: bimAsset.id,
                pointcloudAssetId: pointcloudAsset.id,
                bimDisplayName: bimAsset.sourceName || `BIM-${bimAsset.id}`,
                pointcloudDisplayName: pointcloudAsset.sourceName || `点云-${pointcloudAsset.id}`,
                bimCreatedAt: bimAsset.createdAt,
                pointcloudCreatedAt: pointcloudAsset.createdAt,
              }
            }
          }

          // Fallback for older backends whose scan summary predates the
          // alignment row: probe the small set of ready BIM assets directly.
          for (const bimAsset of readyBimMap.values()) {
            try {
              const alignment = await getBimAlignment({
                modelScanFileId: pointcloudAsset.id,
                modelBimFileId: bimAsset.id,
              })
              if (!alignment?.data) continue
              return {
                key: `${pointcloudAsset.id}:${bimAsset.id}`,
                bimAssetId: bimAsset.id,
                pointcloudAssetId: pointcloudAsset.id,
                bimDisplayName: bimAsset.sourceName || `BIM-${bimAsset.id}`,
                pointcloudDisplayName: pointcloudAsset.sourceName || `点云-${pointcloudAsset.id}`,
                bimCreatedAt: bimAsset.createdAt,
                pointcloudCreatedAt: pointcloudAsset.createdAt,
              }
            } catch (error: any) {
              if (error?.response?.status !== 400 && error?.response?.status !== 404) {
                throw error
              }
            }
          }
          return null
        } catch (error: any) {
          const status = error?.response?.status
          if (status === 400 || status === 404) {
            return null
          }
          throw error
        }
      }),
    )

    const options = rawOptions
      .filter(isCalibratedSplitPreviewOption)
      .sort((a, b) => (b.pointcloudCreatedAt ?? 0) - (a.pointcloudCreatedAt ?? 0))

    calibratedSplitPreviewOptions.value = options
    return options
  } catch (error) {
    calibratedSplitPreviewOptions.value = []
    if (!silent) {
      ElMessage.error(error instanceof Error ? error.message : '加载已校准文件组失败')
    }
    return []
  }
}

async function openSplitPreviewSelector() {
  splitPreviewDialogVisible.value = true
  loadingSplitPreviewOptions.value = true
  selectedSplitPreviewKey.value = ''

  try {
    await loadAssets(true)
    const options = await loadCalibratedSplitPreviewOptions(false)
    selectedSplitPreviewKey.value = options[0]?.key ?? ''
  } finally {
    loadingSplitPreviewOptions.value = false
  }
}

function confirmSplitPreviewSelection() {
  const selected = selectedSplitPreviewOption.value
  if (!selected) {
    ElMessage.warning('请选择已校准的文件组')
    return
  }

  splitPreviewDialogVisible.value = false
  openPreviewPage(
    buildSplitPreviewRoute(
      {
        id: selected.bimAssetId,
        sourceName: selected.bimDisplayName,
      } as AssetDetail,
      {
        id: selected.pointcloudAssetId,
        sourceName: selected.pointcloudDisplayName,
      } as AssetDetail,
    ),
  )
}

function confirmAlignmentSelection() {
  const selectedBim = getAssetById('bim', selectedAlignmentBimId.value)
  const selectedPointcloud = getAssetById('pointcloud', selectedAlignmentPointcloudId.value)

  if (!selectedBim || !selectedPointcloud) {
    ElMessage.warning('请选择 BIM 和点云文件')
    return
  }

  if (!isAssetReady(selectedBim.status) || !isAssetReady(selectedPointcloud.status)) {
    ElMessage.warning('请选择已就绪的 BIM 和点云文件')
    return
  }

  alignmentDialogVisible.value = false
  void router.push(buildAlignmentRoute(selectedBim, selectedPointcloud))
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

function handleHeaderStepClick(
  step: (typeof headerWorkflowSteps.value)[number],
) {
  if (step.disabled) {
    return
  }

  const { action } = step
  if (action === 'alignment') {
    openAlignmentSelector()
    return
  }

  if (action === 'split-preview') {
    void openSplitPreviewSelector()
  }
}

function buildSplitPreviewRoute(
  bimAsset: AssetDetail,
  pointcloudAsset: AssetDetail,
): RouteLocationRaw {
  return {
    path: '/preview/split',
    query: {
      bimAssetId: String(bimAsset.id),
      pointcloudAssetId: String(pointcloudAsset.id),
      bimDisplayName: bimAsset.sourceName || 'BIM 模型',
      pointcloudDisplayName: pointcloudAsset.sourceName || '点云场景',
    },
  }
}

function buildAlignmentRoute(
  bimAsset: AssetDetail,
  pointcloudAsset: AssetDetail,
): RouteLocationRaw {
  return {
    path: '/alignment/model',
    query: {
      bimAssetId: String(bimAsset.id),
      pointcloudAssetId: String(pointcloudAsset.id),
      bimDisplayName: bimAsset.sourceName || 'BIM 模型',
      pointcloudDisplayName: pointcloudAsset.sourceName || '点云场景',
    },
  }
}

function buildAssetPreviewRoute(
  mode: Exclude<PreviewMode, 'split'>,
  asset: AssetDetail,
): RouteLocationRaw {
  return {
    path: '/preview/asset',
    query: {
      previewType: mode,
      assetId: String(asset.id),
      displayName: asset.sourceName || undefined,
    },
  }
}

function openPreviewPage(location: RouteLocationRaw) {
  void router.push(location)
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

    openPreviewPage(buildAssetPreviewRoute('bim', refreshed))
    return
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

    openPreviewPage(buildAssetPreviewRoute('pointcloud', refreshed))
    return
  }

  if (mode === 'split') {
    const selectedPointcloud = ensurePreviewSelection('pointcloud')
    const selectedBim = ensurePreviewSelection('bim')

    const [nextPointcloud, nextBim] = await Promise.all([
      selectedPointcloud ? refreshUploadedFile('pointcloud', true) : Promise.resolve(null),
      selectedBim ? refreshUploadedFile('bim', true) : Promise.resolve(null),
    ])

    const pointcloudReady = isPreviewReady(nextPointcloud || selectedPointcloud)
    const bimReady = isPreviewReady(nextBim || selectedBim)

    if (pointcloudReady && bimReady && selectedPointcloud && selectedBim) {
      const previewRoute = buildSplitPreviewRoute(
        nextBim || selectedBim,
        nextPointcloud || selectedPointcloud,
      )
      openPreviewPage(previewRoute)
      return
    }

    ElMessage.info('BIM 和点云都完成真实解析后才能打开实模对比')
    return
  }
}

</script>

<template>
  <section class="upload-view">
    <div class="page-shell">
      <header class="page-header card-surface">
        <div class="header-copy">
          <h2>实模一致系统</h2>
        </div>

        <div class="header-actions">
          <el-button plain :icon="SwitchButton" @click="$emit('logout')">
            退出登录
          </el-button>
        </div>

        <div class="header-progress">
          <el-steps
            class="header-steps"
            :active="headerWorkflowActive"
            finish-status="success"
            process-status="process"
            align-center
          >
            <el-step
              v-for="step in headerWorkflowSteps"
              :key="step.title"
              :title="step.title"
              class="header-step"
              :class="{ 'is-disabled': step.disabled }"
              @click="handleHeaderStepClick(step)"
            />
          </el-steps>
        </div>
      </header>

      <section class="upload-grid">
        <UploadDropCard v-model:file="pointCloudFile" :config="POINT_CLOUD_UPLOAD_CONFIG">
          <template #actions>

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
            </div>
          </template>
        </UploadDropCard>

        <UploadDropCard v-model:file="bimFile" :config="BIM_UPLOAD_CONFIG">
          <template #actions>

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
            </div>
          </template>
        </UploadDropCard>
      </section>

      <section class="file-library card-surface">
        <div class="library-head">
          <div>
            <h2>资产列表</h2>
            <p class="library-summary">
              BIM {{ bimUploadedFiles.length }} 个，点云 {{ pointcloudUploadedFiles.length }} 个
            </p>
          </div>
          <div class="library-head-actions">
            <el-button plain :loading="loadingAssets" @click="loadAssets()">
              刷新列表
            </el-button>
            <el-button text @click="assetLibraryExpanded = !assetLibraryExpanded">
              {{ assetLibraryExpanded ? '收起' : '展开' }}
            </el-button>
          </div>
        </div>

        <div v-if="assetLibraryExpanded" class="library-grid">
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
                  <el-tag v-if="asset.status !== 'ready'" :type="getStatusTagType(asset.status)" effect="light">
                    {{ getStatusText(asset.status) }}
                  </el-tag>
                  <div class="library-actions">
                    <el-button
                      plain
                      size="small"
                      :disabled="!isAssetReady(asset.status)"
                      @click="handlePreviewFromList('bim', asset)"
                    >
                      预览
                    </el-button>
                    <div
                      v-if="isAssetReady(asset.status)"
                      class="mesh-remesh-list-status"
                      :class="{
                        'is-complete': asset.meshRemesh?.status === 'succeeded',
                        'is-failed': asset.meshRemesh?.status === 'failed',
                      }"
                    >
                      {{ getRemeshStatusText(asset) }}
                    </div>
                    <el-button
                      plain
                      size="small"
                      type="danger"
                      :loading="!!deletingAssetIds[asset.id]"
                      @click="handleDeleteAsset('bim', asset)"
                    >
                      删除
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
                  <el-tag v-if="asset.status !== 'ready'" :type="getStatusTagType(asset.status)" effect="light">
                    {{ getStatusText(asset.status) }}
                  </el-tag>
                  <div class="library-actions">
                    <el-button
                      plain
                      size="small"
                      :disabled="!isAssetReady(asset.status)"
                      @click="handlePreviewFromList('pointcloud', asset)"
                    >
                      预览
                    </el-button>
                    <el-button
                      plain
                      size="small"
                      type="danger"
                      :loading="!!deletingAssetIds[asset.id]"
                      @click="handleDeleteAsset('pointcloud', asset)"
                    >
                      删除
                    </el-button>
                  </div>
                </div>
              </article>
            </div>
          </div>
        </div>
      </section>

      <el-dialog
        v-model="splitPreviewDialogVisible"
        title="选择已校准文件组"
        width="640px"
        destroy-on-close
      >
        <div class="split-preview-dialog-body">
          <div v-if="loadingSplitPreviewOptions" class="dialog-loading">
            正在加载已校准文件组...
          </div>

          <div v-else-if="!calibratedSplitPreviewOptions.length" class="dialog-empty">
            <strong>暂无已校准的文件组</strong>
            <p>请先完成 BIM 与点云校准，再返回这里进行实模对比。</p>
          </div>

          <el-radio-group
            v-else
            v-model="selectedSplitPreviewKey"
            class="split-preview-options"
          >
            <el-radio
              v-for="option in calibratedSplitPreviewOptions"
              :key="option.key"
              :label="option.key"
              border
              class="split-preview-option"
            >
              <div class="split-preview-option__meta">
                <div class="split-preview-option__row">
                  <span class="split-preview-option__label">BIM</span>
                  <span class="split-preview-option__value">{{ option.bimDisplayName }}</span>
                </div>
                <div class="split-preview-option__row">
                  <span class="split-preview-option__label">点云</span>
                  <span class="split-preview-option__value">{{ option.pointcloudDisplayName }}</span>
                </div>
              </div>
            </el-radio>
          </el-radio-group>
        </div>

        <template #footer>
          <div class="alignment-dialog-footer">
            <el-button @click="splitPreviewDialogVisible = false">取消</el-button>
            <el-button
              type="primary"
              :disabled="loadingSplitPreviewOptions || !selectedSplitPreviewOption"
              @click="confirmSplitPreviewSelection"
            >
              预览
            </el-button>
          </div>
        </template>
      </el-dialog>

      <el-dialog
        v-model="alignmentDialogVisible"
        title="选择校准文件"
        width="520px"
        destroy-on-close
      >
        <div class="alignment-dialog-body">
          <div class="alignment-field">
            <span class="alignment-field__label">BIM 文件</span>
            <el-select
              v-model="selectedAlignmentBimId"
              placeholder="请选择 BIM 文件"
              style="width: 100%"
            >
              <el-option
                v-for="option in alignmentBimOptions"
                :key="`alignment-bim-${option.value}`"
                :label="option.label"
                :value="option.value"
                :disabled="option.disabled"
              />
            </el-select>
          </div>

          <div class="alignment-field">
            <span class="alignment-field__label">点云文件</span>
            <el-select
              v-model="selectedAlignmentPointcloudId"
              placeholder="请选择点云文件"
              style="width: 100%"
            >
              <el-option
                v-for="option in alignmentPointcloudOptions"
                :key="`alignment-pointcloud-${option.value}`"
                :label="option.label"
                :value="option.value"
                :disabled="option.disabled"
              />
            </el-select>
          </div>
        </div>

        <template #footer>
          <div class="alignment-dialog-footer">
            <el-button @click="alignmentDialogVisible = false">取消</el-button>
            <el-button type="primary" @click="confirmAlignmentSelection">
              打开校准页面
            </el-button>
          </div>
        </template>
      </el-dialog>

    </div>
  </section>
</template>

<style scoped>
.header-copy h2{
margin: 0;
}
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
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  /* gap: 24px; */
  padding: 28px 32px 10px;
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

.header-progress {
  grid-column: 1 / -1;
  padding-top: 4px;
}

.header-steps {
  width: 100%;
}

.header-progress :deep(.el-step__head) {
  --el-color-primary: #2563eb;
  --el-text-color-placeholder: #cbd5e1;
}

.header-progress :deep(.el-step__icon) {
  width: 34px;
  height: 34px;
  font-weight: 700;
}

.header-progress :deep(.el-step__title) {
  font-size: 0.95rem;
  font-weight: 700;
  color: #64748b;
}

.header-progress :deep(.el-step__description) {
  margin-top: 6px;
  line-height: 1.5;
  color: #64748b;
}

.header-progress :deep(.is-process .el-step__icon) {
  background: #eff6ff;
  border-color: #2563eb;
  color: #2563eb;
}

.header-progress :deep(.is-process .el-step__title) {
  color: #2563eb;
}

.header-progress :deep(.is-success .el-step__icon) {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #3b82f6;
}

.header-progress :deep(.is-success .el-step__title) {
  color: #334155;
}

.header-progress :deep(.el-step__line) {
  top: 16px;
}

.header-step {
  cursor: pointer;
}

.header-step:hover :deep(.el-step__title) {
  color: #2563eb;
}

.header-step.is-disabled {
  cursor: not-allowed;
}

.header-step.is-disabled:hover :deep(.el-step__title) {
  color: #64748b;
}

.header-step.is-disabled :deep(.el-step__icon) {
  background: #f8fafc;
  border-color: #dbe3ef;
  color: #cbd5e1;
}

.header-step.is-disabled :deep(.el-step__title) {
  color: #94a3b8;
}

.header-step.is-disabled :deep(.el-step__line) {
  background: #e2e8f0;
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

.alignment-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.alignment-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alignment-field__label {
  font-size: 0.92rem;
  font-weight: 600;
  color: #0f172a;
}

.alignment-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.split-preview-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dialog-loading,
.dialog-empty {
  display: flex;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  border: 1px dashed #d7deea;
  border-radius: 16px;
  background: #f8fafc;
  color: #475569;
  text-align: center;
  padding: 24px;
}

.dialog-empty p {
  margin: 0;
  color: #64748b;
}

.split-preview-options {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.split-preview-option {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  column-gap: 14px;
  width: 100%;
  height: auto;
  margin-right: 0;
  align-items: flex-start;
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid #dbe3f0;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.split-preview-option:hover {
  border-color: #93c5fd;
  box-shadow: 0 16px 28px rgba(37, 99, 235, 0.08);
  transform: translateY(-1px);
}

.split-preview-option :deep(.el-radio__label) {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 10px;
  padding: 0;
  white-space: normal;
  line-height: 1.5;
}

.split-preview-option :deep(.el-radio__input) {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  margin-top: 2px;
}

.split-preview-option :deep(.el-radio__inner) {
  width: 16px;
  height: 16px;
}

.split-preview-option.is-checked {
  border-color: #2563eb;
  background:
    radial-gradient(circle at top right, rgba(37, 99, 235, 0.12), transparent 36%),
    linear-gradient(180deg, #f8fbff 0%, #eef5ff 100%);
  box-shadow: 0 18px 36px rgba(37, 99, 235, 0.12);
}

.split-preview-option__meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 0.88rem;
  color: #64748b;
}

.split-preview-option__row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.split-preview-option__label {
  color: #94a3b8;
}

.split-preview-option__value {
  color: #334155;
  word-break: break-word;
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
  /* margin-bottom: 20px; */
}

.library-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.library-summary {
  margin: 8px 0 0;
  color: #64748b;
  font-size: 0.84rem;
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
  min-width: 0;
}

.library-item-side .el-tag {
  max-width: 260px;
  white-space: normal;
  line-height: 1.35;
  text-align: center;
}

.library-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.mesh-remesh-list-status {
  display: inline-flex;
  align-items: center;
  padding: 0 10px;
  border: 1px solid #f3d19e;
  border-radius: 4px;
  color: #a15c00;
  background: #fdf6ec;
  font-size: 0.82rem;
  white-space: nowrap;
}

.mesh-remesh-list-status.is-complete {
  border-color: #b3e19d;
  color: #529b2e;
  background: #f0f9eb;
}

.mesh-remesh-list-status.is-failed {
  border-color: #fab6b6;
  color: #c45656;
  background: #fef0f0;
}

@media (max-width: 960px) {
  .page-header,
  .library-head {
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }

  .upload-grid,
  .library-grid {
    grid-template-columns: 1fr;
  }
}
</style>
