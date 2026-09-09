<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Aim, DataAnalysis, Delete, Refresh, Search, Upload, View } from '@element-plus/icons-vue'
import { deleteAsset, listAssets, type AssetSummary } from '@/api/backend-file'
import { getBimAlignment } from '@/api/backend-alignment'
import FileUploadDialog from '@/components/upload/FileUploadDialog.vue'
import { formatFileSize } from '@/features/upload/upload.utils'
import type { AuthSession } from '@/features/auth/auth.service'
import NeumorphicPagination from '@/components/NeumorphicPagination.vue'

const props = defineProps<{ projectId: number; projectName?: string; session: AuthSession }>()
const router = useRouter()
const assets = ref<AssetSummary[]>([])
const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(6)
const filters = reactive({ keyword: '', componentType: 'all', fileState: 'all', dateRange: null as [Date, Date] | null })
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

async function loadAssets() { loading.value = true; try { const [scanResponse, bimResponse] = await Promise.all([listAssets({ projectId: props.projectId, type: 'pointcloud', page: 1, pageSize: 500 }), listAssets({ projectId: props.projectId, type: 'bim', page: 1, pageSize: 500 })]); assets.value = scanResponse.data?.list || []; bimOptions.value = bimResponse.data?.list || [] } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载实测点云失败') } finally { loading.value = false } }
function assetStatusText(status: AssetSummary['status']) { return ({ uploading: '上传中', queued: '排队中', processing: '处理中', ready: '已就绪', failed: '处理失败', terminated: '已终止' } as Record<string, string>)[status] || status }
function assetStatusType(status: AssetSummary['status']) { return status === 'ready' ? 'success' : status === 'failed' || status === 'terminated' ? 'danger' : 'warning' }
function formatDate(value?: number) { return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatScanDate(value?: number) { return value ? new Date(value * 1000).toLocaleDateString('zh-CN') : '-' }
function componentText(value?: string) { return ({ YKT: '预制空调板 YKT', YTY: '预制空调板 YTY', PCLT: '预制楼梯 PCLT', DLB: '叠合板 DLB', YB: '叠合板 YB' } as Record<string, string>)[value || ''] || '-' }
function resetFilters() { filters.keyword = ''; filters.componentType = 'all'; filters.fileState = 'all'; filters.dateRange = null; currentPage.value = 1 }
function previewAsset(asset: AssetSummary) { if (asset.status !== 'ready') { ElMessage.warning('文件尚未处理完成，暂时不能预览'); return }; void router.push({ path: '/preview/asset', query: { projectId: props.projectId, projectName: props.projectName, previewType: asset.type, assetId: asset.id, displayName: asset.sourceName } }) }
function linkedBim(pointcloud: AssetSummary) { return bimOptions.value.find((asset) => asset.id === pointcloud.linkedBimId) }
function openAlignment(pointcloud: AssetSummary) {
  const bim = linkedBim(pointcloud)
  if (!bim) { ElMessage.warning('该点云尚未关联 IFC 设计模型'); return }
  if (pointcloud.status !== 'ready' || bim.status !== 'ready') { ElMessage.warning('点云或 IFC 模型尚未处理完成'); return }
  void router.push({ path: '/alignment', query: { projectId: props.projectId, projectName: props.projectName, bimAssetId: bim.id, pointcloudAssetId: pointcloud.id, bimDisplayName: `${pointcloud.archiveCode} · ${bim.sourceName}`, pointcloudDisplayName: pointcloud.sourceName } })
}
async function openResult(pointcloud: AssetSummary) {
  const bim = linkedBim(pointcloud)
  if (!bim) { ElMessage.warning('该点云尚未关联 IFC 设计模型'); return }
  actionLoading.value = true
  try {
    const response = await getBimAlignment({ modelScanFileId: pointcloud.id, modelBimFileId: bim.id })
    if (!response.data) { ElMessage.warning('该次扫描尚未完成配准'); return }
    void router.push({ path: '/preview/split', query: { projectId: props.projectId, projectName: props.projectName, bimAssetId: bim.id, pointcloudAssetId: pointcloud.id, displayName: bim.sourceName, pointcloudDisplayName: pointcloud.sourceName } })
  } catch (error: any) {
    if (error?.response?.status === 400 || error?.response?.status === 404) ElMessage.warning('该次扫描尚未完成配准')
    else ElMessage.error(error instanceof Error ? error.message : '加载实模结果失败')
  } finally {
    actionLoading.value = false
  }
}
async function removeAsset(asset: AssetSummary) { try { await ElMessageBox.confirm(`确定删除文件“${asset.sourceName}”吗？相关预览、配准和分析结果也会被删除。`, '删除文件', { type: 'warning' }); await deleteAsset(asset.id); ElMessage.success('文件已删除'); await loadAssets() } catch (error) { if (error === 'cancel' || error === 'close') return; ElMessage.error(error instanceof Error ? error.message : '删除文件失败') } }
watch([() => filteredAssets.value.length, pageSize], () => { if (currentPage.value > pageCount.value) currentPage.value = pageCount.value })
onMounted(() => { void loadAssets() })
</script>

<template>
  <section class="project-page">
    <header class="project-header"><div class="project-toolbar"><div class="toolbar-filters"><el-input v-model="filters.keyword" class="project-search-input" placeholder="搜索归档编号或文件名称" :prefix-icon="Search" clearable /><el-select v-model="filters.componentType" class="soft-select" placeholder="楼板类型"><el-option label="全部类型" value="all" /><el-option label="预制空调板 YKT" value="YKT" /><el-option label="预制空调板 YTY" value="YTY" /><el-option label="预制楼梯 PCLT" value="PCLT" /><el-option label="叠合板 DLB" value="DLB" /><el-option label="叠合板 YB" value="YB" /></el-select><el-select v-model="filters.fileState" class="soft-select" placeholder="文件状态"><el-option label="文件状态" value="all" /><el-option label="上传中" value="uploading" /><el-option label="处理中" value="processing" /><el-option label="已就绪" value="ready" /><el-option label="处理失败" value="failed" /></el-select><el-date-picker v-model="filters.dateRange" class="date-range-field is-date" type="daterange" unlink-panels format="YYYY/MM/DD" range-separator="-" start-placeholder="扫描开始" end-placeholder="扫描结束" clearable /></div><div class="toolbar-actions"><button class="toolbar-button is-primary" type="button" @click="uploadVisible=true"><el-icon :size="16"><Upload /></el-icon>上传点云</button><button class="toolbar-button" type="button" @click="resetFilters"><el-icon :size="16"><Refresh /></el-icon>重置</button><button class="toolbar-button" type="button" @click="loadAssets"><el-icon :size="16"><Refresh /></el-icon>刷新</button></div></div></header>
    <section class="project-table card"><h2 class="table-title">实测扫描</h2><el-table class="zeemo-table" v-loading="loading" :data="pagedAssets" row-key="id" highlight-current-row empty-text="当前项目暂无点云扫描"><el-table-column label="归档编号" width="150" align="left"><template #default="{ row }"><strong class="archive-code-cell">{{ row.archiveCode || '未归档' }}</strong></template></el-table-column><el-table-column label="文件名称" width="190" align="left"><template #default="{ row }"><div class="asset-name-cell"><span class="asset-type-icon pointcloud">点云</span><span :title="row.sourceName">{{ row.sourceName }}</span></div></template></el-table-column><el-table-column prop="building" label="楼栋" width="90" /><el-table-column label="构件类型" width="130"><template #default="{ row }">{{ componentText(row.componentType) }}</template></el-table-column><el-table-column label="扫描日期" width="120"><template #default="{ row }">{{ formatScanDate(row.scanDate) }}</template></el-table-column><el-table-column label="关联模型" min-width="150"><template #default="{ row }"><span :class="linkedBim(row) ? 'link-ready' : 'link-missing'">{{ linkedBim(row)?.sourceName || '未匹配 IFC' }}</span></template></el-table-column><el-table-column label="状态" width="100"><template #default="{ row }"><el-tag size="small" :type="assetStatusType(row.status)">{{ assetStatusText(row.status) }}</el-tag></template></el-table-column><el-table-column label="操作" width="190" fixed="right" class-name="asset-operation-column" label-class-name="asset-operation-column"><template #default="{ row }"><div class="row-actions"><button class="action-button is-align" type="button" title="自动匹配并进入配准" :disabled="actionLoading || !row.linkedBimId" @click.stop="openAlignment(row)"><el-icon><Aim /></el-icon></button><button class="action-button is-result" type="button" title="查看实模结果" :disabled="actionLoading || !row.linkedBimId" @click.stop="openResult(row)"><el-icon><DataAnalysis /></el-icon></button><button class="action-button is-preview" type="button" title="预览点云" :disabled="row.status !== 'ready'" @click="previewAsset(row)"><el-icon><View /></el-icon></button><button class="action-button is-delete" type="button" title="删除点云" @click="removeAsset(row)"><el-icon><Delete /></el-icon></button></div></template></el-table-column></el-table></section>
    <NeumorphicPagination v-model:current-page="currentPage" v-model:page-size="pageSize" :total="filteredAssets.length" />
    <FileUploadDialog v-model="uploadVisible" :session="session" :project-id="projectId" :project-name="projectName" :allowed-kinds="['pointcloud']" title="上传实测点云" @uploaded="loadAssets" />
  </section>
</template>

<style scoped>
.project-page{box-sizing:border-box;width:100%;height:100%;min-height:600px;display:flex;flex-direction:column;color:#233a5a}.project-header{flex:0 0 auto;margin-bottom:18px}.project-toolbar{min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:12px}.toolbar-filters{flex:1;min-width:0;display:flex;align-items:center;gap:8px}.toolbar-actions{display:flex;align-items:center;gap:8px}.project-search-input{flex:1 1 210px;min-width:170px;max-width:230px}.soft-select{width:132px}.project-toolbar :deep(.project-search-input .el-input__wrapper),.project-toolbar :deep(.soft-select .el-select__wrapper){height:40px;min-height:40px;padding-inline:10px;border:1px solid rgb(255 255 255 / 70%);border-radius:16px;background:#f5f9ff;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%)!important}.project-toolbar :deep(.date-range-field.is-date){flex:0 0 210px;width:210px;min-width:210px;max-width:210px}.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper){height:40px;min-height:40px;padding-inline:10px;border:1px solid rgb(255 255 255 / 70%);border-radius:16px;background:#f5f9ff;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%)!important}.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper.is-focus),.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper:focus-within){border-color:rgb(255 255 255 / 70%)!important;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%)!important}.project-toolbar :deep(.project-search-input .el-input__inner),.project-toolbar :deep(.soft-select .el-select__selected-item),.project-toolbar :deep(.date-range-field .el-range-input),.project-toolbar :deep(.date-range-field .el-range-separator){color:#617694;font-size:13px;font-weight:600}.project-toolbar :deep(.date-range-field .el-range-input::placeholder){color:#a2b0c4}.project-toolbar :deep(.date-range-field .el-input__icon){color:#96a7bf}.toolbar-button{height:40px;min-height:40px;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:0;border-radius:14px;padding:0 16px;color:#657a99;background:#f3f7fd;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%);font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap}.toolbar-button:hover{color:#4b7fd8}.card{background:rgb(255 255 255 / 88%);border:1px solid rgb(214 223 236 / 72%);border-radius:18px;box-shadow:7px 7px 16px rgb(163 177 198 / 22%),-7px -7px 16px rgb(255 255 255 / 82%);padding:18px}.project-table{min-width:0;min-height:0;display:flex;flex:1 1 auto;flex-direction:column;margin-bottom:0}.table-title{flex:0 0 auto;margin:0 0 14px;color:#233a5a;font-size:17px;font-weight:650}.zeemo-table{min-width:0;min-height:0;flex:1 1 auto;width:100%;border-radius:14px;overflow:hidden;background:transparent}.zeemo-table :deep(.el-table__inner-wrapper::before){display:none}.zeemo-table :deep(.el-table__header-wrapper th.el-table__cell){height:52px;border:0;background:#f4f8fd;color:#6f7d95;font-size:13px;font-weight:600;text-align:center}.zeemo-table :deep(.el-table__header-wrapper th .cell),.zeemo-table :deep(.el-table__body td .cell){text-align:center}.zeemo-table :deep(.el-table__header-wrapper th:first-child .cell),.zeemo-table :deep(.el-table__body td:first-child .cell){text-align:left}.zeemo-table :deep(.el-table__body tr){height:64px;background:#fff;box-shadow:0 8px 18px rgb(163 177 198 / 16%)}.zeemo-table :deep(.el-table__body tr+tr td){border-top:10px solid transparent}.zeemo-table :deep(.el-table__body td.el-table__cell){padding:0 12px;border-top:1px solid rgb(222 231 244 / 72%);border-bottom:1px solid rgb(214 225 240 / 62%);background:#fff;font-size:13px;font-weight:500;text-align:center}.zeemo-table :deep(.el-table__body tr:hover>td.el-table__cell){background:#f6faff}.zeemo-table :deep(.el-table__body tr td:first-child){border-left:1px solid rgb(222 231 244 / 72%);border-radius:14px 0 0 14px}.zeemo-table :deep(.el-table__body tr td:last-child){border-right:1px solid rgb(214 225 240 / 62%);border-radius:0 14px 14px 0}.zeemo-table :deep(.el-table-fixed-column--right),.zeemo-table :deep(.asset-operation-column){position:sticky!important;right:0!important;z-index:3;background:#fff!important}.zeemo-table :deep(.el-table__header-wrapper .el-table-fixed-column--right),.zeemo-table :deep(.el-table__header-wrapper .asset-operation-column){z-index:4;background:#f4f8fd!important}.asset-name-cell{min-width:0;display:flex;align-items:center;justify-content:flex-start;gap:10px;text-align:left}.asset-name-cell>span:last-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.asset-type-icon{width:34px;height:25px;display:grid;place-items:center;flex:0 0 34px;border-radius:7px;background:#eef4ff;color:#4f78bf;font-size:10px;font-weight:700}.asset-type-icon.pointcloud{background:#eaf9f4;color:#16866a}.row-actions{display:flex;align-items:center;justify-content:center;gap:8px}.action-button{width:34px;height:34px;display:grid;place-items:center;flex:0 0 34px;padding:0;border:0;border-radius:10px;background:#f5f8fd;color:#7185a3;box-shadow:4px 4px 9px rgb(169 184 210 / 18%),-4px -4px 9px rgb(255 255 255 / 82%);cursor:pointer}.action-button:hover{transform:translateY(-1px)}.action-button.is-preview:hover{color:#4a90e2;background:#eef5ff}.action-button.is-preview:disabled{color:#b8c0cc;background:#f2f4f7;cursor:not-allowed;transform:none}.action-button.is-delete:hover{color:#df5e67;background:#fff1f2}.project-table-footer{flex:0 0 auto;display:flex;justify-content:center;margin-top:auto;padding:18px 0 4px}.pagination{display:flex;align-items:center;justify-content:center;gap:8px;padding:8px 12px;color:#6f82a0}.pager-summary{margin-right:4px;font-size:13px;font-weight:400;white-space:nowrap}.pager-button,.page-size{height:34px;border:0;border-radius:10px;background:#fff;color:#7b8ca7;box-shadow:5px 5px 12px rgb(169 184 210 / 22%),-5px -5px 12px rgb(255 255 255 / 96%),inset 1px 1px 0 rgb(255 255 255 / 92%)}.pager-button{min-width:34px;padding:0 10px;font-size:13px;font-weight:400;cursor:pointer}.pager-button.active{color:#fff;background:linear-gradient(145deg,#88b4f8,#73a2f3)}.pager-button:disabled{color:#b8c2d2;background:#fff;box-shadow:none;cursor:not-allowed}.pager-ellipsis{padding:0 3px;color:#9caac0;font-weight:400}.page-size{display:inline-flex;align-items:center;overflow:hidden;margin-left:4px}.page-size select{height:34px;min-width:100px;padding:0 12px;border:0;outline:0;color:#536a8c;background:transparent;font-size:13px;font-weight:400;cursor:pointer}@media(max-width:900px){.project-toolbar{display:block}.toolbar-filters{flex-wrap:wrap}.project-search-input{max-width:none}.toolbar-actions{margin-top:12px;justify-content:flex-end}}@media(max-width:620px){.project-page{min-height:700px}.pagination{flex-wrap:wrap}.project-table{padding:12px}}
 .action-button.is-align:hover{color:#7c5ce0;background:#f3efff}.action-button.is-result:hover{color:#0e9f78;background:#ebfbf5}.action-button:disabled{cursor:not-allowed;opacity:.58;transform:none}.action-dialog-body{display:flex;flex-direction:column;gap:18px;padding:8px 0}.action-field{display:flex;flex-direction:column;gap:8px}.action-field>span{color:#53657e;font-size:13px;font-weight:600}.toolbar-button.is-primary{color:#fff;background:#76a6ed;box-shadow:5px 5px 12px rgb(76 120 190 / 18%)}.archive-code-cell{color:#436fae;font-size:12px}.link-ready{color:#258060;font-size:11px}.link-missing{color:#c47b66;font-size:11px}
</style>

<style scoped>
.project-page { color: var(--text-primary); }
.project-toolbar :deep(.project-search-input .el-input__inner), .project-toolbar :deep(.soft-select .el-select__selected-item), .project-toolbar :deep(.date-range-field .el-range-input), .project-toolbar :deep(.date-range-field .el-range-separator) { color: var(--text-secondary); font-size: var(--font-size-sm); }
.project-toolbar :deep(.date-range-field .el-range-input::placeholder) { color: var(--text-placeholder); }
.toolbar-button { color: var(--text-secondary); background: var(--bg-control); font-size: var(--font-size-sm); border-radius: var(--radius-md); }
.toolbar-button:hover { color: var(--text-link); }
.card { border-color: var(--border-color-light); border-radius: var(--radius-lg); background: var(--bg-card-translucent); box-shadow: var(--shadow-md); }
.table-title { color: var(--text-primary); font-size: var(--font-size-lg); }
.zeemo-table :deep(.el-table__header-wrapper th.el-table__cell) { background: var(--bg-control); color: var(--text-secondary); font-size: var(--font-size-sm); }
.zeemo-table :deep(.el-table__body tr), .zeemo-table :deep(.el-table__body td.el-table__cell) { background: var(--bg-card); }
.zeemo-table :deep(.el-table__body td.el-table__cell) { font-size: var(--font-size-sm); border-top-color: var(--border-color-light); border-bottom-color: var(--border-color-light); }
.zeemo-table :deep(.el-table__body tr:hover > td.el-table__cell) { background: var(--color-primary-soft); }
.action-button { background: var(--bg-control); color: var(--text-secondary); }
.action-button.is-preview:hover { color: var(--text-link); background: var(--color-primary-soft); }
.action-button.is-preview:disabled { color: var(--text-disabled); }
.pagination { color: var(--text-secondary); }
.archive-code-cell { color: var(--text-link); font-size: var(--font-size-xs); }
.link-ready { color: var(--text-success); font-size: var(--font-size-xs); }
.link-missing { color: var(--text-warning); font-size: var(--font-size-xs); }
.toolbar-button.is-primary { color: var(--bg-card); background: var(--color-primary); }
.toolbar-button.is-primary:hover { color: var(--bg-card); background: var(--color-primary-hover); }
.project-toolbar :deep(.project-search-input .el-input__wrapper), .project-toolbar :deep(.soft-select .el-select__wrapper), .project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper) { background: var(--bg-filter-control); box-shadow: var(--shadow-filter-control) !important; }
.project-toolbar :deep(.project-search-input .el-input__inner), .project-toolbar :deep(.soft-select .el-select__selected-item), .project-toolbar :deep(.date-range-field .el-range-input), .project-toolbar :deep(.date-range-field .el-range-separator) { color: var(--text-secondary); font-size: var(--font-size-sm); font-weight: 600; }
.project-toolbar :deep(.date-range-field .el-range-input::placeholder) { color: var(--text-placeholder); }
.project-toolbar :deep(.date-range-field .el-input__icon), .project-toolbar :deep(.date-range-field .el-range__icon), .project-toolbar :deep(.date-range-field .el-range__close-icon) { color: var(--text-tertiary); }
.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper.is-focus), .project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper:focus-within) { border-color: rgb(255 255 255 / 70%) !important; box-shadow: var(--shadow-filter-control) !important; }
</style>
