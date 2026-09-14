import { backendRequest, type BackendResult } from '@/api/backend-http'

export interface DenoiseResult {
  version: string
  fresh: boolean
  staleReason?: string
  updatedAt: string
  result: {
    pointsBefore: number
    pointsAfter: number
    counts: Record<'unknown' | 'table' | 'fixture' | 'steel' | 'noise', number>
    previewPointCount: number
    previewOrigin: [number, number, number]
    normalK?: number
    elapsedSeconds: number
    instanceCount: number
    algorithmVersion: string
    instanceContract?: 'rebar-instance-map-v1'
    instancesSha256?: string
  }
}
export function computeDenoise(scanId: number, bimId: number) {
  return backendRequest<BackendResult<DenoiseResult>>('/alignments/bim/denoise', {
    method: 'POST', data: { modelScanFileId: scanId, modelBimFileId: bimId }, timeout: 3_720_000,
  })
}
export function getLatestDenoise(scanId: number, bimId: number) {
  return backendRequest<BackendResult<DenoiseResult | null>>('/alignments/bim/denoise/latest', {
    params: { modelScanFileId: scanId, modelBimFileId: bimId },
  })
}
export function denoiseArtifactUrl(scanId: number, bimId: number, version: string, name: 'preview.ply' | 'cleaned.las' | 'instance-map.json') {
  return `/alignments/bim/denoise/artifacts/${name}?${new URLSearchParams({ modelScanFileId: String(scanId), modelBimFileId: String(bimId), version })}`
}
