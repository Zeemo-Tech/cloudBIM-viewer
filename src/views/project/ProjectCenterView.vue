<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Edit, Delete, Refresh, Folder, View, Search, CirclePlus, Aim, DataAnalysis, MoreFilled } from '@element-plus/icons-vue'
import { createProject, deleteProject, listProjects, updateProject, type ProjectSummary } from '@/api/backend-project'
import { useRouter } from 'vue-router'
import { deleteAsset, formatFileSize, getBimAlignment, listAssets, type AssetSummary } from '@cloudbim/viewer-core'

const projects = ref<ProjectSummary[]>([])
const router = useRouter()
const loading = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({ name: '', description: '' })
const actionLoading = ref(false)
const alignmentDialogVisible = ref(false)
const resultDialogVisible = ref(false)
const actionProject = ref<ProjectSummary | null>(null)
const bimOptions = ref<AssetSummary[]>([])
const pointcloudOptions = ref<AssetSummary[]>([])
const selectedBimId = ref<number | null>(null)
const selectedPointcloudId = ref<number | null>(null)
const resultOptions = ref<Array<{ key: string; bim: AssetSummary; pointcloud: AssetSummary }>>([])
const selectedResultKey = ref('')
const projectAssets = reactive<Record<number, AssetSummary[]>>({})
const assetLoading = reactive<Record<number, boolean>>({})
const expandedProjectIds = ref<number[]>([])
const projectTableRef = ref()
const currentPage = ref(1)
const pageSize = ref(6)
const filters = reactive({ keyword: '', fileType: 'all', fileState: 'all', dateRange: null as [Date, Date] | null })
const filteredProjects = computed(() => projects.value.filter((project) => {
  const keyword = filters.keyword.trim().toLowerCase()
  const matchesKeyword = !keyword || `${project.name} ${project.description || ''}`.toLowerCase().includes(keyword)
  const matchesType = filters.fileType === 'all' || (filters.fileType === 'bim' ? project.bimCount > 0 : project.pointcloudCount > 0)
  const matchesState = filters.fileState === 'all' || project.status === filters.fileState
  const scanDate = project.scanDate ? project.scanDate * 1000 : 0
  const matchesDate = !filters.dateRange || (scanDate >= filters.dateRange[0].setHours(0, 0, 0, 0) && scanDate <= filters.dateRange[1].setHours(23, 59, 59, 999))
  return matchesKeyword && matchesType && matchesState && matchesDate
}))
const pageCount = computed(() => Math.max(1, Math.ceil(filteredProjects.value.length / pageSize.value)))
const pagedProjects = computed(() => filteredProjects.value.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value))
const visiblePages = computed<Array<number | string>>(() => {
  if (pageCount.value <= 7) return Array.from({ length: pageCount.value }, (_, index) => index + 1)
  if (currentPage.value <= 4) return [1, 2, 3, 4, 5, 'ellipsis', pageCount.value]
  if (currentPage.value >= pageCount.value - 3) return [1, 'ellipsis', pageCount.value - 4, pageCount.value - 3, pageCount.value - 2, pageCount.value - 1, pageCount.value]
  return [1, 'ellipsis', currentPage.value - 1, currentPage.value, currentPage.value + 1, 'ellipsis-end', pageCount.value]
})

function resetForm() { editingId.value = null; form.name = ''; form.description = '' }
function openCreate() { resetForm(); dialogVisible.value = true }
function openEdit(project: ProjectSummary) { editingId.value = project.id; form.name = project.name; form.description = project.description || ''; dialogVisible.value = true }

