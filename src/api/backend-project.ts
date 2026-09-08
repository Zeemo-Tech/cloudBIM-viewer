import { backendRequest, type BackendResult } from '@/api/backend-http'
import { getStoredAccessToken } from '@/features/auth/auth.storage'

const projectApiBaseUrl = import.meta.env.DEV
  ? (import.meta.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8090').replace(/\/$/, '')
  : ''

function projectApiPath(path = '') {
  return `${projectApiBaseUrl}/projects${path}`
}

function projectRequestOptions(method: 'GET' | 'POST' | 'PATCH' | 'DELETE', data?: ProjectPayload) {
  const token = getStoredAccessToken()
  return {
    method,
    data,
    headers: {
      // Direct development requests are cross-origin. Keep them CORS-simple apart
      // from the required Authorization header.
      'X-Requested-With': undefined,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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
