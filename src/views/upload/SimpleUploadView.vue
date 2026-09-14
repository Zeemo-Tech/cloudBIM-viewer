<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, Document, Loading, Upload } from '@element-plus/icons-vue'
import { listAssets, type AssetArchiveMetadata, type AssetSummary, type ComponentType } from '@/api/backend-file'
import { uploadFile } from '@/features/upload/upload.service'
import { BIM_UPLOAD_CONFIG, CAD_UPLOAD_CONFIG, POINT_CLOUD_UPLOAD_CONFIG } from '@/features/upload/upload.config'
import { formatFileSize } from '@/features/upload/upload.utils'
import type { UploadKind } from '@/features/upload/upload.types'
import type { AuthSession } from '@/features/auth/auth.service'

interface SimpleUploadTask {
  status: 'idle' | 'uploading' | 'success' | 'error'
  progress: number
  errorMessage?: string
}

const props = withDefaults(defineProps<{
  session: AuthSession
  projectId: number
  projectName?: string
  allowedKinds?: UploadKind[]
  compact?: boolean
}>(), {
  allowedKinds: () => ['bim', 'pointcloud'],
  compact: false,
})

const emit = defineEmits<{ uploaded: [kind: UploadKind] }>()
const availableKinds = computed<UploadKind[]>(() => {
  const kinds = props.allowedKinds.filter((kind): kind is UploadKind => kind === 'bim' || kind === 'cad' || kind === 'pointcloud')
  return kinds.length ? [...new Set(kinds)] : ['bim']
})
const activeTab = ref<UploadKind>(availableKinds.value[0])
const selectedFiles = reactive<Record<UploadKind, File | null>>({ bim: null, cad: null, pointcloud: null })
const tasks = reactive<Record<UploadKind, SimpleUploadTask>>({ bim: { status: 'idle', progress: 0 }, cad: { status: 'idle', progress: 0 }, pointcloud: { status: 'idle', progress: 0 } })
const archiveForm = reactive({ building: '', floor: '', componentType: '' as ComponentType | '', archiveSerial: '', scanDate: '' })
const designModels = ref<AssetSummary[]>([])
const designModelsLoading = ref(false)
const uploading = computed(() => tasks[activeTab.value].status === 'uploading')
const fileInput = ref<HTMLInputElement | null>(null)
const activeConfig = computed(() => activeTab.value === 'bim' ? BIM_UPLOAD_CONFIG : activeTab.value === 'cad' ? CAD_UPLOAD_CONFIG : POINT_CLOUD_UPLOAD_CONFIG)
const activeFile = computed(() => selectedFiles[activeTab.value])
const supportedFormats = computed(() => activeConfig.value.extensions.map((extension) => extension.toUpperCase()).join(' / '))
const isIfcOnly = computed(() => availableKinds.value.length === 1 && availableKinds.value[0] === 'bim')
const activeTypeName = computed(() => activeTab.value === 'bim' ? 'IFC 模型' : activeTab.value === 'cad' ? 'CAD 图纸' : '点云文件')
const archiveCode = computed(() => archiveForm.floor && archiveForm.componentType && archiveForm.archiveSerial ? `${archiveForm.floor.toUpperCase()}-${archiveForm.componentType}-${archiveForm.archiveSerial.toUpperCase()}` : '待生成')
function archivePart(value?: string) {
  return value?.trim().toUpperCase() || ''
}
const matchingDesign = computed(() => designModels.value.find((asset) => archivePart(asset.building) === archivePart(archiveForm.building) && archivePart(asset.floor) === archivePart(archiveForm.floor) && archivePart(asset.componentType) === archivePart(archiveForm.componentType) && archivePart(asset.archiveSerial) === archivePart(archiveForm.archiveSerial)))
const buildings = computed(() => [...new Set(designModels.value.map((asset) => archivePart(asset.building)).filter(Boolean))] as string[])
const floors = computed(() => [...new Set(designModels.value.filter((asset) => !archiveForm.building || archivePart(asset.building) === archivePart(archiveForm.building)).map((asset) => archivePart(asset.floor)).filter(Boolean))] as string[])
const componentTypes = computed(() => [...new Set(designModels.value.filter((asset) => (!archiveForm.building || archivePart(asset.building) === archivePart(archiveForm.building)) && (!archiveForm.floor || archivePart(asset.floor) === archivePart(archiveForm.floor))).map((asset) => archivePart(asset.componentType)).filter(Boolean))] as ComponentType[])
const serials = computed(() => [...new Set(designModels.value.filter((asset) => (!archiveForm.building || archivePart(asset.building) === archivePart(archiveForm.building)) && (!archiveForm.floor || archivePart(asset.floor) === archivePart(archiveForm.floor)) && (!archiveForm.componentType || archivePart(asset.componentType) === archivePart(archiveForm.componentType))).map((asset) => archivePart(asset.archiveSerial)).filter(Boolean))] as string[])
function componentTypeLabel(value: string) {
  return ({ YKT: '预制空调板 YKT', YTY: '预制空调板 YTY', PCLT: '预制楼梯 PCLT', DLB: '叠合板 DLB', YB: '叠合板 YB' } as Record<string, string>)[value] || value
}

