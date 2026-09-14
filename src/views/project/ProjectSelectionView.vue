<script setup lang="ts">
import { readListState, writeListState } from '@/features/workspace/listState'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, CirclePlus, Delete, Edit, Folder, Refresh, Search, SwitchButton } from '@element-plus/icons-vue'
import { createProject, deleteProject, listProjects, updateProject, type ProjectSummary } from '@/api/backend-project'
import type { AuthSession } from '@/features/auth/auth.service'
import NeumorphicPagination from '@/components/NeumorphicPagination.vue'

const props = defineProps<{ session: AuthSession }>()
const emit = defineEmits<{ logout: [] }>()
const router = useRouter()
const projects = ref<ProjectSummary[]>([])
const loading = ref(false)
const listStateKey = `cloudbim.list.v1:${props.session.username}:projects:projects`
const restoredList = readListState(listStateKey, { keyword: '', fileType: 'all', fileState: 'all', dateRange: null as [Date, Date] | null }, [8, 12, 16, 24])
const currentPage = ref(restoredList.page)
const pageSize = ref(restoredList.pageSize)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const form = reactive({ name: '', description: '' })
const filters = reactive(restoredList.filters)
const filteredProjects = computed(() => projects.value.filter((project) => {
  const keyword = filters.keyword.trim().toLowerCase()
  const scanDate = project.scanDate ? project.scanDate * 1000 : 0
  const matchesDate = !filters.dateRange || (scanDate >= new Date(filters.dateRange[0]).setHours(0, 0, 0, 0) && scanDate <= new Date(filters.dateRange[1]).setHours(23, 59, 59, 999))
  const matchesType = filters.fileType === 'all'
    || (filters.fileType === 'bim' && project.bimCount > 0)
    || (filters.fileType === 'pointcloud' && project.pointcloudCount > 0)
  return (!keyword || `${project.name} ${project.description || ''}`.toLowerCase().includes(keyword))
    && matchesType
    && (filters.fileState === 'all' || project.status === filters.fileState)
    && matchesDate
}))
const hasActiveFilters = computed(() => Boolean(
  filters.keyword.trim()
  || filters.fileType !== 'all'
  || filters.fileState !== 'all'
  || filters.dateRange,
))
const pageCount = computed(() => Math.max(1, Math.ceil(filteredProjects.value.length / pageSize.value)))
const pagedProjects = computed(() => filteredProjects.value.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value))

async function loadProjects() {
  loading.value = true
  try {
    projects.value = (await listProjects()).data?.list || []
    if (currentPage.value > pageCount.value) currentPage.value = pageCount.value
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载项目失败')
  } finally {
    loading.value = false
  }
}

function enterProject(project: ProjectSummary) {
  void router.push({
    path: '/design/overview',
    query: { projectId: project.id, projectName: project.name },
  })
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.description = ''
  dialogVisible.value = true
}

function openEdit(project: ProjectSummary, event: Event) {
  event.stopPropagation()
  editingId.value = project.id
  form.name = project.name
  form.description = project.description || ''
  dialogVisible.value = true
}

async function saveProject() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入项目名称')
    return
  }
  saving.value = true
  try {
    const payload = { name: form.name.trim(), description: form.description.trim() }
    if (editingId.value) await updateProject(editingId.value, payload)
    else await createProject(payload)
    dialogVisible.value = false
    ElMessage.success(editingId.value ? '项目已更新' : '项目已创建')
    await loadProjects()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存项目失败')
  } finally {
    saving.value = false
  }
}

async function removeProject(project: ProjectSummary, event: Event) {
  event.stopPropagation()
  try {
    await ElMessageBox.confirm(`确定删除项目“${project.name}”吗？项目下必须没有文件。`, '删除项目', { type: 'warning' })
    await deleteProject(project.id)
    ElMessage.success('项目已删除')
    await loadProjects()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error instanceof Error ? error.message : '删除项目失败')
  }
}

function statusText(status: ProjectSummary['status']) {
  return ({ pending: '待上传', processing: '处理中', ready: '已就绪', failed: '处理失败' } as Record<string, string>)[status] || status
}

function formatDate(value: string) {
  return value ? new Date(value).toLocaleDateString('zh-CN') : '-'
}

function resetFilters() {
  filters.keyword = ''
  filters.fileType = 'all'
  filters.fileState = 'all'
  filters.dateRange = null
  currentPage.value = 1
}

