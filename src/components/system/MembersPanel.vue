<template>
  <div class="members-panel">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>成员与权限</h2>
          <p>工作区成员的角色、账号状态与数据归属。</p>
        </div>
        <div class="heading-actions">
          <span class="status-chip">{{ summary }}</span>
          <button class="action" type="button" :disabled="loading" @click="reload">
            {{ loading ? '刷新中...' : '刷新' }}
          </button>
        </div>
      </div>

      <p v-if="!canManage" class="notice">
        当前角色为{{ viewerRoleLabel }}，仅显示本账号的工作区记录；成员角色与账号状态由管理员维护。
      </p>

      <p v-else class="notice">
        工作区须保留至少一名启用的管理员；不能修改自己的角色或状态；成员仍拥有项目、资产或未完成上传时不可删除。
      </p>

      <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
      <details v-if="canManage" class="operator-account-create">
        <summary>创建工人账号</summary>
        <form class="operator-account-form" @submit.prevent="createWorker">
          <label>登录账号<input v-model="newWorker.username" required autocomplete="off" maxlength="64" placeholder="例如 worker01" /></label>
          <label>工人姓名<input v-model="newWorker.displayName" maxlength="100" placeholder="用于操作记录" /></label>
          <label>初始密码<input v-model="newWorker.password" type="password" required minlength="8" autocomplete="new-password" placeholder="至少8位" /></label>
          <label>分配项目<select v-model="newWorker.projectId"><option value="">暂不分配</option><option v-for="p in projects" :key="p.id" :value="String(p.id)">{{ p.name }}</option></select></label>
          <button type="submit" class="action" :disabled="creating">{{ creating ? '正在创建…' : '创建工人账号' }}</button>
        </form><p class="muted">工人只查看已分配项目的检测结果、定位问题和登记处理；无法修改设计、算法参数或管理成员。</p>
        <p v-if="createError" class="panel-error" role="alert">{{ createError }}</p>
      </details>

      <template v-if="members.length">
        <div class="table-scroll">
          <table class="member-table">
            <thead>
              <tr>
                <th scope="col">成员</th>
                <th scope="col">角色</th>
                <th scope="col">分配项目</th><th scope="col">状态</th>
                <th scope="col">数据量</th>
                <th scope="col">最近登录</th>
                <th scope="col" class="is-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="member in members" :key="member.id">
                <td>
                  <div class="member-cell">
                    <span class="member-avatar">{{ (member.displayName || member.username).slice(0, 1).toUpperCase() }}</span>
                    <div class="member-text">
                      <strong>{{ member.displayName || member.username }}</strong>
                      <span>
                        {{ member.username }}
                        <template v-if="member.isSelf"> · 当前账号</template>
                        <template v-if="member.email"> · {{ member.email }}</template>
                      </span>
                    </div>
                  </div>
                </td>
                <td>
                  <el-select
                    v-if="canManage && !member.isSelf"
                    :model-value="draftRole(member)"
                    size="default"
                    :disabled="savingId === member.id"
                    aria-label="成员角色"
                    @update:model-value="(value: string) => setDraftRole(member, value)"
                  >
                    <el-option label="管理员" value="admin" />
                    <el-option label="项目成员" value="member" /><el-option label="现场工人" value="operator" />
                  </el-select>
                  <span v-else class="status-chip" :class="member.role === 'admin' ? 'is-ok' : ''">{{ member.roleLabel }}</span>
                </td>
                <td><select v-if="canManage && !member.isSelf && draftRole(member)==='operator'" class="worker-project-select" :value="draftProject(member) || ''" :disabled="savingId===member.id" :aria-label="`分配项目 ${member.username}`" @change="setDraftProject(member, ($event.target as HTMLSelectElement).value)"><option value="">暂不分配</option><option v-if="draftProject(member) && !projects.some(p=>p.id===draftProject(member))" :value="String(draftProject(member))">已分配项目 {{ draftProject(member) }}</option><option v-for="p in projects" :key="p.id" :value="String(p.id)">{{ p.name }}</option></select><span v-else class="muted">{{ member.role==='operator' ? (projects.find(p=>p.id===member.operatorProjectId)?.name || '未分配') : '自有项目' }}</span></td>
                <td>
                  <el-select
                    v-if="canManage && !member.isSelf"
                    :model-value="draftStatus(member)"
                    size="default"
                    :disabled="savingId === member.id"
                    aria-label="成员状态"
                    @update:model-value="(value: string) => setDraftStatus(member, value)"
                  >
                    <el-option label="启用" value="active" />
                    <el-option label="停用" value="disabled" />
                  </el-select>
                  <span v-else class="status-chip" :class="member.status === 'disabled' ? 'is-danger' : 'is-ok'">
                    {{ member.status === 'disabled' ? '已停用' : '启用中' }}
                  </span>
                </td>
                <td>
                  <span class="numeric">{{ member.projectCount }} 项目 / {{ member.assetCount }} 资产</span>
                  <small class="muted">对齐 {{ member.alignmentCount }} · 量测 {{ member.measurementCount }}</small>
                </td>
                <td>{{ formatDateTime(member.lastLoginAt) }}</td>
                <td class="is-actions">
                  <div class="row-actions">
                    <span v-if="member.isLastAdmin" class="status-chip is-warning">唯一管理员</span>
                    <template v-if="canManage && !member.isSelf">
                      <button
                        class="action"
                        type="button"
                        :disabled="!isDirty(member) || savingId === member.id"
                        @click="saveMember(member)"
                      >
                        {{ savingId === member.id ? '保存中...' : '保存' }}
                      </button>
                      <button
                        class="action is-danger"
                        type="button"
                        :disabled="savingId === member.id || deletingId === member.id || hasOwnedData(member)"
                        :title="hasOwnedData(member) ? '该成员仍有数据归属，需先移交或清理后再删除' : `删除成员 ${member.displayName || member.username}`"
                        @click="removeMember(member)"
                      >
                        {{ deletingId === member.id ? '删除中...' : '删除' }}
                      </button>
                    </template>
                    <span v-else-if="member.isSelf" class="muted">当前账号</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <strong>暂无成员数据</strong>
        <span>工作区还没有可管理的成员账号。</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createOperatorAccount,
  deleteWorkspaceMember,
  listWorkspaceMembers,
  updateWorkspaceMember,
  type MemberRole,
  type MemberStatus,
  type WorkspaceMember,
} from '@/api/backend-system'

