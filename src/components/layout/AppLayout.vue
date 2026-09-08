<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MagicStick, Monitor, Share, SwitchButton } from '@element-plus/icons-vue'
import type { AuthSession } from '@/features/auth/auth.service'

const props = defineProps<{ session: AuthSession }>()
const emit = defineEmits<{ logout: [] }>()
const route = useRoute()
const router = useRouter()
const activePath = computed(() => route.path.startsWith('/projects') ? '/projects' : '/upload')

function go(path: string) {
  void router.push(path)
}

</script>

<template>
  <div class="app-layout">
    <aside class="app-sidebar">
      <div class="brand" @click="go('/upload')">
        <div class="brand-mark"><el-icon :size="22"><Share /></el-icon></div>
        <div><strong>CloudBIM</strong><span>实模一致平台</span></div>
      </div>

      <div class="menu-content">
        <div class="menu-section">
          <div class="section-title">项目执行序列</div>
          <div class="menu-items">
            <button class="menu-item" :class="{ 'is-active': activePath === '/upload' }" @click="go('/upload')">
              <span class="menu-icon"><el-icon><Monitor /></el-icon></span>
              <span class="menu-text"><span class="menu-title">文件上传</span><span class="menu-status status-active">上传入口</span></span>
            </button>
            <button class="menu-item" :class="{ 'is-active': activePath === '/projects' }" @click="go('/projects')">
              <span class="menu-icon"><el-icon><MagicStick /></el-icon></span>
              <span class="menu-text"><span class="menu-title">项目中心</span><span class="menu-status status-active">项目管理</span></span>
            </button>
          </div>
        </div>
      </div>

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
.app-layout{display:flex;height:100vh;background:#f8f9fb;color:#1a1a1a}.app-sidebar{width:260px;height:100%;display:flex;flex-direction:column;overflow:hidden;background:#f8f9fb}.brand{display:flex;align-items:center;padding:24px 20px;cursor:pointer}.brand-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;margin-right:12px;color:#1a1a1a}.brand strong{font-size:20px;font-weight:600;letter-spacing:2px}.brand span{display:none}.menu-content{flex:1;overflow-y:auto;padding:20px 0}.menu-content::-webkit-scrollbar{width:4px}.menu-content::-webkit-scrollbar-thumb{background:#e0e0e0;border-radius:4px}.menu-section{margin-bottom:32px}.section-title{padding:0 20px;margin-bottom:12px;font-size:12px;color:#8f959e;font-weight:500;letter-spacing:.5px}.menu-items{display:flex;flex-direction:column}.menu-item{display:flex;align-items:center;width:calc(100% - 24px);margin:0 12px 4px;padding:12px 20px;border:0;border-radius:8px;background:transparent;text-align:left;cursor:pointer;transition:all .2s ease}.menu-item:hover{background:#f5f7fa}.menu-item.is-active{background:#fff;box-shadow:0 2px 8px rgba(74,144,226,.25)}.menu-icon{width:20px;height:20px;margin-right:12px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#8f959e}.menu-item.is-active .menu-icon{color:#4a90e2}.menu-text{flex:1;min-width:0}.menu-title,.menu-status{display:block}.menu-title{font-size:14px;color:#1a1a1a;line-height:20px}.menu-item.is-active .menu-title{color:#4a90e2;font-weight:500}.menu-status{font-size:12px;color:#8f959e;margin-top:2px;line-height:16px}.user-section{box-sizing:border-box;width:calc(100% - 24px);min-height:72px;margin:0 12px 20px;padding:16px 14px;display:flex;align-items:center;gap:12px;border-radius:18px;background:#fff;box-shadow:0 5px 16px rgb(163 177 198 / 12%)}.user-avatar{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;flex:0 0 40px;background:#e6efff;color:#4a90e2;font-weight:600}.user-info{flex:1;min-width:0}.user-name,.user-role{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.user-name{font-size:14px;font-weight:500;line-height:20px}.user-role{font-size:12px;color:#8f959e;line-height:18px}.logout-button{width:32px;height:32px;display:grid;place-items:center;flex:0 0 32px;padding:0;border:0;border-radius:10px;background:transparent;color:#8f959e;cursor:pointer}.logout-button:hover{color:#4a90e2;background:#f3f7fd}.right-panel{min-width:0;flex:1;background:#fff;border-radius:15px;overflow:hidden;padding:10px;margin:20px 20px 20px 0}.panel-content{box-sizing:border-box;height:100%;min-height:0;padding:10px;overflow:auto}@media(max-width:800px){.app-sidebar{width:72px;flex-basis:72px}.brand{padding:24px 20px}.brand>div:last-child,.section-title,.menu-title,.menu-status,.user-info{display:none}.menu-item{justify-content:center;padding:12px 10px}.menu-icon{margin:0}.user-section{width:56px;margin:0 8px 14px;justify-content:center;padding:8px}.logout-button{display:none}.right-panel{margin:10px 10px 10px 0}}
</style>
