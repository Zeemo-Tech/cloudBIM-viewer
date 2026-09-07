import { backendRequest, type BackendResult } from '@/api/backend-http'

export type RebarCapability = 'class' | 'direction' | 'instance' | 'confidence' | 'sceneClass' | 'intersection'

export interface RebarCapabilities {
  class: boolean
  direction: boolean
  instance: boolean
  confidence: boolean
  sceneClass?: boolean
  rebarFlags?: boolean
  rawLabels?: boolean
  fixtureKind?: boolean
  rebarRole?: boolean
  bimPrior?: boolean
}

export interface RebarVisualizationMetadata {
  schema: 'rebar-visualization-v1' | 'rebar-visualization-v2' | 'rebar-visualization-v3'
  defaultMode: 'rebar-class'
  attributes: Record<string, unknown>
  values: Record<string, unknown>
  colors: Record<string, string>
  instanceStrategy: 'golden-angle-v1'
}

export interface RebarParameterProperty {
  type?: 'number' | 'integer' | 'boolean' | 'string'
  title?: string
  description?: string
  default?: unknown
  minimum?: number
  maximum?: number
  step?: number
  unit?: string
}

export interface RebarAlgorithmDescriptor {
  id: string
  version: string
  name: string
  analysisSchema: string
  capabilities: RebarCapabilities
  inputOptionSchema?: {
    properties?: Record<string, RebarParameterProperty>
  }
  parameterSchema?: {
    properties?: Record<string, RebarParameterProperty>
  }
  uiHints?: {
    order?: string[]
    advanced?: string[]
    labels?: Record<string, string>
    units?: Record<string, string>
  }
  visualization?: RebarVisualizationMetadata
}

export interface RebarSegmentationSummary {
  totalPointCount: number
  rebarPointCount: number
  directionCount: number
  instanceCount: number
  sceneClassCounts?: Record<string, number>
  fixtureKindCounts?: { unknown: number; squareTube: number; plate: number; bolt: number }
  rebarRoleCounts?: { unresolved: number; planar: number; web: number }
  directionPointCounts?: Record<string, number>
  /** V5 counts reconstructed intersections, rather than assigning points to one. */
  intersectionCount?: number
  diagnostics?: Record<string, unknown>
  rawSource?: { finitePointCount: number; ambiguousPointCount: number; instanceCount?: number; sceneClassCounts: Record<string, number> }
  rawLabelsPath?: string
  bimPrior?: { designBarCount: number; diagnostics: Record<string, unknown> }
}

export interface RebarSegmentationResult {
  assetId: number
  artifactVersion: string
  algorithm: { id: string; version: string }
  analysisSchema: string
  capabilities: RebarCapabilities
  visualization?: RebarVisualizationMetadata
  inputOptions?: Record<string, unknown>
  effectiveParameters: Record<string, unknown>
  summary: RebarSegmentationSummary
  tilesetUrl: string
  resultUrl: string
  cached?: boolean
  updatedAt?: string
}

export interface ComputeRebarSegmentationRequest {
  algorithm?: string
  inputOptions?: Record<string, unknown>
  parameters?: Record<string, unknown>
  bimPrior?: { bimAssetId: number }
}

export interface RebarInstance {
  id: number
  centerline: number[][]
  radius: number
  directionId?: number
  designId?: string
  evidence?: string
  role?: 'planar' | 'web' | 'unresolved'
  layerId?: number
  inferredSegments?: { points?: number[][]; centerline?: number[][] }[]
  observedSegments?: { points: number[][] }[]
}

export interface RebarIntersection {
  id: number
  position: [number, number, number]
  instanceIds: number[]
  segmentRefs: Array<{ instanceId: number; segmentIndex: number; edgeIndex: number }>
  angleDegrees: number
  residual: number
  evidence: 'observed-finite-centerlines'
}

export interface RebarAnalysisV2 {
  instances: RebarInstance[]
  connections?: unknown[]
  intersections: RebarIntersection[]
  diagnostics?: Record<string, unknown>
}

export interface RebarInspection {
  instances: RebarInstance[]
  intersections: RebarIntersection[]
  selectedId: number | null
  selectedIntersectionId?: number | null
  showCenterlines: boolean
  showIntersections: boolean
  hideFixtures: boolean
  pointVisibility?: Partial<Record<RebarPointVisibilityCategory, boolean>>
  visibleRebarRoles?: Partial<Record<RebarRole, boolean>>
}

export type FixtureKind = 'unknown' | 'squareTube' | 'plate' | 'bolt'
export type RebarRole = 'unresolved' | 'planar' | 'web'
export type RebarPointVisibilityCategory =
  | 'unknown' | 'table' | 'noise'
  | 'fixtureUnknown' | 'fixtureSquareTube' | 'fixturePlate' | 'fixtureBolt'
  | 'rebarUnresolved' | 'rebarPlanar' | 'rebarWeb'

export function getRebarAnalysis(resultUrl: string) {
  return backendRequest<{ analysis: RebarAnalysisV2 }>(resultUrl, { method: 'GET' })
}

export { isRebarV5Result } from '@/features/rebar-visualization/result'

export function listRebarAlgorithms() {
  return backendRequest<BackendResult<{ algorithms: RebarAlgorithmDescriptor[] }>>(
    '/rebar-segmentation/algorithms',
    { method: 'GET' },
  )
}

export function getLatestRebarSegmentation(assetId: number) {
  return backendRequest<BackendResult<RebarSegmentationResult | null>>(
    `/assets/${assetId}/rebar-segmentation/latest`,
    { method: 'GET' },
  )
}

export function computeRebarSegmentation(
  assetId: number,
  request: ComputeRebarSegmentationRequest,
  options: { force?: boolean } = {},
) {
  return backendRequest<BackendResult<RebarSegmentationResult>>(
    `/assets/${assetId}/rebar-segmentation`,
    {
      method: 'POST',
      params: { force: options.force ? 'true' : undefined },
      data: request,
      timeout: 62 * 60_000,
    },
  )
}