let applyingPointcloudDefaults = false

function applyPointcloudDefaults(building: string, options: { onlyIfEmpty?: boolean } = {}) {
  if (activeTab.value !== 'pointcloud' || !building) return
  if (options.onlyIfEmpty && (archiveForm.floor || archiveForm.componentType || archiveForm.archiveSerial)) return

  const design = designModels.value.find((asset) => archivePart(asset.building) === archivePart(building))
  applyingPointcloudDefaults = true
  archiveForm.floor = design ? archivePart(design.floor) : ''
  archiveForm.componentType = design ? archivePart(design.componentType) as ComponentType : ''
  archiveForm.archiveSerial = design ? archivePart(design.archiveSerial) : ''
  applyingPointcloudDefaults = false
}

watch(availableKinds, (kinds) => {
  if (!kinds.includes(activeTab.value)) activeTab.value = kinds[0]
})

function requestUpload(kind: UploadKind) {
  if (!selectedFiles[kind]) { ElMessage.warning(kind === 'bim' ? '请先选择 IFC 模型文件' : kind === 'cad' ? '请先选择 CAD 图纸' : '请先选择点云文件'); return }
  if (!archiveForm.building || !archiveForm.floor || !archiveForm.componentType || !archiveForm.archiveSerial) { ElMessage.warning('请完整填写楼栋、楼层、构件类型和归档序号'); return }
  if (kind === 'pointcloud' && !archiveForm.scanDate) { ElMessage.warning('请选择扫描日期'); return }
  if (kind === 'pointcloud' && !matchingDesign.value) { ElMessage.warning('当前归档编号没有匹配的 IFC 设计模型'); return }
  void confirmUpload(kind)
}
function chooseFile() {
  if (!uploading.value) fileInput.value?.click()
}
function resetTask(kind: UploadKind) {
  tasks[kind] = { status: 'idle', progress: 0 }
}
function setSelectedFile(kind: UploadKind, file: File) {
  if (tasks[kind].status === 'uploading') return
  const extension = file.name.split('.').pop()?.toLowerCase() || ''
  const config = kind === 'bim' ? BIM_UPLOAD_CONFIG : kind === 'cad' ? CAD_UPLOAD_CONFIG : POINT_CLOUD_UPLOAD_CONFIG
  if (!config.extensions.includes(extension)) {
    ElMessage.warning(isIfcOnly.value ? '这里只能上传 IFC 文件' : `请选择 ${config.accept.toUpperCase()} 格式的文件`)
    return
  }
  // Selecting a replacement starts a fresh visual task, so the previous
  // completion/error message cannot leak into the next upload.
  selectedFiles[kind] = file
  resetTask(kind)
}
function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) setSelectedFile(activeTab.value, file)
  input.value = ''
}
function handleDrop(event: DragEvent) {
  if (uploading.value) return
  const file = event.dataTransfer?.files?.[0]
  if (file) setSelectedFile(activeTab.value, file)
}
function clearActiveFile() {
  if (!uploading.value) {
    selectedFiles[activeTab.value] = null
    resetTask(activeTab.value)
  }
}
function selectUploadType(kind: UploadKind) {
  activeTab.value = kind
}
async function confirmUpload(kind: UploadKind) {
  const file = selectedFiles[kind]
  if (!file) return
  tasks[kind] = { status: 'uploading', progress: 0 }
  try {
    const archiveMetadata: AssetArchiveMetadata = {
      building: archiveForm.building.toUpperCase(),
      floor: archiveForm.floor.toUpperCase(),
      componentType: archiveForm.componentType as ComponentType,
      archiveSerial: archiveForm.archiveSerial.toUpperCase(),
      ...(archiveForm.scanDate ? { scanDate: Math.floor(new Date(`${archiveForm.scanDate}T00:00:00`).getTime() / 1000) } : {}),
    }
    await uploadFile({ type: kind, file, projectId: props.projectId, archiveMetadata, onProgress: (progress) => { tasks[kind].progress = progress } })
    tasks[kind] = { status: 'success', progress: 100 }
    selectedFiles[kind] = null
    if (kind === 'bim') await loadDesignModels()
    ElMessage.success(`${kind === 'bim' ? 'IFC 模型' : kind === 'cad' ? 'CAD 图纸' : '点云文件'}上传并处理完成`)
    emit('uploaded', kind)
  } catch (error) {
    tasks[kind] = { status: 'error', progress: tasks[kind].progress, errorMessage: error instanceof Error ? error.message : '上传失败' }
    ElMessage.error(tasks[kind].errorMessage || '上传失败')
  }
}

