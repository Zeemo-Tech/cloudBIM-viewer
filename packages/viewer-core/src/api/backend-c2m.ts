import { backendRequest, normalizeBackendUrl, type BackendResult } from '@/api/backend-http'

export type C2MProfile = 'quick' | 'reference'

export type C2MMetricDirection =
  | 'mesh-vertices-to-scan-points'
  | 'mesh-vertices-to-instance-scan-points'
  | 'scan-points-to-mesh-triangles'

export interface C2MApproximation {
  voxelSize?: number
  downsampleEnabled?: boolean
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
  /** Client-derived tail counts; older stored histograms may omit these. */
  underflowCount?: number
  positiveOverflowCount?: number
  unknownCount?: number
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
  stats: C2MStats | null
  histogram?: C2MHistogram | null
  visualization?: C2MVisualization
  diagnostics?: { scanBboxRaw?: { min: number[]; max: number[] }; scanBboxAfterTransform?: { min: number[]; max: number[] }; meshBbox?: { min: number[]; max: number[] }; bboxOverlapIoU?: number; rebarComparison?: RebarComparison }
  coloredPlyAvailable?: boolean
  fresh?: boolean
  staleReason?: string
  distancesAvailable?: boolean
  resultVersion?: string
  createdAt?: string
  updatedAt?: string
  analysis?: {
    status: 'queued' | 'processing' | 'ready' | 'failed'
    version?: string
    contentHash?: string
    analysisMeshContentHash?: string
    baseUrl?: string
    manifestUrl?: string
    analysisMeshBaseUrl?: string
    analysisMeshTilesetUrl?: string
    analysisMeshComponentsUrl?: string
    error?: string | null
  }
}

export interface RebarComparisonBar {
  ifcGlobalId: string
  designBarId: string
  name: string
  instanceIds: number[]
  reviewInstanceIds?: number[]
  reviewPointCount?: number
  pointCount: number
  pointsAfter: number
  vertexStart: number
  vertexCount: number
  knownCount: number
  unknownCount: number
  status: 'matched' | 'missing' | 'review'
  stats: C2MStats | null
  measurement?: RebarMeasurement
  scanSurface?: { method: string; supportedPointCount: number; rejectedPointCount: number; supportedSectionCount: number; sectionCount: number } | null
  constraint?: { enabled: boolean; topologyAvailable: boolean; constrainedKnownCount: number; fallbackKnownCount: number }
  timingSeconds?: Record<string, number>
}

export interface RebarProfileSample {
  designUnitId: string
  stationM: number
  designCenterM: [number, number, number]
  observedCenterM: [number, number, number] | null
  transverseOffsetM: number | null
  offsetVectorM?: [number, number, number] | null
  radiusM?: number | null
  radiusDeltaM?: number | null
  fitRmseM?: number | null
  arcCoverageDeg?: number | null
  centerUncertaintyM?: number | null
  inlierCount?: number
  quality?: string
}

export interface RebarMeasurement {
  surface: { maxAbs: number | null; maxLocationM: [number, number, number] | null }
  longitudinalProfile: RebarProfileSample[]
  crossSection?: { maxAbsRadiusDeltaM: number | null; supportedSectionCount: number; sectionCount: number }
  bending: {
    maxCentrelineDepartureM: number | null
    residualBowM: number | null
    curvatureMInv: number | null
    method: string
    quality: string
  }
}

export interface RebarComparison {
  schema: 'rebar-comparison-v1'
  bars: RebarComparisonBar[]
  excludedComponentCount: number
  unassignedPointCount: number
  knownVertexCount: number
  unknownVertexCount: number
  instanceMapHash: string
  measurement?: { schema: string; coordinateFrame: string; method?: string }
  effective?: { knnK: number; normalConstraintEnabled: boolean; normalHalfSpaceOnly: boolean; normalMaxAngleDeg: number; normalFallbackMode: string; maxSearchDistance?: number }
  timings?: Record<string, number>
  algorithmVersion?: string
  inspection?: C2MRebarInspection
}

