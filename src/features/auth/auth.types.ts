export interface LoginPayload {
  username: string
  password: string
}

export interface RegisterPayload extends LoginPayload {
  registerCode: string
}

export type MemberRole = 'admin' | 'member'

export interface AuthUser {
  id: number
  username: string
  displayName?: string
  email?: string
  phone?: string
  role?: MemberRole
  status?: 'active' | 'disabled'
  createdAt?: string
  updatedAt?: string
  lastLoginAt?: string | null
  projectCount?: number
  assetCount?: number
  alignmentCount?: number
}

export interface AuthSession {
  id: number
  accessToken?: string
  username: string
  displayName?: string
  role: MemberRole
  loginAt: string
}
