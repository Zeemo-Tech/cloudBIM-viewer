import { backendRequest, type BackendResult } from '@/api/backend-http'

export type MemberRole = 'admin' | 'member'
export type MemberStatus = 'active' | 'disabled'

export interface WorkspaceMember {
  id: number
  username: string
  displayName: string
  email?: string
  phone?: string
  role: MemberRole
  roleLabel: string
  status: MemberStatus
  createdAt: string
  updatedAt: string
  lastLoginAt: string | null
  projectCount: number
  assetCount: number
  alignmentCount: number
  measurementCount: number
  isSelf: boolean
  isLastAdmin: boolean
}

export interface MemberListResult {
  total: number
  list: WorkspaceMember[]
  canManage: boolean
  activeAdminCount: number
  viewerId: number
}

export interface AssetTypeCount {
  type: string
  total: number
}

export interface SystemCapability {
  key: string
  label: string
  description: string
}

export interface ServiceHealth {
  status: 'ok' | 'unavailable'
  url: string
  latencyMs: number
  httpStatus?: number
}

export interface SystemInfo {
  service: string
  environment: string
  serverTime: string
  startedAt: string
  uptimeSeconds: number
  database: string
  databaseStatus: 'ready' | 'unavailable'
  storageConfigured: boolean
  dataDir?: string
  sessionExpiresAt?: string
  projectCount: number
  assetCount: number
  alignmentCount: number
  measurementCount: number
  role: MemberRole
  roleLabel: string
  member: {
    id: number
    role: MemberRole
    permissions: string[]
  }
  workspace: {
    name: string
    description: string
    memberCount: number
    adminCount: number
    projectCount: number
    assetCount: number
    alignmentCount: number
    measurementCount: number
    allowRegistration: boolean
  }
  storage: {
    sourceBytes: number
    derivativeBytes: number
    pendingUploadBytes: number
    totalBytes: number
  }
  assets: {
    total: number
    byType: AssetTypeCount[]
    byStatus: AssetTypeCount[]
    workerCount: number
  }
  meshService: ServiceHealth
  capabilities: SystemCapability[]
}

export function getSystemInfo() {
  return backendRequest<BackendResult<SystemInfo>>('/system/info', { method: 'GET' })
}

export function listWorkspaceMembers() {
  return backendRequest<BackendResult<MemberListResult>>('/system/members', { method: 'GET' })
}

export function updateWorkspaceMember(id: number, payload: { role?: MemberRole; status?: MemberStatus }) {
  return backendRequest<BackendResult<WorkspaceMember>>(`/system/members/${id}`, {
    method: 'PATCH',
    data: payload,
  })
}

export function deleteWorkspaceMember(id: number) {
  return backendRequest<BackendResult<{ id: number; username: string; deletedAt: string }>>(
    `/system/members/${id}`,
    { method: 'DELETE' },
  )
}