onMounted(() => { void loadProjects() })
watch([pageSize, () => filters.keyword, () => filters.fileType, () => filters.fileState, () => filters.dateRange], () => {
  currentPage.value = 1
})
watch([filters, currentPage, pageSize], () => {
  writeListState(listStateKey, { filters: filters, page: currentPage.value, pageSize: pageSize.value })
}, { deep: true, flush: 'sync' })
</script>

<template>
  <main class="project-entry-page">
    <header class="entry-header">
      <div class="entry-brand"><span class="brand-mark"><el-icon><Folder /></el-icon></span><div><strong>CloudBIM</strong><small>选择要进入的项目</small></div></div><h1 class="header-page-title">项目列表</h1>
      <div class="entry-user"><span class="user-avatar">{{ (props.session.username || 'U').slice(0, 1).toUpperCase() }}</span><span>{{ props.session.username }}</span><button type="button" title="退出登录" @click="emit('logout')"><el-icon><SwitchButton /></el-icon></button></div>
    </header>

    <section class="entry-content">
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
          <button class="toolbar-button" type="button" @click="loadProjects"><el-icon :size="16"><Refresh /></el-icon>刷新</button>
          <button class="toolbar-button is-primary" type="button" @click="openCreate"><el-icon :size="16"><CirclePlus /></el-icon>新建项目</button>
        </div>
      </div>

      <div v-loading="loading" class="project-grid">
        <article v-for="project in pagedProjects" :key="project.id" class="project-card" tabindex="0" role="link" :aria-label="`进入项目：${project.name}`" @click="enterProject(project)" @keydown.enter.self="enterProject(project)" @keydown.space.self.prevent="enterProject(project)">
          <div class="card-top"><span class="project-folder"><el-icon><Folder /></el-icon></span><div class="card-actions"><button type="button" title="编辑项目" @click="openEdit(project, $event)"><el-icon><Edit /></el-icon></button><button type="button" title="删除项目" @click="removeProject(project, $event)"><el-icon><Delete /></el-icon></button></div></div>
          <div class="card-copy"><h2>{{ project.name }}</h2><p>{{ project.description || '暂无项目描述' }}</p></div>
          <div class="card-stats"><span><strong>{{ project.assetCount }}</strong>文件</span><span><strong>{{ project.bimCount }}</strong>BIM</span><span><strong>{{ project.pointcloudCount }}</strong>点云</span></div>
          <footer><span class="project-status" :class="`is-${project.status}`">{{ statusText(project.status) }}</span><span class="project-date">更新于 {{ formatDate(project.updatedAt) }}</span><button class="enter-link" type="button" :aria-label="`进入项目：${project.name}`" @click.stop="enterProject(project)">进入项目<el-icon><ArrowRight /></el-icon></button></footer>
        </article>

        <button v-if="!loading && !projects.length" class="empty-card" type="button" @click="openCreate"><el-icon><CirclePlus /></el-icon><strong>创建第一个项目</strong><span>开始管理 BIM 与点云文件</span></button>
        <div v-else-if="!loading && !filteredProjects.length" class="empty-card"><el-icon><Search /></el-icon><strong>没有匹配的项目</strong><span>当前筛选条件下没有结果</span><button v-if="hasActiveFilters" class="empty-action" type="button" @click="resetFilters">清除筛选</button></div>
      </div>
      <NeumorphicPagination v-model:current-page="currentPage" v-model:page-size="pageSize" :total="filteredProjects.length" :page-size-options="[8, 12, 16, 24]" aria-label="项目列表分页" />
    </section>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑项目' : '新建项目'" width="480px">
      <el-form label-position="top"><el-form-item label="项目名称"><el-input v-model="form.name" maxlength="160" placeholder="请输入项目名称" /></el-form-item><el-form-item label="项目描述"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="500" placeholder="补充项目位置、阶段等信息" /></el-form-item></el-form>
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveProject">保存</el-button></template>
    </el-dialog>
  </main>
</template>

