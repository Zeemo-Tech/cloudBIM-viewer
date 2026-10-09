<template>
  <section class="system-page">
    <p v-if="infoError" class="page-error" role="alert">{{ infoError }}</p>

    <div class="tab-layout">
      <nav class="tab-rail" role="tablist" aria-label="系统管理模块" @keydown="handleTabKeydown">
        <button
          v-for="(tab, index) in TABS"
          :key="tab.id"
          :ref="(element) => registerTabRef(element, index)"
          class="tab-button"
          :class="{ 'is-active': tab.id === activeTab }"
          type="button"
          role="tab"
          :id="`system-tab-${tab.id}`"
          :aria-selected="tab.id === activeTab"
          :aria-controls="`system-panel-${tab.id}`"
          :tabindex="tab.id === activeTab ? 0 : -1"
          :title="tab.description"
          @click="selectTab(tab.id)"
        >
          <el-icon :size="16"><component :is="tab.icon" /></el-icon>
          <span class="tab-text">
            <span class="tab-title">{{ tab.label }}</span>
            <span class="tab-hint">{{ tab.hint }}</span>
          </span>
        </button>
      </nav>

      <div class="tab-panels">
        <div
          v-if="activeTab === 'profile'"
          id="system-panel-profile"
          class="tab-panel"
          role="tabpanel"
          aria-labelledby="system-tab-profile"
          tabindex="0"
        >
          <AccountProfilePanel @updated="handleProfileUpdated" />
        </div>

        <div
          v-else-if="activeTab === 'security'"
          id="system-panel-security"
          class="tab-panel"
          role="tabpanel"
          aria-labelledby="system-tab-security"
          tabindex="0"
        >
          <PasswordSecurityPanel :session-expires-at="info?.sessionExpiresAt" @updated="loadInfo" />
        </div>

        <div
          v-else-if="activeTab === 'status'"
          id="system-panel-status"
          class="tab-panel"
          role="tabpanel"
          aria-labelledby="system-tab-status"
          tabindex="0"
        >
          <ServiceStatusPanel :info="info" :loading="loading" :error="infoError" @refresh="loadInfo" />
        </div>

        <div
          v-else-if="activeTab === 'members'"
          id="system-panel-members"
          class="tab-panel"
          role="tabpanel"
          aria-labelledby="system-tab-members"
          tabindex="0"
        >
          <MembersPanel :viewer-role="role" @updated="loadInfo" />
        </div>

        <div
          v-else
          id="system-panel-members"
          class="tab-panel"
          role="tabpanel"
          aria-labelledby="system-tab-members"
          tabindex="0"
        >
          <MembersPanel :viewer-role="role" @updated="loadInfo" />
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue'
import { Bell, Lock, Monitor, User } from '@element-plus/icons-vue'
import AccountProfilePanel from '@/components/system/AccountProfilePanel.vue'
import MembersPanel from '@/components/system/MembersPanel.vue'
import PasswordSecurityPanel from '@/components/system/PasswordSecurityPanel.vue'
import ServiceStatusPanel from '@/components/system/ServiceStatusPanel.vue'
import { getSystemInfo, type MemberRole, type SystemInfo } from '@/api/backend-system'
import type { AccountProfile } from '@/api/backend-auth'

type TabId = 'profile' | 'security' | 'status' | 'members'

interface TabDefinition {
  id: TabId
  label: string
  hint: string
  description: string
  icon: Component
}

const TABS: TabDefinition[] = [
  { id: 'profile', label: '账号资料', hint: '昵称与联系方式', description: '维护昵称、邮箱与联系电话', icon: User },
  { id: 'security', label: '密码安全', hint: '密码与会话', description: '修改密码并结束其他设备的登录状态', icon: Lock },
  { id: 'status', label: '服务状态', hint: '运行与用量', description: '查看服务、存储与用量统计', icon: Monitor },
  { id: 'members', label: '成员与权限', hint: '角色与状态', description: '管理工作区成员的角色与账号状态', icon: Bell },
]

const emit = defineEmits<{ sessionUpdated: [profile: AccountProfile] }>()

const info = ref<SystemInfo | null>(null)
const loading = ref(false)
const infoError = ref('')
const activeTab = ref<TabId>('profile')
const tabElements = ref<(Element | null)[]>([])

const role = computed<MemberRole>(() => info.value?.role ?? 'member')

function registerTabRef(element: Element | Component | null, index: number) {
  tabElements.value[index] = (element as Element) ?? null
}

function selectTab(id: TabId) {
  activeTab.value = id
}

// Arrow keys follow the tablist convention; other keys stay with the browser.
function handleTabKeydown(event: KeyboardEvent) {
  const keys = ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End']

  if (!keys.includes(event.key)) {
    return
  }

  const tabs = TABS
  const currentIndex = tabs.findIndex((tab) => tab.id === activeTab.value)

  if (currentIndex < 0) {
    return
  }

  event.preventDefault()

  let nextIndex = currentIndex

  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    nextIndex = (currentIndex + 1) % tabs.length
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    nextIndex = (currentIndex - 1 + tabs.length) % tabs.length
  } else if (event.key === 'Home') {
    nextIndex = 0
  } else if (event.key === 'End') {
    nextIndex = tabs.length - 1
  }

  activeTab.value = tabs[nextIndex].id
  const element = tabElements.value[nextIndex]

  if (element instanceof HTMLElement) {
    element.focus()
  }
}

function handleProfileUpdated(profile: AccountProfile) {
  emit('sessionUpdated', profile)
  void loadInfo()
}

async function loadInfo() {
  if (loading.value) {
    return
  }

  loading.value = true
  infoError.value = ''

  try {
    const result = await getSystemInfo()
    info.value = result.data
  } catch (error) {
    infoError.value = error instanceof Error ? error.message : '读取系统信息失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

onMounted(loadInfo)
</script>

<style scoped lang="scss">
@use '@/styles/system-panels' as panels;

.system-page { display: flex; flex-direction: column; gap: var(--workspace-gap); min-height: 100%; color: var(--text-primary); }

.page-error { @include panels.notice; border-left-color: var(--color-danger); color: var(--color-danger); }

.tab-layout { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: var(--workspace-gap); align-items: start; }
.tab-rail { display: flex; flex-direction: column; gap: var(--spacing-xs); min-width: 0; }

.tab-button {
  display: flex;
  align-items: center;
  gap: var(--spacing-compact);
  width: 100%;
  min-height: 56px;
  padding: var(--spacing-sm) var(--spacing-compact);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);

  &:hover { background: var(--bg-control-hover); }
  &:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
  &.is-active { border-color: var(--border-color-focus); color: var(--color-primary); background: var(--color-primary-soft); }
}

.tab-text { display: flex; flex-direction: column; min-width: 0; }
.tab-title { font-size: var(--font-size-sm); }
.tab-hint { margin-top: 2px; color: var(--text-tertiary); font-size: var(--font-size-xs); }
.tab-button.is-active .tab-title { font-weight: 600; }

.tab-panels { min-width: 0; }
.tab-panel { min-width: 0; }
.tab-panel:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 4px; border-radius: var(--radius-sm); }

@media (max-width: 960px) {
  .tab-layout { grid-template-columns: minmax(0, 1fr); }
  .tab-rail { flex-direction: row; flex-wrap: wrap; }
  .tab-button { flex: 1 1 180px; min-height: 44px; }
  .tab-hint { display: none; }
}
</style>
