<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Document, Refresh, Search, Upload, View } from '@element-plus/icons-vue'
import { deleteAsset, listAssets, type AssetSummary } from '@/api/backend-file'
import { listProjects, type ProjectSummary } from '@/api/backend-project'
import FileUploadDialog from '@/components/upload/FileUploadDialog.vue'
import NeumorphicPagination from '@/components/NeumorphicPagination.vue'
import type { AuthSession } from '@/features/auth/auth.service'
import { formatFileSize } from '@/features/upload/upload.utils'

type DesignMode = 'bim' | 'cad' | 'overview'

const props = defineProps<{ mode: DesignMode; session: AuthSession; projectId: number; projectName?: string }>()
const router = useRouter()
const loading = ref(false)
const project = ref<ProjectSummary | null>(null)
const assets = ref<AssetSummary[]>([])
const uploadVisible = ref(false)
const bimCurrentPage = ref(1)
const bimPageSize = ref(6)
const bimFilters = reactive({ keyword: '', status: 'all', dateRange: null as [Date, Date] | null })

const readyCadAssets = computed(() => assets.value.filter((asset) => asset.type === 'cad' && asset.status === 'ready'))
const filteredBimAssets = computed(() => {
  const keyword = bimFilters.keyword.trim().toLowerCase()
  return assets.value
    .filter((asset) => {
      if (asset.type !== 'bim') return false
      const createdAt = asset.createdAt * 1000
      const matchesDate = !bimFilters.dateRange
        || (createdAt >= new Date(bimFilters.dateRange[0]).setHours(0, 0, 0, 0)
          && createdAt <= new Date(bimFilters.dateRange[1]).setHours(23, 59, 59, 999))
      return (!keyword || `${asset.sourceName} ${asset.archiveCode || ''}`.toLowerCase().includes(keyword))
        && (bimFilters.status === 'all' || asset.status === bimFilters.status)
        && matchesDate
    })
    .sort((a, b) => b.createdAt - a.createdAt)
})
const pagedBimAssets = computed(() => filteredBimAssets.value.slice(
  (bimCurrentPage.value - 1) * bimPageSize.value,
  bimCurrentPage.value * bimPageSize.value,
))
const bimPageCount = computed(() => Math.max(1, Math.ceil(filteredBimAssets.value.length / bimPageSize.value)))
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

function assetStatusType(status: AssetSummary['status']) {
  return status === 'ready' ? 'success' : status === 'failed' || status === 'terminated' ? 'danger' : 'warning'
}

function uniformizationText(asset: AssetSummary) {
  return ({ idle: '未均匀化', queued: '排队中', processing: '处理中', succeeded: '已均匀化', failed: '处理失败' } as Record<string, string>)[asset.meshRemesh?.status || 'idle'] || '未均匀化'
}

function uniformizationTagType(asset: AssetSummary) {
  const status = asset.meshRemesh?.status
  return status === 'succeeded' ? 'success' : status === 'failed' ? 'danger' : status === 'queued' || status === 'processing' ? 'warning' : 'info'
}

function resetBimFilters() {
  bimFilters.keyword = ''
  bimFilters.status = 'all'
  bimFilters.dateRange = null
  bimCurrentPage.value = 1
}

function handleBimPageChange(page: number) {
  bimCurrentPage.value = page
}

function handleBimPageSizeChange(size: number) {
  bimPageSize.value = size
  bimCurrentPage.value = 1
}

onMounted(() => { void loadData() })
watch([() => filteredBimAssets.value.length, bimPageSize], () => {
  if (bimCurrentPage.value > bimPageCount.value) bimCurrentPage.value = bimPageCount.value
})
watch([() => bimFilters.keyword, () => bimFilters.status, () => bimFilters.dateRange], () => {
  bimCurrentPage.value = 1
})
</script>

