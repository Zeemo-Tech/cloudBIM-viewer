<template>
  <div class="account-panel">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>账号资料</h2>
          <p>账号标识与联系方式，保存后立即写入服务端并同步到页头显示。</p>
        </div>
        <span class="status-chip" :class="isAdmin ? 'is-ok' : ''">{{ roleLabel }}</span>
      </div>

      <p v-if="loadError" class="panel-error" role="alert">{{ loadError }}</p>

      <form class="profile-form" @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <el-input :model-value="profile?.username || ''" disabled />
        </label>
        <label class="field">
          <span>昵称</span>
          <el-input v-model="form.displayName" maxlength="128" placeholder="请输入昵称" :disabled="saving || loading" />
        </label>
        <label class="field">
          <span>邮箱</span>
          <el-input v-model="form.email" maxlength="160" placeholder="name@example.com" :disabled="saving || loading" />
        </label>
        <label class="field">
          <span>联系电话</span>
          <el-input v-model="form.phone" maxlength="32" placeholder="请输入联系电话" :disabled="saving || loading" />
        </label>

        <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>

        <div class="form-actions">
          <button class="action is-primary" type="submit" :disabled="saving || loading || !isDirty">
            {{ saving ? '保存中...' : '保存资料' }}
          </button>
          <button class="action" type="button" :disabled="saving || loading || !isDirty" @click="resetForm">
            重置
          </button>
        </div>
      </form>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>账号信息</h2>
          <p>服务端记录的账号状态与数据归属。</p>
        </div>
        <button class="action" type="button" :disabled="loading" @click="reload">刷新</button>
      </div>

      <dl class="facts">
        <div class="fact"><dt>角色</dt><dd>{{ roleLabel }}</dd></div>
        <div class="fact"><dt>账号状态</dt><dd>{{ profile?.status === 'disabled' ? '已停用' : '启用中' }}</dd></div>
        <div class="fact"><dt>注册时间</dt><dd>{{ formatDateTime(profile?.createdAt) }}</dd></div>
        <div class="fact"><dt>最近登录</dt><dd>{{ formatDateTime(profile?.lastLoginAt) }}</dd></div>
        <div class="fact"><dt>资料更新时间</dt><dd>{{ formatDateTime(profile?.updatedAt) }}</dd></div>
        <div class="fact"><dt>我的项目</dt><dd>{{ count(profile?.projectCount) }}</dd></div>
        <div class="fact"><dt>我的资产</dt><dd>{{ count(profile?.assetCount) }}</dd></div>
        <div class="fact"><dt>我的对齐记录</dt><dd>{{ count(profile?.alignmentCount) }}</dd></div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { loadAccountProfile, saveAccountProfile } from '@/features/auth/auth.service'
import type { AccountProfile } from '@/api/backend-auth'

const emit = defineEmits<{ updated: [profile: AccountProfile] }>()

const profile = ref<AccountProfile | null>(null)
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const formError = ref('')

const form = reactive({ displayName: '', email: '', phone: '' })

const isAdmin = computed(() => profile.value?.role === 'admin')
const roleLabel = computed(() => (isAdmin.value ? '管理员' : '项目成员'))
const isDirty = computed(() => {
  if (!profile.value) {
    return false
  }

  return (
    form.displayName.trim() !== (profile.value.displayName || '').trim() ||
    form.email.trim() !== (profile.value.email || '').trim() ||
    form.phone.trim() !== (profile.value.phone || '').trim()
  )
})

function count(value?: number) {
  return (value ?? 0).toLocaleString('zh-CN')
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return '暂无记录'
  }

  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '暂无记录' : date.toLocaleString('zh-CN', { hour12: false })
}

function resetForm() {
  form.displayName = profile.value?.displayName || ''
  form.email = profile.value?.email || ''
  form.phone = profile.value?.phone || ''
  formError.value = ''
}

async function reload() {
  loading.value = true
  loadError.value = ''

  try {
    profile.value = await loadAccountProfile()
    resetForm()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取账号资料失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

function validate() {
  if (form.email.trim() && !form.email.includes('@')) {
    return '邮箱格式不正确。'
  }

  if (form.displayName.trim().length > 128) {
    return '昵称不能超过 128 个字符。'
  }

  if (form.phone.trim().length > 32) {
    return '联系电话不能超过 32 个字符。'
  }

  return ''
}

async function submit() {
  if (saving.value) {
    return
  }

  formError.value = validate()

  if (formError.value) {
    return
  }

  saving.value = true

  try {
    const next = await saveAccountProfile({
      displayName: form.displayName.trim(),
      email: form.email.trim(),
      phone: form.phone.trim(),
    })
    profile.value = next
    resetForm()
    ElMessage.success('账号资料已保存。')
    emit('updated', next)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '保存账号资料失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

onMounted(reload)
</script>

<style scoped lang="scss">
@use '@/styles/system-panels' as panels;
@use '@cloudbim/viewer-core/styles/workspace-controls.scss' as controls;

.account-panel { display: flex; flex-direction: column; gap: var(--workspace-gap); min-width: 0; }
.panel { @include panels.panel; }
.panel-heading { @include panels.panel-heading; }
.facts { @include panels.facts; }
.status-chip { @include panels.status-chip; }
.panel-error { @include panels.notice; border-left-color: var(--color-danger); color: var(--color-danger); }
.field-error { @include panels.notice; margin: 0; border-left-color: var(--color-danger); color: var(--color-danger); }

.profile-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--spacing-md); }
.field { display: flex; flex-direction: column; gap: var(--spacing-xs); min-width: 0; }
.field > span { color: var(--text-secondary); font-size: var(--font-size-sm); font-weight: 500; }
.field-error, .form-actions { grid-column: 1 / -1; }
.form-actions { display: flex; gap: var(--spacing-sm); }

.action { @include controls.action; }
.action.is-primary { @include controls.primary; }

@include panels.form-controls;
</style>
