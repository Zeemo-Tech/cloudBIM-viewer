<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Hide, View } from '@element-plus/icons-vue'
import {
  getStoredLastUsername,
  loginWithPassword,
  registerWithPassword,
  type AuthSession,
} from '@/features/auth/auth.service'

type AuthMode = 'login' | 'register'
type FocusField = 'username' | 'password' | 'registerCode' | 'submit' | null

const emit = defineEmits<{
  'login-success': [session: AuthSession]
}>()

const mode = ref<AuthMode>('login')
const isSubmitting = ref(false)
const activeField = ref<FocusField>('username')
const buttonPulse = ref(false)
const isLoginPasswordVisible = ref(false)
const isRegisterPasswordVisible = ref(false)
const initialUsername = getStoredLastUsername()
let buttonPulseTimer: number | null = null

const loginForm = reactive({
  username: initialUsername,
  password: '',
})

const registerForm = reactive({
  username: initialUsername,
  password: '',
  registerCode: '',
})

const isRegisterMode = computed(() => mode.value === 'register')
const currentPassword = computed(() =>
  isRegisterMode.value ? registerForm.password : loginForm.password,
)
const canSubmit = computed(() => {
  if (isRegisterMode.value) {
    return (
      registerForm.username.trim().length > 0 &&
      registerForm.password.trim().length >= 6 &&
      registerForm.registerCode.trim().length > 0
    )
  }

  return loginForm.username.trim().length > 0 && loginForm.password.trim().length > 0
})

const panelTitle = computed(() => (isRegisterMode.value ? 'register' : 'login'))
const submitText = computed(() => (isRegisterMode.value ? '注册' : '登录'))

function setActiveField(field: Exclude<FocusField, null>) {
  activeField.value = field
}

function clearActiveField() {
  activeField.value = null
}

function togglePasswordVisibility(formType: 'login' | 'register') {
  if (formType === 'login') {
    isLoginPasswordVisible.value = !isLoginPasswordVisible.value
    return
  }

  isRegisterPasswordVisible.value = !isRegisterPasswordVisible.value
}

function getFieldGroupClass(field: Exclude<FocusField, null>) {
  return {
    'field-group': true,
    'is-active': activeField.value === field,
  }
}

function triggerButtonPulse() {
  buttonPulse.value = false

  if (buttonPulseTimer) {
    window.clearTimeout(buttonPulseTimer)
  }

  requestAnimationFrame(() => {
    buttonPulse.value = true
    buttonPulseTimer = window.setTimeout(() => {
      buttonPulse.value = false
      buttonPulseTimer = null
    }, 780)
  })
}

function switchMode(nextMode: AuthMode) {
  const currentUsername = isRegisterMode.value
    ? registerForm.username.trim()
    : loginForm.username.trim()

  if (currentUsername) {
    loginForm.username = currentUsername
    registerForm.username = currentUsername
  }

  mode.value = nextMode
  activeField.value = 'username'
}

watch(canSubmit, (nextValue, previousValue) => {
  if (nextValue && !previousValue) {
    triggerButtonPulse()
  }
})

onBeforeUnmount(() => {
  if (buttonPulseTimer) {
    window.clearTimeout(buttonPulseTimer)
  }
})

function validateForm() {
  if (!canSubmit.value) {
    if (isRegisterMode.value) {
      if (registerForm.password.trim().length > 0 && registerForm.password.trim().length < 6) {
        throw new Error('注册密码至少需要 6 位。')
      }

      throw new Error('请完整填写用户名、密码和注册码。')
    }

    throw new Error('请先输入账号和密码。')
  }
}

