<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Box,
  DataBoard,
  Document,
  Folder,
  MagicStick,
  Monitor,
  Setting,
  Share,
  SwitchButton,
} from '@element-plus/icons-vue'
import type { AuthSession } from '@/features/auth/auth.service'

const props = withDefaults(defineProps<{
  session: AuthSession
  projectId: number
  projectName: string
  showSidebar?: boolean
}>(), { showSidebar: true })
const emit = defineEmits<{ logout: [] }>()
const route = useRoute()
const router = useRouter()

const activePath = computed(() => {
  if (route.path.startsWith('/survey')) return '/survey'
  if (route.path.startsWith('/design/bim')) return '/design/bim'
  if (route.path.startsWith('/design/cad')) return '/design/cad'
  if (route.path.startsWith('/design/overview')) return '/design/overview'
  return route.path
})

const activeTopTab = computed(() => {
  if (route.path.startsWith('/system')) return 'system'
  if (route.path.startsWith('/survey')) return 'devices'
  return 'projects'
})

function withProject(path: string) {
  return { path, query: { projectId: props.projectId, projectName: props.projectName } }
}

function go(path: string) {
  void router.push(path === '/projects' ? path : withProject(path))
}

function goTopTab(tab: 'projects' | 'devices' | 'system') {
  if (tab === 'projects') {
    void router.push('/projects')
    return
  }
  void router.push(withProject(tab === 'devices' ? '/devices' : '/system'))
}
</script>

<template>
  <div class="app-layout" :class="{ 'is-sidebarless': !props.showSidebar }">
    <header class="app-header">
      <div class="header-left">
        <button class="header-brand" type="button" title="CloudBIM" aria-label="CloudBIM" @click="goTopTab('projects')">
          <span class="brand-mark"><el-icon :size="21"><Share /></el-icon></span>
          <span class="brand-name">CloudBIM</span>
        </button>

        <nav class="top-tabs" aria-label="主导航">
          <button class="top-tab" :class="{ 'is-active': activeTopTab === 'projects' }" type="button" :aria-current="activeTopTab === 'projects' ? 'page' : undefined" @click="goTopTab('projects')">
            <el-icon><Folder /></el-icon><span>项目</span>
          </button>
          <button class="top-tab" :class="{ 'is-active': activeTopTab === 'devices' }" type="button" :aria-current="activeTopTab === 'devices' ? 'page' : undefined" @click="goTopTab('devices')">
            <el-icon><Monitor /></el-icon><span>设备中心</span>
          </button>
          <button class="top-tab" :class="{ 'is-active': activeTopTab === 'system' }" type="button" :aria-current="activeTopTab === 'system' ? 'page' : undefined" @click="goTopTab('system')">
            <el-icon><Setting /></el-icon><span>系统管理</span>
          </button>
        </nav>
      </div>

      <div class="header-user">
        <div class="user-avatar">{{ (props.session.username || 'U').slice(0, 1).toUpperCase() }}</div>
        <div class="user-info"><div class="user-name">{{ props.session.username }}</div><div class="user-role">项目成员</div></div>
        <button class="logout-button" type="button" title="退出登录" aria-label="退出登录" @click="emit('logout')"><el-icon><SwitchButton /></el-icon></button>
      </div>
    </header>

    <div class="app-body">
      <aside v-if="props.showSidebar" class="app-sidebar">
        <div class="sidebar-project" :title="props.projectName">
          <span class="project-icon"><el-icon><DataBoard /></el-icon></span>
          <div><span class="project-label">当前项目</span><strong>{{ props.projectName }}</strong></div>
        </div>

        <nav class="menu-content" aria-label="项目功能导航">
          <div class="menu-section">
            <div class="section-title">设计信息</div>
            <div class="menu-items">
              <button class="menu-item" :class="{ 'is-active': activePath === '/design/overview' }" :aria-current="activePath === '/design/overview' ? 'page' : undefined" title="项目概述" @click="go('/design/overview')">
                <span class="menu-icon"><el-icon><DataBoard /></el-icon></span>
                <span class="menu-text"><span class="menu-title">项目概述</span><span class="menu-status">项目总览</span></span>
              </button>
              <button class="menu-item" :class="{ 'is-active': activePath === '/design/cad' }" :aria-current="activePath === '/design/cad' ? 'page' : undefined" title="CAD图纸" @click="go('/design/cad')">
                <span class="menu-icon"><el-icon><Document /></el-icon></span>
                <span class="menu-text"><span class="menu-title">CAD图纸</span><span class="menu-status">图纸管理</span></span>
              </button>
              <button class="menu-item" :class="{ 'is-active': activePath === '/design/bim' }" :aria-current="activePath === '/design/bim' ? 'page' : undefined" title="设计模型" @click="go('/design/bim')">
                <span class="menu-icon"><el-icon><Box /></el-icon></span>
                <span class="menu-text"><span class="menu-title">设计模型</span><span class="menu-status">模型浏览</span></span>
              </button>
            </div>
          </div>
          <div class="menu-section">
            <div class="section-title">实测数据</div>
            <div class="menu-items">
              <button class="menu-item" :class="{ 'is-active': activePath === '/survey' }" :aria-current="activePath === '/survey' ? 'page' : undefined" title="扫描点云" @click="go('/survey')">
                <span class="menu-icon"><el-icon><MagicStick /></el-icon></span>
                <span class="menu-text"><span class="menu-title">扫描点云</span><span class="menu-status">扫描归档与分析</span></span>
              </button>
            </div>
          </div>
        </nav>
      </aside>
      <main class="right-panel"><div class="panel-content"><slot /></div></main>
    </div>
  </div>
