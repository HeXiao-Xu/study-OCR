/** 认证相关类型（与 API.md §1 的字段一一对应）。 */
import type { UserInfo } from './api'

export interface AuthTokens {
  access_token: string
  token_type: string
  expires_in: number
  refresh_token: string
  refresh_expires_in: number
}

export interface LoginData extends AuthTokens {
  user: UserInfo
}