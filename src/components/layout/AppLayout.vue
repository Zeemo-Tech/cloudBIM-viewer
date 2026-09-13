<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Box, DataBoard, Document, MagicStick, Share, SwitchButton } from '@element-plus/icons-vue'
import type { AuthSession } from '@/features/auth/auth.service'

const props = defineProps<{ session: AuthSession; projectId: number; projectName: string }>()
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

function go(path: string) {
  if (path === '/projects') {
    void router.push('/projects')
    return
  }
  void router.push({ path, query: { projectId: props.projectId, projectName: props.projectName } })
}

</script>

<template>
  <div class="app-layout">
    <aside class="app-sidebar">
      <button class="brand" type="button" title="返回项目列表" aria-label="CloudBIM，返回项目列表" @click="go('/projects')">
        <div class="brand-mark"><el-icon :size="22"><Share /></el-icon></div>
        <div><strong>CloudBIM</strong><span>{{ props.projectName }}</span></div>
      </button>

      <nav class="menu-content" aria-label="项目功能导航">
        <div class="section-title project-context"><button class="back-to-projects" type="button" title="返回项目列表" aria-label="返回项目列表" @click="go('/projects')"><el-icon><ArrowLeft /></el-icon><span>返回项目列表</span></button></div>
        <div class="menu-section design-section">
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
        <div class="menu-section survey-section">
          <div class="section-title">实测数据</div>
          <div class="menu-items">
            <button class="menu-item" :class="{ 'is-active': activePath === '/survey' }" :aria-current="activePath === '/survey' ? 'page' : undefined" title="扫描点云" @click="go('/survey')">
              <span class="menu-icon"><el-icon><MagicStick /></el-icon></span>
              <span class="menu-text"><span class="menu-title">扫描点云</span><span class="menu-status">扫描归档与分析</span></span>
            </button>
          </div>
        </div>
      </nav>

      <div class="user-section">
        <div class="user-avatar">{{ (props.session.username || 'U').slice(0, 1).toUpperCase() }}</div>
        <div class="user-info"><div class="user-name">{{ props.session.username }}</div><div class="user-role">项目成员</div></div>
        <button class="logout-button" type="button" title="退出登录" aria-label="退出登录" @click="emit('logout')"><el-icon><SwitchButton /></el-icon></button>
      </div>
    </aside>
    <main class="right-panel"><div class="panel-content"><slot /></div></main>
  </div>
</template>

<style scoped>
.app-layout { display: flex; height: 100vh; height: 100dvh; background: var(--bg-page); color: var(--text-primary); }
.app-sidebar { width: var(--sidebar-width); flex: 0 0 var(--sidebar-width); display: flex; flex-direction: column; min-height: 0; }
.brand { display: flex; align-items: center; gap: var(--spacing-sm); width: 100%; padding: var(--spacing-lg) var(--spacing-md); border: 0; background: transparent; text-align: left; cursor: pointer; }
.brand-mark { display: grid; place-items: center; flex: 0 0 32px; color: var(--brand-sapphire); }
.brand > div:last-child { min-width: 0; }
.brand strong { display: block; color: var(--brand-sapphire); font-size: var(--font-size-xl); font-weight: 650; }
.brand span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-tertiary); font-size: var(--font-size-xs); }
.menu-content { flex: 1; min-height: 0; overflow-y: auto; padding: var(--spacing-sm); }
.project-context { margin-bottom: var(--spacing-lg); }
.section-title { padding: 0 var(--spacing-compact); margin-bottom: var(--spacing-sm); color: var(--text-tertiary); font-size: var(--font-size-xs); font-weight: 600; }
.back-to-projects { display: inline-flex; align-items: center; gap: var(--spacing-sm); min-height: var(--control-height); padding: 0; border: 0; background: transparent; color: var(--text-secondary); cursor: pointer; }
.back-to-projects:hover { color: var(--color-primary); }
.menu-section + .menu-section { margin-top: var(--spacing-lg); }
.menu-items { display: flex; flex-direction: column; gap: var(--spacing-xs); }
.menu-item { display: flex; align-items: center; gap: var(--spacing-compact); width: 100%; min-height: 60px; padding: var(--spacing-sm) var(--spacing-compact); border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; text-align: left; cursor: pointer; transition: background-color var(--transition-fast); }
.menu-item:hover { background: var(--bg-control-hover); }
.menu-item.is-active { background: var(--color-primary-soft); border-color: var(--border-color-focus); }
.menu-icon { display: grid; place-items: center; flex: 0 0 20px; color: var(--text-secondary); }
.menu-text { min-width: 0; }
.menu-title, .menu-status { display: block; }
.menu-title { font-size: var(--font-size-sm); color: var(--text-primary); }
.menu-status { margin-top: var(--spacing-xs); font-size: var(--font-size-xs); color: var(--text-tertiary); }
.menu-item.is-active .menu-title, .menu-item.is-active .menu-icon { color: var(--color-primary); font-weight: 600; }
.user-section { display: flex; align-items: center; gap: var(--spacing-sm); margin: var(--spacing-md) var(--spacing-sm); padding: var(--spacing-compact) var(--spacing-sm); border-top: 1px solid var(--border-color); }
.user-avatar { display: grid; place-items: center; width: var(--control-height); height: var(--control-height); flex-shrink: 0; border-radius: var(--radius-pill); background: var(--color-primary-soft); color: var(--color-primary); font-weight: 600; }
.user-info { flex: 1; min-width: 0; }
.user-name, .user-role { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-role { font-size: var(--font-size-xs); color: var(--text-tertiary); }
.logout-button { display: grid; place-items: center; flex: 0 0 var(--control-height); height: var(--control-height); padding: 0; border: 0; border-radius: var(--radius-sm); color: var(--text-secondary); background: transparent; cursor: pointer; }
.logout-button:hover { background: var(--color-primary-soft); color: var(--color-primary); }
.right-panel { flex: 1; min-width: 0; margin: var(--spacing-md) var(--spacing-md) var(--spacing-md) 0; overflow: hidden; border: 1px solid var(--border-color-light); border-radius: var(--radius-md); background: var(--bg-card); }
.panel-content { height: 100%; min-height: 0; padding: var(--workspace-gutter); overflow: auto; }

@media (max-width: 800px) {
  .app-sidebar { width: 64px; flex-basis: 64px; }
  .brand { padding: var(--spacing-lg) var(--spacing-md); }
  .brand > div:last-child, .menu-text, .menu-section > .section-title, .user-info, .user-avatar, .back-to-projects span { display: none; }
  .section-title { padding: 0; }
  .back-to-projects, .menu-item { justify-content: center; width: 100%; }
  .menu-item { min-height: 44px; padding: var(--spacing-sm); }
  .user-section { justify-content: center; padding-inline: 0; }
  .right-panel { margin: var(--spacing-sm) var(--spacing-sm) var(--spacing-sm) 0; }
}
</style>