import { listProjects, type ProjectSummary } from '@/api/backend-project'

const props = defineProps<{ viewerRole: MemberRole }>()
const emit = defineEmits<{ updated: [] }>()

const projects = ref<ProjectSummary[]>([])
const creating = ref(false); const createError = ref('')
const newWorker = reactive({username:'',displayName:'',password:'',projectId:''})
const members = ref<WorkspaceMember[]>([])
const canManage = ref(false)
const loading = ref(false)
const error = ref('')
const savingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)

// Draft values keep the selects responsive while an explicit save applies them.
const drafts = reactive<Record<number, { role: MemberRole; status: MemberStatus; projectId: number | null }>>({})

const viewerRoleLabel = computed(() => (props.viewerRole === 'admin' ? '管理员' : '项目成员'))
const summary = computed(() => {
  if (!members.value.length) {
    return '暂无成员'
  }

  const admins = members.value.filter((member) => member.role === 'admin' && member.status === 'active').length
  const disabled = members.value.filter((member) => member.status === 'disabled').length

  return `共 ${members.value.length} 名成员 · 管理员 ${admins} 名${disabled ? ` · 已停用 ${disabled} 名` : ''}`
})

function formatDateTime(value?: string | null) {
  if (!value) {
    return '暂无记录'
  }

  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '暂无记录' : date.toLocaleString('zh-CN', { hour12: false })
}

function draftRole(member: WorkspaceMember): MemberRole {
  return drafts[member.id]?.role ?? member.role
}

function draftStatus(member: WorkspaceMember): MemberStatus {
  return drafts[member.id]?.status ?? member.status
}

function setDraftRole(member: WorkspaceMember, value: string) {
  drafts[member.id] = { role: value === 'admin' ? 'admin' : value === 'operator' ? 'operator' : 'member', status: draftStatus(member), projectId: value === 'operator' ? draftProject(member) : null }
}

function setDraftStatus(member: WorkspaceMember, value: string) {
  drafts[member.id] = { role: draftRole(member), status: value === 'disabled' ? 'disabled' : 'active', projectId: draftProject(member) }
}

function draftProject(member: WorkspaceMember): number | null { return drafts[member.id] ? drafts[member.id]!.projectId : member.operatorProjectId ?? null }
function setDraftProject(member: WorkspaceMember, value: string) { drafts[member.id] = { role: draftRole(member), status: draftStatus(member), projectId: value ? Number(value) : null } }
async function createWorker() {
  if (creating.value) return
  creating.value=true;createError.value=''
  try { await createOperatorAccount({username:newWorker.username.trim(),displayName:newWorker.displayName.trim(),password:newWorker.password,operatorProjectId:newWorker.projectId?Number(newWorker.projectId):null});newWorker.password='';newWorker.username='';newWorker.displayName='';ElMessage.success('工人账号已创建，登录后直接进入工人操作页。');await reload();emit('updated') }
  catch(e) { createError.value=e instanceof Error?e.message:'账号创建失败，请检查后重试。' }
  finally { creating.value=false }
}
function isDirty(member: WorkspaceMember) {
  const draft = drafts[member.id]

  return Boolean(draft) && (draft.role !== member.role || draft.status !== member.status || draft.projectId !== (member.operatorProjectId ?? null))
}

