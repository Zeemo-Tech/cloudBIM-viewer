import { backendRequest, type BackendResult } from '@/api/backend-http'

export interface ScanHistoryItem {
  scanFileId: number
  producedAt: string
  hasCadAlignment: boolean
  hasBimAlignment: boolean
  calibrated: boolean
}

export interface ScanCalibrationStatus {
  scanFileId: number
  calibrated: boolean
  hasCadAlignment: boolean
  hasBimAlignment: boolean
  hasGaussBinding: boolean
  cadFileId: number | null
  bimFileId: number | null
  gaussFileId: number | null
}

export interface ModelPair {
  modelScanX: number
  modelScanY: number
  modelScanZ: number
  modelBimX: number
  modelBimY: number
  modelBimZ: number
}

export interface BimAlignmentPayload {
  modelScanFileId: number
  modelBimFileId: number
  modelPairs: ModelPair[]
}

export interface BimAlignmentResult {
  modelId: number
  modelScanFileId: number
  modelBimFileId: number
  modelBimBuildingName?: string | null
  modelRotationQx: number
  modelRotationQy: number
  modelRotationQz: number
  modelRotationQw: number
  modelTranslationX: number
  modelTranslationY: number
  modelTranslationZ: number
  modelMatrix: number[]
  modelRmse: number
  modelMaxError: number
  modelPairCount: number
  modelInlierCount: number
}

export interface FineAlignmentParams {
  modelScanFileId: number
  modelBimFileId: number
  maxCorrespondenceDistance?: number
  rmseRegressRatio?: number
  fitnessRegressRatio?: number
  applyWhenRegressed?: boolean
}

export interface FineAlignmentMetrics {
  initFitness: number
  initRmse: number
  fineFitness: number
  fineRmse: number
  deltaTranslationM: number
  deltaRotationDeg: number
  elapsedS: number
  sourceTotalPoints: number
  targetPoints: number
}

export interface FineAlignmentResult {
  modelScanFileId: number
  modelBimFileId: number
  modelBimBuildingName?: string | null
  modelMatrix: number[]
  regressed: boolean
  appliedFineResult: boolean
  fallback: boolean
  rmseRegressRatio: number
  fitnessRegressRatio: number
  applyWhenRegressed: boolean
  metrics: FineAlignmentMetrics
  modelRotationQx: number
  modelRotationQy: number
  modelRotationQz: number
  modelRotationQw: number
  modelTranslationX: number
  modelTranslationY: number
  modelTranslationZ: number
}

interface PaginatedData<T> {
  total: number
  page: number
  pageSize: number
  list: T[]
}

export function listScans(params: {
  from?: string
  to?: string
  page?: number
  pageSize?: number
} = {}) {
  return backendRequest<BackendResult<PaginatedData<ScanHistoryItem>>>('/scans', {
    method: 'GET',
    params: {
      from: params.from,
      to: params.to,
      page: params.page ?? 1,
      pageSize: params.pageSize ?? 100,
    },
  })
}

export function getScanCalibration(scanFileId: number) {
  return backendRequest<BackendResult<ScanCalibrationStatus>>(
    `/scans/${scanFileId}/calibration`,
    {
      method: 'GET',
    },
  )
}

export function createBimAlignment(payload: BimAlignmentPayload) {
  return backendRequest<BackendResult<BimAlignmentResult>>('/alignments/bim', {
    method: 'POST',
    data: payload,
  })
}

export function getBimAlignment(params: {
  modelScanFileId: number
  modelBimFileId: number
}) {
  return backendRequest<BackendResult<BimAlignmentResult>>('/alignments/bim', {
    method: 'GET',
    params,
  })
}

export function computeFineAlignment(params: FineAlignmentParams) {
  return backendRequest<BackendResult<FineAlignmentResult>>('/alignments/bim/fine', {
    method: 'POST',
    data: params,
    timeout: 600_000,
  })
}
