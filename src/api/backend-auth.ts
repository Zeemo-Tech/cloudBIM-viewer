import { backendRequest, type BackendResult } from '@/api/backend-http'
import type { AuthUser, LoginPayload, RegisterPayload } from '@/features/auth/auth.types'

interface LoginResponse {
  token?: string
}

export interface AccountProfile extends AuthUser {
  displayName: string
  email: string
  phone: string
  role: 'admin' | 'member'
  status: 'active' | 'disabled'
  createdAt: string
  updatedAt: string
  lastLoginAt: string | null
  projectCount: number
  assetCount: number
  alignmentCount: number
}

export interface PasswordChangeResult {
  changedAt: string
  token?: string
  revokedOtherSessions?: boolean
}

export interface SessionRevokeResult {
  revokedAt: string
  token?: string
}

export function healthCheck() {
  return backendRequest<BackendResult<Record<string, never>>>('/health', {
    method: 'GET',
  })
}

export function registerAccount(payload: RegisterPayload) {
  return backendRequest<BackendResult<AuthUser>>('/auth/register', {
    method: 'POST',
    data: payload,
  })
}

export function login(payload: LoginPayload) {
  return backendRequest<BackendResult<LoginResponse>>('/auth/login', {
    method: 'POST',
    data: {
      username: payload.username.trim(),
      password: payload.password,
    },
  })
}

export function getCurrentUser(token?: string) {
  return backendRequest<BackendResult<AccountProfile>>('/auth/me', {
    method: 'GET',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
}

export function logout() {
  return backendRequest<BackendResult<Record<string, never>>>('/auth/logout', {
    method: 'POST',
  })
}

export function updateAccountProfile(payload: { displayName: string; email: string; phone: string }) {
  return backendRequest<BackendResult<AccountProfile>>('/auth/profile', {
    method: 'PATCH',
    data: payload,
  })
}

export function changeAccountPassword(payload: { currentPassword: string; newPassword: string }) {
  return backendRequest<BackendResult<PasswordChangeResult>>('/auth/password', {
    method: 'POST',
    data: payload,
  })
}

export function revokeOtherSessions(payload: { currentPassword: string }) {
  return backendRequest<BackendResult<SessionRevokeResult>>('/auth/sessions/revoke', {
    method: 'POST',
    data: payload,
  })
}
