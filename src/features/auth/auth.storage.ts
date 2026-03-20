import type { AuthSession } from './auth.types'

export const AUTH_STORAGE_KEY = 'cloudbim-viewer.auth-session'
export const LAST_USERNAME_STORAGE_KEY = 'cloudbim-viewer.last-username'

export function getStoredSession(): AuthSession | null {
  const rawValue = window.localStorage.getItem(AUTH_STORAGE_KEY)

  if (!rawValue) {
    return null
  }

  try {
    return JSON.parse(rawValue) as AuthSession
  } catch {
    window.localStorage.removeItem(AUTH_STORAGE_KEY)
    return null
  }
}

export function setStoredSession(session: AuthSession) {
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session))
}

export function clearStoredSession() {
  window.localStorage.removeItem(AUTH_STORAGE_KEY)
}

export function getStoredAccessToken() {
  return getStoredSession()?.accessToken?.trim() || ''
}

export function getStoredLastUsername() {
  return window.localStorage.getItem(LAST_USERNAME_STORAGE_KEY)?.trim() || ''
}

export function setStoredLastUsername(username: string) {
  const normalizedUsername = username.trim()

  if (!normalizedUsername) {
    return
  }

  window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, normalizedUsername)
}