async function loadDesignModels() {
  if (!props.projectId) {
    designModels.value = []
    return
  }
  designModelsLoading.value = true
  try {
    const response = await listAssets({ projectId: props.projectId, type: 'bim', status: 'ready', page: 1, pageSize: 500 })
    designModels.value = (response.data?.list || []).filter((asset) => asset.type === 'bim' && asset.status === 'ready' && archivePart(asset.building) && archivePart(asset.floor) && archivePart(asset.componentType) && archivePart(asset.archiveSerial))
  } catch {
    designModels.value = []
  } finally {
    designModelsLoading.value = false
  }
}

watch(() => archiveForm.building, (building) => {
  if (activeTab.value === 'pointcloud') applyPointcloudDefaults(building)
})
watch(() => archiveForm.floor, () => {
  if (activeTab.value === 'pointcloud' && !applyingPointcloudDefaults) {
    archiveForm.componentType = ''
    archiveForm.archiveSerial = ''
  }
}, { flush: 'sync' })
watch(() => archiveForm.componentType, () => {
  if (activeTab.value === 'pointcloud' && !applyingPointcloudDefaults) archiveForm.archiveSerial = ''
}, { flush: 'sync' })
watch(() => props.projectId, () => { void loadDesignModels() })
watch(activeTab, (kind) => {
  if (kind === 'pointcloud') {
    void loadDesignModels().then(() => applyPointcloudDefaults(archiveForm.building, { onlyIfEmpty: true }))
  }
})
onMounted(() => { void loadDesignModels() })
</script>

