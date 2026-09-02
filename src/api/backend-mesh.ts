import { backendRequest, backendRequestRaw, normalizeBackendUrl, type BackendResult } from '@/api/backend-http'

export interface MeshAlgorithmParam {
  key: string
  label: string
  type: 'int' | 'float' | 'bool'
  default: number | boolean | null
  min?: number
  max?: number
  tooltip?: string
  visible_when?: { key: string; value: boolean | number } | null
}

export interface MeshAlgorithm {
  name: string
  label: string
  params: MeshAlgorithmParam[]
}

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
}

export function getMeshAlgorithms() {
  return backendRequest<BackendResult<MeshAlgorithm[]>>('/mesh/algorithms', { method: 'GET' })
}

export function remeshBimAsset(assetId: number, payload: { algorithm: string; params?: Record<string, unknown>; force?: boolean }) {
  return backendRequest<BackendResult<{ status: string; resultFileId: number; stats: RemeshStats }>>(`/assets/${assetId}/mesh/remesh`, {
    method: 'POST',
    data: payload,
    timeout: 2 * 60 * 60 * 1000,
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
  })
  return response.data
}
