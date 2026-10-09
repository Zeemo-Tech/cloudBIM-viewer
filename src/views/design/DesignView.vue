<script setup lang="ts">
import { readListState, writeListState } from '@/features/workspace/listState'
import WorkspaceHeading from '@/components/layout/WorkspaceHeading.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Refresh, Search, Upload, View } from '@element-plus/icons-vue'
import { deleteAsset, formatFileSize, listAssets, type AssetSummary } from '@cloudbim/viewer-core'
import { listProjects, type ProjectSummary } from '@/api/backend-project'
import FileUploadDialog from '@/components/upload/FileUploadDialog.vue'
import NeumorphicPagination from '@/components/NeumorphicPagination.vue'
import type { AuthSession } from '@/features/auth/auth.service'

type DesignMode = 'bim' | 'cad' | 'overview'

const props = defineProps<{ mode: DesignMode; session: AuthSession; projectId: number; projectName?: string }>()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const project = ref<ProjectSummary | null>(null)
const assets = ref<AssetSummary[]>([])
const uploadVisible = ref(false)
const fileKind = computed(() => props.mode === 'cad' ? 'cad' : 'bim')
const pageTitle = computed(() => props.mode === 'cad' ? 'CAD图纸' : '设计模型')
const selectedCad = ref<AssetSummary | null>(null)
const cadDetailsVisible = ref(false)
const listStateKey = `cloudbim.list.v1:${props.session.username}:design:${props.mode}:${props.projectId}`
const restoredList = readListState(listStateKey, { keyword: '', componentType: 'all', status: 'all', dateRange: null as [Date, Date] | null }, [10, 20, 50])
const bimCurrentPage = ref(restoredList.page)
const bimPageSize = ref(restoredList.pageSize)
const bimFilters = reactive(restoredList.filters)

