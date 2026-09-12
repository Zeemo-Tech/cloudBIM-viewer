<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Close, Document, Loading, Refresh, Upload } from '@element-plus/icons-vue'
import { listAssets, type AssetArchiveMetadata, type AssetSummary, type ComponentType } from '@/api/backend-file'
import { uploadFile } from '@/features/upload/upload.service'
import { BIM_UPLOAD_CONFIG, CAD_UPLOAD_CONFIG, POINT_CLOUD_UPLOAD_CONFIG } from '@/features/upload/upload.config'
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
const isIfcOnly = computed(() => availableKinds.value.length === 1 && availableKinds.value[0] === 'bim')
const activeTypeName = computed(() => activeTab.value === 'bim' ? (isIfcOnly.value ? 'IFC模型' : 'BIM模型') : activeTab.value === 'cad' ? 'CAD图纸' : '点云文件')
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
function toggleUploadType() {
  if (availableKinds.value.length < 2) return
  const index = availableKinds.value.indexOf(activeTab.value)
  activeTab.value = availableKinds.value[(index + 1) % availableKinds.value.length]
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
  <section class="simple-upload-page" :class="{ 'is-compact': props.compact }">
    <div class="upload-stage">
      <div class="upload-stack-wrapper">
        <div class="stack-layer stack-layer-3"></div><div class="stack-layer stack-layer-2"></div><div class="stack-layer stack-layer-1"><div class="decor-grid"></div></div>
        <div :key="activeTab" class="main-upload-card" @click="chooseFile" @dragover.prevent @drop.prevent="handleDrop">
          <input ref="fileInput" class="hidden-input" type="file" :accept="activeConfig.accept" @change="handleFileChange" />
          <div class="corner-mark corner-tl"></div><div class="corner-mark corner-tr"></div><div class="corner-mark corner-bl"></div><div class="corner-mark corner-br"></div>
          <template v-if="!activeFile"><div class="upload-icon-wrapper"><el-icon :size="46"><Document /></el-icon><span class="plus-badge">+</span></div><h2>添加{{ activeTypeName }}</h2><p>拖拽到这里，或点击选择文件</p><span class="format-pill">支持 {{ activeConfig.accept.replace('.', '').toUpperCase() }} 格式</span></template>
          <template v-else><div class="scan-line" aria-hidden="true"></div><div class="upload-icon-wrapper has-file"><el-icon :size="44"><Check /></el-icon></div><h2 class="selected-name">{{ activeFile.name }}</h2><p>{{ (activeFile.size / 1024 / 1024).toFixed(2) }} MB</p><button class="replace-file" type="button">重新选择</button></template>
        </div>
      </div>
      <div class="upload-controls-shell">
        <div class="control-bar"><div class="file-control"><div v-if="availableKinds.length > 1" class="type-selector"><button class="file-type-btn" type="button" title="切换文件类型" :aria-label="`切换到${activeTab === 'bim' ? '点云文件' : 'BIM模型'}`" @click.stop="toggleUploadType"><el-icon><Refresh /></el-icon><span class="type-dot"></span></button></div><div v-else class="locked-type-badge" :title="`仅支持${activeTypeName}`"><el-icon><Document /></el-icon></div><div :key="activeTab" class="guide-text"><span>{{ activeFile ? activeFile.name : `添加${activeTypeName}` }}</span><small>{{ isIfcOnly ? '仅支持 IFC 模型文件' : activeTab === 'cad' ? 'CAD 设计图纸' : activeTab === 'bim' ? 'BIM 模型文件' : '点云文件' }}</small></div><button v-if="activeFile" class="clear-btn" type="button" title="清除文件" @click="clearActiveFile"><el-icon><Close /></el-icon></button><button class="submit-btn" :class="{ active: activeFile && !uploading, loading: uploading }" type="button" :disabled="!activeFile || uploading" @click="requestUpload(activeTab)"><el-icon :size="21"><Loading v-if="uploading" /><Upload v-else /></el-icon></button></div></div>
        <section class="archive-form" :class="{ 'has-scan-date': activeTab === 'pointcloud' }">
        <div class="archive-form-heading"><div><strong>归档信息</strong><span>模型与点云通过归档编号自动关联</span></div><code>{{ archiveCode }}</code></div>
        <div class="archive-fields">
          <div class="archive-field"><span>楼栋</span><el-input v-if="activeTab !== 'pointcloud'" v-model="archiveForm.building" placeholder="如 2#" clearable /><el-select v-else v-model="archiveForm.building" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="当前项目没有带归档信息的 ready BIM" placeholder="选择楼栋"><el-option v-for="item in buildings" :key="item" :label="item" :value="item" /></el-select></div>
          <div class="archive-field"><span>楼层</span><el-input v-if="activeTab !== 'pointcloud'" v-model="archiveForm.floor" placeholder="如 16F" clearable /><el-select v-else v-model="archiveForm.floor" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="请先选择楼栋" placeholder="选择楼层"><el-option v-for="item in floors" :key="item" :label="item" :value="item" /></el-select></div>
          <div class="archive-field"><span>楼板类型</span><el-select v-model="archiveForm.componentType" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="请先选择楼栋和楼层" placeholder="选择类型"><template v-if="activeTab === 'pointcloud'"><el-option v-for="item in componentTypes" :key="item" :label="componentTypeLabel(item)" :value="item" /></template><template v-else><el-option label="预制空调板 YKT" value="YKT" /><el-option label="预制空调板 YTY" value="YTY" /><el-option label="预制楼梯 PCLT" value="PCLT" /><el-option label="叠合板 DLB" value="DLB" /><el-option label="叠合板 YB" value="YB" /></template></el-select></div>
          <div class="archive-field"><span>归档序号</span><el-select v-if="activeTab === 'pointcloud'" v-model="archiveForm.archiveSerial" filterable allow-create default-first-option clearable :loading="designModelsLoading" no-data-text="请先选择楼栋、楼层和楼板类型" placeholder="选择序号"><el-option v-for="item in serials" :key="item" :label="item" :value="item" /></el-select><el-input v-else v-model="archiveForm.archiveSerial" placeholder="如 21" clearable /></div>
          <div v-if="activeTab === 'pointcloud'" class="archive-field"><span>扫描日期</span><el-date-picker v-model="archiveForm.scanDate" type="date" value-format="YYYY-MM-DD" clearable placeholder="选择日期" /></div>
        </div>
        <div v-if="activeTab === 'pointcloud'" class="match-status" :class="{ matched: matchingDesign }">{{ matchingDesign ? `已匹配 IFC：${matchingDesign.sourceName}` : '请选择完整归档信息以匹配 IFC 模型' }}</div>
        </section>
      </div>
      <div v-if="uploading || tasks[activeTab].status === 'success' || tasks[activeTab].status === 'error'" class="upload-progress"><el-progress :percentage="tasks[activeTab].progress" :status="tasks[activeTab].status === 'success' ? 'success' : tasks[activeTab].status === 'error' ? 'exception' : undefined" /><span>{{ tasks[activeTab].status === 'error' ? tasks[activeTab].errorMessage : tasks[activeTab].status === 'success' ? '上传处理完成' : '正在上传并处理文件...' }}</span></div>
    </div>
  </section>
</template>

<style scoped>
.simple-upload-page{box-sizing:border-box;width:100%;height:100%;min-height:650px;display:flex;flex-direction:column;align-items:center;padding:28px 24px;overflow:auto}.upload-stage{width:100%;display:flex;flex:1;flex-direction:column;align-items:center;justify-content:center}.upload-stack-wrapper{position:relative;width:100%;max-width:42rem;display:flex;align-items:center;justify-content:center;margin-bottom:55px}.stack-layer{position:absolute;border-radius:3rem;pointer-events:none;overflow:hidden}.stack-layer-3{width:380px;height:300px;background:rgb(255 255 255 / 20%);border:1px solid rgb(255 255 255 / 30%);transform:rotate(12deg) translate(4rem,-1.5rem);filter:blur(2px);z-index:0}.stack-layer-2{width:400px;height:310px;background:rgb(255 255 255 / 40%);border:1px solid rgb(255 255 255 / 40%);box-shadow:0 25px 50px -12px rgb(0 0 0 / 18%);transform:rotate(6deg) translate(2rem,-.5rem);z-index:1}.stack-layer-1{width:400px;height:310px;background:rgb(255 255 255 / 60%);border:1px solid rgb(255 255 255 / 60%);box-shadow:0 25px 50px -12px rgb(0 0 0 / 16%);transform:rotate(-4deg) translate(-1.5rem,.25rem);z-index:2}.decor-grid{position:absolute;inset:0;opacity:.06;background-image:radial-gradient(#000 1px,transparent 0);background-size:20px 20px}.main-upload-card{position:relative;z-index:3;width:410px;height:320px;display:flex;flex-direction:column;align-items:center;justify-content:center;border:1px solid #e5e7eb;border-radius:3rem;background:#fff;box-shadow:0 32px 64px -16px rgb(0 0 0 / 12%);cursor:pointer;transition:.35s;overflow:hidden}.main-upload-card:hover{transform:scale(1.02);border-color:#bfdbfe;box-shadow:0 42px 76px -18px rgb(0 0 0 / 15%)}.hidden-input{display:none}.corner-mark{position:absolute;width:1rem;height:1rem;border-color:#f3f4f6}.corner-tl{top:2rem;left:2rem;border-top:2px solid;border-left:2px solid}.corner-tr{top:2rem;right:2rem;border-top:2px solid;border-right:2px solid}.corner-bl{bottom:2rem;left:2rem;border-bottom:2px solid;border-left:2px solid}.corner-br{right:2rem;bottom:2rem;border-right:2px solid;border-bottom:2px solid}.upload-icon-wrapper{position:relative;width:78px;height:78px;display:grid;place-items:center;margin-bottom:22px;border-radius:24px;background:#f7f9fc;color:#9aa5b5;box-shadow:inset 0 0 0 1px #eef1f5}.upload-icon-wrapper.has-file{background:#ecf8f2;color:#23a36d}.plus-badge{position:absolute;right:-2px;bottom:-2px;width:29px;height:29px;display:grid;place-items:center;border:4px solid #fff;border-radius:50%;background:#6497e5;color:#fff;font-size:18px;font-weight:700}.main-upload-card h2{max-width:330px;margin:0;color:#111827;font-size:20px;font-weight:700;letter-spacing:.08em}.main-upload-card p{margin:9px 0 0;color:#9ca3af;font-size:12px}.selected-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.format-pill,.replace-file{margin-top:18px;border:0;border-radius:999px;background:#f3f6fa;color:#8290a4;font-size:11px;font-weight:600}.format-pill{padding:7px 13px}.replace-file{padding:8px 15px;cursor:pointer}.control-bar{width:100%;max-width:640px;padding:10px;border:1px solid rgb(255 255 255 / 60%);border-radius:1.5rem;background:rgb(255 255 255 / 72%);box-shadow:0 10px 40px rgb(0 0 0 / 7%);backdrop-filter:blur(20px)}.file-control{display:flex;align-items:center;gap:12px}.type-selector{position:relative}.file-type-btn{position:relative;width:52px;height:52px;display:grid;place-items:center;flex:0 0 52px;border:0;border-radius:16px;background:#fff;color:#111827;box-shadow:0 5px 14px rgb(0 0 0 / 6%);cursor:pointer}.file-type-btn.open{background:#eff6ff;color:#4a90e2}.type-dot{position:absolute;right:7px;bottom:6px;width:6px;height:6px;border-radius:50%;background:#6497e5}.type-menu{position:absolute;left:0;bottom:64px;z-index:20;width:270px;padding:8px;border:1px solid rgb(255 255 255 / 80%);border-radius:20px;background:rgb(255 255 255 / 94%);box-shadow:0 22px 48px rgb(31 41 55 / 16%);backdrop-filter:blur(20px)}.type-menu button{width:100%;display:flex;align-items:center;gap:12px;padding:11px;border:0;border-radius:14px;background:transparent;text-align:left;cursor:pointer}.type-menu button:hover,.type-menu button.active{background:#f4f7fb}.menu-icon{width:38px;height:38px;display:grid;place-items:center;flex:0 0 38px;border-radius:11px}.menu-icon.bim{background:#eef4ff;color:#5d86d4}.menu-icon.pointcloud{background:#eaf9f4;color:#209273}.type-menu button>span:nth-child(2){min-width:0;flex:1}.type-menu strong,.type-menu small{display:block}.type-menu strong{color:#344054;font-size:13px}.type-menu small{margin-top:3px;color:#a0a9b7;font-size:9px;letter-spacing:.12em}.menu-check{color:#6497e5}.menu-fade-enter-active,.menu-fade-leave-active{transition:.18s}.menu-fade-enter-from,.menu-fade-leave-to{transform:translateY(8px) scale(.97);opacity:0}.guide-text{min-width:0;flex:1}.guide-text span,.guide-text small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.guide-text span{color:#667085;font-size:13px;font-weight:600}.guide-text small{margin-top:4px;color:#b1b8c4;font-size:9px;font-weight:700;letter-spacing:.14em}.clear-btn{width:40px;height:40px;display:grid;place-items:center;border:0;background:transparent;color:#9ca3af;cursor:pointer}.submit-btn{width:54px;height:54px;display:grid;place-items:center;border:0;border-radius:16px;background:#f2f4f7;color:#ababab;cursor:not-allowed}.submit-btn.active,.submit-btn.loading{background:#6497e5;color:#fff;box-shadow:0 12px 24px rgb(37 99 235 / 25%);cursor:pointer}.submit-btn.loading .el-icon{animation:spin 1s linear infinite}.upload-progress{width:100%;max-width:620px;margin-top:18px;color:#8491a4;font-size:12px;text-align:center}.upload-progress span{display:block;margin-top:7px}@keyframes spin{to{transform:rotate(360deg)}}.project-dialog{display:flex;gap:14px;padding:8px 0 4px}.project-dialog-icon{width:42px;height:42px;display:grid;place-items:center;flex:0 0 42px;border-radius:12px;background:#eef5ff;color:#4a90e2}.project-dialog-field{flex:1}.project-dialog-field>span,.project-dialog-field small{display:block}.project-dialog-field>span{margin-bottom:8px;color:#445a78;font-size:13px;font-weight:600}.project-dialog-field small{margin-top:8px;color:#93a0b3;line-height:1.6}@media(max-width:650px){.simple-upload-page{padding:18px 10px}.upload-stack-wrapper{transform:scale(.85);margin-block:-18px 30px}.control-bar{width:100%}}
</style>

<!-- Keep the compact selector and its decorative stack out of document flow. -->
<style scoped>
.simple-upload-page {
  scrollbar-gutter: stable both-edges;
}

.upload-stack-wrapper {
  isolation: isolate;
}

.stack-layer {
  will-change: transform, box-shadow;
  transition: transform 0.7s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.7s ease,
    border-color 0.7s ease;
}

.stack-layer-3 {
  animation: stack-drift-back 6s ease-in-out infinite;
}

.stack-layer-2 {
  animation: stack-drift-middle 6.5s ease-in-out -1.2s infinite;
}

.stack-layer-1 {
  animation: stack-drift-front 7s ease-in-out -2.4s infinite;
}

.upload-stack-wrapper:hover .stack-layer-3 {
  border-color: rgb(129 140 248 / 62%);
  box-shadow: 0 32px 56px -14px rgb(79 70 229 / 22%);
}

.upload-stack-wrapper:hover .stack-layer-2 {
  border-color: rgb(96 165 250 / 72%);
  box-shadow: 0 32px 56px -14px rgb(37 99 235 / 24%);
}

.upload-stack-wrapper:hover .stack-layer-1 {
  border-color: rgb(45 212 191 / 68%);
  box-shadow: 0 32px 56px -14px rgb(13 148 136 / 22%);
}

.main-upload-card {
  will-change: transform, box-shadow;
  animation: upload-card-glow 5.5s ease-in-out infinite;
}

.corner-mark {
  transition: border-color 0.35s ease, filter 0.35s ease, transform 0.35s ease;
}

.corner-tl {
  border-top-color: #4f46e5;
  border-left-color: #4f46e5;
}

.corner-tr {
  border-top-color: #1687d9;
  border-right-color: #1687d9;
}

.corner-bl {
  border-bottom-color: #0f9f80;
  border-left-color: #0f9f80;
}

.corner-br {
  border-right-color: #e08a2e;
  border-bottom-color: #e08a2e;
}

.main-upload-card:hover .corner-tl {
  transform: translate(-2px, -2px);
  filter: drop-shadow(0 0 5px rgb(79 70 229 / 38%));
}

.main-upload-card:hover .corner-tr {
  transform: translate(2px, -2px);
  filter: drop-shadow(0 0 5px rgb(22 135 217 / 38%));
}

.main-upload-card:hover .corner-bl {
  transform: translate(-2px, 2px);
  filter: drop-shadow(0 0 5px rgb(15 159 128 / 38%));
}

.main-upload-card:hover .corner-br {
  transform: translate(2px, 2px);
  filter: drop-shadow(0 0 5px rgb(224 138 46 / 38%));
}

.type-selector {
  width: 50px;
  min-width: 50px;
  flex: 0 0 50px;
}

.file-control {
  min-height: 50px;
}

.guide-text {
  height: 32px;
  contain: layout;
}

.type-menu {
  position: absolute;
  top: calc(100% + 8px);
  bottom: auto;
  width: 286px;
  padding: 7px;
  border-radius: 18px;
  contain: layout paint;
}

.type-menu button {
  min-height: 46px;
  gap: 10px;
  padding: 7px 9px;
  border-radius: 12px;
}

.menu-icon {
  width: 32px;
  height: 32px;
  flex-basis: 32px;
  border-radius: 9px;
}

.type-menu strong {
  font-size: 13px;
}

.type-menu small {
  margin-top: 2px;
  font-size: 8px;
}

.menu-check {
  font-size: 14px;
}

@keyframes stack-drift-back {
  0%, 100% { transform: rotate(12deg) translate(5rem, -2.4rem); }
  50% { transform: rotate(13deg) translate(5.15rem, -2.65rem); }
}

@keyframes stack-drift-middle {
  0%, 100% { transform: rotate(6deg) translate(2.5rem, -1.3rem); }
  50% { transform: rotate(7deg) translate(2.7rem, -1.5rem); }
}

@keyframes stack-drift-front {
  0%, 100% { transform: rotate(-4deg) translate(-2rem, 0.8rem); }
  50% { transform: rotate(-3deg) translate(-1.85rem, 0.65rem); }
}

@keyframes upload-card-glow {
  0%, 100% { box-shadow: 0 25px 50px -12px rgb(0 0 0 / 10%); }
  50% { box-shadow: 0 30px 58px -14px rgb(37 99 235 / 16%); }
}

@media (prefers-reduced-motion: reduce) {
  .stack-layer,
  .main-upload-card {
    animation: none;
    transition: none;
  }
}
</style>

<!-- The dimensions and motion below intentionally mirror xunjian/Inspection.vue. -->
<style scoped>
.simple-upload-page{padding:28px 24px;background:linear-gradient(135deg,#f9fafb 0%,#f1f6ff 100%)}
.upload-stage{justify-content:center}
.upload-stack-wrapper{max-width:42rem;margin-bottom:60px}
.stack-layer{border-radius:3rem;transition:all .7s}
.stack-layer-3{width:400px;height:320px;background:rgb(255 255 255 / 20%);border:1px solid rgb(255 255 255 / 30%);box-shadow:0 25px 50px -12px rgb(0 0 0 / 15%);transform:rotate(12deg) translate(4rem,-2rem);filter:blur(3px)}
.stack-layer-2{width:420px;height:330px;background:rgb(255 255 255 / 40%);border:1px solid rgb(255 255 255 / 40%);box-shadow:0 25px 50px -12px rgb(0 0 0 / 20%);transform:rotate(6deg) translate(2rem,-1rem)}
.stack-layer-1{width:420px;height:330px;background:rgb(255 255 255 / 60%);border:1px solid rgb(255 255 255 / 60%);box-shadow:0 25px 50px -12px rgb(0 0 0 / 20%);transform:rotate(-4deg) translate(-1.5rem,.5rem)}
.upload-stack-wrapper:hover .stack-layer-3{transform:rotate(14deg) translate(4.5rem,-2.5rem)}
.upload-stack-wrapper:hover .stack-layer-2{transform:rotate(8deg) translate(2.5rem,-1.5rem)}
.upload-stack-wrapper:hover .stack-layer-1{transform:rotate(-5deg) translate(-2rem,.75rem)}
.decor-grid{opacity:.05;background-size:20px 20px}
.main-upload-card{width:450px;height:350px;border:1px solid #e5e7eb;border-radius:3rem;box-shadow:0 25px 50px -12px rgb(0 0 0 / 10%);transition:all .5s}
.main-upload-card:hover{transform:scale(1.01);border-color:#dbeafe;box-shadow:0 25px 50px -12px rgb(0 0 0 / 15%)}
.corner-mark{width:1rem;height:1rem;border-color:#f3f4f6}
.corner-tl,.corner-tr{top:2rem}.corner-bl,.corner-br{bottom:2rem}.corner-tl,.corner-bl{left:2rem}.corner-tr,.corner-br{right:2rem}
.upload-icon-wrapper{width:80px;height:80px;margin-bottom:24px;border-radius:24px;background:linear-gradient(135deg,#f9fafb 0%,#f3f4f6 100%);color:#9ca3af;box-shadow:inset 0 0 0 1px #f3f4f6;transition:all .3s}
.main-upload-card:hover .upload-icon-wrapper{transform:translateY(-4px);background:linear-gradient(135deg,#eff6ff 0%,#dbeafe 100%);color:#60a5fa}
.upload-icon-wrapper.has-file{background:#ecf8f2;color:#23a36d}
.plus-badge{right:-2px;bottom:-2px;width:32px;height:32px;border:4px solid #fff;background:#60a5fa;font-size:18px;transition:transform .3s}
.main-upload-card:hover .plus-badge{transform:rotate(90deg)}
.main-upload-card h2{max-width:350px;font-size:20px;letter-spacing:.1em}
.main-upload-card p{margin-top:10px;font-size:12px}
.format-pill,.replace-file{margin-top:20px;padding:8px 16px;background:#f9fafb;font-size:11px;letter-spacing:.05em}
.control-bar{max-width:660px;padding:12px;border:2px solid rgb(255 255 255 / 50%);border-radius:2rem;background:rgb(255 255 255 / 70%);box-shadow:0 10px 15px -3px rgb(0 0 0 / 5%);backdrop-filter:blur(24px)}
.file-control{gap:12px}
.file-type-btn{width:64px;height:64px;flex-basis:64px;border-radius:24px;background:#fff;box-shadow:0 4px 6px -1px rgb(0 0 0 / 5%);transition:all .3s}
.file-type-btn:hover{transform:scale(1.05);background:#f9fafb}
.type-dot{right:8px;bottom:7px;width:7px;height:7px}
.type-menu{left:0;bottom:80px;width:320px;padding:12px;border:1px solid rgb(255 255 255 / 50%);border-radius:24px;background:rgb(255 255 255 / 95%);box-shadow:0 25px 50px -12px rgb(0 0 0 / 20%);backdrop-filter:blur(24px);transform-origin:bottom left}
.type-menu button{gap:12px;padding:12px;border-radius:16px;transition:all .2s}
.type-menu button:hover{background:#f9fafb}.type-menu button.active{background:#eff6ff}
.menu-icon{width:40px;height:40px;flex-basis:40px;border-radius:12px}
.type-menu strong{font-size:14px}.type-menu small{margin-top:4px;font-size:9px}
.menu-fade-enter-active,.menu-fade-leave-active{transition:all .2s ease-out}
.menu-fade-enter-from,.menu-fade-leave-to{transform:translateY(8px) scale(.95);opacity:0}
.guide-text span{font-size:14px}.guide-text small{margin-top:4px;font-size:9px;letter-spacing:.12em}
.clear-btn{width:44px;height:44px;transition:all .2s}.clear-btn:hover{transform:scale(1.08);color:#6b7280}
.submit-btn{width:60px;height:60px;border-radius:20px;background:#f3f4f6;transition:all .5s}
.submit-btn.active,.submit-btn.loading{transform:scale(1);background:#6497e5;box-shadow:0 20px 25px -5px rgb(37 99 235 / 20%);cursor:pointer}
.submit-btn.active:hover{transform:scale(1.05);background:#3b82f6}
.upload-progress{max-width:660px;margin-top:20px}
@media(max-width:650px){.upload-stack-wrapper{transform:scale(.82);margin-block:-28px 24px}.control-bar{width:100%}.type-menu{width:290px}}
</style>

<style scoped>
.stack-layer-3{width:450px;height:360px;transform:rotate(12deg) translate(5rem,-2.4rem)}
.stack-layer-2{width:470px;height:370px;transform:rotate(6deg) translate(2.5rem,-1.3rem)}
.stack-layer-1{width:470px;height:370px;transform:rotate(-4deg) translate(-2rem,.8rem)}
.upload-stack-wrapper:hover .stack-layer-3{transform:rotate(14deg) translate(5.5rem,-2.9rem)}
.upload-stack-wrapper:hover .stack-layer-2{transform:rotate(8deg) translate(3rem,-1.8rem)}
.upload-stack-wrapper:hover .stack-layer-1{transform:rotate(-5deg) translate(-2.5rem,1rem)}
.corner-mark{border-color:#111827}
.corner-tl{border-top-color:#111827;border-left-color:#111827}.corner-tr{border-top-color:#111827;border-right-color:#111827}.corner-bl{border-bottom-color:#111827;border-left-color:#111827}.corner-br{border-right-color:#111827;border-bottom-color:#111827}
.control-bar{padding:8px 10px;border-radius:1.5rem}
.file-control{gap:10px}
.file-type-btn{width:50px;height:50px;flex-basis:50px;border-radius:15px;color:#2563eb;background:#eaf2ff;box-shadow:0 5px 12px rgb(37 99 235 / 16%)}
.file-type-btn :deep(.el-icon){font-size:24px}.file-type-btn.open{color:#fff;background:#4a90e2}.type-dot{right:5px;bottom:5px;width:8px;height:8px;border:2px solid #fff;background:#16a34a}
.submit-btn{width:38px;height:38px;border-radius:15px}.clear-btn{width:36px;height:36px}
.type-menu{top:60px;bottom:auto;left:0;transform-origin:top left}
.menu-fade-enter-from,.menu-fade-leave-to{transform:translateY(-8px) scale(.95);opacity:0}
@media(max-width:650px){.stack-layer-3{width:450px;height:360px}.stack-layer-2,.stack-layer-1{width:470px;height:370px}.type-menu{top:58px;bottom:auto}}
</style>

<style scoped>
/* Final interaction sizing: the menu is overlayed and never changes page height. */
.simple-upload-page{scrollbar-gutter:stable both-edges}
.simple-upload-page{overflow-x:hidden}
.upload-stack-wrapper{isolation:isolate}
.stack-layer{will-change:transform,box-shadow;border-color:rgb(255 255 255 / 42%);transition:transform .7s cubic-bezier(.22,1,.36,1),box-shadow .7s ease,border-color .7s ease}
.stack-layer-3{animation:stack-drift-back 6s ease-in-out infinite}
.stack-layer-2{animation:stack-drift-middle 6.5s ease-in-out -1.2s infinite}
.stack-layer-1{animation:stack-drift-front 7s ease-in-out -2.4s infinite}
.upload-stack-wrapper:hover .stack-layer-3{animation:none;border-color:rgb(129 140 248 / 90%);box-shadow:0 32px 56px -14px rgb(79 70 229 / 22%);transform:rotate(14deg) translate(5.5rem,-2.9rem)}
.upload-stack-wrapper:hover .stack-layer-2{animation:none;border-color:rgb(14 165 233 / 92%);box-shadow:0 32px 56px -14px rgb(37 99 235 / 24%);transform:rotate(8deg) translate(3rem,-1.8rem)}
.upload-stack-wrapper:hover .stack-layer-1{animation:none;border-color:rgb(16 185 129 / 92%);box-shadow:0 32px 56px -14px rgb(13 148 136 / 22%);transform:rotate(-5deg) translate(-2.5rem,1rem)}
.main-upload-card{will-change:transform,box-shadow;animation:upload-card-glow 5.5s ease-in-out infinite}
.corner-mark{transition:border-color .35s ease,filter .35s ease,transform .35s ease}
.corner-tl{border-top-color:#4f46e5;border-left-color:#4f46e5}
.corner-tr{border-top-color:#1687d9;border-right-color:#1687d9}
.corner-bl{border-bottom-color:#0f9f80;border-left-color:#0f9f80}
.corner-br{border-right-color:#e08a2e;border-bottom-color:#e08a2e}
.main-upload-card:hover .corner-tl{border-color:#7c3aed;transform:translate(-2px,-2px);filter:drop-shadow(0 0 5px rgb(124 58 237 / 48%))}
.main-upload-card:hover .corner-tr{border-color:#0ea5e9;transform:translate(2px,-2px);filter:drop-shadow(0 0 5px rgb(14 165 233 / 48%))}
.main-upload-card:hover .corner-bl{border-color:#10b981;transform:translate(-2px,2px);filter:drop-shadow(0 0 5px rgb(16 185 129 / 48%))}
.main-upload-card:hover .corner-br{border-color:#f59e0b;transform:translate(2px,2px);filter:drop-shadow(0 0 5px rgb(245 158 11 / 48%))}
.scan-line{position:absolute;top:0;left:0;width:100%;height:2px;background:linear-gradient(90deg,transparent 0%,rgb(96 165 250 / 25%) 18%,rgb(59 130 246 / 90%) 50%,rgb(96 165 250 / 25%) 82%,transparent 100%);filter:blur(.5px);box-shadow:0 0 14px rgb(59 130 246 / 75%);pointer-events:none;animation:scan-card 3s linear infinite}
.upload-icon-wrapper.has-file{animation:icon-breathe 2.2s ease-in-out infinite}
.upload-icon-wrapper.has-file :deep(.el-icon){animation:icon-breathe-mark 2.2s ease-in-out infinite}
.type-selector{width:50px;min-width:50px;flex:0 0 50px}
.file-control{min-height:50px}
.guide-text{height:32px;contain:layout}
.type-menu{top:calc(100% + 6px);bottom:auto;width:270px;padding:5px;border-radius:15px;contain:layout paint}
.menu-fade-enter-active,.menu-fade-leave-active{transition:opacity .1s ease,transform .1s ease}
.type-menu button{min-height:39px;gap:8px;padding:5px 7px;border-radius:10px}
.menu-icon{width:28px;height:28px;flex-basis:28px;border-radius:8px}
.type-menu strong{font-size:13px}.type-menu small{margin-top:2px;font-size:8px}.menu-check{font-size:14px}
@keyframes stack-drift-back{0%,100%{transform:rotate(12deg) translate(5rem,-2.4rem)}50%{transform:rotate(13deg) translate(5.15rem,-2.65rem)}}
@keyframes stack-drift-middle{0%,100%{transform:rotate(6deg) translate(2.5rem,-1.3rem)}50%{transform:rotate(7deg) translate(2.7rem,-1.5rem)}}
@keyframes stack-drift-front{0%,100%{transform:rotate(-4deg) translate(-2rem,.8rem)}50%{transform:rotate(-3deg) translate(-1.85rem,.65rem)}}
@keyframes upload-card-glow{0%,100%{box-shadow:0 25px 50px -12px rgb(0 0 0 / 10%)}50%{box-shadow:0 30px 58px -14px rgb(37 99 235 / 16%)}}
@keyframes scan-card{0%{top:0;opacity:0}10%{opacity:1}90%{opacity:1}100%{top:100%;opacity:0}}
@keyframes icon-breathe{0%,100%{transform:scale(1);box-shadow:inset 0 0 0 1px #d3f1e1,0 0 0 0 rgb(35 163 109 / 0%)}50%{transform:scale(1.045);box-shadow:inset 0 0 0 1px #b7e8ce,0 0 0 9px rgb(35 163 109 / 0%)}}
@keyframes icon-breathe-mark{0%,100%{transform:scale(1);opacity:.9}50%{transform:scale(1.12);opacity:1}}
@media (prefers-reduced-motion:reduce){.stack-layer,.main-upload-card,.scan-line,.upload-icon-wrapper.has-file,.upload-icon-wrapper.has-file :deep(.el-icon){animation:none;transition:none}}
.locked-type-badge{width:50px;height:50px;display:grid;place-items:center;flex:0 0 50px;border-radius:15px;color:#2563eb;background:#eaf2ff;box-shadow:0 5px 12px rgb(37 99 235 / 16%)}
.locked-type-badge :deep(.el-icon){font-size:22px}
.simple-upload-page.is-compact{min-height:640px;height:auto;padding:20px 18px 24px;}
.simple-upload-page.is-compact .upload-stack-wrapper{margin-bottom:54px;transform:scale(.98);transform-origin:center}
.simple-upload-page.is-compact .control-bar{margin-top:-12px}
@media(max-height:760px){.simple-upload-page.is-compact{min-height:570px}.simple-upload-page.is-compact .upload-stack-wrapper{margin-block:-22px 30px;transform:scale(.84)}.simple-upload-page.is-compact .control-bar{margin-top:-8px}}
.archive-form{box-sizing:border-box;width:100%;max-width:660px;margin:12px auto 0;padding:10px 12px;border:1px solid rgb(215 226 240 / 78%);border-radius:16px;background:rgb(255 255 255 / 70%);box-shadow:inset 1px 1px 1px rgb(255 255 255 / 85%)}
.archive-form-heading{align-items:center;margin-bottom:8px}.archive-form-heading strong{font-size:12px}.archive-form-heading span{display:inline;margin-left:8px;font-size:10px}.archive-form-heading code{padding:5px 9px;font-size:11px}.archive-fields{gap:7px}.archive-fields :deep(.el-select__wrapper),.archive-fields :deep(.el-input__wrapper),.archive-fields :deep(.el-date-editor){min-height:34px;height:34px;border-radius:9px;font-size:11px}.match-status{margin-top:6px;font-size:10px}.simple-upload-page.is-compact .archive-form{margin:10px auto 0}.simple-upload-page.is-compact .upload-stack-wrapper{margin-bottom:42px}
@media(max-width:760px){.archive-form-heading{align-items:flex-start;flex-direction:row}.archive-form-heading span{display:none}.archive-form-heading code{margin-left:auto}.archive-fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
.upload-controls-shell{box-sizing:border-box;width:100%;max-width:660px;border:1px solid rgb(214 226 241 / 82%);border-radius:20px;background:rgb(255 255 255 / 76%);box-shadow:7px 7px 16px rgb(163 177 198 / 15%),-7px -7px 16px rgb(255 255 255 / 78%);overflow:hidden}
.upload-controls-shell .control-bar{box-sizing:border-box;width:100%;max-width:none;padding:8px 10px;border:0;border-radius:0;background:transparent;box-shadow:none;backdrop-filter:none}
.upload-controls-shell .archive-form{width:100%;max-width:none;margin:0;padding:9px 12px 11px;border:0;border-top:1px solid rgb(222 231 244 / 78%);border-radius:0;background:transparent;box-shadow:none}
.upload-controls-shell .archive-form-heading{display:flex;flex-wrap:nowrap;min-width:0;margin-bottom:7px}.upload-controls-shell .archive-form-heading>div{min-width:0;flex:1}.upload-controls-shell .archive-form-heading code{flex:0 0 auto;white-space:nowrap}.upload-controls-shell .archive-fields{display:flex;flex-wrap:nowrap;align-items:flex-start;gap:7px;min-width:0}.upload-controls-shell .archive-fields>*{min-width:0;flex:1 1 0}.upload-controls-shell .archive-field{min-width:0;display:flex;flex:1 1 0;flex-direction:column;gap:4px}.upload-controls-shell .archive-field>span{color:#6f819c;font-size:10px;font-weight:600;line-height:1.2;white-space:nowrap}.upload-controls-shell .archive-field :deep(.el-select),.upload-controls-shell .archive-field :deep(.el-input),.upload-controls-shell .archive-field :deep(.el-date-editor){width:100%;min-width:0}.upload-controls-shell .archive-field :deep(.el-select__wrapper),.upload-controls-shell .archive-field :deep(.el-input__wrapper),.upload-controls-shell .archive-field :deep(.el-date-editor){background:#f7faff}
.simple-upload-page.is-compact .upload-controls-shell{max-width:660px}.simple-upload-page.is-compact .upload-controls-shell .archive-form{margin:0}.simple-upload-page.is-compact .upload-stack-wrapper{margin-bottom:42px}
@media(max-width:760px){.upload-controls-shell .archive-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));}.upload-controls-shell .archive-fields>*{width:100%;flex:none}.upload-controls-shell .archive-form-heading{align-items:center;flex-direction:row}.upload-controls-shell .archive-form-heading span{display:none}}
</style>
