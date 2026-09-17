import type { LocationQuery, RouteLocationRaw, Router } from 'vue-router'

export type NavigationRouteState = {
  path: string
  projectId: number | null
  projectName?: string
  view?: string
  previewType?: string
  assetId: number | null
  bimAssetId: number | null
  pointcloudAssetId: number | null
  displayName?: string
  pointcloudDisplayName?: string
  returnTo?: string
  step: number | null
}

const returnPaths = new Set([
  '/projects',
  '/survey',
  '/design/bim',
  '/design/cad',
  '/design/overview',
  '/upload',
])

function firstString(value: unknown): string | undefined {
  const candidate = Array.isArray(value) ? value[0] : value
  return typeof candidate === 'string' && candidate ? candidate : undefined
}

function positiveInteger(value: unknown): number | null {
  const candidate = firstString(value)
  if (!candidate || !/^\d+$/.test(candidate)) return null

  const parsed = Number(candidate)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

function readReturnTo(value: unknown, projectId: number | null): string | undefined {
  const candidate = firstString(value)
  if (!candidate || !candidate.startsWith('/') || candidate.startsWith('//') || candidate.includes('#')) {
    return undefined
  }

  const [path, query = ''] = candidate.split('?')
  if (!path || !returnPaths.has(path)) return undefined

  const targetProjectId = positiveInteger(new URLSearchParams(query).get('projectId'))
  const projectScopedPath = path !== '/projects'
  if (projectScopedPath && (!projectId || targetProjectId !== projectId)) return undefined
  if (projectId && targetProjectId !== null && targetProjectId !== projectId) return undefined

  return candidate
}

/**
 * Normalizes query state shared by App and viewer navigation. IDs must be
 * positive safe integers so malformed deep links consistently enter the
 * existing missing-selection state instead of requesting an arbitrary asset.
 */
export function readNavigationRouteState(path: string, query: LocationQuery): NavigationRouteState {
  const projectId = positiveInteger(query.projectId)
  return {
    path,
    projectId,
    projectName: firstString(query.projectName),
    view: firstString(query.view),
    previewType: firstString(query.previewType),
    assetId: positiveInteger(query.assetId),
    bimAssetId: positiveInteger(query.bimAssetId) ?? positiveInteger(query.bimFileId),
    pointcloudAssetId:
      positiveInteger(query.pointcloudAssetId) ?? positiveInteger(query.pointcloudFileId),
    displayName: firstString(query.displayName) ?? firstString(query.bimDisplayName),
    pointcloudDisplayName: firstString(query.pointcloudDisplayName) ?? firstString(query.scanDisplayName),
    returnTo: readReturnTo(query.returnTo, projectId),
    step: positiveInteger(query.step),
  }
}

/**
 * Only identity changes recreate expensive workspaces. Presentation and return
 * query changes remain reactive props and therefore keep the user's workspace.
 */
export function getRouteInstanceKey(state: NavigationRouteState): string {
  if (state.path.startsWith('/preview/asset') || state.view === 'asset-preview') {
    return `asset-preview:${state.previewType ?? 'bim'}:${state.assetId ?? 'missing'}`
  }
  if (state.path.startsWith('/preview/split') || state.view === 'split-preview') {
    return `split-preview:${state.bimAssetId ?? 'missing'}:${state.pointcloudAssetId ?? 'missing'}`
  }
  if (state.path.startsWith('/alignment')) {
    return `alignment:${state.bimAssetId ?? 'missing'}:${state.pointcloudAssetId ?? 'missing'}`
  }
  return `${state.path}:${state.projectId ?? 'missing'}`
}

/**
 * Resolves an explicit, same-app return context. `returnTo` is path allowlisted
 * before use; callers can safely persist a source URL in a viewer deep link.
 */
export function getViewerReturnLocation(
  state: NavigationRouteState,
  fallbackPath = getViewerFallbackPath(state),
): RouteLocationRaw {
  if (state.returnTo) return state.returnTo

  if (state.projectId) {
    return {
      path: fallbackPath,
      query: {
        projectId: String(state.projectId),
        ...(state.projectName ? { projectName: state.projectName } : {}),
      },
    }
  }

  return fallbackPath
}

export function getViewerFallbackPath(state: NavigationRouteState): string {
  if (!state.projectId) return '/projects'
  if (state.path.startsWith('/preview/asset') || state.view === 'asset-preview') {
    return state.previewType === 'pointcloud' ? '/survey' : '/design/bim'
  }
  return '/survey'
}

/**
 * Return to the exact source entry when it is the browser's previous history
 * item; otherwise replace the viewer entry so a close action cannot form a
 * push/back loop after refresh or a pasted deep link.
 */
export function navigateViewerBack(
  router: Router,
  state: NavigationRouteState,
  fallbackPath?: string,
): void {
  const target = getViewerReturnLocation(state, fallbackPath)
  const targetFullPath = typeof target === 'string' ? target : router.resolve(target).fullPath
  if (router.options.history.state.back === targetFullPath) {
    void router.back()
    return
  }
  void router.replace(target)
}