</template>

<style scoped>
.app-layout { display: flex; flex-direction: column; height: 100vh; height: 100dvh; overflow: hidden; background: var(--bg-page); color: var(--text-primary); }
.app-header { display: flex; align-items: center; justify-content: space-between; flex: 0 0 var(--header-height); min-width: 0; padding: 0 var(--spacing-lg); border-bottom: 1px solid var(--border-color-light); background: var(--bg-card); }
.header-left, .header-brand, .top-tabs, .header-user, .logout-button { display: flex; align-items: center; }
.header-left { min-width: 0; height: 100%; }
.header-brand { gap: var(--spacing-sm); flex: 0 0 auto; height: 100%; padding: 0 64px 0 0; border: 0; border-right: 1px solid var(--border-color-light); color: var(--brand-sapphire); background: transparent; cursor: pointer; }
.brand-mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: var(--radius-sm); color: #fff; background: var(--brand-sapphire); }
.brand-name { font-size: var(--font-size-lg); font-weight: 700; letter-spacing: .04em; }
.top-tabs { align-self: stretch; gap: var(--spacing-xs); margin-left: var(--spacing-lg); }
.top-tab { position: relative; display: inline-flex; align-items: center; gap: var(--spacing-sm); height: 100%; padding: 0 var(--spacing-md); border: 0; color: var(--text-secondary); background: transparent; cursor: pointer; transition: color var(--transition-fast); }
.top-tab::after { position: absolute; right: var(--spacing-md); bottom: 0; left: var(--spacing-md); height: 3px; border-radius: var(--radius-pill) var(--radius-pill) 0 0; background: transparent; content: ''; transition: background-color var(--transition-fast); }
.top-tab:hover, .top-tab.is-active { color: var(--color-primary); }
.top-tab.is-active { font-weight: 600; }
.top-tab.is-active::after { background: var(--color-primary); }
.header-user { gap: var(--spacing-sm); flex: 0 0 auto; }
.user-avatar { display: grid; place-items: center; width: 34px; height: 34px; flex-shrink: 0; border-radius: var(--radius-pill); color: var(--color-primary); background: var(--color-primary-soft); font-weight: 650; }
.user-info { min-width: 0; }
.user-name, .user-role { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-name { max-width: 140px; color: var(--text-primary); font-size: var(--font-size-sm); }
.user-role { margin-top: 1px; color: var(--text-tertiary); font-size: var(--font-size-xs); }
.logout-button { gap: var(--spacing-xs); height: var(--control-height); margin-left: var(--spacing-sm); padding: 0 var(--spacing-sm); border: 0; border-radius: var(--radius-sm); color: var(--text-secondary); background: transparent; cursor: pointer; }
.logout-button:hover { color: var(--color-primary); background: var(--color-primary-soft); }
.app-body { display: flex; flex: 1; min-height: 0; }
.is-sidebarless .right-panel { margin-left: var(--spacing-md); }
.app-sidebar { width: var(--sidebar-width); flex: 0 0 var(--sidebar-width); display: flex; flex-direction: column; min-height: 0; padding-top: var(--spacing-md); background: var(--bg-card); border-right: 1px solid var(--border-color-light); }
.sidebar-project { display: flex; align-items: center; gap: var(--spacing-sm); min-width: 0; margin: 0 var(--spacing-sm) var(--spacing-md); padding: var(--spacing-sm) var(--spacing-compact);  }
.project-icon { display: grid; place-items: center; flex: 0 0 28px; height: 28px; border-radius: var(--radius-xs); color: var(--color-primary); background: var(--color-primary-soft); }
.sidebar-project > div { min-width: 0; }
.project-label, .sidebar-project strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.project-label { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.sidebar-project strong { margin-top: 2px; color: var(--text-primary); font-size: var(--font-size-sm); font-weight: 600; }
.menu-content { flex: 1; min-height: 0; overflow-y: auto; padding: 0 var(--spacing-sm); }
.section-title { padding: 0 var(--spacing-compact); margin-bottom: var(--spacing-sm); color: var(--text-tertiary); font-size: var(--font-size-xs); font-weight: 600; }
.menu-section + .menu-section { margin-top: var(--spacing-lg); }
.menu-items { display: flex; flex-direction: column; gap: var(--spacing-xs); }
.menu-item { display: flex; align-items: center; gap: var(--spacing-compact); width: 100%; min-height: 60px; padding: var(--spacing-sm) var(--spacing-compact); border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; text-align: left; cursor: pointer; transition: background-color var(--transition-fast); }
.menu-item:hover { background: var(--bg-control-hover); }
.menu-item.is-active { border-color: var(--border-color-focus); background: var(--color-primary-soft); }
.menu-icon { display: grid; place-items: center; flex: 0 0 20px; color: var(--text-secondary); }
.menu-text { min-width: 0; }
.menu-title, .menu-status { display: block; }
.menu-title { color: var(--text-primary); font-size: var(--font-size-sm); }
.menu-status { margin-top: var(--spacing-xs); color: var(--text-tertiary); font-size: var(--font-size-xs); }
.menu-item.is-active .menu-title, .menu-item.is-active .menu-icon { color: var(--color-primary); font-weight: 600; }
.right-panel { flex: 1; min-width: 0; min-height: 0; margin: var(--spacing-md) var(--spacing-md) var(--spacing-md) 0; overflow: hidden; border: 1px solid var(--border-color-light); border-radius: var(--radius-md); background: var(--bg-card); }
.panel-content { height: 100%; min-height: 0; padding: var(--workspace-gutter); overflow: auto; }

@media (max-width: 800px) {
  .app-header { padding-inline: var(--spacing-md); }
  .brand-name { display: none; }
  .header-brand { padding-right: var(--spacing-md); }
  .top-tabs { margin-left: var(--spacing-sm); }
  .top-tab { gap: var(--spacing-xs); padding-inline: var(--spacing-sm); }
  .top-tab::after { right: var(--spacing-sm); left: var(--spacing-sm); }
  .user-info { display: none; }
  .app-sidebar { width: 64px; flex-basis: 64px; padding-top: var(--spacing-sm); }
  .sidebar-project { justify-content: center; margin: 0 0 var(--spacing-sm); padding: var(--spacing-sm); border-bottom: 0; }
  .sidebar-project > div, .menu-text, .menu-section > .section-title { display: none; }
  .menu-content { padding-inline: var(--spacing-sm); }
  .section-title { padding: 0; }
  .menu-item { justify-content: center; min-height: 44px; padding: var(--spacing-sm); }
  .right-panel { margin: var(--spacing-sm) var(--spacing-sm) var(--spacing-sm) 0; }
}

@media (max-width: 520px) {
  .app-header { padding-inline: var(--spacing-sm); }
  .top-tabs { margin-left: var(--spacing-xs); }
  .top-tab { font-size: var(--font-size-xs); }
  .top-tab .el-icon { display: none; }
  .header-user { gap: var(--spacing-xs); }
  .logout-button { margin-left: 0; }
}
</style>
