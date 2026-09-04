<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  clearStoredSession,
  getStoredSession,
  type AuthSession,
  validateStoredSession,
} from '@/features/auth/auth.service'
import LoginView from '@/views/login/LoginView.vue'

const UploadView = defineAsyncComponent(() => import('@/views/upload/UploadView.vue'))
const AssetPreviewView = defineAsyncComponent(() => import('@/views/preview/AssetPreviewView.vue'))
const SplitPreviewView = defineAsyncComponent(() => import('@/views/preview/SplitPreviewView.vue'))
const BimPointcloudAlignView = defineAsyncComponent(
  () => import('@/views/alignment/BimPointcloudAlignView.vue'),
)

const session = ref<AuthSession | null>(getStoredSession())
const route = useRoute()
const router = useRouter()

function readRouteState() {
  const parseNumber = (value: string | null) => {
    if (!value) return null
    const next = Number(value)
    return Number.isFinite(next) ? next : null
  }
  const pickString = (value: unknown) => {
    if (Array.isArray(value)) {
      return typeof value[0] === 'string' ? value[0] : null
    }

    return typeof value === 'string' ? value : null
  }

  return {
    path: route.path,
    view: pickString(route.query.view),
    previewType: pickString(route.query.previewType),
    assetId: parseNumber(pickString(route.query.assetId)),
    bimAssetId: parseNumber(pickString(route.query.bimAssetId) || pickString(route.query.bimFileId)),
    pointcloudAssetId: parseNumber(
      pickString(route.query.pointcloudAssetId) || pickString(route.query.pointcloudFileId),
    ),
    displayName:
      pickString(route.query.displayName) || pickString(route.query.bimDisplayName) || undefined,
    pointcloudDisplayName:
      pickString(route.query.pointcloudDisplayName) ||
      pickString(route.query.scanDisplayName) ||
      undefined,
  }
}

const routeState = computed(() => readRouteState())
const routeKey = computed(() => route.fullPath)

const currentView = computed(() => {
  if (!session.value) {
    return 'login'
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

  if (
    routeState.value.path.startsWith('/preview/split') ||
    routeState.value.view === 'split-preview'
  ) {
    return 'split-preview'
  }

  return 'upload'
})

function handleLoginSuccess(nextSession: AuthSession) {
  session.value = nextSession
  if (route.path === '/') {
    void router.replace('/upload')
  }
}

onMounted(() => {
  if (session.value) {
    void validateStoredSession()
      .then((nextSession) => {
        session.value = nextSession
      })
      .catch(() => {
        clearStoredSession()
        session.value = null
      })
  }
})

function handleLogout() {
  clearStoredSession()
  session.value = null
  void router.replace('/upload')
}
</script>

<template>
  <LoginView
    v-if="currentView === 'login'"
    @login-success="handleLoginSuccess"
  />

  <UploadView
    v-else-if="session && currentView === 'upload'"
    :key="routeKey"
    :session="session"
    @logout="handleLogout"
  />

  <AssetPreviewView
    v-else-if="session && currentView === 'asset-preview'"
    :key="routeKey"
    :preview-type="routeState.previewType === 'pointcloud' ? 'pointcloud' : 'bim'"
    :asset-id="routeState.assetId"
    :display-name="routeState.displayName"
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
</template>
