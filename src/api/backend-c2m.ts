import { backendRequest, normalizeBackendUrl, type BackendResult } from '@/api/backend-http'

export interface C2MStats {
  min: number
  max: number
  mean: number
  std: number
  p50: number
  p90: number
  p95: number
  p99: number
}

export interface C2MResult {
  modelScanFileId: number
  modelBimFileId: number
  voxelSize: number
  pointsBefore: number
  pointsAfter: number
  meshVertexCount: number
  stats: C2MStats
  histogram?: { binEdges: number[]; counts: number[]; overflowCount?: number }
  diagnostics?: { scanBboxRaw?: { min: number[]; max: number[] }; scanBboxAfterTransform?: { min: number[]; max: number[] }; meshBbox?: { min: number[]; max: number[] }; bboxOverlapIoU?: number }
  coloredPlyAvailable?: boolean
}

export interface C2MParams {
  modelScanFileId: number
  modelBimFileId: number
  voxelSize?: number
  maxColormapDistance?: number
  maxHistogramDistance?: number
  histogramBins?: number
  toleranceLimit?: number
  knnK?: number
  normalConstraintEnabled?: boolean
  normalHalfSpaceOnly?: boolean
  normalMaxAngleDeg?: number
  normalFallbackMode?: string
}

export function computeC2M(params: C2MParams) {
  return backendRequest<BackendResult<C2MResult>>('/alignments/bim/c2m', {
    method: 'POST',
    data: params,
    timeout: 600_000,
  })
}

export function getLatestC2M(scanId: number, bimId: number) {
  return backendRequest<BackendResult<C2MResult>>('/alignments/bim/c2m/latest', {
    method: 'GET',
    params: { modelScanFileId: scanId, modelBimFileId: bimId },
  })
}

export function getC2MColoredPlyUrl(scanId: number, bimId: number) {
  return normalizeBackendUrl(`/alignments/bim/c2m/colored-ply?modelScanFileId=${scanId}&modelBimFileId=${bimId}`)
}
