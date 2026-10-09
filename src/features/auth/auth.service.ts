import {
  changeAccountPassword,
  getCurrentUser,
  login,
  logout,
  registerAccount,
  revokeOtherSessions,
  updateAccountProfile,
  type AccountProfile,
  type PasswordChangeResult,
  type SessionRevokeResult,
} from '@/api/backend-auth'
import {
  clearStoredSession,
  getStoredLastUsername,
  getStoredSession,
  setMemoryAccessToken,
  setStoredLastUsername,
  setStoredSession,
} from './auth.storage'
import type { AuthSession, AuthUser, LoginPayload, RegisterPayload } from './auth.types'

export type { AuthSession, AuthUser, LoginPayload, MemberRole, RegisterPayload } from './auth.types'
export {
  clearStoredSession,
  getStoredLastUsername,
  getStoredSession,
} from './auth.storage'

function createSession(user: AuthUser) {
  return {
    id: user.id,
    username: user.username,
    displayName: user.displayName?.trim() || user.username,
    role: user.role === 'admin' ? 'admin' : 'member',
    loginAt: new Date().toISOString(),
  } satisfies AuthSession
}

function getErrorStatus(error: unknown) {
  return (error as any)?.response?.status as number | undefined
}

function getErrorMessage(error: unknown) {
  const responseData = (error as any)?.response?.data

  if (responseData?.msg && typeof responseData.msg === 'string') {
    return responseData.msg.trim()
  }

  if (responseData?.message && typeof responseData.message === 'string') {
    return responseData.message.trim()
  }

  return error instanceof Error ? error.message : ''
}

function normalizeLoginError(error: unknown) {
  const status = getErrorStatus(error)
  const message = getErrorMessage(error)

  if (status === 401) {
    return new Error('用户名或密码错误，请检查后重试。')
  }

  if (status === 400) {
    return new Error(message || '登录参数不完整，请重新填写账号和密码。')
  }

  return error instanceof Error ? error : new Error(message || '登录失败，请稍后重试。')
}

function normalizeRegisterError(error: unknown) {
  const status = getErrorStatus(error)
  const message = getErrorMessage(error)

  if (status === 400) {
    if (message.includes('注册码')) {
      return new Error(message)
    }

    return new Error(message || '注册信息不合法，请检查用户名、密码和注册码。')
  }

  if (status === 409) {
    if (message.includes('用户名')) {
      return new Error('该用户名已存在，请更换其他用户名。')
    }

    if (message.includes('注册码')) {
      return new Error(message)
    }

    return new Error(message || '注册信息冲突，请检查用户名或注册码状态。')
  }

  return error instanceof Error ? error : new Error(message || '注册失败，请稍后重试。')
}

export async function loginWithPassword(
  payload: LoginPayload,
): Promise<AuthSession> {
  const normalizedUsername = payload.username.trim()
  setStoredLastUsername(normalizedUsername)

  try {
    const loginResult = await login(payload)
    setMemoryAccessToken(loginResult.data.token || '')

    const meResult = await getCurrentUser(loginResult.data.token)
    const session = createSession(meResult.data)
    setStoredSession(session)
    setStoredLastUsername(session.username)

    return session
  } catch (error) {
    throw normalizeLoginError(error)
  }
}

export async function registerWithPassword(
  payload: RegisterPayload,
): Promise<AuthSession> {
  const normalizedUsername = payload.username.trim()
  setStoredLastUsername(normalizedUsername)

  try {
    await registerAccount({
      username: normalizedUsername,
      password: payload.password,
      registerCode: payload.registerCode.trim(),
    })
  } catch (error) {
    throw normalizeRegisterError(error)
  }

  return loginWithPassword({
    username: normalizedUsername,
    password: payload.password,
  })
}

export async function validateStoredSession(): Promise<AuthSession | null> {
  try {
    const meResult = await getCurrentUser()
    return createSession(meResult.data)
  } catch (error: any) {
    if (error?.response?.status === 401) {
      clearStoredSession()
      return null
    }

    throw error
  }
}

export async function logoutCurrentSession() {
  await logout()
}

// Account management lives next to the session helpers so the system page reuses
// the same token plumbing instead of duplicating it.

export async function loadAccountProfile(): Promise<AccountProfile> {
  const result = await getCurrentUser()
  return result.data
}

export async function saveAccountProfile(payload: {
  displayName: string
  email: string
  phone: string
}): Promise<AccountProfile> {
  const result = await updateAccountProfile(payload)
  return result.data
}

export async function changePassword(payload: {
  currentPassword: string
  newPassword: string
}): Promise<PasswordChangeResult> {
  const result = await changeAccountPassword(payload)
  applyReplacementToken(result.data.token)
  return result.data
}

export async function revokeOtherAccountSessions(
  currentPassword: string,
): Promise<SessionRevokeResult> {
  const result = await revokeOtherSessions({ currentPassword })
  applyReplacementToken(result.data.token)
  return result.data
}

// Changing credentials rotates the session token; keeping the replacement in
// memory avoids signing the current browser out.
function applyReplacementToken(token?: string) {
  if (token && token.trim()) {
    setMemoryAccessToken(token.trim())
  }
}

export async function refreshCurrentSession(): Promise<AuthSession> {
  const meResult = await getCurrentUser()
  const session = createSession(meResult.data)
  setStoredSession(session)
  return session
}
