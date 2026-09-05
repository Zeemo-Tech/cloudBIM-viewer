import { backendRequest, normalizeBackendUrl, type BackendResult } from '@/api/backend-http'

export type C2MProfile = 'quick' | 'reference'

export type C2MMetricDirection =
  | 'mesh-vertices-to-scan-points'
  | 'scan-points-to-mesh-triangles'

export interface C2MApproximation {
  voxelSize?: number
  [key: string]: number | string | boolean | null | undefined
}

export interface C2MStats {
  min: number
  max: number
  mean: number
  std: number
  p50: number
  p90: number
  p95: number
  p99: number
  meanAbs?: number
  rmse?: number
  p95Abs?: number
  withinToleranceRatio?: number
}

export interface C2MVisualization {
  maxColormapDistance: number
  maxHistogramDistance: number
  histogramBins: number
  toleranceLimit: number
  colorDistanceField?: 'raw' | 'smoothed'
  smoothingIterations?: number
  smoothingStrength?: number
}

export interface C2MHistogram {
  binEdges: number[]
  counts: number[]
  overflowCount?: number
}

export interface C2MResult {
  modelScanFileId: number
  modelBimFileId: number
  voxelSize: number
  pointsBefore: number
  pointsAfter: number
  meshVertexCount: number
  profile: C2MProfile
  algorithmVersion?: string
  metricDirection?: C2MMetricDirection | ''
  approximation?: C2MApproximation | null
  stats: C2MStats
  histogram?: C2MHistogram | null
  visualization?: C2MVisualization
  diagnostics?: { scanBboxRaw?: { min: number[]; max: number[] }; scanBboxAfterTransform?: { min: number[]; max: number[] }; meshBbox?: { min: number[]; max: number[] }; bboxOverlapIoU?: number }
  coloredPlyAvailable?: boolean
  fresh?: boolean
  staleReason?: string
  distancesAvailable?: boolean
  resultVersion?: string
  createdAt?: string
  updatedAt?: string
}

export interface C2MParams {
  modelScanFileId: number
  modelBimFileId: number
  profile: C2MProfile
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
    timeout: 1_800_000,
  })
}

export type C2MRecolorParams = Pick<
  C2MVisualization,
  | 'maxColormapDistance'
  | 'maxHistogramDistance'
  | 'histogramBins'
  | 'toleranceLimit'
> & Pick<C2MParams, 'modelScanFileId' | 'modelBimFileId'> & { resultVersion: string }

export function recolorC2M(params: C2MRecolorParams) {
  return backendRequest<BackendResult<C2MResult>>('/alignments/bim/c2m/recolor', {
    method: 'POST',
    data: params,
    timeout: 1_800_000,
  })
}

export function getLatestC2M(scanId: number, bimId: number) {
  return backendRequest<BackendResult<C2MResult>>('/alignments/bim/c2m/latest', {
    method: 'GET',
    params: { modelScanFileId: scanId, modelBimFileId: bimId },
  })
}

function getC2MArtifactUrl(path: string, scanId: number, bimId: number, resultVersion?: string) {
  const params = new URLSearchParams({
    modelScanFileId: String(scanId),
    modelBimFileId: String(bimId),
  })
  if (resultVersion) params.set('resultVersion', resultVersion)
  return normalizeBackendUrl(`${path}?${params.toString()}`)
}

export function getC2MColoredPlyUrl(scanId: number, bimId: number, resultVersion?: string) {
  return getC2MArtifactUrl('/alignments/bim/c2m/colored-ply', scanId, bimId, resultVersion)
}

export function getC2MDistancesUrl(scanId: number, bimId: number, resultVersion?: string) {
  return getC2MArtifactUrl('/alignments/bim/c2m/distances', scanId, bimId, resultVersion)
}

/**
 * Only a positive freshness assertion is safe to render. Older result rows are
 * returned with `fresh: false`; responses from an older API that omit the field
 * are also treated as unverifiable instead of silently loading stale geometry.
 */
export function isC2MResultFresh(result: C2MResult | null | undefined) {
  return result?.fresh === true
}