<style scoped>
.project-entry-page{min-height:100vh;color:#263b59;background:radial-gradient(circle at 12% 10%,rgb(219 234 254 / 75%),transparent 30%),radial-gradient(circle at 88% 8%,rgb(224 231 255 / 70%),transparent 32%),#f5f8fc}.entry-header{box-sizing:border-box;height:82px;display:flex;align-items:center;justify-content:space-between;padding:0 5vw;border-bottom:1px solid rgb(210 221 236 / 72%);background:rgb(255 255 255 / 72%);backdrop-filter:blur(18px)}.entry-brand,.entry-user{display:flex;align-items:center}.brand-mark{width:42px;height:42px;display:grid;place-items:center;margin-right:12px;border-radius:14px;color:#fff;background:linear-gradient(145deg,#779fee,#527dd5);box-shadow:0 10px 24px rgb(82 125 213 / 25%)}.entry-brand strong,.entry-brand small{display:block}.entry-brand strong{font-size:20px;letter-spacing:.08em}.entry-brand small{margin-top:3px;color:#8a9bb2;font-size:11px}.entry-user{gap:10px;color:#657892;font-size:13px}.user-avatar{width:36px;height:36px;display:grid;place-items:center;border-radius:50%;color:#547cc7;background:#e9f0ff;font-weight:700}.entry-user button,.card-actions button{display:grid;place-items:center;border:0;background:transparent;color:#8b9ab0;cursor:pointer}.entry-user button{width:34px;height:34px;border-radius:10px}.entry-user button:hover,.card-actions button:hover{color:#4f7ed3;background:#eef4ff}.entry-content{box-sizing:border-box;width:min(1320px,92vw);margin:0 auto;padding:62px 0}.entry-title{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:32px}.entry-title p{margin:0 0 8px;color:#7891b5;font-size:11px;font-weight:800;letter-spacing:.2em}.entry-title h1{margin:0;color:#263b59;font-size:32px;letter-spacing:.03em}.entry-title>div>span{display:block;margin-top:10px;color:#8999ae;font-size:13px}.create-button{height:44px;display:flex;align-items:center;gap:7px;padding:0 18px;border:0;border-radius:14px;color:#fff;background:linear-gradient(145deg,#749eea,#5680d3);box-shadow:0 12px 24px rgb(67 106 180 / 24%);cursor:pointer}.project-grid{min-height:260px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:22px}.project-card{box-sizing:border-box;min-height:270px;display:flex;flex-direction:column;padding:24px;border-radius:24px;background:rgb(255 255 255 / 88%);box-shadow:8px 10px 26px rgb(138 155 184 / 14%),-8px -8px 22px rgb(255 255 255 / 90%);cursor:pointer;transition:transform .25s ease,box-shadow .25s ease,border-color .25s ease}.project-card:hover,.project-card:focus-visible{outline:0;transform:translateY(-5px);box-shadow:12px 18px 34px rgb(120 145 184 / 20%),-8px -8px 22px #fff}.card-top{display:flex;align-items:flex-start;justify-content:space-between}.project-folder{width:48px;height:48px;display:grid;place-items:center;border-radius:16px;color:#5d83ce;background:#edf3ff;font-size:22px}.card-actions{display:flex;gap:3px;opacity:.35;transition:.2s}.project-card:hover .card-actions{opacity:1}.card-actions button{width:32px;height:32px;border-radius:9px}.card-actions button:last-child:hover{color:#d95d67;background:#fff0f1}.card-copy{margin-top:22px}.card-copy h2{margin:0;overflow:hidden;color:#2c476c;font-size:19px;text-overflow:ellipsis;white-space:nowrap}.card-copy p{height:38px;margin:8px 0 0;overflow:hidden;color:#91a0b4;font-size:12px;line-height:19px}.card-stats{display:flex;gap:8px;margin-top:20px}.card-stats span{flex:1;padding:9px 6px;border-radius:11px;color:#91a0b4;background:#f5f8fc;font-size:10px;text-align:center}.card-stats strong{margin-right:3px;color:#557397;font-size:13px}.project-card footer{display:flex;align-items:center;gap:10px;margin-top:auto;padding-top:18px}.project-status{padding:4px 8px;border-radius:99px;color:#7689a3;background:#f1f4f8;font-size:10px}.project-status.is-ready{color:#218462;background:#e9f8f1}.project-status.is-processing{color:#aa7625;background:#fff6df}.project-status.is-failed{color:#c24d58;background:#fff0f1}.project-date{color:#a1adbd;font-size:10px}.enter-link{display:flex;align-items:center;gap:3px;margin-left:auto;color:#5b84d1;font-size:12px;font-weight:600}.empty-card{min-height:260px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;border:1px dashed #b9cbe8;border-radius:24px;color:#7790b3;background:rgb(255 255 255 / 52%);cursor:pointer}.empty-card .el-icon{font-size:30px}.empty-card span{color:#9aa8ba;font-size:12px}@media(max-width:680px){.entry-header{padding-inline:18px}.entry-user>span:nth-child(2){display:none}.entry-content{padding-top:36px}.entry-title{align-items:flex-start;gap:20px}.entry-title h1{font-size:26px}.create-button{flex:0 0 auto}.project-grid{grid-template-columns:1fr}}
 </style>

<style scoped>
.entry-title {
  align-items: center;
}

.entry-title h1 {
  margin: 0;
  color: #263b59;
  font-size: 18px;
  font-weight: 650;
  letter-spacing: .03em;
}

.entry-brand strong {
  font-size: 18px;
  letter-spacing: .06em;
}

.entry-brand small {
  margin-top: 2px;
  font-size: 10px;
}

.entry-user {
  font-size: 12px;
}

.header-page-title {
  flex: 0 0 auto;
  margin: 0 0 0 24px;
  color: #263b59;
  font-size: 18px;
  font-weight: 650;
  text-align: left;
}

.entry-title {
  justify-content: flex-end;
  margin-bottom: 18px;
}

.entry-content {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 82px);
  box-sizing: border-box;
  padding-top: 34px;
}