const filteredBimAssets = computed(() => {
  const keyword = bimFilters.keyword.trim().toLowerCase()
  return assets.value
    .filter((asset) => {
      if (asset.type !== fileKind.value) return false
      const createdAt = asset.createdAt * 1000
      const matchesDate = !bimFilters.dateRange
        || (createdAt >= new Date(bimFilters.dateRange[0]).setHours(0, 0, 0, 0)
          && createdAt <= new Date(bimFilters.dateRange[1]).setHours(23, 59, 59, 999))
      return (!keyword || `${asset.sourceName} ${asset.archiveCode || ''}`.toLowerCase().includes(keyword))
        && (bimFilters.componentType === 'all' || asset.componentType === bimFilters.componentType)
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
const hasBimAssets = computed(() => assets.value.some((asset) => asset.type === fileKind.value))
const hasBimFilters = computed(() => Boolean(
  bimFilters.keyword.trim()
  || bimFilters.componentType !== 'all'
  || bimFilters.status !== 'all'
  || bimFilters.dateRange,
))
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
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载文件列表失败')
  } finally {
    loading.value = false
  }
}

function preview(asset: AssetSummary) {
  if (asset.type === 'cad') { selectedCad.value = asset; cadDetailsVisible.value = true; return }
  if (asset.status !== 'ready') return
  void router.push({ path: '/preview/asset', query: { projectId: props.projectId, projectName: props.projectName, returnTo: route.fullPath, previewType: asset.type, assetId: asset.id, displayName: asset.sourceName } })
}

async function removeModel(asset: AssetSummary) {
  try {
    await ElMessageBox.confirm(
      asset.type === 'cad' ? `确定删除 CAD图纸“${asset.sourceName}”吗？` : `确定删除设计模型“${asset.sourceName}”吗？相关预览、配准和分析结果也会被删除。`,
      `删除${pageTitle.value}`,
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await deleteAsset(asset.id)
    ElMessage.success(`${pageTitle.value}已删除`)
    await loadData()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error instanceof Error ? error.message : '删除文件失败')
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
  bimFilters.componentType = 'all'
  bimFilters.dateRange = null
  bimCurrentPage.value = 1
}

function handleBimEmptyAction() {
  if (hasBimFilters.value) resetBimFilters()
  else uploadVisible.value = true
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
watch([() => bimFilters.keyword, () => bimFilters.componentType, () => bimFilters.status, () => bimFilters.dateRange], () => {
  bimCurrentPage.value = 1
})
watch([bimFilters, bimCurrentPage, bimPageSize], () => {
  writeListState(listStateKey, { filters: bimFilters, page: bimCurrentPage.value, pageSize: bimPageSize.value })
}, { deep: true, flush: 'sync' })
</script>

<template>
  <section class="design-page workspace-list" :class="{ 'is-bim-mode': props.mode === 'bim' }" v-loading="loading">
    <template v-if="props.mode !== 'overview'">
      <WorkspaceHeading :title="pageTitle" :description="fileKind === 'cad' ? '管理设计图纸与归档信息。' : '管理设计模型与归档信息，为扫描点云关联提供依据。'" />
      <header class="bim-toolbar workspace-toolbar">
        <div class="bim-filters workspace-filters">
          <el-input v-model="bimFilters.keyword" class="bim-search-input workspace-search" placeholder="搜索归档编号或文件名称" :prefix-icon="Search" clearable />
          <el-select v-model="bimFilters.componentType" class="workspace-select" placeholder="构件类型">
            <el-option label="构件类型" value="all" /><el-option label="预制空调板 YKT" value="YKT" /><el-option label="预制空调板 YTY" value="YTY" /><el-option label="预制楼梯 PCLT" value="PCLT" /><el-option label="叠合板 DLB" value="DLB" /><el-option label="叠合板 YB" value="YB" />
          </el-select>
          <el-select v-model="bimFilters.status" class="bim-status-select workspace-select" placeholder="文件状态">
            <el-option label="文件状态" value="all" />
            <el-option label="上传中" value="uploading" />
            <el-option label="排队中" value="queued" />
            <el-option label="处理中" value="processing" />
            <el-option label="已就绪" value="ready" />
            <el-option label="处理失败" value="failed" /><el-option label="已终止" value="terminated" />
          </el-select>
          <el-date-picker v-model="bimFilters.dateRange" class="bim-date-range workspace-date" type="daterange" unlink-panels format="YYYY/MM/DD" range-separator="-" start-placeholder="开始日期" end-placeholder="结束日期" clearable />
        </div>
        <div class="bim-toolbar-actions workspace-actions">
          <button class="bim-toolbar-button is-primary" type="button" @click="uploadVisible = true"><el-icon :size="16"><Upload /></el-icon>{{ fileKind === 'cad' ? '上传图纸' : '上传模型' }}</button>
          <button class="bim-toolbar-button" type="button" @click="resetBimFilters"><el-icon :size="16"><Refresh /></el-icon>重置</button>
          <button class="bim-toolbar-button" type="button" @click="loadData"><el-icon :size="16"><Refresh /></el-icon>刷新</button>
        </div>
      </header>
      <section class="bim-table-card workspace-table-card">
        <div class="bim-table-heading"><h2>文件列表</h2></div>
        <el-table class="bim-table workspace-table" :fit="true" v-loading="loading" :data="pagedBimAssets" row-key="id" tabindex="0" :aria-label="`${pageTitle}文件列表，可横向滚动`">
          <el-table-column label="归档编号" min-width="104" align="left"><template #default="{ row }"><strong class="bim-archive-code">{{ row.archiveCode || '未归档' }}</strong><small class="archive-metadata">{{ row.building || '—' }} · {{ row.floor || '—' }} · {{ row.componentType || '—' }}</small></template></el-table-column>
          <el-table-column label="文件名称" width="200" align="left"><template #default="{ row }"><div class="bim-name-cell"><span :title="row.sourceName">{{ row.sourceName }}</span></div></template></el-table-column>

          <el-table-column label="文件大小" min-width="84" align="left"><template #default="{ row }">{{ formatFileSize(row.sourceSize) }}</template></el-table-column>
          <el-table-column label="上传时间" min-width="150" align="left"><template #default="{ row }">{{ formatDate(row.createdAt) }}</template></el-table-column>
          <el-table-column label="状态" min-width="84" align="left"><template #default="{ row }"><el-tag size="small" :type="assetStatusType(row.status)">{{ statusText(row.status) }}</el-tag></template></el-table-column>
          <el-table-column v-if="fileKind === 'bim'" label="网格均匀化" min-width="108" align="left"><template #default="{ row }"><el-tag size="small" :type="uniformizationTagType(row)">{{ uniformizationText(row) }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="172" align="right" class-name="bim-operation-column" label-class-name="bim-operation-column"><template #default="{ row }"><div class="bim-row-actions workspace-row-actions"><button class="bim-action-button bim-action-button--primary" type="button" :title="fileKind === 'cad' ? '查看详情' : '预览模型'" :aria-label="fileKind === 'cad' ? '查看详情' : '预览模型'" :disabled="fileKind === 'bim' && row.status !== 'ready'" @click="preview(row)"><el-icon><View /></el-icon><span>{{ fileKind === 'cad' ? '查看详情' : '预览模型' }}</span></button><button class="bim-action-button is-delete" type="button" :title="`删除${pageTitle}`" :aria-label="`删除${pageTitle}`" @click="removeModel(row)"><el-icon><Delete /></el-icon></button></div></template></el-table-column>
          <template #empty>
            <div class="bim-table-empty">
              <strong>{{ hasBimAssets ? `没有匹配的${pageTitle}` : `当前项目暂无${pageTitle}` }}</strong>
              <span>{{ hasBimAssets ? '当前筛选条件下没有结果' : '上传文件后可在此查看和管理' }}</span>
              <button type="button" @click="handleBimEmptyAction">{{ hasBimFilters ? '清除筛选' : fileKind === 'cad' ? '上传图纸' : '上传模型' }}</button>
            </div>
          </template>
        </el-table>
      </section>
      <NeumorphicPagination :current-page="bimCurrentPage" :page-size="bimPageSize" :total="filteredBimAssets.length" :page-size-options="[10, 20, 50]" :aria-label="`${pageTitle}分页`" @update:current-page="handleBimPageChange" @update:page-size="handleBimPageSizeChange" />
      <FileUploadDialog
        v-model="uploadVisible"
        :session="session"
        :project-id="projectId"
        :project-name="projectName"
        :allowed-kinds="[fileKind]"
        :title="`上传${pageTitle}`"
        @uploaded="loadData"
      />
    </template>

    <template v-else>
      <WorkspaceHeading title="项目概述" description="查看项目基本信息、资产与配准状态。" />
      <div class="overview-project"><div><h2>{{ project?.name || props.projectName || `项目 ${props.projectId}` }}</h2><p>{{ project?.description || '暂无项目描述' }}</p></div><span class="overview-status">{{ project?.status === 'ready' ? '已就绪' : project?.status === 'processing' ? '处理中' : '待上传' }}</span></div>
      <div class="overview-stats"><div><span>资产总数</span><strong>{{ project?.assetCount ?? assets.length }}</strong></div><div><span>BIM 模型</span><strong>{{ project?.bimCount ?? assets.filter((asset) => asset.type === 'bim').length }}</strong></div><div><span>点云文件</span><strong>{{ project?.pointcloudCount ?? assets.filter((asset) => asset.type === 'pointcloud').length }}</strong></div><div><span>配准状态</span><strong>{{ project?.hasAlignment ? '已配准' : '未配准' }}</strong></div></div>
      <section class="recent-panel"><div class="design-toolbar"><span>最近资产</span><small>{{ recentAssets.length }} 个文件</small></div><div v-if="recentAssets.length" class="recent-list"><div v-for="asset in recentAssets" :key="asset.id" class="recent-row"><span class="recent-type" :class="asset.type">{{ asset.type === 'bim' ? 'IFC' : asset.type === 'cad' ? 'CAD' : '点云' }}</span><strong :title="asset.sourceName">{{ asset.sourceName }}</strong><span>{{ formatFileSize(asset.sourceSize) }}</span><span class="asset-status" :class="asset.status === 'ready' ? 'is-ready' : ''">{{ statusText(asset.status) }}</span><button class="icon-button" type="button" title="预览文件" :disabled="asset.status !== 'ready'" @click="preview(asset)"><el-icon><View /></el-icon></button></div></div><div v-else class="design-empty compact"><span>当前项目暂无资产</span></div></section>
    </template>
    <el-dialog v-model="cadDetailsVisible" title="CAD图纸详情" width="min(600px, calc(100vw - 32px))">
      <el-descriptions v-if="selectedCad" :column="1" border>
        <el-descriptions-item label="文件名称"><span class="cad-detail-name">{{ selectedCad.sourceName }}</span></el-descriptions-item>
        <el-descriptions-item label="归档编号">{{ selectedCad.archiveCode || '未归档' }}</el-descriptions-item>
        <el-descriptions-item label="楼栋 / 楼层">{{ selectedCad.building || '—' }} / {{ selectedCad.floor || '—' }}</el-descriptions-item>
        <el-descriptions-item label="构件类型">{{ selectedCad.componentType || '—' }}</el-descriptions-item>

        <el-descriptions-item label="文件大小">{{ formatFileSize(selectedCad.sourceSize) }}</el-descriptions-item>
        <el-descriptions-item label="上传时间">{{ formatDate(selectedCad.createdAt) }}</el-descriptions-item>
        <el-descriptions-item label="文件状态">{{ statusText(selectedCad.status) }}</el-descriptions-item>
        <el-descriptions-item v-if="selectedCad.errorMessage" label="处理说明">{{ selectedCad.errorMessage }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </section>
</template>

<style scoped>
.design-page{box-sizing:border-box;min-height:100%;padding:4px;color:#233a5a}.design-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.design-heading{display:flex;align-items:center;gap:12px}.design-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:13px;color:#4b7fd8;background:#edf4ff;box-shadow:inset 1px 1px 1px #fff,4px 4px 10px rgb(169 184 210 / 16%)}.design-heading h1{margin:0;font-size:20px;font-weight:650;color:#263b59}.design-heading p{margin:4px 0 0;color:#8a9ab0;font-size:12px}.design-project{display:inline-flex;align-items:center;gap:6px;color:#7b8da7;font-size:12px}.design-toolbar{display:flex;align-items:baseline;gap:10px;margin-bottom:14px}.design-toolbar>span{font-size:16px;font-weight:650;color:#2d486d}.design-toolbar small{color:#9aa9bc;font-size:12px}.bim-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}.bim-card,.cad-panel,.overview-project,.recent-panel{border:1px solid rgb(214 223 236 / 76%);border-radius:18px;background:rgb(255 255 255 / 88%);box-shadow:7px 7px 16px rgb(163 177 198 / 18%),-7px -7px 16px rgb(255 255 255 / 82%)}.bim-card{position:relative;min-height:160px;padding:18px}.bim-card-icon{width:40px;height:40px;display:grid;place-items:center;border-radius:12px;color:#5d83ce;background:#edf3ff;font-size:20px}.bim-card-copy{margin-top:14px;padding-right:36px}.bim-card-copy h2{margin:0;overflow:hidden;color:#2c476c;font-size:15px;text-overflow:ellipsis;white-space:nowrap}.bim-card-copy p{margin:6px 0 0;color:#96a3b5;font-size:11px}.bim-card footer{display:flex;align-items:center;gap:10px;margin-top:20px;color:#9aa8b9;font-size:11px}.asset-status{padding:4px 8px;border-radius:8px;background:#f1f4f8;color:#7689a3;font-size:10px}.asset-status.is-ready{color:var(--color-success);background:var(--color-success-soft)}.icon-button{width:32px;height:32px;display:grid;place-items:center;border:0;border-radius:10px;color:#7185a3;background:#f5f8fd;box-shadow:3px 3px 7px rgb(169 184 210 / 16%),-3px -3px 7px rgb(255 255 255 / 88%);cursor:pointer}.icon-button:hover{color:#4a90e2;background:#eef5ff}.icon-button:disabled{color:#b8c0cc;background:#f2f4f7;cursor:not-allowed}.bim-card>.icon-button{position:absolute;top:18px;right:18px}.design-empty{min-height:260px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;border:1px dashed #bfd0e7;border-radius:18px;color:#7c91ae;background:rgb(255 255 255 / 56%)}.design-empty .el-icon{font-size:30px}.design-empty span{color:#9aa8ba;font-size:12px}.design-empty.compact{min-height:140px}.cad-panel{padding:18px}.cad-preview{display:flex;align-items:center;gap:10px;padding:18px;border-radius:14px;color:#6380ab;background:#f3f7fd}.cad-preview .el-icon{font-size:24px}.cad-preview strong{font-size:14px}.cad-preview span{margin-left:auto;color:#91a0b4;font-size:12px}.cad-panel>.design-empty{margin-top:16px}.overview-project{display:flex;align-items:flex-start;justify-content:space-between;padding:22px;margin-bottom:16px}.eyebrow{color:#92a2b6;font-size:11px}.overview-project h2{margin:8px 0 6px;color:#2c476c;font-size:20px}.overview-project p{margin:0;color:#91a0b4;font-size:12px}.overview-status{padding:5px 10px;border-radius:9px;color:var(--color-success);background:var(--color-success-soft);font-size:11px}.overview-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}.overview-stats>div{padding:18px;border-radius:14px;background:#f3f7fd;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 90%),inset -2px -2px 5px rgb(180 200 230 / 12%)}.overview-stats span,.overview-stats strong{display:block}.overview-stats span{color:#91a0b4;font-size:11px}.overview-stats strong{margin-top:8px;color:#52749e;font-size:22px}.recent-panel{padding:18px}.recent-list{display:flex;flex-direction:column;gap:2px}.recent-row{display:grid;grid-template-columns:48px minmax(0,1fr) 100px 72px 34px;align-items:center;gap:10px;min-height:50px;padding:6px 0;border-bottom:1px solid #edf1f6}.recent-row:last-child{border-bottom:0}.recent-row strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#4a6381;font-size:12px}.recent-row>span:not(.recent-type){color:#98a5b6;font-size:11px}.recent-type{display:inline-flex;align-items:center;justify-content:center;width:38px;height:24px;border-radius:7px;background:#eef4ff;color:#4f78bf;font-size:10px;font-weight:700}.recent-type.pointcloud{background:var(--color-info-soft);color:var(--color-info)}.recent-row .icon-button{justify-self:end}@media(max-width:760px){.design-header{align-items:flex-start;gap:12px}.design-project{display:none}.overview-stats{grid-template-columns:repeat(2,1fr)}.cad-preview{align-items:flex-start;flex-wrap:wrap}.cad-preview span{width:100%;margin-left:34px}.recent-row{grid-template-columns:46px minmax(0,1fr) 34px}.recent-row>span:not(.recent-type):not(.asset-status){display:none}}@media(max-width:520px){.bim-grid{grid-template-columns:1fr}}
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



<style scoped lang="scss">
@use '@cloudbim/viewer-core/styles/workspace-controls.scss' as controls;
.design-page { padding: 0; }
.design-page.is-bim-mode { height: auto; min-height: 100%; overflow: visible; }
.bim-toolbar { flex-wrap: wrap; @include controls.filters; }
.bim-filters { flex-wrap: wrap; }
.bim-search-input { max-width: 320px; flex-basis: 240px; }
.bim-toolbar-button, .upload-model-button { @include controls.action; }
.bim-toolbar-button.is-primary, .upload-model-button { @include controls.primary; }
.bim-action-button, .icon-button { @include controls.action; width: var(--control-height); padding: 0; }
.bim-action-button--primary { width: auto; padding: 0 var(--spacing-compact); @include controls.primary; }
.bim-action-button.is-delete:hover { color: var(--color-danger); background: var(--color-danger-soft); border-color: var(--color-danger); }
.bim-table-card, .bim-card, .overview-project, .recent-panel { padding: var(--spacing-md); border-radius: var(--radius-md); box-shadow: none; background: var(--bg-card); }
.bim-table-heading h2 { font-size: var(--font-size-md); font-weight: 600; }
.bim-table :deep(.el-table__header-wrapper th.el-table__cell) { height: 44px; }
.bim-table :deep(.el-table__body tr) { height: 56px; }
.bim-table :deep(.el-table__body td.el-table__cell) { padding: var(--spacing-sm) 0; border-top: 0; }
.bim-table :deep(.el-table__header-wrapper th:nth-child(2) .cell) { text-align: left; }
.bim-archive-code { color: var(--text-secondary); font-size: var(--font-size-sm); }
.bim-card-copy p, .bim-card footer, .overview-stats span, .recent-row > span:not(.recent-type), .recent-row strong, .overview-status { font-size: var(--font-size-xs); }
.overview-project h2 { margin-top: 0; }
.overview-stats strong { color: var(--text-primary); font-size: var(--font-size-xl); }
.overview-stats > div { border-radius: var(--radius-sm); box-shadow: none; }
.bim-table-empty button { @include controls.action; @include controls.primary; }

.bim-name-cell { min-width: 0; }
.bim-name-cell > span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bim-table-heading { margin-bottom: var(--spacing-compact); }
.bim-table-heading h2 { margin: 0; }
.bim-table-empty { min-height: 180px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: var(--spacing-sm); color: var(--text-secondary); }
.bim-table-empty button { margin-top: var(--spacing-sm); }
.bim-table-empty strong { color: var(--text-primary); }
.archive-metadata { display: block; color: var(--text-secondary); font-size: var(--font-size-xs); overflow-wrap: anywhere; }
.cad-detail-name { overflow-wrap: anywhere; }
.workspace-list { @include controls.list-surface; }

</style>
