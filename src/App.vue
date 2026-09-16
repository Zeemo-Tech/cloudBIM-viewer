<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  logoutCurrentSession,
  refreshCurrentSession,
  type AuthSession,
  validateStoredSession,
} from '@/features/auth/auth.service'
import LoginView from '@/views/login/LoginView.vue'
import AppLayout from '@/components/layout/AppLayout.vue'
import ProjectSelectionView from '@/views/project/ProjectSelectionView.vue'
import { getRouteInstanceKey, readNavigationRouteState } from '@/router/navigation'

const UploadView = defineAsyncComponent(() => import('@/views/upload/SimpleUploadView.vue'))
const ProjectSurveyView = defineAsyncComponent(() => import('@/views/project/ProjectSurveyView.vue'))
const DesignView = defineAsyncComponent(() => import('@/views/design/DesignView.vue'))
const AssetPreviewView = defineAsyncComponent(() => import('@/views/preview/AssetPreviewView.vue'))
const SplitPreviewView = defineAsyncComponent(() => import('@/views/preview/SplitPreviewView.vue'))
const SystemManagementView = defineAsyncComponent(() => import('@/views/system/SystemManagementView.vue'))
const DeviceCenterView = defineAsyncComponent(() => import('@/views/devices/DeviceCenterView.vue'))
const BimPointcloudAlignView = defineAsyncComponent(
  () => import('@/views/alignment/BimPointcloudAlignView.vue'),
)

const session = ref<AuthSession | null>(null)
const authReady = ref(false)
const route = useRoute()
const router = useRouter()

const routeState = computed(() => readNavigationRouteState(route.path, route.query))
const routeKey = computed(() => getRouteInstanceKey(routeState.value))

const currentView = computed(() => {
  if (!authReady.value) {
    return 'auth-loading'
  }

  if (!session.value) {
    return 'login'
  }

  if (routeState.value.path === '/' || routeState.value.path.startsWith('/projects')) {
    return 'project-selection'
  }

  if (routeState.value.path.startsWith('/alignment')) {
    return 'alignment'
  }

  if (
    routeState.value.path.startsWith('/preview/asset') ||
    routeState.value.view === 'asset-preview'
  ) {
    return 'asset-preview'
  }

  if (routeState.value.path.startsWith('/survey')) {
    return routeState.value.projectId ? 'survey' : 'project-selection'
  }

  if (routeState.value.path.startsWith('/devices')) {
    return 'devices'
  }

  if (routeState.value.path.startsWith('/system')) {
    return 'system'
  }

  if (routeState.value.path.startsWith('/design/bim')) {
    return routeState.value.projectId ? 'design-bim' : 'project-selection'
  }

  if (routeState.value.path.startsWith('/design/cad')) {
    return routeState.value.projectId ? 'design-cad' : 'project-selection'
  }

  if (routeState.value.path.startsWith('/design/overview')) {
    return routeState.value.projectId ? 'design-overview' : 'project-selection'
  }

  if (
    routeState.value.path.startsWith('/preview/split') ||
    routeState.value.view === 'split-preview'
  ) {
    return 'split-preview'
  }

  if (routeState.value.path.startsWith('/upload')) {
    return routeState.value.projectId ? 'upload' : 'project-selection'
  }

  return 'project-selection'
})

function handleLoginSuccess(nextSession: AuthSession) {
  session.value = nextSession
}

onMounted(() => {
  void validateStoredSession()
    .then((nextSession) => {
      session.value = nextSession
    })
    .catch(() => {
      session.value = null
    })
    .finally(() => {
      authReady.value = true
    })
})

async function handleLogout() {
  try {
    await logoutCurrentSession()
  } catch {
    // Clear the local view even if the server is already unavailable.
  }
  session.value = null
  void router.replace('/')
}

// Profile and role changes made in the system page are reflected in the header
// without forcing a reload.
async function handleSessionUpdated() {
  try {
    session.value = await refreshCurrentSession()
  } catch {
    // Keep the current session when the server cannot be reached.
  }
}
</script>

<template>
  <LoginView
    v-if="currentView === 'login'"
    @login-success="handleLoginSuccess"
  />

  <div v-else-if="currentView === 'auth-loading'" class="auth-loading" aria-live="polite">
    正在恢复登录状态...
  </div>

  <ProjectSelectionView
    v-else-if="session && currentView === 'project-selection'"
    :session="session"
    @logout="handleLogout"
  />

  <AssetPreviewView
    v-else-if="session && currentView === 'asset-preview'"
    :key="routeKey"
    :preview-type="routeState.previewType === 'pointcloud' ? 'pointcloud' : 'bim'"
    :asset-id="routeState.assetId"
    :display-name="routeState.displayName"
    :project-id="routeState.projectId || 0"
    :project-name="routeState.projectName"
  />
  <SplitPreviewView
    v-else-if="session && currentView === 'split-preview'"
    :key="routeKey"
    :bim-asset-id="routeState.bimAssetId"
    :pointcloud-asset-id="routeState.pointcloudAssetId"
    :bim-display-name="routeState.displayName"
    :pointcloud-display-name="routeState.pointcloudDisplayName"
  />
  <BimPointcloudAlignView
    v-else-if="session && currentView === 'alignment'"
    :key="routeKey"
    :bim-asset-id="routeState.bimAssetId"
    :pointcloud-asset-id="routeState.pointcloudAssetId"
    :bim-display-name="routeState.displayName"
    :pointcloud-display-name="routeState.pointcloudDisplayName"
  />
  <AppLayout
    v-else-if="session && (routeState.projectId || currentView === 'devices' || currentView === 'system')"
    :session="session"
    :project-id="routeState.projectId || 0"
    :project-name="routeState.projectName || (routeState.projectId ? `项目 ${routeState.projectId}` : '工作区')"
    :show-sidebar="currentView !== 'devices' && currentView !== 'system'"
    @logout="handleLogout"
  >
    <DesignView
      v-if="currentView === 'design-bim'"
      :key="routeKey"
      mode="bim"
      :session="session"
      :project-id="routeState.projectId || 0"
      :project-name="routeState.projectName"
    />
    <DesignView
      v-else-if="currentView === 'design-cad'"
      :key="routeKey"
      mode="cad"
      :session="session"
      :project-id="routeState.projectId || 0"
      :project-name="routeState.projectName"
    />
    <DesignView
      v-else-if="currentView === 'design-overview'"
      :key="routeKey"
      mode="overview"
      :session="session"
      :project-id="routeState.projectId || 0"
      :project-name="routeState.projectName"
    />
    <ProjectSurveyView
      v-else-if="currentView === 'survey'"
      :key="routeKey"
      :session="session"
      :project-id="routeState.projectId || 0"
      :project-name="routeState.projectName"
    />
    <DeviceCenterView
      v-else-if="currentView === 'devices'"
      :key="routeKey"
    />
    <SystemManagementView
      v-else-if="currentView === 'system' && session"
      :key="routeKey"
      @session-updated="handleSessionUpdated"
    />
    <UploadView
      v-else
      :key="routeKey"
      :session="session"
      :project-id="routeState.projectId || 0"
      :project-name="routeState.projectName"
      @logout="handleLogout"
    />
  </AppLayout>
</template>

<style>
.auth-loading {
  min-height: 100vh;
  display: grid;
  place-items: center;
  color: #7185a3;
  background: #f8f9fb;
  font-size: 14px;
}
</style>
