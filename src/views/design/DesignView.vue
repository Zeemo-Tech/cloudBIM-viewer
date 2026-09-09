<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Box, DataBoard, Delete, Document, Folder, Upload, View } from '@element-plus/icons-vue'
import { deleteAsset, listAssets, type AssetSummary } from '@/api/backend-file'
import { listProjects, type ProjectSummary } from '@/api/backend-project'
import FileUploadDialog from '@/components/upload/FileUploadDialog.vue'
import type { AuthSession } from '@/features/auth/auth.service'
import { formatFileSize } from '@/features/upload/upload.utils'

type DesignMode = 'bim' | 'cad' | 'overview'

const props = defineProps<{ mode: DesignMode; session: AuthSession; projectId: number; projectName?: string }>()
const router = useRouter()
const loading = ref(false)
const project = ref<ProjectSummary | null>(null)
const assets = ref<AssetSummary[]>([])
const uploadVisible = ref(false)

const modeMeta = computed(() => ({
  bim: { title: 'IFC 模型', subtitle: '浏览当前项目的 IFC 模型', icon: Box },
  cad: { title: 'CAD 图纸', subtitle: '管理当前项目的 CAD 图纸资料', icon: Document },
  overview: { title: '项目概览', subtitle: '查看当前项目的整体进度和资产概况', icon: DataBoard },
}[props.mode]))

const readyBimAssets = computed(() => assets.value.filter((asset) => asset.type === 'bim' && asset.status === 'ready'))
const readyCadAssets = computed(() => assets.value.filter((asset) => asset.type === 'cad' && asset.status === 'ready'))
const recentAssets = computed(() => [...assets.value].sort((a, b) => b.createdAt - a.createdAt).slice(0, 5))

async function loadData() {
  loading.value = true
  try {
    const [projectResponse, assetsResponse] = await Promise.all([
      listProjects(),
      listAssets({ projectId: props.projectId, page: 1, pageSize: 500 }),
    ])
    project.value = projectResponse.data?.list?.find((item) => item.id === props.projectId) || null
    assets.value = assetsResponse.data?.list || []
  } finally {
    loading.value = false
  }
}

function preview(asset: AssetSummary) {
  if (asset.status !== 'ready') return
  void router.push({ path: '/preview/asset', query: { projectId: props.projectId, projectName: props.projectName, previewType: asset.type, assetId: asset.id, displayName: asset.sourceName } })
}

