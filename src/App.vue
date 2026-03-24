<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  clearStoredSession,
  getStoredSession,
  type AuthSession,
  validateStoredSession,
} from '@/features/auth/auth.service'
import LoginView from '@/views/login/LoginView.vue'
import BimPointcloudAlignView from '@/views/alignment/BimPointcloudAlignView.vue'
import AssetPreviewView from '@/views/preview/AssetPreviewView.vue'
import SplitPreviewView from '@/views/preview/SplitPreviewView.vue'
import UploadView from '@/views/upload/UploadView.vue'

const session = ref<AuthSession | null>(getStoredSession())
const currentUrl = ref(window.location.href)

function readRouteState() {
  const url = new URL(currentUrl.value)
  const search = url.searchParams
  const parseNumber = (value: string | null) => {
    if (!value) return null
    const next = Number(value)
    return Number.isFinite(next) ? next : null
  }

  return {
    path: url.pathname,
    view: search.get('view'),
    previewType: search.get('previewType'),
    assetId: parseNumber(search.get('assetId')),
    bimAssetId: parseNumber(search.get('bimAssetId') || search.get('bimFileId')),
    pointcloudAssetId: parseNumber(
      search.get('pointcloudAssetId') || search.get('pointcloudFileId'),
    ),
    displayName:
      search.get('displayName') || search.get('bimDisplayName') || undefined,
    pointcloudDisplayName:
      search.get('pointcloudDisplayName') || search.get('scanDisplayName') || undefined,
  }
}

const routeState = computed(() => readRouteState())
const routeKey = computed(() => currentUrl.value)

const currentView = computed(() => {
  if (!session.value) {
    return 'login'
  }

  if (routeState.value.path.startsWith('/alignment')) {
    return 'alignment'
  }

  if (routeState.value.view === 'asset-preview') {
    return 'asset-preview'
  }

  return routeState.value.view === 'split-preview' ? 'split-preview' : 'upload'
})

function handleLoginSuccess(nextSession: AuthSession) {
  session.value = nextSession
}

function handleLogout() {
  clearStoredSession()
  session.value = null
}

function handleLocationChange() {
  currentUrl.value = window.location.href
}

onMounted(() => {
  window.addEventListener('popstate', handleLocationChange)

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

onBeforeUnmount(() => {
  window.removeEventListener('popstate', handleLocationChange)
})
</script>

<template>
  <LoginView
    v-if="currentView === 'login'"
    @login-success="handleLoginSuccess"
  />

  <UploadView
    v-else-if="session && currentView === 'upload'"
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