.entry-user {
  margin-left: auto;
}

.project-grid {
  flex: 0 0 auto;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.project-toolbar {
  min-height: 68px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 50px;
}

.toolbar-filters {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.project-search-input {
  flex: 1 1 210px;
  min-width: 170px;
  max-width: 230px;
}

.soft-select {
  width: 132px;
}

.project-toolbar :deep(.project-search-input .el-input__wrapper),
.project-toolbar :deep(.soft-select .el-select__wrapper),
.project-toolbar :deep(.date-range-field.el-date-editor.el-input__wrapper) {
  height: 40px;
  min-height: 40px;
  padding-inline: 10px;
  border: 1px solid rgb(255 255 255 / 70%);
  border-radius: 16px;
  background: #f5f9ff;
  box-shadow: inset 1px 1px 1px rgb(255 255 255 / 95%), inset -2px -2px 4px rgb(180 200 230 / 12%), 6px 6px 12px rgb(174 190 215 / 12%), -6px -6px 12px rgb(255 255 255 / 92%) !important;
}

.project-toolbar :deep(.date-range-field.is-date) {
  flex: 0 0 210px;
  width: 210px;
  min-width: 210px;
  max-width: 210px;
}

.project-toolbar :deep(.project-search-input .el-input__inner),
.project-toolbar :deep(.soft-select .el-select__selected-item),
.project-toolbar :deep(.date-range-field .el-range-input),
.project-toolbar :deep(.date-range-field .el-range-separator) {
  color: #617694;
  font-size: 13px;
  font-weight: 600;
}

.project-toolbar :deep(.date-range-field .el-range-input::placeholder) {
  color: #a2b0c4;
}

.project-toolbar :deep(.date-range-field .el-input__icon) {
  color: #96a7bf;
}

.toolbar-button {
  height: 40px;
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 0;
  border-radius: 14px;
  padding: 0 16px;
  color: #657a99;
  background: #f3f7fd;
  box-shadow: inset 1px 1px 1px rgb(255 255 255 / 95%), inset -2px -2px 4px rgb(180 200 230 / 12%), 6px 6px 12px rgb(174 190 215 / 12%), -6px -6px 12px rgb(255 255 255 / 92%);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.toolbar-button:hover {
  color: #4b7fd8;
}

.create-button {
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 16px;
  border: 0;
  border-radius: 14px;
  color: #657a99;
  background: #f3f7fd;
  box-shadow: inset 1px 1px 1px rgb(255 255 255 / 95%), inset -2px -2px 4px rgb(180 200 230 / 12%), 6px 6px 12px rgb(174 190 215 / 12%), -6px -6px 12px rgb(255 255 255 / 92%);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.create-button:hover {
  color: #4b7fd8;
}

.project-card {
  border-radius: 18px;
  background: rgb(255 255 255 / 88%);
  box-shadow: 7px 7px 16px rgb(163 177 198 / 22%), -7px -7px 16px rgb(255 255 255 / 82%);
}

.project-card:hover,
.project-card:focus-visible {
  transform: translateY(-3px);
  border-color: #c5d4ea;
  box-shadow: 10px 12px 22px rgb(163 177 198 / 22%), -7px -7px 16px rgb(255 255 255 / 82%);
}

.card-copy h2 {
  font-size: 16px;
}

.card-stats span {
  background: #f3f7fd;
  box-shadow: inset 1px 1px 1px rgb(255 255 255 / 80%), inset -2px -2px 4px rgb(180 200 230 / 10%);
}

@media (max-width: 680px) {
  .header-page-title {
    margin: 0 12px;
    font-size: 16px;
  }

  .project-toolbar {
    display: block;
  }

  .toolbar-filters {
    flex-wrap: wrap;
  }

  .project-search-input {
    max-width: none;
  }

  .toolbar-actions {
    margin-top: 12px;
    justify-content: flex-end;
    flex-wrap: wrap;
  }

  .project-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) and (min-width: 681px) {
  .project-toolbar {
    display: block;
  }

  .toolbar-filters {
    flex-wrap: wrap;
  }

  .project-search-input {
    max-width: none;
  }

  .toolbar-actions {
    margin-top: 12px;
    justify-content: flex-end;
  }

  .project-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 460px) {
  .project-grid {
    grid-template-columns: 1fr;
  }
}
</style>

<style scoped>
.project-entry-page { color: var(--text-primary); background-color: var(--bg-page); }
.entry-header { height: var(--header-height); border-bottom-color: var(--border-color-light); }
.entry-brand strong, .header-page-title, .entry-title h1 { color: var(--text-primary); }
.entry-brand strong { font-size: var(--font-size-lg); }
.entry-brand small, .entry-title p, .entry-title > div > span { color: var(--text-tertiary); }
.entry-user { color: var(--text-secondary); font-size: var(--font-size-sm); }
.entry-user button, .card-actions button { color: var(--text-tertiary); }
.entry-user button:hover, .card-actions button:hover { color: var(--text-link); background: var(--color-primary-soft); }
.brand-mark { color: var(--bg-card); }
.project-folder { color: var(--color-primary); background: var(--color-primary-soft); }
.card-actions { opacity: .78; }
.card-actions button { color: var(--text-secondary); }
.card-actions button:last-child:hover { color: var(--text-danger); background: var(--color-danger-soft); }
.user-avatar { color: var(--color-primary); background: var(--color-primary-soft); }
.empty-card .el-icon { color: var(--color-primary); }
.entry-content { padding-top: var(--spacing-xl); }
.project-card { border-radius: var(--radius-lg); background: var(--bg-card-translucent); box-shadow: var(--shadow-md); }
.card-copy h2 { color: var(--text-primary); font-size: var(--font-size-md); }
.card-copy p, .card-stats span, .empty-card span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.card-stats span { background: var(--bg-control); }
.card-stats strong { color: var(--text-secondary); font-size: var(--font-size-sm); }
.project-status { color: var(--text-tertiary); background: var(--bg-muted); font-size: var(--font-size-xs); }
.project-status.is-ready { color: var(--text-success); background: var(--color-success-soft); }
.project-status.is-processing { color: var(--text-warning); background: var(--color-warning-soft); }
.project-status.is-failed { color: var(--text-danger); background: var(--color-danger-soft); }
.project-date { color: var(--text-disabled); font-size: var(--font-size-xs); }
.enter-link { color: var(--text-link); font-size: var(--font-size-xs); }
.empty-card { color: var(--text-tertiary); border-color: var(--border-color-hover); background: var(--bg-card-translucent); }
.entry-content { width: min(1800px, 92vw); }
.project-toolbar { margin-bottom: var(--spacing-lg); }
.project-grid { grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: var(--spacing-lg); }
.toolbar-button.is-primary { color: var(--bg-card); background: var(--color-primary); box-shadow: var(--shadow-sm); }
.toolbar-button.is-primary:hover { color: var(--bg-card); background: var(--color-primary-hover); }
.project-card:focus-visible { outline: 2px solid var(--border-color-focus); outline-offset: 3px; }
.card-actions button { width: 40px; height: 40px; }
.enter-link { min-height: 36px; padding: 0 2px; border: 0; background: transparent; cursor: pointer; }
.enter-link:focus-visible, .empty-action:focus-visible { outline: 2px solid var(--border-color-focus); outline-offset: 2px; }
.empty-action { min-height: var(--control-height); padding: 0 var(--spacing-md); border: 0; border-radius: var(--radius-sm); color: var(--bg-card); background: var(--color-primary); cursor: pointer; }
</style>

<style scoped lang="scss">
@use '@/styles/workspace-controls' as controls;
.toolbar-button { @include controls.action; } .toolbar-button.is-primary { @include controls.primary; } .project-toolbar { @include controls.filters; }
.entry-brand .brand-mark { background: var(--brand-sapphire); box-shadow: none; }
.project-entry-page { background: var(--bg-page); }
</style>
