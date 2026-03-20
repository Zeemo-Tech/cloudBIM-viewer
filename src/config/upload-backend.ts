import { getStoredAccessToken } from '@/features/auth/auth.storage'

export function createUploadHeaders(extraHeaders?: HeadersInit) {
  const headers = new Headers(extraHeaders)
  const token = getStoredAccessToken()

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const result: Record<string, string> = {}
  headers.forEach((value, key) => {
    result[key] = value
  })

  return result
}
