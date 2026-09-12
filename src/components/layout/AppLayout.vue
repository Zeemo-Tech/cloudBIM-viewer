<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Box, DataBoard, Document, MagicStick, Monitor, Share, SwitchButton } from '@element-plus/icons-vue'
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
  return '/upload'
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
        <div class="menu-section">
          <div class="section-title project-context"><button class="back-to-projects" type="button" title="返回项目列表" aria-label="返回项目列表" @click="go('/projects')"><el-icon><ArrowLeft /></el-icon><span>返回项目列表</span></button></div>
          <div class="menu-items">
            <button class="menu-item" :class="{ 'is-active': activePath === '/upload' }" :aria-current="activePath === '/upload' ? 'page' : undefined" title="文件上传" @click="go('/upload')">
              <span class="menu-icon"><el-icon><Monitor /></el-icon></span>
              <span class="menu-text"><span class="menu-title">文件上传</span><span class="menu-status status-active">上传入口</span></span>
            </button>
            <button class="menu-item" :class="{ 'is-active': activePath === '/survey' }" :aria-current="activePath === '/survey' ? 'page' : undefined" title="实测项目资产" @click="go('/survey')">
              <span class="menu-icon"><el-icon><MagicStick /></el-icon></span>
              <span class="menu-text"><span class="menu-title">实测</span><span class="menu-status status-active">项目资产</span></span>
            </button>
          </div>
        </div>
        <div class="menu-section design-section">
          <div class="section-title">设计</div>
          <div class="menu-items">
            <button class="menu-item" :class="{ 'is-active': activePath === '/design/bim' }" :aria-current="activePath === '/design/bim' ? 'page' : undefined" title="IFC 模型" @click="go('/design/bim')">
              <span class="menu-icon"><el-icon><Box /></el-icon></span>
              <span class="menu-text"><span class="menu-title">IFC 模型</span><span class="menu-status">模型浏览</span></span>
            </button>
            <button class="menu-item" :class="{ 'is-active': activePath === '/design/cad' }" :aria-current="activePath === '/design/cad' ? 'page' : undefined" title="CAD 图纸" @click="go('/design/cad')">
              <span class="menu-icon"><el-icon><Document /></el-icon></span>
              <span class="menu-text"><span class="menu-title">CAD 图纸</span><span class="menu-status">图纸管理</span></span>
            </button>
            <button class="menu-item" :class="{ 'is-active': activePath === '/design/overview' }" :aria-current="activePath === '/design/overview' ? 'page' : undefined" title="项目概览" @click="go('/design/overview')">
              <span class="menu-icon"><el-icon><DataBoard /></el-icon></span>
              <span class="menu-text"><span class="menu-title">项目概览</span><span class="menu-status">项目总览</span></span>
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
.app-layout{display:flex;height:100vh;background:#f8f9fb;color:#1a1a1a}.app-sidebar{width:220px;flex:0 0 220px;height:100%;display:flex;flex-direction:column;overflow:hidden;background:#f8f9fb}.brand{display:flex;align-items:center;padding:24px 18px;cursor:pointer}.brand-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;margin-right:10px;color:#1a1a1a}.brand strong{font-size:20px;font-weight:600;letter-spacing:2px}.brand span{display:none}.menu-content{flex:1;overflow-y:auto;padding:20px 0}.menu-content::-webkit-scrollbar{width:4px}.menu-content::-webkit-scrollbar-thumb{background:#e0e0e0;border-radius:4px}.menu-section{margin-bottom:32px}.section-title{padding:0 18px;margin-bottom:12px;font-size:12px;color:#8f959e;font-weight:500;letter-spacing:.5px}.menu-items{display:flex;flex-direction:column}.menu-item{display:flex;align-items:center;width:calc(100% - 20px);margin:0 10px 4px;padding:12px 16px;border:0;border-radius:8px;background:transparent;text-align:left;cursor:pointer;transition:all .2s ease}.menu-item:hover{background:#f5f7fa}.menu-item.is-active{background:#fff;box-shadow:0 2px 8px rgba(74,144,226,.25)}.menu-icon{width:20px;height:20px;margin-right:10px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#8f959e}.menu-item.is-active .menu-icon{color:#4a90e2}.menu-text{flex:1;min-width:0}.menu-title,.menu-status{display:block}.menu-title{font-size:14px;color:#1a1a1a;line-height:20px}.menu-item.is-active .menu-title{color:#4a90e2;font-weight:500}.menu-status{font-size:12px;color:#8f959e;margin-top:2px;line-height:16px}.user-section{box-sizing:border-box;width:calc(100% - 20px);min-height:72px;margin:0 10px 20px;padding:14px 10px;display:flex;align-items:center;gap:9px;border-radius:16px;background:#fff;box-shadow:0 5px 16px rgb(163 177 198 / 12%)}.user-avatar{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;flex:0 0 36px;background:#e6efff;color:#4a90e2;font-weight:600}.user-info{flex:1;min-width:0}.user-name,.user-role{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.user-name{font-size:14px;font-weight:500;line-height:20px}.user-role{font-size:12px;color:#8f959e;line-height:18px}.logout-button{width:30px;height:30px;display:grid;place-items:center;flex:0 0 30px;padding:0;border:0;border-radius:10px;background:transparent;color:#8f959e;cursor:pointer}.logout-button:hover{color:#4a90e2;background:#f3f7fd}.right-panel{min-width:0;flex:1;background:#fff;border-radius:15px;overflow:hidden;padding:10px;margin:20px 20px 20px 0}.panel-content{box-sizing:border-box;height:100%;min-height:0;padding:10px;overflow:auto}@media(max-width:800px){.app-sidebar{width:64px;flex-basis:64px}.brand{padding:24px 16px}.brand>div:last-child,.section-title,.menu-title,.menu-status,.user-info{display:none}.menu-item{justify-content:center;padding:12px 10px}.menu-icon{margin:0}.user-section{width:50px;margin:0 7px 14px;justify-content:center;padding:8px}.logout-button{display:none}.right-panel{margin:10px 10px 10px 0}}
.project-context{display:flex;align-items:center}.project-context .back-to-projects{display:inline-flex;align-items:center;gap:5px;max-width:100%;padding:0;border:0;border-radius:0;color:#8f959e;background:transparent;font-size:12px;font-weight:500;line-height:18px;cursor:pointer}.project-context .back-to-projects:hover{color:#4a90e2;background:transparent}.project-context .back-to-projects span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.brand>div:last-child{min-width:0}.brand span{display:block;max-width:145px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b99ab;font-size:10px}
</style>

<style scoped>
@media (max-width: 800px) {
  .project-context {
    display: flex !important;
    padding: 0 16px !important;
  }

  .project-context .back-to-projects {
    width: 32px;
    justify-content: center;
  }

.project-context .back-to-projects span {
    display: none;
  }
}
</style>

<style scoped>
.app-layout { background: var(--bg-page); color: var(--text-primary); }
.app-sidebar { width: var(--sidebar-width); flex-basis: var(--sidebar-width); background: var(--bg-page); }
.brand strong { font-size: var(--font-size-xl); color: var(--text-primary); }
.section-title, .menu-status, .user-role, .project-context .back-to-projects, .brand span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.menu-item { border-radius: var(--radius-sm); transition: background-color var(--transition-fast), color var(--transition-fast); }
.menu-item:hover { background: var(--bg-control-hover); }
.menu-item.is-active, .user-section, .right-panel { background: var(--bg-card); }
.menu-title, .user-name { color: var(--text-primary); font-size: var(--font-size-sm); }
.menu-item.is-active .menu-icon, .menu-item.is-active .menu-title, .logout-button:hover, .project-context .back-to-projects:hover { color: var(--text-link); }
.menu-icon, .logout-button { color: var(--text-tertiary); }
.user-avatar { background: var(--color-primary-soft); color: var(--color-primary); }
.menu-icon, .logout-button { color: var(--text-secondary); }
.menu-item.is-active .menu-icon { color: var(--color-primary); }
.right-panel { border-radius: var(--radius-lg); }
.brand { width: 100%; border: 0; background: transparent; color: inherit; text-align: left; }
.logout-button { width: 40px; height: 40px; flex-basis: 40px; }
@media (max-width: 800px) {
  .app-sidebar { width: 64px; flex-basis: 64px; }
  .brand { justify-content: center; padding-inline: 16px; }
  .brand-mark { margin-right: 0; }
  .logout-button { display: none; }
}
</style>
