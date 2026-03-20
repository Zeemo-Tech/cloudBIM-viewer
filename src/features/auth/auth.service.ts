import { getCurrentUser, login, registerAccount } from '@/api/backend-auth'
import {
  clearStoredSession,
  getStoredLastUsername,
  getStoredSession,
  setStoredLastUsername,
  setStoredSession,
} from './auth.storage'
import type { AuthSession, LoginPayload, RegisterPayload } from './auth.types'

export type { AuthSession, LoginPayload, RegisterPayload } from './auth.types'
export {
  clearStoredSession,
  getStoredLastUsername,
  getStoredSession,
} from './auth.storage'

function createSession(accessToken: string, user: { id: number; username: string }) {
  return {
    id: user.id,
    accessToken,
    username: user.username,
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
    const accessToken = loginResult.data.token

    if (!accessToken) {
      throw new Error('登录成功，但未获取到有效 token')
    }

    const meResult = await getCurrentUser(accessToken)
    const session = createSession(accessToken, meResult.data)
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
  const currentSession = getStoredSession()

  if (!currentSession?.accessToken) {
    return null
  }

  try {
    const meResult = await getCurrentUser(currentSession.accessToken)
    const nextSession = createSession(currentSession.accessToken, meResult.data)
    setStoredSession(nextSession)
    return nextSession
  } catch (error: any) {
    if (error?.response?.status === 401) {
      clearStoredSession()
      return null
    }

    throw error
  }
}
