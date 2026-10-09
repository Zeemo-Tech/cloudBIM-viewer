import { backendRequest, type BackendResult, type C2MResult } from '@cloudbim/viewer-core'
import type { InspectionAction, InspectionActionRecord } from '@/features/inspection/inspection-data'

export interface OperatorTask {
  projectId: number; projectName: string; scanId: number; bimId: number | null
  scanName: string; bimName: string; scanStatus: string; hasResult: boolean; resultVersion?: string
}
export interface OperatorTaskList { items: OperatorTask[]; scanner: { connected: boolean; reason: string } }
export const listOperatorTasks = () => backendRequest<BackendResult<OperatorTaskList>>('/operator/tasks', { method: 'GET' })
export const getOperatorResult = (task: OperatorTask) => backendRequest<BackendResult<C2MResult | null>>(`/operator/tasks/${task.scanId}/result?bimId=${task.bimId}`, { method: 'GET' })
export const getOperatorGeometry = (task: OperatorTask, result: C2MResult) => backendRequest<ArrayBuffer>(`/operator/tasks/${task.scanId}/geometry?bimId=${task.bimId}&version=${encodeURIComponent(result.resultVersion || '')}`, { method: 'GET', responseType: 'arraybuffer' })
export const getOperatorActions = (version: string) => backendRequest<BackendResult<{ items: InspectionActionRecord[] }>>(`/operator/reports/${encodeURIComponent(version)}/actions`, { method: 'GET' })
export const postOperatorAction = (version: string, data: { ifcGlobalId: string; action: InspectionAction; note: string; demonstration: boolean }) => backendRequest<BackendResult<InspectionActionRecord>>(`/operator/reports/${encodeURIComponent(version)}/actions`, { method: 'POST', data })