<template>
  <section class="design-page" :class="{ 'is-bim-mode': props.mode === 'bim' }" v-loading="loading">
    <template v-if="props.mode === 'bim'">
      <header class="bim-toolbar">
        <div class="bim-filters">
          <el-input v-model="bimFilters.keyword" class="bim-search-input" placeholder="搜索 IFC 文件名或归档编号" :prefix-icon="Search" clearable />
          <el-select v-model="bimFilters.status" class="bim-status-select" placeholder="文件状态">
            <el-option label="文件状态" value="all" />
            <el-option label="上传中" value="uploading" />
            <el-option label="排队中" value="queued" />
            <el-option label="处理中" value="processing" />
            <el-option label="已就绪" value="ready" />
            <el-option label="处理失败" value="failed" />
          </el-select>
          <el-date-picker v-model="bimFilters.dateRange" class="bim-date-range" type="daterange" unlink-panels format="YYYY/MM/DD" range-separator="-" start-placeholder="开始时间" end-placeholder="结束时间" clearable />
        </div>
        <div class="bim-toolbar-actions">
          <button class="bim-toolbar-button is-primary" type="button" @click="uploadVisible = true"><el-icon :size="16"><Upload /></el-icon>上传 IFC</button>
          <button class="bim-toolbar-button" type="button" @click="resetBimFilters"><el-icon :size="16"><Refresh /></el-icon>重置</button>
          <button class="bim-toolbar-button" type="button" @click="loadData"><el-icon :size="16"><Refresh /></el-icon>刷新</button>
        </div>
      </header>
      <section class="bim-table-card">
        <div class="bim-table-heading"><h2>IFC 模型列表</h2></div>
        <el-table class="bim-table" v-loading="loading" :data="pagedBimAssets" row-key="id" empty-text="当前项目暂无 IFC 模型">
          <el-table-column label="归档编号" width="150" align="center"><template #default="{ row }"><strong class="bim-archive-code">{{ row.archiveCode || '未归档' }}</strong></template></el-table-column>
          <el-table-column label="文件名称" min-width="130" align="center"><template #default="{ row }"><div class="bim-name-cell"><span :title="row.sourceName">{{ row.sourceName }}</span></div></template></el-table-column>
          <el-table-column label="文件大小" width="120" align="center"><template #default="{ row }">{{ formatFileSize(row.sourceSize) }}</template></el-table-column>
          <el-table-column label="上传时间" width="180" align="center"><template #default="{ row }">{{ formatDate(row.createdAt) }}</template></el-table-column>
          <el-table-column label="状态" width="110" align="center"><template #default="{ row }"><el-tag size="small" :type="assetStatusType(row.status)">{{ statusText(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="网格均匀化" width="125" align="center"><template #default="{ row }"><el-tag size="small" :type="uniformizationTagType(row)">{{ uniformizationText(row) }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="130" align="center" fixed="right" class-name="bim-operation-column" label-class-name="bim-operation-column"><template #default="{ row }"><div class="bim-row-actions"><button class="bim-action-button" type="button" title="预览 IFC 模型" :disabled="row.status !== 'ready'" @click="preview(row)"><el-icon><View /></el-icon></button><button class="bim-action-button is-delete" type="button" title="删除 IFC 模型" @click="removeModel(row)"><el-icon><Delete /></el-icon></button></div></template></el-table-column>
        </el-table>
      </section>
      <NeumorphicPagination :current-page="bimCurrentPage" :page-size="bimPageSize" :total="filteredBimAssets.length" :page-size-options="[6, 10, 20, 50]" @update:current-page="handleBimPageChange" @update:page-size="handleBimPageSizeChange" />
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

