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
