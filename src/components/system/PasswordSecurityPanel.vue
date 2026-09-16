<template>
  <div class="password-panel">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>修改密码</h2>
          <p>修改成功后其他设备的登录状态立即失效，当前浏览器保持登录。</p>
        </div>
      </div>

      <form class="password-form" autocomplete="off" @submit.prevent="submitPassword">
        <label class="field">
          <span>当前密码</span>
          <el-input
            v-model="passwordForm.currentPassword"
            type="password"
            show-password
            name="cloudbim-password-current"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
            data-bwignore
            data-form-type="other"
            :disabled="changingPassword"
          />
        </label>
        <label class="field">
          <span>新密码</span>
          <el-input
            v-model="passwordForm.newPassword"
            type="password"
            show-password
            name="cloudbim-password-next"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
            data-bwignore
            data-form-type="other"
            :disabled="changingPassword"
          />
          <small>长度不少于 6 位，且不能与当前密码相同。</small>
        </label>
        <label class="field">
          <span>确认新密码</span>
          <el-input
            v-model="passwordForm.confirmPassword"
            type="password"
            show-password
            name="cloudbim-password-confirm"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
            data-bwignore
            data-form-type="other"
            :disabled="changingPassword"
          />
        </label>

        <p v-if="passwordError" class="field-error" role="alert">{{ passwordError }}</p>

        <div class="form-actions">
          <button class="action is-primary" type="submit" :disabled="changingPassword || !canSubmitPassword">
            {{ changingPassword ? '提交中...' : '修改密码' }}
          </button>
          <button class="action" type="button" :disabled="changingPassword" @click="resetPasswordForm">清空</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>登录会话</h2>
          <p>结束其他设备上的登录状态，仅保留当前浏览器。</p>
        </div>
        <span class="status-chip" :class="sessionExpiresAt ? 'is-ok' : ''">
          {{ sessionExpiresAt ? `有效期至 ${formatDateTime(sessionExpiresAt)}` : '有效期由服务端控制' }}
        </span>
      </div>

      <p class="notice">
        执行后其他设备的会话令牌立即失效，需要重新登录；操作要求验证当前密码。
      </p>

      <form class="revoke-form" autocomplete="off" @submit.prevent="revokeSessions">
        <label class="field">
          <span>当前密码</span>
          <el-input
            v-model="revokePassword"
            type="password"
            show-password
            name="cloudbim-session-confirm"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            data-1p-ignore
            data-lpignore="true"
            data-bwignore
            data-form-type="other"
            :disabled="revoking"
          />
        </label>
        <div class="form-actions">
          <button class="action is-danger" type="submit" :disabled="revoking || !revokePassword.trim()">
            {{ revoking ? '处理中...' : '结束其他设备登录' }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { changePassword, revokeOtherAccountSessions } from '@/features/auth/auth.service'

defineProps<{ sessionExpiresAt?: string }>()
const emit = defineEmits<{ updated: [] }>()

const passwordForm = reactive({ currentPassword: '', newPassword: '', confirmPassword: '' })
const changingPassword = ref(false)
const passwordError = ref('')

const revokePassword = ref('')
const revoking = ref(false)

// Credential fields must always start empty. Browsers and password managers can
// prefill a saved or generated value for password inputs, which is both
// misleading and unrelated to the real account, so it is cleared on mount.
onMounted(() => {
  resetPasswordForm()
  revokePassword.value = ''
})

const canSubmitPassword = computed(
  () =>
    passwordForm.currentPassword.length > 0 &&
    passwordForm.newPassword.length > 0 &&
    passwordForm.confirmPassword.length > 0,
)

function formatDateTime(value?: string) {
  if (!value) {
    return '-'
  }

  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

function resetPasswordForm() {
  passwordForm.currentPassword = ''
  passwordForm.newPassword = ''
  passwordForm.confirmPassword = ''
  passwordError.value = ''
}

function validatePassword() {
  if (passwordForm.newPassword.length < 6) {
    return '新密码长度不能少于 6 位。'
  }

  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    return '两次输入的新密码不一致。'
  }

  if (passwordForm.newPassword === passwordForm.currentPassword) {
    return '新密码不能与当前密码相同。'
  }

  return ''
}

async function submitPassword() {
  if (changingPassword.value) {
    return
  }

  passwordError.value = validatePassword()

  if (passwordError.value) {
    return
  }

  changingPassword.value = true

  try {
    await changePassword({
      currentPassword: passwordForm.currentPassword,
      newPassword: passwordForm.newPassword,
    })
    resetPasswordForm()
    ElMessage.success('密码已更新，其他设备的登录状态已失效。')
    emit('updated')
  } catch (error) {
    passwordError.value = error instanceof Error ? error.message : '修改密码失败。'
  } finally {
    changingPassword.value = false
  }
}

async function revokeSessions() {
  if (revoking.value || !revokePassword.value.trim()) {
    return
  }

  try {
    await ElMessageBox.confirm(
      '将结束其他设备上的全部登录状态，仅保留当前浏览器。是否继续？',
      '结束其他设备登录',
      { type: 'warning', confirmButtonText: '确认结束', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  revoking.value = true

  try {
    await revokeOtherAccountSessions(revokePassword.value)
    revokePassword.value = ''
    ElMessage.success('其他设备的登录状态已结束。')
    emit('updated')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '结束其他会话失败，请稍后重试。')
  } finally {
    revoking.value = false
  }
}
</script>

<style scoped lang="scss">
@use '@/styles/system-panels' as panels;
@use '@/styles/workspace-controls' as controls;

.password-panel { display: flex; flex-direction: column; gap: var(--workspace-gap); min-width: 0; }
.panel { @include panels.panel; }
.panel-heading { @include panels.panel-heading; }
.status-chip { @include panels.status-chip; }
.notice { @include panels.notice; }
.field-error { @include panels.notice; margin: 0; border-left-color: var(--color-danger); color: var(--color-danger); }

.password-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--spacing-md); }
.revoke-form { display: grid; grid-template-columns: minmax(220px, 320px); gap: var(--spacing-md); }
.field { display: flex; flex-direction: column; gap: var(--spacing-xs); min-width: 0; }
.field > span { color: var(--text-secondary); font-size: var(--font-size-sm); font-weight: 500; }
.field small { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.field-error, .form-actions { grid-column: 1 / -1; }
.form-actions { display: flex; gap: var(--spacing-sm); }

.action { @include controls.action; }
.action.is-primary { @include controls.primary; }
.action.is-danger { color: var(--color-danger); border-color: var(--color-danger); }

@include panels.form-controls;
</style>