<style scoped>
.design-page { color: var(--text-primary); }
.design-heading h1, .design-toolbar > span, .model-toolbar > div > span { color: var(--text-primary); }
.design-heading h1 { font-size: var(--font-size-lg); }
.design-heading p, .design-project, .design-toolbar small { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.bim-card, .cad-panel, .overview-project, .recent-panel { border-color: var(--border-color-light); border-radius: var(--radius-lg); background: var(--bg-card-translucent); box-shadow: var(--shadow-md); }
.bim-card-copy h2, .overview-project h2 { color: var(--text-primary); }
.bim-card-copy p, .bim-card footer, .overview-project p, .overview-stats span, .recent-row > span:not(.recent-type) { color: var(--text-tertiary); }
.asset-status { color: var(--text-tertiary); background: var(--bg-muted); font-size: var(--font-size-xs); }
.asset-status.is-ready { color: var(--text-success); background: var(--color-success-soft); }
.icon-button { color: var(--text-secondary); background: var(--bg-control); }
.icon-button:hover { color: var(--text-link); background: var(--color-primary-soft); }
.icon-button:disabled { color: var(--text-disabled); }
.design-empty { color: var(--text-tertiary); border-color: var(--border-color-hover); background: var(--bg-card-translucent); }
.design-empty span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.cad-preview { color: var(--text-secondary); background: var(--bg-control); }
.cad-preview span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.recent-row { border-bottom-color: var(--border-color-light); }
</style>

<style scoped>
.bim-toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--spacing-md); margin-bottom: var(--spacing-md); }
.design-page.is-bim-mode { height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.bim-filters, .bim-toolbar-actions { display: flex; align-items: center; gap: var(--spacing-sm); }
.bim-filters { flex: 1; min-width: 0; }
.bim-search-input { flex: 1 1 210px; min-width: 170px; max-width: 230px; }
.bim-status-select { width: 132px; }
.bim-date-range { flex: 0 0 210px !important; width: 210px !important; min-width: 210px !important; max-width: 210px !important; }
.bim-toolbar :deep(.el-input__wrapper), .bim-toolbar :deep(.el-select__wrapper), .bim-toolbar :deep(.el-date-editor.el-input__wrapper) { height: 40px; min-height: 40px; padding-inline: 10px; border: 1px solid rgb(255 255 255 / 70%); border-radius: var(--radius-lg); background: var(--bg-filter-control); box-shadow: var(--shadow-filter-control) !important; }
.bim-toolbar :deep(.el-input__inner), .bim-toolbar :deep(.el-select__selected-item), .bim-toolbar :deep(.el-range-input), .bim-toolbar :deep(.el-range-separator) { color: var(--text-secondary); font-size: var(--font-size-sm); font-weight: 600; }
.bim-toolbar :deep(.el-input__inner::placeholder), .bim-toolbar :deep(.el-range-input::placeholder) { color: var(--text-placeholder); }
.bim-toolbar :deep(.el-input__icon), .bim-toolbar :deep(.el-range__icon), .bim-toolbar :deep(.el-range__close-icon) { color: var(--text-tertiary); }
.bim-toolbar :deep(.bim-date-range.el-date-editor.el-input__wrapper), .bim-toolbar :deep(.bim-date-range.el-range-editor.el-input__wrapper) { flex: 0 0 210px !important; width: 210px !important; min-width: 210px !important; max-width: 210px !important; padding-inline: 10px; box-sizing: border-box; }
.bim-toolbar :deep(.el-date-editor.el-input__wrapper.is-focus), .bim-toolbar :deep(.el-date-editor.el-input__wrapper:focus-within) { border-color: rgb(255 255 255 / 70%) !important; box-shadow: var(--shadow-filter-control) !important; }
.bim-toolbar-actions { flex: 0 0 auto; }
.bim-toolbar-button { height: 40px; min-height: 40px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 var(--spacing-md); border: 0; border-radius: 14px; color: var(--text-secondary); background: var(--bg-control); box-shadow: var(--shadow-filter-control); font: inherit; font-size: var(--font-size-sm); cursor: pointer; transition: color var(--transition-fast), background-color var(--transition-fast), transform var(--transition-fast); white-space: nowrap; }
.bim-toolbar-button:hover { color: var(--text-link); background: var(--color-primary-soft); transform: translateY(-1px); }
.bim-toolbar-button.is-primary { color: var(--bg-card); background: var(--color-primary); }
.bim-toolbar-button.is-primary:hover { color: var(--bg-card); background: var(--color-primary-hover); }
.bim-table-card { min-width: 0; min-height: 0; display: flex; flex: 1 1 auto; flex-direction: column; padding: var(--card-padding); border: var(--border-width) solid var(--border-color-light); border-radius: var(--radius-lg); background: var(--bg-card-translucent); box-shadow: var(--shadow-md); overflow: hidden; }
.bim-table-heading { flex: 0 0 auto; display: flex; align-items: baseline; justify-content: space-between; margin-bottom: var(--spacing-md); }
.bim-table-heading h2 { margin: 0; color: var(--text-primary); font-size: var(--font-size-lg); font-weight: 650; }
.bim-table-heading small { margin-left: var(--spacing-sm); color: var(--text-tertiary); font-size: var(--font-size-xs); }
.bim-table { min-width: 0; min-height: 0; flex: 1 1 auto; width: 100%; border-radius: var(--radius-md); overflow: hidden; }
.bim-table :deep(.el-table__inner-wrapper::before) { display: none; }
.bim-table :deep(.el-table__header-wrapper th.el-table__cell) { height: 52px; border: 0; background: var(--bg-control); color: var(--text-secondary); font-size: var(--font-size-sm); font-weight: 600; }
.bim-table :deep(.el-table__header-wrapper th .cell), .bim-table :deep(.el-table__body td .cell) { text-align: center; }
.bim-table :deep(.el-table__body tr) { height: 64px; background: var(--bg-card); }
.bim-table :deep(.el-table__body td.el-table__cell) { padding: 0 12px; border-top: var(--border-width) solid var(--border-color-light); border-bottom: var(--border-width) solid var(--border-color-light); background: var(--bg-card); color: var(--text-secondary); font-size: var(--font-size-sm); }
.bim-table :deep(.el-table__body tr:hover > td.el-table__cell) { background: var(--color-primary-soft); }
.bim-table :deep(.bim-operation-column) { background: var(--bg-card) !important; }
.bim-table :deep(.el-table__header-wrapper .bim-operation-column) { background: var(--bg-control) !important; }
.bim-name-cell { min-width: 0; display: flex; align-items: center; justify-content: center; }
.bim-name-cell > span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary); }
.bim-archive-code { color: var(--text-link); font-size: var(--font-size-xs); }
.bim-row-actions { display: flex; align-items: center; justify-content: center; gap: var(--spacing-sm); }
.bim-action-button { width: 34px; height: 34px; display: grid; place-items: center; padding: 0; border: 0; border-radius: var(--radius-sm); color: var(--text-secondary); background: var(--bg-control); box-shadow: var(--shadow-sm); cursor: pointer; transition: color var(--transition-fast), background-color var(--transition-fast), transform var(--transition-fast); }
.bim-action-button:hover { color: var(--text-link); background: var(--color-primary-soft); transform: translateY(-1px); }
.bim-action-button.is-delete:hover { color: var(--text-danger); background: var(--color-danger-soft); }
.bim-action-button:disabled { color: var(--text-disabled); background: var(--bg-muted); cursor: not-allowed; transform: none; }
@media (max-width: 900px) { .bim-toolbar { display: block; } .bim-filters { flex-wrap: wrap; } .bim-search-input { max-width: none; } .bim-toolbar-actions { margin-top: var(--spacing-sm); justify-content: flex-end; } }
@media (max-width: 620px) { .bim-date-range { flex: 1 1 100% !important; width: 100% !important; min-width: 0 !important; max-width: none !important; } .bim-status-select { flex: 1; } .bim-toolbar-actions { flex-wrap: wrap; } .bim-table-card { padding: var(--spacing-md); } }
</style>