async function removeModel(asset: AssetSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除 IFC 模型“${asset.sourceName}”吗？相关预览、配准和分析结果也会被删除。`,
      '删除 IFC 模型',
      { type: 'warning' },
    )
    await deleteAsset(asset.id)
    ElMessage.success('IFC 模型已删除')
    await loadData()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error instanceof Error ? error.message : '删除 IFC 模型失败')
  }
}

function statusText(status: AssetSummary['status']) {
  return ({ uploading: '上传中', queued: '排队中', processing: '处理中', ready: '已就绪', failed: '处理失败', terminated: '已终止' } as Record<string, string>)[status] || status
}

function formatDate(value?: number) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '-'
}

onMounted(() => { void loadData() })
</script>

<template>
  <section class="design-page" v-loading="loading">
    <header class="design-header">
      <div class="design-heading"><span class="design-icon"><el-icon><component :is="modeMeta.icon" /></el-icon></span><div><h1>{{ modeMeta.title }}</h1><p>{{ modeMeta.subtitle }}</p></div></div>
      <span class="design-project"><el-icon><Folder /></el-icon>{{ props.projectName || `项目 ${props.projectId}` }}</span>
    </header>

    <template v-if="props.mode === 'bim'">
      <div class="design-toolbar model-toolbar"><div><span>模型列表</span><small>{{ readyBimAssets.length }} 个可预览模型</small></div><button class="upload-model-button" type="button" @click="uploadVisible = true"><el-icon><Upload /></el-icon><span>上传 IFC</span></button></div>
      <div v-if="readyBimAssets.length" class="bim-grid">
        <article v-for="asset in readyBimAssets" :key="asset.id" class="bim-card">
          <div class="bim-card-icon"><el-icon><Box /></el-icon></div><div class="bim-card-copy"><h2 :title="asset.sourceName">{{ asset.sourceName }}</h2><p>{{ formatFileSize(asset.sourceSize) }} · {{ formatDate(asset.createdAt) }}</p></div><div class="bim-card-actions"><button class="icon-button" type="button" title="预览 IFC 模型" :disabled="asset.status !== 'ready'" @click="preview(asset)"><el-icon><View /></el-icon></button><button class="icon-button is-delete" type="button" title="删除 IFC 模型" @click="removeModel(asset)"><el-icon><Delete /></el-icon></button></div>
          <footer><span class="asset-status is-ready">{{ statusText(asset.status) }}</span><span>IFC / BIM 模型</span></footer>
        </article>
      </div>
      <div v-else class="design-empty"><el-icon><Box /></el-icon><strong>当前项目暂无 IFC 模型</strong><span>点击上方“上传 IFC”添加模型文件</span></div>
      <FileUploadDialog
        v-model="uploadVisible"
        :session="session"
        :project-id="projectId"
        :project-name="projectName"
        :allowed-kinds="['bim']"
        title="上传 IFC 模型"
        @uploaded="loadData"
      />
    </template>

    <template v-else-if="props.mode === 'cad'">
      <div class="design-toolbar model-toolbar"><div><span>图纸档案</span><small>{{ readyCadAssets.length }} 份设计图纸</small></div><button class="upload-model-button" type="button" @click="uploadVisible = true"><el-icon><Upload /></el-icon><span>上传 CAD</span></button></div>
      <div v-if="readyCadAssets.length" class="bim-grid"><article v-for="asset in readyCadAssets" :key="asset.id" class="bim-card"><div class="bim-card-icon"><el-icon><Document /></el-icon></div><div class="bim-card-copy"><h2 :title="asset.sourceName">{{ asset.sourceName }}</h2><p>{{ asset.building }} · {{ asset.archiveCode }} · {{ formatFileSize(asset.sourceSize) }}</p></div><footer><span class="asset-status is-ready">{{ statusText(asset.status) }}</span><span>{{ ({ YKT: '预制空调板 YKT', YTY: '预制空调板 YTY', PCLT: '预制楼梯 PCLT', DLB: '叠合板 DLB', YB: '叠合板 YB' } as Record<string, string>)[asset.componentType || ''] || asset.componentType }}</span></footer></article></div>
      <div v-else class="design-empty"><el-icon><Document /></el-icon><strong>当前项目暂无 CAD 图纸</strong><span>点击上方“上传 CAD”建立设计归档</span></div>
      <FileUploadDialog v-model="uploadVisible" :session="session" :project-id="projectId" :project-name="projectName" :allowed-kinds="['cad']" title="上传 CAD 设计图纸" @uploaded="loadData" />
    </template>

    <template v-else>
      <div class="overview-project"><div><span class="eyebrow">当前项目</span><h2>{{ project?.name || props.projectName || `项目 ${props.projectId}` }}</h2><p>{{ project?.description || '暂无项目描述' }}</p></div><span class="overview-status">{{ project?.status === 'ready' ? '已就绪' : project?.status === 'processing' ? '处理中' : '待上传' }}</span></div>
      <div class="overview-stats"><div><span>资产总数</span><strong>{{ project?.assetCount ?? assets.length }}</strong></div><div><span>BIM 模型</span><strong>{{ project?.bimCount ?? assets.filter((asset) => asset.type === 'bim').length }}</strong></div><div><span>点云文件</span><strong>{{ project?.pointcloudCount ?? assets.filter((asset) => asset.type === 'pointcloud').length }}</strong></div><div><span>配准状态</span><strong>{{ project?.hasAlignment ? '已配准' : '未配准' }}</strong></div></div>
      <section class="recent-panel"><div class="design-toolbar"><span>最近资产</span><small>{{ recentAssets.length }} 个文件</small></div><div v-if="recentAssets.length" class="recent-list"><div v-for="asset in recentAssets" :key="asset.id" class="recent-row"><span class="recent-type" :class="asset.type">{{ asset.type === 'bim' ? 'BIM' : '点云' }}</span><strong :title="asset.sourceName">{{ asset.sourceName }}</strong><span>{{ formatFileSize(asset.sourceSize) }}</span><span class="asset-status" :class="asset.status === 'ready' ? 'is-ready' : ''">{{ statusText(asset.status) }}</span><button class="icon-button" type="button" title="预览文件" :disabled="asset.status !== 'ready'" @click="preview(asset)"><el-icon><View /></el-icon></button></div></div><div v-else class="design-empty compact"><span>当前项目暂无资产</span></div></section>
    </template>
  </section>
</template>

<style scoped>
.design-page{box-sizing:border-box;min-height:100%;padding:4px;color:#233a5a}.design-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.design-heading{display:flex;align-items:center;gap:12px}.design-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:13px;color:#4b7fd8;background:#edf4ff;box-shadow:inset 1px 1px 1px #fff,4px 4px 10px rgb(169 184 210 / 16%)}.design-heading h1{margin:0;font-size:20px;font-weight:650;color:#263b59}.design-heading p{margin:4px 0 0;color:#8a9ab0;font-size:12px}.design-project{display:inline-flex;align-items:center;gap:6px;color:#7b8da7;font-size:12px}.design-toolbar{display:flex;align-items:baseline;gap:10px;margin-bottom:14px}.design-toolbar>span{font-size:16px;font-weight:650;color:#2d486d}.design-toolbar small{color:#9aa9bc;font-size:12px}.bim-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}.bim-card,.cad-panel,.overview-project,.recent-panel{border:1px solid rgb(214 223 236 / 76%);border-radius:18px;background:rgb(255 255 255 / 88%);box-shadow:7px 7px 16px rgb(163 177 198 / 18%),-7px -7px 16px rgb(255 255 255 / 82%)}.bim-card{position:relative;min-height:160px;padding:18px}.bim-card-icon{width:40px;height:40px;display:grid;place-items:center;border-radius:12px;color:#5d83ce;background:#edf3ff;font-size:20px}.bim-card-copy{margin-top:14px;padding-right:36px}.bim-card-copy h2{margin:0;overflow:hidden;color:#2c476c;font-size:15px;text-overflow:ellipsis;white-space:nowrap}.bim-card-copy p{margin:6px 0 0;color:#96a3b5;font-size:11px}.bim-card footer{display:flex;align-items:center;gap:10px;margin-top:20px;color:#9aa8b9;font-size:11px}.asset-status{padding:4px 8px;border-radius:8px;background:#f1f4f8;color:#7689a3;font-size:10px}.asset-status.is-ready{color:#218462;background:#e9f8f1}.icon-button{width:32px;height:32px;display:grid;place-items:center;border:0;border-radius:10px;color:#7185a3;background:#f5f8fd;box-shadow:3px 3px 7px rgb(169 184 210 / 16%),-3px -3px 7px rgb(255 255 255 / 88%);cursor:pointer}.icon-button:hover{color:#4a90e2;background:#eef5ff}.icon-button:disabled{color:#b8c0cc;background:#f2f4f7;cursor:not-allowed}.bim-card>.icon-button{position:absolute;top:18px;right:18px}.design-empty{min-height:260px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;border:1px dashed #bfd0e7;border-radius:18px;color:#7c91ae;background:rgb(255 255 255 / 56%)}.design-empty .el-icon{font-size:30px}.design-empty span{color:#9aa8ba;font-size:12px}.design-empty.compact{min-height:140px}.cad-panel{padding:18px}.cad-preview{display:flex;align-items:center;gap:10px;padding:18px;border-radius:14px;color:#6380ab;background:#f3f7fd}.cad-preview .el-icon{font-size:24px}.cad-preview strong{font-size:14px}.cad-preview span{margin-left:auto;color:#91a0b4;font-size:12px}.cad-panel>.design-empty{margin-top:16px}.overview-project{display:flex;align-items:flex-start;justify-content:space-between;padding:22px;margin-bottom:16px}.eyebrow{color:#92a2b6;font-size:11px}.overview-project h2{margin:8px 0 6px;color:#2c476c;font-size:20px}.overview-project p{margin:0;color:#91a0b4;font-size:12px}.overview-status{padding:5px 10px;border-radius:9px;color:#218462;background:#e9f8f1;font-size:11px}.overview-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}.overview-stats>div{padding:18px;border-radius:14px;background:#f3f7fd;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 90%),inset -2px -2px 5px rgb(180 200 230 / 12%)}.overview-stats span,.overview-stats strong{display:block}.overview-stats span{color:#91a0b4;font-size:11px}.overview-stats strong{margin-top:8px;color:#52749e;font-size:22px}.recent-panel{padding:18px}.recent-list{display:flex;flex-direction:column;gap:2px}.recent-row{display:grid;grid-template-columns:48px minmax(0,1fr) 100px 72px 34px;align-items:center;gap:10px;min-height:50px;padding:6px 0;border-bottom:1px solid #edf1f6}.recent-row:last-child{border-bottom:0}.recent-row strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#4a6381;font-size:12px}.recent-row>span:not(.recent-type){color:#98a5b6;font-size:11px}.recent-type{display:inline-flex;align-items:center;justify-content:center;width:38px;height:24px;border-radius:7px;background:#eef4ff;color:#4f78bf;font-size:10px;font-weight:700}.recent-type.pointcloud{background:#eaf9f4;color:#16866a}.recent-row .icon-button{justify-self:end}@media(max-width:760px){.design-header{align-items:flex-start;gap:12px}.design-project{display:none}.overview-stats{grid-template-columns:repeat(2,1fr)}.cad-preview{align-items:flex-start;flex-wrap:wrap}.cad-preview span{width:100%;margin-left:34px}.recent-row{grid-template-columns:46px minmax(0,1fr) 34px}.recent-row>span:not(.recent-type):not(.asset-status){display:none}}@media(max-width:520px){.bim-grid{grid-template-columns:1fr}}
.model-toolbar{align-items:center;justify-content:space-between}.model-toolbar>div{display:flex;align-items:baseline;gap:10px}.model-toolbar>div>span{color:#2d486d;font-size:16px;font-weight:650}.upload-model-button{height:36px;display:inline-flex;align-items:center;gap:7px;padding:0 14px;border:1px solid rgb(214 223 236 / 72%);border-radius:11px;color:#5278b6;background:#f5f8fd;box-shadow:4px 4px 9px rgb(169 184 210 / 18%),-4px -4px 9px rgb(255 255 255 / 92%);font-size:12px;cursor:pointer;transition:.2s}.upload-model-button:hover{color:#3674d0;background:#eef5ff;transform:translateY(-1px)}
.bim-card-actions{position:absolute;top:18px;right:18px;display:flex;align-items:center;gap:7px}.bim-card-actions .is-delete:hover{color:#df5e67;background:#fff1f2}
</style>
