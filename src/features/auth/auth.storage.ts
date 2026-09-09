import type { AuthSession } from './auth.types'

export const AUTH_STORAGE_KEY = 'cloudbim-viewer.auth-session'
export const LAST_USERNAME_STORAGE_KEY = 'cloudbim-viewer.last-username'

let memoryAccessToken = ''

export function getStoredSession(): AuthSession | null {
  return null
}

export function setStoredSession(_session: AuthSession) {
  // Authentication is maintained by the backend HttpOnly cookie.
}

export function clearStoredSession() {
  memoryAccessToken = ''
}

export function getStoredAccessToken() {
  return memoryAccessToken
}

export function setMemoryAccessToken(token: string) {
  memoryAccessToken = token.trim()
}

export function getStoredLastUsername() {
  return ''
}

export function setStoredLastUsername(_username: string) {
  // Usernames are intentionally not persisted in browser storage.
}
