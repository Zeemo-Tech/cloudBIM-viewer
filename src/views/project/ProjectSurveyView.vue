<script setup lang="ts">
import { readListState, writeListState } from '@/features/workspace/listState'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Aim, DataAnalysis, Delete, Refresh, Search, Upload, View } from '@element-plus/icons-vue'
import { deleteAsset, formatFileSize, getBimAlignment, listAssets, type AssetSummary } from '@cloudbim/viewer-core'
import FileUploadDialog from '@/components/upload/FileUploadDialog.vue'
import type { AuthSession } from '@/features/auth/auth.service'
import WorkspaceHeading from '@/components/layout/WorkspaceHeading.vue'
import NeumorphicPagination from '@/components/NeumorphicPagination.vue'

const props = defineProps<{ projectId: number; projectName?: string; session: AuthSession }>()
const router = useRouter()
const route = useRoute()
const assets = ref<AssetSummary[]>([])
const loading = ref(false)
const listStateKey = `cloudbim.list.v1:${props.session.username}:survey:${props.projectId}`
const restoredList = readListState(listStateKey, { keyword: '', componentType: 'all', fileState: 'all', dateRange: null as [Date, Date] | null }, [10, 20, 50])
const currentPage = ref(restoredList.page)
const pageSize = ref(restoredList.pageSize)
const filters = reactive(restoredList.filters)
const actionLoading = ref(false)
const bimOptions = ref<AssetSummary[]>([])
const uploadVisible = ref(false)

const filteredAssets = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return assets.value.filter((asset) => {
    const createdAt = (asset.scanDate || asset.createdAt) * 1000
    const matchesDate = !filters.dateRange || (createdAt >= new Date(filters.dateRange[0]).setHours(0, 0, 0, 0) && createdAt <= new Date(filters.dateRange[1]).setHours(23, 59, 59, 999))
    return (!keyword || asset.sourceName.toLowerCase().includes(keyword) || asset.archiveCode?.toLowerCase().includes(keyword))
      && (filters.componentType === 'all' || asset.componentType === filters.componentType)
      && (filters.fileState === 'all' || asset.status === filters.fileState)
      && matchesDate
  }).sort((a, b) => (b.scanDate || b.createdAt) - (a.scanDate || a.createdAt))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filteredAssets.value.length / pageSize.value)))
const pagedAssets = computed(() => filteredAssets.value.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value))
const hasActiveFilters = computed(() => Boolean(
  filters.keyword.trim()
  || filters.componentType !== 'all'
  || filters.fileState !== 'all'
  || filters.dateRange,
))

