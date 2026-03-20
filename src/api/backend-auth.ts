import { backendRequest, type BackendResult } from '@/api/backend-http'
import type { AuthUser, LoginPayload, RegisterPayload } from '@/features/auth/auth.types'

interface LoginResponse {
  token: string
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
  return backendRequest<BackendResult<AuthUser>>('/auth/me', {
    method: 'GET',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
}
