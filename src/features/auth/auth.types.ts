export interface LoginPayload {
  username: string
  password: string
}

export interface RegisterPayload extends LoginPayload {
  registerCode: string
}

export interface AuthUser {
  id: number
  username: string
}

export interface AuthSession {
  id: number
  accessToken: string
  username: string
  loginAt: string
}