async function loadAssets() { loading.value = true; try { const [scanResponse, bimResponse] = await Promise.all([listAssets({ projectId: props.projectId, type: 'pointcloud', page: 1, pageSize: 500 }), listAssets({ projectId: props.projectId, type: 'bim', page: 1, pageSize: 500 })]); assets.value = scanResponse.data?.list || []; bimOptions.value = bimResponse.data?.list || [] } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载扫描点云失败') } finally { loading.value = false } }
function assetStatusText(status: AssetSummary['status']) { return ({ uploading: '上传中', queued: '排队中', processing: '处理中', ready: '已就绪', failed: '处理失败', terminated: '已终止' } as Record<string, string>)[status] || status }
function assetStatusType(status: AssetSummary['status']) { return status === 'ready' ? 'success' : status === 'failed' || status === 'terminated' ? 'danger' : 'warning' }
function formatDate(value?: number) { return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatScanDate(value?: number) { return value ? new Date(value * 1000).toLocaleDateString('zh-CN') : '-' }
function componentText(value?: string) { return ({ YKT: '预制空调板 YKT', YTY: '预制空调板 YTY', PCLT: '预制楼梯 PCLT', DLB: '叠合板 DLB', YB: '叠合板 YB' } as Record<string, string>)[value || ''] || '-' }
function resetFilters() { filters.keyword = ''; filters.componentType = 'all'; filters.fileState = 'all'; filters.dateRange = null; currentPage.value = 1 }
function handleEmptyAction() { if (hasActiveFilters.value) resetFilters(); else uploadVisible.value = true }
function previewAsset(asset: AssetSummary) { if (asset.status !== 'ready') { ElMessage.warning('文件尚未处理完成，暂时不能预览'); return }; void router.push({ path: '/preview/asset', query: { projectId: props.projectId, projectName: props.projectName, returnTo: route.fullPath, previewType: asset.type, assetId: asset.id, displayName: asset.sourceName } }) }
function linkedBim(pointcloud: AssetSummary) { return bimOptions.value.find((asset) => asset.id === pointcloud.linkedBimId) }
function openAlignment(pointcloud: AssetSummary) {
  const bim = linkedBim(pointcloud)
  if (!bim) { ElMessage.warning('该点云尚未关联 IFC 设计模型'); return }
  if (pointcloud.status !== 'ready' || bim.status !== 'ready') { ElMessage.warning('点云或 IFC 模型尚未处理完成'); return }
  void router.push({ path: '/alignment', query: { projectId: props.projectId, projectName: props.projectName, returnTo: route.fullPath, bimAssetId: bim.id, pointcloudAssetId: pointcloud.id, bimDisplayName: pointcloud.archiveCode ? `${pointcloud.archiveCode} · ${bim.sourceName}` : bim.sourceName, pointcloudDisplayName: pointcloud.sourceName } })
}
async function openResult(pointcloud: AssetSummary) {
  const bim = linkedBim(pointcloud)
  if (!bim) { ElMessage.warning('该点云尚未关联 IFC 设计模型'); return }
  actionLoading.value = true
  try {
    const response = await getBimAlignment({ modelScanFileId: pointcloud.id, modelBimFileId: bim.id })
    if (!response.data) { ElMessage.warning('该次扫描尚未完成配准'); return }
    void router.push({ path: '/preview/split', query: { projectId: props.projectId, projectName: props.projectName, returnTo: route.fullPath, bimAssetId: bim.id, pointcloudAssetId: pointcloud.id, displayName: bim.sourceName, pointcloudDisplayName: pointcloud.sourceName } })
  } catch (error: any) {
    if (error?.response?.status === 400 || error?.response?.status === 404) ElMessage.warning('该次扫描尚未完成配准')
    else ElMessage.error(error instanceof Error ? error.message : '加载实模结果失败')
  } finally {
    actionLoading.value = false
  }
}
async function removeAsset(asset: AssetSummary) { try { await ElMessageBox.confirm(`确定删除文件“${asset.sourceName}”吗？相关预览、配准和分析结果也会被删除。`, '删除文件', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }); await deleteAsset(asset.id); ElMessage.success('文件已删除'); await loadAssets() } catch (error) { if (error === 'cancel' || error === 'close') return; ElMessage.error(error instanceof Error ? error.message : '删除文件失败') } }
watch([() => filteredAssets.value.length, pageSize], () => { if (currentPage.value > pageCount.value) currentPage.value = pageCount.value })
watch([() => filters.keyword, () => filters.componentType, () => filters.fileState, () => filters.dateRange, pageSize], () => { currentPage.value = 1 })
onMounted(() => { void loadAssets() })
watch([filters, currentPage, pageSize], () => {
  writeListState(listStateKey, { filters: filters, page: currentPage.value, pageSize: pageSize.value })
}, { deep: true, flush: 'sync' })
</script>

<template>
  <section class="project-page workspace-list">
    <WorkspaceHeading title="扫描点云" description="管理扫描归档，关联设计模型并进入分析。" />
    <header class="project-header"><div class="project-toolbar workspace-toolbar"><div class="toolbar-filters workspace-filters"><el-input v-model="filters.keyword" class="project-search-input workspace-search" placeholder="搜索归档编号或文件名称" :prefix-icon="Search" clearable /><el-select v-model="filters.componentType" class="soft-select workspace-select" placeholder="构件类型"><el-option label="构件类型" value="all" /><el-option label="预制空调板 YKT" value="YKT" /><el-option label="预制空调板 YTY" value="YTY" /><el-option label="预制楼梯 PCLT" value="PCLT" /><el-option label="叠合板 DLB" value="DLB" /><el-option label="叠合板 YB" value="YB" /></el-select><el-select v-model="filters.fileState" class="soft-select workspace-select" placeholder="文件状态"><el-option label="文件状态" value="all" /><el-option label="上传中" value="uploading" /><el-option label="排队中" value="queued" /><el-option label="处理中" value="processing" /><el-option label="已就绪" value="ready" /><el-option label="处理失败" value="failed" /><el-option label="已终止" value="terminated" /></el-select><el-date-picker v-model="filters.dateRange" class="date-range-field is-date workspace-date" type="daterange" unlink-panels format="YYYY/MM/DD" range-separator="-" start-placeholder="开始日期" end-placeholder="结束日期" clearable /></div><div class="toolbar-actions workspace-actions"><button class="toolbar-button is-primary" type="button" @click="uploadVisible=true"><el-icon :size="16"><Upload /></el-icon>上传点云</button><button class="toolbar-button" type="button" @click="resetFilters"><el-icon :size="16"><Refresh /></el-icon>重置</button><button class="toolbar-button" type="button" @click="loadAssets"><el-icon :size="16"><Refresh /></el-icon>刷新</button></div></div></header>
    <section class="project-table card workspace-table-card">
      <h2 class="table-title">文件列表</h2>
      <div class="table-scroll-region" tabindex="0" aria-label="扫描点云文件列表，可横向滚动">
        <el-table class="zeemo-table workspace-table" :fit="true" v-loading="loading" :data="pagedAssets" row-key="id" highlight-current-row>
          <el-table-column label="归档编号" min-width="98" align="left"><template #default="{ row }"><strong class="archive-code-cell">{{ row.archiveCode || '未归档' }}</strong></template></el-table-column>
          <el-table-column label="文件名称" width="200" align="left"><template #default="{ row }"><div class="asset-name-cell"><span :title="row.sourceName">{{ row.sourceName }}</span><small class="asset-related-model" :title="linkedBim(row)?.sourceName" :class="linkedBim(row) ? 'link-ready' : 'link-missing'">关联：{{ linkedBim(row)?.sourceName || '未匹配 IFC' }}</small></div></template></el-table-column>

          <el-table-column label="楼栋" min-width="64"><template #default="{ row }"><span class="cell-wrap">{{ row.building || '—' }}</span></template></el-table-column>
          <el-table-column label="构件类型" min-width="98"><template #default="{ row }"><span class="cell-wrap">{{ componentText(row.componentType) }}</span></template></el-table-column>
          <el-table-column label="扫描日期" min-width="104"><template #default="{ row }">{{ formatScanDate(row.scanDate) }}</template></el-table-column>
          <el-table-column label="状态" min-width="84"><template #default="{ row }"><el-tag size="small" :type="assetStatusType(row.status)">{{ assetStatusText(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="254" align="right" class-name="asset-operation-column" label-class-name="asset-operation-column"><template #default="{ row }"><div class="row-actions workspace-row-actions"><button class="action-button action-button--primary is-align" type="button" title="进入分析" aria-label="进入分析" :disabled="actionLoading || !row.linkedBimId" @click.stop="openAlignment(row)"><el-icon><Aim /></el-icon><span>进入分析</span></button><el-tooltip content="查看实模结果" placement="top"><button class="action-button is-result" type="button" title="查看实模结果" aria-label="查看实模结果" :disabled="actionLoading || !row.linkedBimId" @click.stop="openResult(row)"><el-icon><DataAnalysis /></el-icon></button></el-tooltip><el-tooltip content="预览点云" placement="top"><button class="action-button is-preview" type="button" title="预览点云" aria-label="预览点云" :disabled="row.status !== 'ready'" @click="previewAsset(row)"><el-icon><View /></el-icon></button></el-tooltip><el-tooltip content="删除点云" placement="top"><button class="action-button is-delete" type="button" title="删除点云" aria-label="删除点云" @click="removeAsset(row)"><el-icon><Delete /></el-icon></button></el-tooltip></div></template></el-table-column>
          <template #empty>
            <div class="table-empty">
              <strong>{{ assets.length ? '没有匹配的扫描点云' : '当前项目暂无扫描点云' }}</strong>
              <span>{{ assets.length ? '当前筛选条件下没有结果' : '上传点云后可在此预览并进入分析' }}</span>
              <button type="button" @click="handleEmptyAction">{{ hasActiveFilters ? '清除筛选' : '上传点云' }}</button>
            </div>
          </template>
        </el-table>
      </div>
    </section>
    <NeumorphicPagination v-model:current-page="currentPage" v-model:page-size="pageSize" :total="filteredAssets.length" :page-size-options="[10, 20, 50]" aria-label="文件列表分页" />
    <FileUploadDialog v-model="uploadVisible" :session="session" :project-id="projectId" :project-name="projectName" :allowed-kinds="['pointcloud']" title="上传扫描点云" @uploaded="loadAssets" />
  </section>
</template>

<style scoped lang="scss">
@use '@cloudbim/viewer-core/styles/workspace-controls.scss' as controls;
.project-page { min-width: 0; display: flex; flex-direction: column; color: var(--text-primary); }
.project-header { margin-bottom: var(--spacing-md); }
.project-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--spacing-md); @include controls.filters; }
.toolbar-filters { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; align-items: center; gap: var(--spacing-sm); }
.toolbar-actions { display: flex; align-items: center; gap: var(--spacing-sm); }
.project-search-input { flex: 1 1 240px; min-width: 200px; max-width: 320px; }
.soft-select { width: 132px; }
.project-toolbar :deep(.date-range-field.is-date) { flex: 0 0 240px; width: 240px; }
.toolbar-button { @include controls.action; }
.toolbar-button.is-primary { @include controls.primary; }
.project-table { min-width: 0; padding: var(--spacing-md); border: 1px solid var(--border-color-light); border-radius: var(--radius-md); background: var(--bg-card); }
.table-title { margin: 0 0 var(--spacing-md); font-size: var(--font-size-md); font-weight: 600; }
.table-scroll-region { min-width: 0; overflow-x: auto; }
.zeemo-table { width: 100%; }
.zeemo-table :deep(.el-table__inner-wrapper::before) { display: none; }
.zeemo-table :deep(th.el-table__cell) { height: 44px; background: var(--bg-control); color: var(--text-secondary); font-weight: 600; }
.zeemo-table :deep(td.el-table__cell) { height: 56px; padding: var(--spacing-sm) 0; font-size: var(--font-size-sm); }
.zeemo-table :deep(.el-table-fixed-column--right) { background: var(--bg-card); }
.zeemo-table :deep(th.el-table-fixed-column--right) { background: var(--bg-control); }
.asset-name-cell { min-width: 0; display: flex; align-items: center; gap: var(--spacing-sm); }
.asset-name-cell > span:last-child, .link-ready { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset-type-icon { padding: var(--spacing-xs); border-radius: var(--radius-xs); background: var(--color-primary-soft); color: var(--color-primary); font-size: var(--font-size-xs); flex-shrink: 0; }
.archive-code-cell { color: var(--text-secondary); font-size: var(--font-size-sm); }
.link-ready { color: var(--text-secondary); }
.link-missing { color: var(--text-warning); }
.row-actions { display: flex; align-items: center; justify-content: center; gap: var(--spacing-sm); }
.action-button { @include controls.action; width: var(--control-height); flex: 0 0 var(--control-height); padding: 0; }
.action-button--primary { width: auto; flex-basis: auto; padding: 0 var(--spacing-compact); @include controls.primary; }
.action-button.is-delete:hover { color: var(--color-danger); border-color: var(--color-danger); background: var(--color-danger-soft); }
.table-empty { min-height: 180px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: var(--spacing-sm); color: var(--text-tertiary); }
.table-empty strong { color: var(--text-primary); font-size: var(--font-size-md); }
.table-empty button { @include controls.action; @include controls.primary; margin-top: var(--spacing-sm); }

.asset-name-cell { display: grid; gap: 2px; }
.asset-name-cell > span, .asset-related-model { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset-related-model { font-size: var(--font-size-xs); font-weight: 400; }
.cell-wrap { overflow-wrap: anywhere; }
.workspace-list { @include controls.list-surface; }
</style>
