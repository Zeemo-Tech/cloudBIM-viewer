import { backendRequest, type BackendResult } from '@/api/backend-http'

const projectApiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/$/, '')

function projectApiPath(path = '') {
  return `${projectApiBaseUrl}/projects${path}`
}

function projectRequestOptions(method: 'GET' | 'POST' | 'PATCH' | 'DELETE', data?: ProjectPayload) {
  return {
    method,
    data,
    headers: {
      // Keep project requests compatible with the same-origin Vite proxy.
      'X-Requested-With': undefined,
    },
  }
}

export interface ProjectSummary {
  id: number
  name: string
  description: string
  createdAt: string
  updatedAt: string
  assetCount: number
  bimCount: number
  pointcloudCount: number
  status: 'pending' | 'processing' | 'ready' | 'failed'
  hasAlignment: boolean
  scanDate: number
}

export interface ProjectPayload {
  name: string
  description?: string
}

export function listProjects() {
  return backendRequest<BackendResult<{ total: number; list: ProjectSummary[] }>>(projectApiPath(), projectRequestOptions('GET'))
}

export function createProject(payload: ProjectPayload) {
  return backendRequest<BackendResult<ProjectSummary>>(projectApiPath(), projectRequestOptions('POST', payload))
}

export function updateProject(id: number, payload: ProjectPayload) {
  return backendRequest<BackendResult<ProjectSummary>>(projectApiPath(`/${id}`), projectRequestOptions('PATCH', payload))
}

export function deleteProject(id: number) {
  return backendRequest<BackendResult<null>>(projectApiPath(`/${id}`), projectRequestOptions('DELETE'))
}