// Deleting a member that still owns work is rejected by the server; reflecting the
// same rule here keeps the destructive action from failing after confirmation.
function hasOwnedData(member: WorkspaceMember) {
  return (
    member.projectCount + member.assetCount + member.alignmentCount + member.measurementCount > 0
  )
}

async function reload() {
  loading.value = true
  error.value = ''

  try {
    const result = await listWorkspaceMembers()
    members.value = result.data.list
    canManage.value = result.data.canManage
    if (canManage.value) projects.value = (await listProjects()).data.list
    Object.keys(drafts).forEach((key) => delete drafts[Number(key)])
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : '读取成员列表失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function saveMember(member: WorkspaceMember) {
  if (savingId.value !== null || !isDirty(member)) {
    return
  }

  savingId.value = member.id

  try {
    await updateWorkspaceMember(member.id, {
      role: draftRole(member),
      status: draftStatus(member),
      operatorProjectId: draftRole(member)==='operator' ? draftProject(member) : null,
    })
    ElMessage.success(`已更新 ${member.displayName || member.username} 的角色与状态。`)
    await reload()
    emit('updated')
  } catch (saveError) {
    ElMessage.error(saveError instanceof Error ? saveError.message : '保存成员变更失败，请稍后重试。')
  } finally {
    savingId.value = null
  }
}

async function removeMember(member: WorkspaceMember) {
  const name = member.displayName || member.username

  try {
    await ElMessageBox.confirm(
      `确认删除成员「${name}」？仅当该成员没有项目、资产与未完成上传时可删除，操作不可撤销。`,
      '删除成员',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingId.value = member.id

  try {
    await deleteWorkspaceMember(member.id)
    ElMessage.success(`成员「${name}」已删除。`)
    await reload()
    emit('updated')
  } catch (deleteError) {
    ElMessage.error(deleteError instanceof Error ? deleteError.message : '删除成员失败，请稍后重试。')
  } finally {
    deletingId.value = null
  }
}

onMounted(reload)
</script>

<style scoped lang="scss">
@use '@/styles/system-panels' as panels;
@use '@cloudbim/viewer-core/styles/workspace-controls.scss' as controls;

.members-panel { display: flex; flex-direction: column; gap: var(--workspace-gap); min-width: 0; }
.panel { @include panels.panel; }
.panel-heading { @include panels.panel-heading; }
.status-chip { @include panels.status-chip; }
.notice { @include panels.notice; }
.empty-state { @include panels.empty-state; }
.action { @include controls.action; }
.action.is-danger { color: var(--color-danger); border-color: var(--color-danger); }
.panel-error { @include panels.notice; border-left-color: var(--color-danger); color: var(--color-danger); }

.heading-actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--spacing-sm); }
.table-scroll { min-width: 0; overflow-x: auto; }

.member-table {
  width: 100%;
  min-width: 880px;
  border-collapse: collapse;
  color: var(--text-secondary);
  font-size: var(--font-size-sm);

  th, td { padding: 0 8px; text-align: left; vertical-align: middle; }
  th { height: 40px; border-bottom: 1px solid var(--border-color-light); color: var(--text-secondary); font-weight: 600; }
  td { height: 64px; border-bottom: 1px solid var(--border-color-light); }
  tbody tr:last-child td { border-bottom: 0; }
  th.is-actions, td.is-actions { text-align: right; }
  :deep(.el-select) { width: 128px; }
}

.member-cell { display: flex; align-items: center; gap: var(--spacing-sm); min-width: 0; }
.member-avatar {
  display: grid; place-items: center; flex: 0 0 32px; height: 32px;
  border-radius: var(--radius-pill);
  color: var(--color-primary);
  background: var(--color-primary-soft);
  font-weight: 650;
}
.member-text { display: flex; flex-direction: column; min-width: 0; }
.member-text strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary); font-weight: 600; }
.member-text span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-tertiary); font-size: var(--font-size-xs); }
.numeric { display: block; font-variant-numeric: tabular-nums; color: var(--text-primary); }
.muted { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.row-actions { display: flex; align-items: center; justify-content: flex-end; gap: var(--spacing-sm); }
</style>

<style scoped>
.operator-account-create{border-block:1px solid var(--border-color-light);padding:16px 0;margin:16px 0}.operator-account-create summary{font-weight:600;color:var(--brand-aether);cursor:pointer}.operator-account-form{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr)) auto;gap:12px;align-items:end;margin:18px 0}.operator-account-form label{display:flex;flex-direction:column;gap:6px;font-size:14px}.operator-account-form input,.operator-account-form select,.worker-project-select{height:40px;border:1px solid var(--border-color);border-radius:7px;background:white;color:var(--text-primary);font:inherit;padding:8px 10px;min-width:0;width:100%}.worker-project-select{min-width:150px;max-width:260px}.operator-account-form :is(input,select):focus-visible,.worker-project-select:focus-visible{outline:2px solid var(--brand-aether);outline-offset:2px}@media(max-width:1200px){.operator-account-form{grid-template-columns:1fr 1fr}}
</style>