<template>
  <section class="simple-upload-page" :class="{ 'is-compact': props.compact }" :aria-busy="uploading">
    <div v-if="availableKinds.length > 1" class="type-selector" role="group" aria-label="上传文件类型">
      <button v-for="kind in availableKinds" :key="kind" class="file-type-btn" :class="{ active: activeTab === kind }" type="button" :aria-pressed="activeTab === kind" :disabled="uploading" @click="selectUploadType(kind)">{{ kind === 'bim' ? 'IFC 模型' : kind === 'cad' ? 'CAD 图纸' : '点云文件' }}</button>
    </div>
    <input ref="fileInput" class="hidden-input" type="file" :accept="activeConfig.accept" @change="handleFileChange" />
    <div class="file-selection" :class="{ 'has-file': activeFile }" @dragover.prevent @drop.prevent="handleDrop">
      <el-icon class="file-symbol" :size="24"><Document /></el-icon>
      <div class="file-details">
        <strong :title="activeFile?.name">{{ activeFile ? activeFile.name : `选择${activeTypeName}` }}</strong>
        <span v-if="activeFile">{{ formatFileSize(activeFile.size) }} · {{ supportedFormats }}</span>
        <span v-else>{{ supportedFormats }} · 可将文件拖放到此处</span>
      </div>
      <div class="file-actions">
        <button class="choose-file-button" type="button" :disabled="uploading" :aria-label="`${activeFile ? '更换' : '选择'}${activeTypeName}`" @click="chooseFile">{{ activeFile ? '更换文件' : '选择文件' }}</button>
        <button v-if="activeFile" class="clear-btn" type="button" title="清除已选文件" aria-label="清除已选文件" :disabled="uploading" @click="clearActiveFile"><el-icon><Close /></el-icon></button>
      </div>
    </div>
    <section class="archive-form" :class="{ 'has-scan-date': activeTab === 'pointcloud' }">
      <div class="archive-form-heading"><div><h2>归档信息</h2><p>模型与点云通过归档编号自动关联</p></div><code :title="archiveCode">{{ archiveCode }}</code></div>
        <div class="archive-fields">
          <div class="archive-field"><span>楼栋</span><el-input v-if="activeTab !== 'pointcloud'" v-model="archiveForm.building" placeholder="如 2#" clearable /><el-select v-else v-model="archiveForm.building" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="当前项目暂无已就绪且归档完整的 IFC 模型" placeholder="楼栋"><el-option v-for="item in buildings" :key="item" :label="item" :value="item" /></el-select></div>
          <div class="archive-field"><span>楼层</span><el-input v-if="activeTab !== 'pointcloud'" v-model="archiveForm.floor" placeholder="如 16F" clearable /><el-select v-else v-model="archiveForm.floor" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="请先选择楼栋" placeholder="楼层"><el-option v-for="item in floors" :key="item" :label="item" :value="item" /></el-select></div>
          <div class="archive-field"><span>楼板类型</span><el-select v-model="archiveForm.componentType" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="请先选择楼栋和楼层" placeholder="选择类型"><template v-if="activeTab === 'pointcloud'"><el-option v-for="item in componentTypes" :key="item" :label="componentTypeLabel(item)" :value="item" /></template><template v-else><el-option label="预制空调板 YKT" value="YKT" /><el-option label="预制空调板 YTY" value="YTY" /><el-option label="预制楼梯 PCLT" value="PCLT" /><el-option label="叠合板 DLB" value="DLB" /><el-option label="叠合板 YB" value="YB" /></template></el-select></div>
          <div class="archive-field"><span>归档序号</span><el-select v-if="activeTab === 'pointcloud'" v-model="archiveForm.archiveSerial" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="请先选择楼栋、楼层和楼板类型" placeholder="选择序号"><el-option v-for="item in serials" :key="item" :label="item" :value="item" /></el-select><el-input v-else v-model="archiveForm.archiveSerial" placeholder="如 21" clearable /></div>
          <div v-if="activeTab === 'pointcloud'" class="archive-field"><span>扫描日期</span><el-date-picker v-model="archiveForm.scanDate" type="date" value-format="YYYY-MM-DD" clearable placeholder="选择日期" /></div>
        </div>
        <div v-if="activeTab === 'pointcloud'" class="match-status" :class="{ matched: matchingDesign }">{{ matchingDesign ? `已匹配 IFC：${matchingDesign.sourceName}` : '请选择完整归档信息以匹配 IFC 模型' }}</div>

    </section>
    <div v-if="uploading || tasks[activeTab].status === 'success' || tasks[activeTab].status === 'error'" class="upload-progress" role="status">
      <el-progress :percentage="tasks[activeTab].progress" :status="tasks[activeTab].status === 'success' ? 'success' : tasks[activeTab].status === 'error' ? 'exception' : undefined" />
      <span :class="{ 'is-error': tasks[activeTab].status === 'error' }">{{ tasks[activeTab].status === 'error' ? tasks[activeTab].errorMessage : tasks[activeTab].status === 'success' ? '上传处理完成' : '正在上传并处理文件…' }}</span>
    </div>
    <footer class="upload-footer">
      <span>{{ activeFile ? '确认归档信息后开始上传' : '请先选择文件，再填写归档信息' }}</span>
      <button class="submit-btn" type="button" :aria-label="uploading ? '正在上传' : `开始上传${activeTypeName}`" :disabled="!activeFile || uploading" @click="requestUpload(activeTab)"><el-icon :class="{ 'is-loading': uploading }"><Loading v-if="uploading" /><Upload v-else /></el-icon>{{ uploading ? '上传中…' : '开始上传' }}</button>
    </footer>
  </section>