async function loadProjects() {
  loading.value = true
  try {
    const response = await listProjects()
    projects.value = response?.data?.list || (response as any)?.list || []
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载项目失败') }
  finally { loading.value = false }
}
async function saveProject() {
  if (!form.name.trim()) { ElMessage.warning('请输入项目名称'); return }
  try {
    if (editingId.value) await updateProject(editingId.value, { name: form.name.trim(), description: form.description.trim() })
    else await createProject({ name: form.name.trim(), description: form.description.trim() })
    dialogVisible.value = false; ElMessage.success(editingId.value ? '项目已更新' : '项目已创建'); await loadProjects()
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '保存项目失败') }
}
async function removeProject(project: ProjectSummary) {
  try { await ElMessageBox.confirm(`确定删除项目“${project.name}”吗？项目下必须没有文件。`, '删除项目', { type: 'warning' }) }
  catch { return }
  try { await deleteProject(project.id); ElMessage.success('项目已删除'); await loadProjects() }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '删除项目失败') }
}
function statusText(status: ProjectSummary['status']) { return ({ pending: '待上传', processing: '处理中', ready: '已就绪', failed: '处理失败' } as Record<string,string>)[status] || status }
function statusTagType(status: ProjectSummary['status']) { return status === 'ready' ? 'success' : status === 'failed' ? 'danger' : status === 'processing' ? 'warning' : 'info' }
function openProject(project: ProjectSummary) { void router.push({ path: '/upload', query: { projectId: project.id } }) }
function handleMoreCommand(command: string, project: ProjectSummary) {
  if (command === 'view') toggleProjectAssets(project)
  else if (command === 'edit') openEdit(project)
  else if (command === 'delete') void removeProject(project)
}
async function loadProjectAssets(project: ProjectSummary, force = false) {
  if (!force && projectAssets[project.id]) return
  assetLoading[project.id] = true
  try {
    const response = await listAssets({ projectId: project.id, page: 1, pageSize: 500 })
    projectAssets[project.id] = sortProjectAssets(response.data?.list || [])
  } catch (error) {
    projectAssets[project.id] = []
    ElMessage.error(error instanceof Error ? error.message : '加载项目资产失败')
  } finally { assetLoading[project.id] = false }
}
function toggleProjectAssets(project: ProjectSummary) {
  const expanded = expandedProjectIds.value.includes(project.id)
  projectTableRef.value?.toggleRowExpansion(project, !expanded)
}
function sortProjectAssets(assets: AssetSummary[]) {
  const typeOrder: Record<AssetSummary['type'], number> = { bim: 0, cad: 1, pointcloud: 2 }
  return [...assets].sort((a, b) => {
    const typeDifference = typeOrder[a.type] - typeOrder[b.type]
    return typeDifference || b.createdAt - a.createdAt
  })
}
async function handleExpandChange(project: ProjectSummary, expandedRows: ProjectSummary[]) {
  expandedProjectIds.value = expandedRows.map((row) => row.id)
  if (expandedProjectIds.value.includes(project.id)) await loadProjectAssets(project)
}
function assetStatusText(status: AssetSummary['status']) {
  return ({ uploading: '上传中', queued: '排队中', processing: '处理中', ready: '已就绪', failed: '处理失败', terminated: '已终止' } as Record<string, string>)[status] || status
}
function assetStatusType(status: AssetSummary['status']) { return status === 'ready' ? 'success' : status === 'failed' || status === 'terminated' ? 'danger' : 'warning' }
function formatAssetDate(value?: number) { return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '-' }
function previewAsset(asset: AssetSummary) {
  if (asset.status !== 'ready') { ElMessage.warning('文件尚未处理完成，暂时不能预览'); return }
  void router.push({ path: '/preview/asset', query: { previewType: asset.type, assetId: asset.id, displayName: asset.sourceName } })
}
function uniformizationText(asset: AssetSummary) {
  if (asset.type !== 'bim') return '不适用'
  return ({ idle: '未均匀化', queued: '排队中', processing: '处理中', succeeded: '已均匀化', failed: '处理失败' } as Record<string, string>)[asset.meshRemesh?.status || 'idle'] || '未均匀化'
}
function uniformizationTagType(asset: AssetSummary) {
  if (asset.type !== 'bim') return 'info'
  return asset.meshRemesh?.status === 'succeeded' ? 'success' : asset.meshRemesh?.status === 'failed' ? 'danger' : asset.meshRemesh?.status === 'processing' || asset.meshRemesh?.status === 'queued' ? 'warning' : 'info'
}
async function removeAsset(project: ProjectSummary, asset: AssetSummary) {
  try { await ElMessageBox.confirm(`确定删除文件“${asset.sourceName}”吗？相关预览、配准和分析结果也会被删除。`, '删除文件', { type: 'warning' }) }
  catch { return }
  try {
    await deleteAsset(asset.id)
    ElMessage.success('文件已删除')
    await Promise.all([loadProjectAssets(project, true), loadProjects()])
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '删除文件失败') }
}
async function loadReadyAssets(project: ProjectSummary) {
  const [bimResponse, pointcloudResponse] = await Promise.all([
    listAssets({ projectId: project.id, type: 'bim', page: 1, pageSize: 200 }),
    listAssets({ projectId: project.id, type: 'pointcloud', page: 1, pageSize: 200 }),
  ])
  bimOptions.value = (bimResponse.data?.list || []).filter((asset) => asset.status === 'ready')
  pointcloudOptions.value = (pointcloudResponse.data?.list || []).filter((asset) => asset.status === 'ready')
}
async function openAlignment(project: ProjectSummary) {
  actionLoading.value = true
  try {
    actionProject.value = project
    await loadReadyAssets(project)
    if (!bimOptions.value.length || !pointcloudOptions.value.length) {
      ElMessage.warning('该项目需要至少一个已就绪的 BIM 模型和点云文件才能配准')
      return
    }
    selectedBimId.value = bimOptions.value[0]?.id || null
    selectedPointcloudId.value = pointcloudOptions.value[0]?.id || null
    alignmentDialogVisible.value = true
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载项目文件失败') }
  finally { actionLoading.value = false }
}
function confirmAlignment() {
  const bim = bimOptions.value.find((asset) => asset.id === selectedBimId.value)
  const pointcloud = pointcloudOptions.value.find((asset) => asset.id === selectedPointcloudId.value)
  if (!bim || !pointcloud) { ElMessage.warning('请选择 BIM 模型和点云文件'); return }
  alignmentDialogVisible.value = false
  void router.push({ path: '/alignment', query: { bimAssetId: bim.id, pointcloudAssetId: pointcloud.id, bimDisplayName: bim.sourceName, pointcloudDisplayName: pointcloud.sourceName } })
}
async function openResult(project: ProjectSummary) {
  actionLoading.value = true
  resultOptions.value = []
  try {
    actionProject.value = project
    await loadReadyAssets(project)
    const checks = await Promise.all(bimOptions.value.flatMap((bim) => pointcloudOptions.value.map(async (pointcloud) => {
      try {
        const response = await getBimAlignment({ modelScanFileId: pointcloud.id, modelBimFileId: bim.id })
        return response.data ? { key: `${pointcloud.id}:${bim.id}`, bim, pointcloud } : null
      } catch (error: any) {
        if (error?.response?.status === 400 || error?.response?.status === 404) return null
        throw error
      }
    })))
    resultOptions.value = checks.filter((item): item is { key: string; bim: AssetSummary; pointcloud: AssetSummary } => item !== null)
    if (!resultOptions.value.length) { ElMessage.warning('该项目暂无已完成配准的实模结果'); return }
    selectedResultKey.value = resultOptions.value[0]?.key || ''
    resultDialogVisible.value = true
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载实模结果失败') }
  finally { actionLoading.value = false }
}
function confirmResult() {
  const option = resultOptions.value.find((item) => item.key === selectedResultKey.value)
  if (!option) { ElMessage.warning('请选择实模结果'); return }
  resultDialogVisible.value = false
  void router.push({ path: '/preview/split', query: { bimAssetId: option.bim.id, pointcloudAssetId: option.pointcloud.id, displayName: option.bim.sourceName, pointcloudDisplayName: option.pointcloud.sourceName } })
}
function setPage(page: number) { currentPage.value = Math.min(Math.max(1, page), pageCount.value) }
function setPageSize(event: Event) { pageSize.value = Number((event.target as HTMLSelectElement).value); currentPage.value = 1 }
function resetFilters() { filters.keyword = ''; filters.fileType = 'all'; filters.fileState = 'all'; filters.dateRange = null; currentPage.value = 1 }
function formatScanDate(value?: number) {
  if (!value) return '暂无扫描'
  return new Date(value * 1000).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
watch([() => filteredProjects.value.length, pageSize, () => filters.keyword, () => filters.fileType, () => filters.fileState, () => filters.dateRange], () => { if (currentPage.value > pageCount.value) currentPage.value = pageCount.value; if (currentPage.value < 1) currentPage.value = 1 })
watch(pagedProjects, async (rows) => {
  await nextTick()
  await Promise.all(rows.map((project) => loadProjectAssets(project)))
  rows.forEach((project) => projectTableRef.value?.toggleRowExpansion(project, true))
  expandedProjectIds.value = rows.map((project) => project.id)
}, { immediate: true, flush: 'post' })
onMounted(() => { void loadProjects() })
</script>

<template>
  <section class="project-page">
    <header class="project-header">
      <div class="project-toolbar">
        <div class="toolbar-filters">
          <el-input v-model="filters.keyword" class="project-search-input" placeholder="搜索项目名称 / 描述" :prefix-icon="Search" clearable />
          <el-select v-model="filters.fileType" class="soft-select" placeholder="文件类型">
            <el-option label="文件类型" value="all" /><el-option label="含 BIM 模型" value="bim" /><el-option label="含点云文件" value="pointcloud" />
          </el-select>
          <el-select v-model="filters.fileState" class="soft-select" placeholder="项目状态">
            <el-option label="项目状态" value="all" /><el-option label="待上传" value="pending" /><el-option label="处理中" value="processing" /><el-option label="已就绪" value="ready" /><el-option label="处理失败" value="failed" />
          </el-select>
          <el-date-picker v-model="filters.dateRange" class="date-range-field is-date" type="daterange" unlink-panels format="YYYY/MM/DD" range-separator="-" start-placeholder="开始日期" end-placeholder="结束日期" clearable />
        </div>
        <div class="toolbar-actions">
          <button class="toolbar-button" type="button" @click="resetFilters"><el-icon :size="16"><Refresh /></el-icon>重置</button>
          <button class="toolbar-button" type="button" @click="openCreate"><el-icon :size="16"><CirclePlus /></el-icon>新增项目</button>
        </div>
      </div>
    </header>
    <section class="project-table card">
      <h2 class="table-title">项目列表</h2>
    <el-table ref="projectTableRef" class="zeemo-table" v-loading="loading" :data="pagedProjects" row-key="id" highlight-current-row empty-text="还没有项目资产" @expand-change="handleExpandChange">
        <el-table-column type="expand" width="54">
          <template #default="{ row }">
            <div class="asset-child-panel" v-loading="assetLoading[row.id]">
              <div class="asset-child-head"><div><strong>{{ row.name }} · 项目资产</strong><span>共 {{ projectAssets[row.id]?.length || 0 }} 个资产</span></div><el-button text type="primary" :icon="Refresh" @click="loadProjectAssets(row, true)">刷新</el-button></div>
              <el-table class="asset-child-table" :data="projectAssets[row.id] || []" row-key="id" empty-text="该项目暂未上传文件">
                <el-table-column label="文件名称" width="210" align="left"><template #default="{ row: asset }"><div class="asset-name-cell"><span class="asset-type-icon" :class="asset.type">{{ asset.type === 'bim' ? 'BIM' : '点云' }}</span><span>{{ asset.sourceName }}</span></div></template></el-table-column>
                <el-table-column label="文件大小" width="130"><template #default="{ row: asset }">{{ formatFileSize(asset.sourceSize) }}</template></el-table-column>
                <el-table-column label="状态" width="110"><template #default="{ row: asset }"><el-tag size="small" :type="assetStatusType(asset.status)">{{ assetStatusText(asset.status) }}</el-tag></template></el-table-column>
                <el-table-column label="BIM 均匀化" width="120"><template #default="{ row: asset }"><el-tag size="small" :type="uniformizationTagType(asset)">{{ uniformizationText(asset) }}</el-tag></template></el-table-column>
                <el-table-column label="上传时间" min-width="190"><template #default="{ row: asset }">{{ formatAssetDate(asset.createdAt) }}</template></el-table-column>
                <el-table-column label="操作" width="110" fixed="right" class-name="asset-operation-column" label-class-name="asset-operation-column"><template #default="{ row: asset }"><div class="child-actions"><button class="child-preview-button" type="button" title="预览文件" :disabled="asset.status !== 'ready'" @click="previewAsset(asset)"><el-icon><View /></el-icon></button><button class="child-delete-button" type="button" title="删除文件" @click="removeAsset(row, asset)"><el-icon><Delete /></el-icon></button></div></template></el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="项目名称" min-width="220"><template #default="{ row }"><div class="project-name-cell"><span class="project-icon"><el-icon><Folder /></el-icon></span><div><strong>{{ row.name }}</strong><small>{{ row.description || '暂无描述' }}</small></div></div></template></el-table-column>
        <el-table-column prop="assetCount" label="文件总数" width="110" align="center" />
        <el-table-column label="文件构成" width="220"><template #default="{ row }"><div class="file-counts"><el-tag size="small" type="primary">BIM {{ row.bimCount }}</el-tag><el-tag size="small" type="success">点云 {{ row.pointcloudCount }}</el-tag></div></template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag size="small" :type="statusTagType(row.status)">{{ statusText(row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="是否配准" width="120"><template #default="{ row }"><el-tag size="small" :type="row.hasAlignment ? 'success' : 'info'">{{ row.hasAlignment ? '已配准' : '未配准' }}</el-tag></template></el-table-column>
        <el-table-column label="扫描日期" width="190"><template #default="{ row }">{{ formatScanDate(row.scanDate) }}</template></el-table-column>
        <el-table-column label="操作" width="160" fixed="right"><template #default="{ row }"><div class="row-actions"><button class="action-button is-align" type="button" title="BIM 点云配准" aria-label="BIM 点云配准" @click.stop="openAlignment(row)"><el-icon><Aim /></el-icon></button><button class="action-button is-result" type="button" title="查看实模结果" aria-label="查看实模结果" @click.stop="openResult(row)"><el-icon><DataAnalysis /></el-icon></button><el-dropdown trigger="click" placement="bottom-end" @command="(command: string) => handleMoreCommand(command, row)" @click.stop><button class="action-button is-more" type="button" title="更多操作" aria-label="更多操作"><el-icon><MoreFilled /></el-icon></button><template #dropdown><el-dropdown-menu><el-dropdown-item command="edit" :icon="Edit">编辑项目</el-dropdown-item><el-dropdown-item command="delete" :icon="Delete" divided class="danger-dropdown-item">删除项目</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></template></el-table-column>
      </el-table>
    </section>
    <footer class="project-table-footer">
      <div class="pagination">
        <span class="pager-summary">共 {{ filteredProjects.length }} 条</span>
        <button class="pager-button" :disabled="currentPage <= 1" @click="setPage(currentPage - 1)">‹</button>
        <template v-for="page in visiblePages" :key="String(page)">
          <button v-if="typeof page === 'number'" class="pager-button" :class="{ active: page === currentPage }" @click="setPage(page)">{{ page }}</button>
          <span v-else class="pager-ellipsis">...</span>
        </template>
        <button class="pager-button" :disabled="currentPage >= pageCount" @click="setPage(currentPage + 1)">›</button>
        <label class="page-size"><select :value="pageSize" @change="setPageSize"><option v-for="size in [6, 8, 10, 12]" :key="size" :value="size">{{ size }} 条/页</option></select></label>
      </div>
    </footer>
    <el-dialog v-model="alignmentDialogVisible" :title="`${actionProject?.name || ''} · BIM 点云配准`" width="520px" destroy-on-close>
      <div class="action-dialog-body"><div class="action-field"><span>BIM 模型</span><el-select v-model="selectedBimId" placeholder="请选择 BIM 模型" style="width:100%"><el-option v-for="asset in bimOptions" :key="asset.id" :label="asset.sourceName" :value="asset.id" /></el-select></div><div class="action-field"><span>点云文件</span><el-select v-model="selectedPointcloudId" placeholder="请选择点云文件" style="width:100%"><el-option v-for="asset in pointcloudOptions" :key="asset.id" :label="asset.sourceName" :value="asset.id" /></el-select></div></div>
      <template #footer><el-button @click="alignmentDialogVisible=false">取消</el-button><el-button type="primary" @click="confirmAlignment">打开配准页面</el-button></template>
    </el-dialog>
    <el-dialog v-model="resultDialogVisible" :title="`${actionProject?.name || ''} · 实模结果`" width="560px" destroy-on-close>
      <div class="action-dialog-body"><div class="action-field"><span>已配准文件组</span><el-select v-model="selectedResultKey" placeholder="请选择实模结果" style="width:100%"><el-option v-for="option in resultOptions" :key="option.key" :label="`${option.bim.sourceName} / ${option.pointcloud.sourceName}`" :value="option.key" /></el-select></div></div>
      <template #footer><el-button @click="resultDialogVisible=false">取消</el-button><el-button type="primary" @click="confirmResult">打开实模结果</el-button></template>
    </el-dialog>
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑项目' : '新建项目'" width="480px"><el-form label-position="top"><el-form-item label="项目名称"><el-input v-model="form.name" maxlength="160" show-word-limit placeholder="例如：上海中心二期" /></el-form-item><el-form-item label="项目描述"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="500" placeholder="补充项目位置、阶段等信息" /></el-form-item></el-form><template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" @click="saveProject">保存</el-button></template></el-dialog>
  </section>
</template>

<style scoped>
.project-page{box-sizing:border-box;width:100%;height:100%;min-height:600px;display:flex;flex-direction:column;color:#233a5a}.project-header{flex:0 0 auto;margin-bottom:18px}.project-toolbar{min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:12px}.toolbar-filters{flex:1;min-width:0;display:flex;align-items:center;gap:8px}.toolbar-actions{display:flex;align-items:center;gap:8px}.project-search-input{flex:1 1 210px;min-width:170px;max-width:230px}.soft-select{width:132px}.project-toolbar :deep(.project-search-input .el-input__wrapper),.project-toolbar :deep(.soft-select .el-select__wrapper){height:40px;min-height:40px;padding-inline:10px;border:1px solid rgb(255 255 255 / 70%);border-radius:16px;background:#f5f9ff;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%) !important}.project-toolbar :deep(.date-range-field.is-date){flex:0 0 210px;width:210px;min-width:210px;max-width:210px}.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper){height:40px;min-height:40px;padding-inline:10px;border:1px solid rgb(255 255 255 / 70%);border-radius:16px;background:#f5f9ff;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%) !important}.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper.is-focus),.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper:focus-within){border-color:rgb(255 255 255 / 70%) !important;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%) !important}.project-toolbar :deep(.project-search-input .el-input__inner),.project-toolbar :deep(.soft-select .el-select__selected-item),.project-toolbar :deep(.date-range-field .el-range-input),.project-toolbar :deep(.date-range-field .el-range-separator){color:#617694;font-size:13px;font-weight:600}.project-toolbar :deep(.date-range-field .el-range-input::placeholder){color:#a2b0c4}.project-toolbar :deep(.date-range-field .el-input__icon){color:#96a7bf}.toolbar-button{height:40px;min-height:40px;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:0;border-radius:14px;padding:0 16px;color:#657a99;background:#f3f7fd;box-shadow:inset 1px 1px 1px rgb(255 255 255 / 95%),inset -2px -2px 4px rgb(180 200 230 / 12%),6px 6px 12px rgb(174 190 215 / 12%),-6px -6px 12px rgb(255 255 255 / 92%);font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap}.toolbar-button:hover{color:#4b7fd8}.card{background:rgb(255 255 255 / 88%);border:1px solid rgb(214 223 236 / 72%);border-radius:18px;box-shadow:7px 7px 16px rgb(163 177 198 / 22%),-7px -7px 16px rgb(255 255 255 / 82%);padding:18px}.project-table{min-height:0;display:flex;flex:1 1 auto;flex-direction:column;margin-bottom:0}.table-title{flex:0 0 auto;margin:0 0 14px;color:#233a5a;font-size:17px;font-weight:650}.project-name-cell{display:flex;align-items:center;justify-content:center;gap:10px}.project-name-cell strong,.project-name-cell small{display:block}.project-name-cell strong{font-size:14px;color:#274c7d}.project-name-cell small{font-size:12px;color:#7f90a8;margin-top:3px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.project-icon{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#eef2ff;color:#4f46e5}.file-counts{display:flex;justify-content:center;gap:6px}.row-actions{display:flex;align-items:center;justify-content:center;gap:8px}.action-button{width:34px;height:34px;display:grid;place-items:center;flex:0 0 34px;padding:0;border:0;border-radius:10px;background:#f5f8fd;color:#7185a3;box-shadow:4px 4px 9px rgb(169 184 210 / 18%),-4px -4px 9px rgb(255 255 255 / 82%);cursor:pointer}.action-button:hover{transform:translateY(-1px)}.action-button.is-align:hover{color:#7c5ce0;background:#f3efff}.action-button.is-result:hover{color:#0e9f78;background:#ebfbf5}.action-button.is-more:hover{color:#4a6b9f;background:#eef4fb}.action-dialog-body{display:flex;flex-direction:column;gap:18px;padding:8px 0}.action-field{display:flex;flex-direction:column;gap:8px}.action-field>span{color:#53657e;font-size:13px;font-weight:600}.zeemo-table{min-height:0;flex:1 1 auto;width:100%;border-radius:14px;overflow:hidden;background:transparent}.zeemo-table :deep(.el-table__inner-wrapper::before){display:none}.zeemo-table :deep(.el-table__header-wrapper th.el-table__cell){height:52px;border:0;background:#f4f8fd;color:#6f7d95;font-size:13px;font-weight:600;text-align:center}.zeemo-table :deep(.el-table__header-wrapper th .cell),.zeemo-table :deep(.el-table__body td .cell){text-align:center}.zeemo-table :deep(.el-table__body tr){height:61px;background:#fff;box-shadow:0 8px 18px rgb(163 177 198 / 16%)}.zeemo-table :deep(.el-table__body tr + tr td){border-top:10px solid transparent}.zeemo-table :deep(.el-table__body td.el-table__cell){padding:0 12px;border-top:1px solid rgb(222 231 244 / 72%);border-bottom:1px solid rgb(214 225 240 / 62%);background:#fff;font-size:13px;font-weight:500;text-align:center}.zeemo-table :deep(.el-table__body tr:hover > td.el-table__cell){background:#f6faff}.zeemo-table :deep(.el-table__body tr td:first-child){border-left:1px solid rgb(222 231 244 / 72%);border-radius:14px 0 0 14px}.zeemo-table :deep(.el-table__body tr td:last-child){border-right:1px solid rgb(214 225 240 / 62%);border-radius:0 14px 14px 0}.project-table-footer{flex:0 0 auto;display:flex;justify-content:center;margin-top:auto;padding:18px 0 4px}.pagination{display:flex;align-items:center;justify-content:center;gap:8px;color:#6f82a0}.pager-summary{margin-right:4px;font-size:13px;font-weight:400;white-space:nowrap}.pager-button,.page-size{height:34px;border:0;border-radius:10px;background:#f5f8fd;color:#7b8ca7;box-shadow:5px 5px 12px rgb(169 184 210 / 22%),-5px -5px 12px rgb(255 255 255 / 86%),inset 1px 1px 0 rgb(255 255 255 / 66%)}.pager-button{min-width:34px;padding:0 10px;font-size:13px;font-weight:400;cursor:pointer}.pager-button.active{color:#fff;background:linear-gradient(145deg,#88b4f8,#73a2f3)}.pager-button:disabled{color:#b8c2d2;background:transparent;box-shadow:none;cursor:not-allowed}.pager-ellipsis{padding:0 3px;color:#9caac0;font-weight:400}.page-size{display:inline-flex;align-items:center;overflow:hidden;margin-left:4px}.page-size select{height:34px;min-width:100px;padding:0 12px;border:0;outline:0;color:#536a8c;background:transparent;font-size:13px;font-weight:400;cursor:pointer}@media(max-width:900px){.project-toolbar{display:block}.toolbar-filters{flex-wrap:wrap}.project-search-input{max-width:none}.toolbar-actions{margin-top:12px;justify-content:flex-end}}@media(max-width:620px){.project-page{min-height:700px}.pagination{flex-wrap:wrap}.project-table{padding:12px}.zeemo-table{min-width:900px;overflow:auto}}
.zeemo-table :deep(.el-table__expanded-cell){padding:0 16px 14px !important;border:0 !important;background:#f7faff !important}.asset-child-panel{margin:0 8px;padding:16px;border:1px solid #e3ebf6;border-radius:14px;background:#f8fbff;box-shadow:inset 2px 2px 6px rgb(174 190 215 / 10%)}.asset-child-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;padding:0 4px}.asset-child-head>div{display:flex;align-items:baseline;gap:10px}.asset-child-head strong{color:#35577f;font-size:14px}.asset-child-head span{color:#8a9ab0;font-size:12px}.asset-child-table{width:100%;border-radius:12px;background:transparent}.asset-child-table :deep(.el-table__inner-wrapper::before){display:none}.zeemo-table .asset-child-table :deep(.el-table__header-wrapper th.el-table__cell){height:34px;padding:0;border:0;background:#edf4fc;color:#70839f;font-size:12px;font-weight:600;text-align:center}.zeemo-table .asset-child-table :deep(.el-table__header-wrapper th .cell),.zeemo-table .asset-child-table :deep(.el-table__body td .cell){text-align:center}.zeemo-table .asset-child-table :deep(.el-table__body tr){height:48px;background:#fff;box-shadow:none}.zeemo-table .asset-child-table :deep(.el-table__body tr + tr td){border-top:0}.zeemo-table .asset-child-table :deep(.el-table__body td.el-table__cell){padding:5px 8px;border:0;border-bottom:1px solid #edf1f6;border-radius:0;background:#fff;font-size:12px;font-weight:400;text-align:center}.asset-name-cell{min-width:0;display:flex;align-items:center;justify-content:center;gap:10px;text-align:center}.asset-name-cell>span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.asset-type-icon{width:34px;height:25px;display:grid;place-items:center;flex:0 0 34px;border-radius:7px;background:#eef4ff;color:#4f78bf;font-size:10px;font-weight:700}.asset-type-icon.pointcloud{background:#eaf9f4;color:#16866a}.child-actions{display:flex;align-items:center;justify-content:center;gap:8px}.child-preview-button,.child-delete-button{width:30px;height:30px;display:grid;place-items:center;padding:0;border:0;border-radius:9px;cursor:pointer}.child-preview-button{background:#eef5ff;color:#4a90e2}.child-preview-button:disabled{background:#f2f4f7;color:#b8c0cc;cursor:not-allowed}.child-delete-button{background:#fff1f2;color:#df5e67}.child-delete-button:hover{background:#ffe6e8}.danger-dropdown-item{color:#e15b64}
.asset-child-table :deep(.el-table-fixed-column--right){background:#fff !important}.asset-child-table :deep(.el-table__header-wrapper th:first-child .cell),.asset-child-table :deep(.el-table__body-wrapper td:first-child .cell){text-align:left !important}.asset-name-cell{justify-content:flex-start;text-align:left}.asset-name-cell>span:last-child{min-width:0}.pagination{padding:8px 12px}.pager-button,.page-size{background:#fff;box-shadow:5px 5px 12px rgb(169 184 210 / 22%),-5px -5px 12px rgb(255 255 255 / 96%),inset 1px 1px 0 rgb(255 255 255 / 92%)}.pager-button:disabled{background:#fff}
</style>
