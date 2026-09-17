import { backendRequest, backendRequestRaw, normalizeBackendUrl, type BackendResult } from '@/api/backend-http'

export const REBAR_SWEEP_ALGORITHM = 'rebar_sweep'
export const DEFAULT_REBAR_SWEEP_PARAMS = {
  cross_section_sides: 16,
  axial_spacing: 0.01,
  max_chord_error: 0.0001,
} as const

export interface RemeshStats {
  vertexBefore: number
  faceBefore: number
  vertexAfter: number
  faceAfter: number
}

export interface RemeshStatus {
  supported: boolean
  status?: 'idle' | 'queued' | 'processing' | 'succeeded' | 'failed'
  canManualRetry?: boolean
  resultFileId?: number | null
  lastError?: string | null
  queuedAt?: string | null
  startedAt?: string | null
  finishedAt?: string | null
  stats?: RemeshStats | null
  algorithm?: string
  implementationVersion?: string
  contractVersion?: string
  parameters?: Record<string, unknown> | null
  contentHash?: string
}

export function remeshBimAsset(assetId: number, payload: { algorithm: typeof REBAR_SWEEP_ALGORITHM; params?: Record<string, unknown>; force?: boolean }) {
  return backendRequest<BackendResult<{ status: 'queued'; message: string }>>(`/assets/${assetId}/mesh/remesh`, {
    method: 'POST',
    data: payload,
  })
}

export function getRemeshStatus(assetId: number) {
  return backendRequest<BackendResult<RemeshStatus>>(`/assets/${assetId}/mesh/remesh/status`, { method: 'GET' })
}

export function getRemeshResultUrl(assetId: number) {
  return normalizeBackendUrl(`/assets/${assetId}/mesh/remesh/latest`)
}

export async function downloadRemeshResult(assetId: number) {
  const response = await backendRequestRaw<Blob>(`/assets/${assetId}/mesh/remesh/latest`, {
    method: 'GET',
    responseType: 'blob',
    // This URL is mutable: each completed run replaces the artifact.
    headers: { 'Cache-Control': 'no-cache' },
  })
  return response.data
}
