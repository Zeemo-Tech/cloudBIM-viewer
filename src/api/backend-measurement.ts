import { backendRequest, type BackendResult } from '@/api/backend-http'

export type MeasurementKind = 'locate' | 'distance' | 'area'
export interface MeasurementSnapshot {
  id: number
  kind: MeasurementKind
  payload: unknown
  createdAt: string
}

export function listMeasurements(assetId: number) {
  return backendRequest<BackendResult<MeasurementSnapshot[]>>(`/assets/${assetId}/measurements`, { method: 'GET' })
}

export function createMeasurement(assetId: number, kind: MeasurementKind, payload: unknown) {
  return backendRequest<BackendResult<MeasurementSnapshot>>(`/assets/${assetId}/measurements`, {
    method: 'POST',
    data: { kind, payload },
  })
}

export function deleteMeasurement(measurementId: number) {
  return backendRequest<BackendResult<null>>(`/measurements/${measurementId}`, { method: 'DELETE' })
}