</template>

<style scoped lang="scss">
@use '@/styles/workspace-controls' as controls;
.simple-upload-page { width: min(100%, 860px); margin-inline: auto; padding: var(--workspace-gutter); display: flex; flex-direction: column; gap: var(--workspace-gap); container-type: inline-size; }
.simple-upload-page.is-compact { width: 100%; padding: 0; }
.simple-upload-page { scrollbar-width: none; }
.simple-upload-page::-webkit-scrollbar { display: none; width: 0; height: 0; }
.hidden-input { display: none; }
.type-selector { display: flex; gap: var(--spacing-sm); flex-wrap: wrap; }
.file-type-btn, .choose-file-button, .clear-btn, .submit-btn { @include controls.action; }
.file-type-btn.active { color: var(--color-primary); background: var(--color-primary-soft); border-color: var(--color-primary); }
.file-selection { display: flex; align-items: center; gap: var(--spacing-md); min-width: 0; min-height: 96px; padding: var(--spacing-md); border: 1px dashed var(--border-color-hover); border-radius: var(--radius-sm); background: var(--bg-control); }
.file-selection.has-file { border-style: solid; }
.file-symbol { color: var(--color-primary); flex: 0 0 32px; }
.file-details { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--spacing-xs); }
.file-details strong { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-details > span { color: var(--text-secondary); font-size: var(--font-size-xs); }
.file-actions { display: flex; gap: var(--spacing-sm); }
.clear-btn { width: var(--control-height); padding: 0; }
.archive-form { min-width: 0; }
.archive-form-heading { display: flex; align-items: start; gap: var(--spacing-md); margin-bottom: var(--spacing-md); }
.archive-form-heading > div { min-width: 0; flex: 1; }
.archive-form-heading h2 { margin: 0; font-size: var(--font-size-md); }
.archive-form-heading p { margin: var(--spacing-xs) 0 0; color: var(--text-secondary); font-size: var(--font-size-xs); }
.archive-form-heading code { max-width: 45%; padding: var(--spacing-xs) var(--spacing-sm); border-radius: var(--radius-xs); background: var(--bg-control); color: var(--text-secondary); overflow-wrap: anywhere; font-size: var(--font-size-xs); }
.archive-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--spacing-md); @include controls.filters; }
.archive-form.has-scan-date .archive-fields { grid-template-columns: minmax(0, .78fr) minmax(0, .78fr) repeat(2, minmax(0, 1fr)) minmax(0, 1.45fr); }
.archive-field { min-width: 0; display: flex; flex-direction: column; gap: var(--spacing-sm); }
.archive-field > span { color: var(--text-secondary); font-size: var(--font-size-sm); }
.archive-field :deep(.el-select), .archive-field :deep(.el-input), .archive-field :deep(.el-date-editor) { width: 100%; min-width: 0; }
.match-status { margin-top: var(--spacing-md); padding: var(--spacing-sm) var(--spacing-compact); border-radius: var(--radius-xs); color: var(--color-info); background: var(--color-info-soft); font-size: var(--font-size-xs); overflow-wrap: anywhere; }
.match-status.matched { color: var(--color-success); background: var(--color-success-soft); }
.upload-footer { display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-md); padding-top: var(--spacing-md); border-top: 1px solid var(--border-color-light); }
.upload-footer > span { color: var(--text-secondary); font-size: var(--font-size-xs); }
.submit-btn { @include controls.primary; flex-shrink: 0; }
.upload-progress { display: grid; gap: var(--spacing-sm); font-size: var(--font-size-xs); color: var(--text-secondary); }
.upload-progress .is-error { color: var(--color-danger); }
@container (max-width: 560px) { .archive-fields, .archive-form.has-scan-date .archive-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); } .file-selection { flex-wrap: wrap; } .file-actions { margin-left: auto; } }
@container (max-width: 340px) { .archive-fields, .archive-form.has-scan-date .archive-fields { grid-template-columns: minmax(0, 1fr); } .upload-footer { align-items: stretch; flex-direction: column; } }
</style>