async function handleSubmit() {
  if (isSubmitting.value) {
    return
  }

  try {
    validateForm()
  } catch (error) {
    ElMessage({
      message: error instanceof Error ? error.message : '表单校验失败',
      type: 'warning',
      grouping: true,
    })
    return
  }

  isSubmitting.value = true

  try {
    const session = isRegisterMode.value
      ? await registerWithPassword(registerForm)
      : await loginWithPassword(loginForm)

    ElMessage({
      message: isRegisterMode.value ? '注册成功，已自动登录。' : '登录成功。',
      type: 'success',
      grouping: true,
    })
    emit('login-success', session)
  } catch (error) {
    ElMessage({
      message: error instanceof Error ? error.message : '请求失败，请稍后重试。',
      type: 'error',
      grouping: true,
    })
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <section class="page">
    <div class="container">
      <div class="left">
        <div class="mode-switch">
          <el-button
            class="mode-btn"
            :class="{ 'is-active': !isRegisterMode }"
            :disabled="isSubmitting"
            native-type="button"
            @click="switchMode('login')"
          >
            登录
          </el-button>
          <el-button
            class="mode-btn"
            :class="{ 'is-active': isRegisterMode }"
            :disabled="isSubmitting"
            native-type="button"
            @click="switchMode('register')"
          >
            注册
          </el-button>
        </div>

        <h1>{{ panelTitle }}</h1>
        <p class="meta-line" v-if="isRegisterMode">注册码: laochen</p>
        <p class="meta-line" v-else>欢迎登录,若没有账号密码,请先注册</p>
        <!-- <p class="meta-line">已自动记住上次成功使用的用户名。</p> -->
      </div>

      <div class="right" :class="{ 'is-register': isRegisterMode }">
        <div class="form-shell">
          <form class="form" @submit.prevent="handleSubmit">
          <template v-if="isRegisterMode">
            <div :class="getFieldGroupClass('username')">
              <label for="registerAccount">账号</label>
              <el-input
                id="registerAccount"
                v-model="registerForm.username"
                type="text"
                autocomplete="off"
                :disabled="isSubmitting"
                @click="setActiveField('username')"
                @focus="setActiveField('username')"
                @blur="clearActiveField"
              />
            </div>

            <div :class="getFieldGroupClass('password')">
              <label for="registerPassword">密码</label>
              <div class="password-input">
                <el-input
                  id="registerPassword"
                  v-model="registerForm.password"
                  :type="isRegisterPasswordVisible ? 'text' : 'password'"
                  :disabled="isSubmitting"
                  @click="setActiveField('password')"
                  @focus="setActiveField('password')"
                  @blur="clearActiveField"
                >
                  <template #suffix>
                    <button
                      v-if="registerForm.password"
                      class="password-toggle"
                      type="button"
                      :disabled="isSubmitting"
                      :aria-label="isRegisterPasswordVisible ? '隐藏密码' : '显示密码'"
                      @mousedown.prevent
                      @mouseenter="setActiveField('password')"
                      @focus="setActiveField('password')"
                      @click="togglePasswordVisibility('register')"
                      @blur="clearActiveField"
                    >
                      <el-icon>
                        <View v-if="isRegisterPasswordVisible" />
                        <Hide v-else />
                      </el-icon>
                    </button>
                  </template>
                </el-input>
              </div>
            </div>

            <div :class="getFieldGroupClass('registerCode')">
              <label for="registerCode">注册码</label>
              <el-input
                id="registerCode"
                v-model="registerForm.registerCode"
                type="text"
                autocomplete="off"
                :disabled="isSubmitting"
                @click="setActiveField('registerCode')"
                @focus="setActiveField('registerCode')"
                @blur="clearActiveField"
              />
            </div>
          </template>

          <template v-else>
            <div :class="getFieldGroupClass('username')">
              <label for="loginAccount">账号</label>
              <el-input
                id="loginAccount"
                v-model="loginForm.username"
                type="text"
                autocomplete="off"
                :disabled="isSubmitting"
                @click="setActiveField('username')"
                @focus="setActiveField('username')"
                @blur="clearActiveField"
              />
            </div>

            <div :class="getFieldGroupClass('password')">
              <label for="loginPassword">密码</label>
              <div class="password-input">
                <el-input
                  id="loginPassword"
                  v-model="loginForm.password"
                  :type="isLoginPasswordVisible ? 'text' : 'password'"
                  :disabled="isSubmitting"
                  @click="setActiveField('password')"
                  @focus="setActiveField('password')"
                  @blur="clearActiveField"
                >
                  <template #suffix>
                    <button
                      v-if="loginForm.password"
                      class="password-toggle"
                      type="button"
                      :disabled="isSubmitting"
                      :aria-label="isLoginPasswordVisible ? '隐藏密码' : '显示密码'"
                      @mousedown.prevent
                      @mouseenter="setActiveField('password')"
                      @focus="setActiveField('password')"
                      @click="togglePasswordVisibility('login')"
                      @blur="clearActiveField"
                    >
                      <el-icon>
                        <View v-if="isLoginPasswordVisible" />
                        <Hide v-else />
                      </el-icon>
                    </button>
                  </template>
                </el-input>
              </div>
            </div>
          </template>

            <div
              class="submit-wrap"
              :class="{
                'is-ready': canSubmit,
                'is-active': activeField === 'submit',
                'is-pulse': buttonPulse,
              }"
            >
              <el-button
                id="submit"
                :disabled="isSubmitting || !canSubmit"
                native-type="submit"
                @click="setActiveField('submit')"
                @focus="setActiveField('submit')"
                @mouseenter="setActiveField('submit')"
                @mouseleave="clearActiveField"
                @blur="clearActiveField"
              >
                {{ isSubmitting ? '提交中...' : submitText }}
              </el-button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.page {
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 24px;
  background-color: #e2e2e5;
}

.container {
  width: min(700px, 100%);
  min-height: 360px;
  display: flex;
}

.left {
  width: 50%;
  height: calc(100% - 40px);
  padding: 28px 32px;
  background-color: #fff;
  position: relative;
  top: 20px;
}

.mode-switch {
  display: inline-flex;
  padding: 4px;
  border-radius: 999px;
  background: #edf0f4;
  gap: 4px;
}

.mode-btn {
  min-width: 72px;
  height: 34px;
  margin: 0;
  border: 0;
  color: #475569;
  background: transparent;
  box-shadow: none;
  transition: all 0.2s ease;
}

.mode-btn.is-active {
  color: #0f172a;
  background: #fff;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
}

.mode-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.mode-switch :deep(.el-button.mode-btn) {
  min-width: 72px;
  height: 34px;
  margin: 0;
  padding: 0 18px;
  border: 1px solid transparent;
  border-radius: 999px;
  color: #475569;
  background: transparent;
  box-shadow: none;
  transition:
    background-color 0.2s ease,
    color 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.mode-switch :deep(.el-button.mode-btn + .el-button.mode-btn) {
  margin-left: 0;
}

.mode-switch :deep(.el-button.mode-btn:not(.is-active):hover),
.mode-switch :deep(.el-button.mode-btn:not(.is-active):focus-visible) {
  color: #334155;
  background: rgba(255, 255, 255, 0.55);
  border-color: rgba(148, 163, 184, 0.18);
}

.mode-switch :deep(.el-button.mode-btn.is-active),
.mode-switch :deep(.el-button.mode-btn.is-active:hover),
.mode-switch :deep(.el-button.mode-btn.is-active:focus-visible),
.mode-switch :deep(.el-button.mode-btn.is-active:active) {
  color: #0f172a;
  background: #fff;
  border-color: #fff;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
}

.mode-switch :deep(.el-button.mode-btn.is-disabled),
.mode-switch :deep(.el-button.mode-btn.is-disabled:hover) {
  background: transparent;
  border-color: transparent;
}

.left h1 {
  color: #222;
  font-size: 50px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 4px;
  margin: 42px 0 28px;
}

.intro {
  color: #999;
  font-size: 14px;
  line-height: 22px;
  margin-bottom: 18px;
}

.meta-line {
  color: #94a3b8;
  font-size: 14px;
  line-height: 1.7;
  margin-top: 8px;
}

.right {
  width: 50%;
  min-height: 100%;
  background-color: #474a59;
  color: #f1f1f1;
  position: relative;
  box-shadow: 0 0 40px 16px rgba(0, 0, 0, 0.2);
}

.right.is-register {
  min-height: 440px;
}

.form-shell {
  position: absolute;
  inset: 40px;
}

.form {
  position: relative;
  z-index: 1;
}

.field-group {
  margin-top: 22px;
  position: relative;
  padding-bottom: 10px;
  overflow: hidden;
}

.field-group:first-child {
  margin-top: 0;
}

.field-group::before,
.field-group::after {
  content: '';
  position: absolute;
  bottom: 0;
  height: 3px;
  border-radius: 999px;
  pointer-events: none;
}

.field-group::after {
  left: 0;
  right: 0;
  background: linear-gradient(90deg, #22d3ee 0%, #fb7185 100%);
  transform: scaleX(0.04);
  transform-origin: left center;
  opacity: 0;
  filter: blur(0px) saturate(1);
  transition:
    transform 620ms cubic-bezier(0.19, 0.8, 0.24, 1),
    opacity 220ms ease 60ms,
    filter 520ms ease,
    box-shadow 520ms ease;
}

.field-group::before {
  left: -28%;
  width: 28%;
  background:
    linear-gradient(
      90deg,
      rgba(34, 211, 238, 0) 0%,
      rgba(34, 211, 238, 0.92) 22%,
      rgba(255, 255, 255, 0.96) 48%,
      rgba(251, 113, 133, 0.9) 78%,
      rgba(251, 113, 133, 0) 100%
    );
  opacity: 0;
  filter: blur(4px);
  transform: translate3d(-136%, 0, 0) skewX(-22deg);
}

.field-group.is-active::after {
  transform: scaleX(1);
  opacity: 1;
  filter: blur(0.35px) saturate(1.12);
  box-shadow:
    0 0 12px rgba(34, 211, 238, 0.44),
    0 0 22px rgba(251, 113, 133, 0.28);
}

.field-group.is-active::before {
  opacity: 1;
  animation: underline-sweep 820ms cubic-bezier(0.16, 0.84, 0.24, 1) 90ms both;
}

.form label {
  color: #c2c2c2;
  display: block;
  font-size: 14px;
  margin-bottom: 5px;
}

.form :deep(.el-input) {
  --el-input-text-color: #f2f2f2;
  --el-input-placeholder-color: rgba(242, 242, 242, 0.45);
  --el-input-hover-border-color: transparent;
  --el-input-focus-border-color: transparent;
}

.form :deep(.el-input__wrapper) {
  width: 100%;
  height: 30px;
  padding: 0;
  background-color: transparent;
  box-shadow: none;
}

.form :deep(.el-input__inner) {
  height: 30px;
  line-height: 30px;
  font-size: 20px;
  color: #f2f2f2;
  background-color: transparent;
  border: none;
  outline: none;
  text-indent: 2px;
}

.password-input {
  position: relative;
}

.password-input :deep(.el-input__inner) {
  padding-right: 34px;
}

.password-input :deep(.el-input__suffix) {
  right: 0;
}

.password-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 999px;
  color: rgba(242, 242, 242, 0.72);
  background: transparent;
  cursor: pointer;
  transition:
    color 220ms ease,
    background-color 220ms ease,
    box-shadow 260ms ease;
}

.password-toggle :deep(.el-icon) {
  width: 18px;
  height: 18px;
}

.password-toggle:hover,
.password-toggle:focus-visible {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
  box-shadow:
    0 0 10px rgba(34, 211, 238, 0.2),
    0 0 16px rgba(251, 113, 133, 0.12);
  outline: none;
}

.password-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.hint {
  display: block;
  margin-top: 6px;
  color: #94a3b8;
  font-size: 12px;
}

.submit-wrap {
  margin-top: 54px;
  position: relative;
  padding: 1px;
  border-radius: 999px;
  background: transparent;
  transform: translateZ(0) scale(1);
  isolation: isolate;
  transition:
    background 280ms ease,
    box-shadow 360ms ease,
    transform 420ms cubic-bezier(0.22, 1, 0.36, 1),
    filter 320ms ease,
    opacity 280ms ease;
}

.submit-wrap::before {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: inherit;
  background: linear-gradient(90deg, rgba(34, 211, 238, 0.24) 0%, rgba(251, 113, 133, 0.22) 100%);
  opacity: 0;
  filter: blur(12px);
  z-index: -1;
  transition:
    opacity 320ms ease,
    filter 320ms ease,
    transform 420ms cubic-bezier(0.22, 1, 0.36, 1);
}

.submit-wrap.is-ready {
  background: linear-gradient(90deg, #22d3ee 0%, #fb7185 100%);
  box-shadow:
    0 0 12px rgba(34, 211, 238, 0.16),
    0 0 18px rgba(251, 113, 133, 0.1);
}

.submit-wrap.is-ready::before {
  opacity: 0.5;
  filter: blur(14px);
}

.submit-wrap.is-ready:hover {
  box-shadow:
    0 0 18px rgba(34, 211, 238, 0.26),
    0 0 30px rgba(251, 113, 133, 0.18);
  transform: translateZ(0) scale(1.012);
}

.submit-wrap.is-ready:hover::before {
  opacity: 0.78;
  filter: blur(18px);
}

.submit-wrap.is-active,
.submit-wrap:focus-within {
  box-shadow:
    0 0 24px rgba(34, 211, 238, 0.38),
    0 0 42px rgba(251, 113, 133, 0.26);
  transform: translateZ(0) scale(1.018);
}

.submit-wrap.is-active::before,
.submit-wrap:focus-within::before {
  opacity: 1;
  filter: blur(20px);
  transform: scale(1.035);
}

.submit-wrap.is-pulse {
  animation: submit-ready-pulse 920ms cubic-bezier(0.16, 0.84, 0.24, 1);
}
.submit {
padding: 10px 0 20px 0;
line-height: 30px;
}

.form :deep(.el-button) {
  width: 100%;
  height: 40px;
  margin: 0;
  color: #d0d0d0;
  font-size: 18px;
  background-color: #474a59;
  border: none;
  border-radius: 999px;
  box-shadow: none;
}

.form :deep(.el-button:hover),
.form :deep(.el-button:focus-visible),
.form :deep(.el-button:active) {
  color: #d0d0d0;
  background-color: #474a59;
  border-color: transparent;
}

.form :deep(.el-button.is-disabled),
.form :deep(.el-button.is-disabled:hover) {
  color: #d0d0d0;
  background-color: #474a59;
  border-color: transparent;
  cursor: not-allowed;
  opacity: 0.5;
}

@keyframes submit-ready-pulse {
  0% {
    transform: scale(1);
    box-shadow:
      0 0 0 rgba(34, 211, 238, 0),
      0 0 0 rgba(251, 113, 133, 0);
  }

  32% {
    transform: scale(1.028);
    box-shadow:
      0 0 30px rgba(34, 211, 238, 0.4),
      0 0 48px rgba(251, 113, 133, 0.28);
  }

  58% {
    transform: scale(1.015);
    box-shadow:
      0 0 22px rgba(34, 211, 238, 0.3),
      0 0 36px rgba(251, 113, 133, 0.2);
  }

  100% {
    transform: scale(1);
    box-shadow:
      0 0 12px rgba(34, 211, 238, 0.16),
      0 0 18px rgba(251, 113, 133, 0.1);
  }
}

@keyframes underline-sweep {
  0% {
    opacity: 0;
    transform: translate3d(-136%, 0, 0) skewX(-22deg);
  }

  12% {
    opacity: 1;
  }

  72% {
    opacity: 1;
  }

  100% {
    opacity: 0;
    transform: translate3d(448%, 0, 0) skewX(-22deg);
  }
}

@media (max-width: 860px) {
  .container {
    flex-direction: column;
    min-height: auto;
  }

  .left,
  .right {
    width: 100%;
    top: 0;
  }

  .left {
    height: auto;
  }

  .right {
    min-height: 420px;
  }

  .form-shell {
    inset: 40px;
  }
}
</style>