export interface RebarSpacingSample {
  stationM: number
  status: 'supported' | 'unknown'
  centerA: [number, number, number] | null
  centerB: [number, number, number] | null
  actualCenterDistanceM: number | null
  signedDifferenceM: number | null
  netClearanceM: number | null
  withinTolerance: boolean | null
}

export interface RebarSpacingInspection {
  pairId: string
  designBarIds: [string, string]
  ifcGlobalIds: [string, string]
  designUnitIds: [string, string]
  familyId: string
  layerId: string | number
  designDirection: [number, number, number]
  spacingDirection: [number, number, number]
  designCenterDistanceM: number
  radiusSource: 'design-prior'
  designRadiiM: [number, number]
  samples: RebarSpacingSample[]
  actualCenterDistanceM: number | null
  signedDifferenceM: number | null
  netClearanceM: number | null
  toleranceM: number
  withinTolerance: boolean | null
  coverage: { status: 'supported' | 'partial' | 'unavailable'; sampleCount: number; sharedSpanM: number; supportedSpanM?: number; reason: string }
}

export interface C2MRebarInspection {
  schema: 'rebar-inspection-v1'
  coordinateFrame: 'bim'
  lengthUnit: 'm'
  method: string
  provenance: { instanceMapHash: string; controlNetAlgorithmVersion: string; alignmentMatrix: number[] }
  bars: Array<{
    designBarId: string
    ifcGlobalId: string
    status: 'supported' | 'partial' | 'missing' | 'review' | 'unavailable'
    unitIds: string[]
    observedSegments: Array<{
      designUnitId: string
      kind: 'body' | 'curve'
      evidence: string
      centerline: [number, number, number][]
      supportedIntervalsM: [number, number][]
      pointCount: number
      radiusM: number
      radiusSource: 'design-prior'
    }>
    knownVertexCount: number
    unknownVertexCount: number
    toleranceM: number
    withinToleranceRatio: number | null
    quality: { supportedUnitCount: number; designUnitCount: number; reason: string }
  }>
  spacing: RebarSpacingInspection[]
  summary: {
    barCount: number
    supportedBarCount: number
    partialBarCount: number
    unavailableBarCount: number
    spacingPairCount: number
    supportedSpacingPairCount: number
    partialSpacingPairCount: number
    unavailableSpacingPairCount: number
    toleranceM: number
    toleranceBasis: string
  }
}

export interface C2MReportRun {
  resultVersion: string
  scanId: number
  bimId: number
  createdAt: string
  paramsJson: string
  timingsJson: string
}

export function listC2MReports(scanId: number, bimId: number, page = 1) {
  return backendRequest<BackendResult<{ items: C2MReportRun[]; total: number }>>('/alignments/bim/c2m/reports', {
    method: 'GET', params: { modelScanFileId: scanId, modelBimFileId: bimId, page, pageSize: 20 },
  })
}

/** Download the immutable server snapshot, including all bars and spacing rows. */
export function downloadC2MReportJSON(version: string) {
  return backendRequest<Blob>(`/alignments/bim/c2m/reports/${encodeURIComponent(version)}/json`, {
    responseType: 'blob',
  })
}

export function getC2MReport(version: string, page = 1, ifcGlobalId?: string) {
  return backendRequest<BackendResult<{ run: C2MReportRun; bars: { rawJson: string }[]; total: number }>>(`/alignments/bim/c2m/reports/${encodeURIComponent(version)}`, {
    method: 'GET', params: { page, pageSize: 100, ifcGlobalId },
  })
}

export interface C2MParams {
  denoiseVersion: string
  modelScanFileId: number
  modelBimFileId: number
  profile: C2MProfile
  voxelSize?: number
  downsampleEnabled?: boolean
  maxColormapDistance?: number
  maxHistogramDistance?: number
  histogramBins?: number
  toleranceLimit?: number
  knnK?: number
  normalConstraintEnabled?: boolean
  normalHalfSpaceOnly?: boolean
  normalMaxAngleDeg?: number
  /** Maximum candidate distance in metres; 0.0001–0.2, default 0.2. */
  maxSearchDistance?: number
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
