/** 认证接口（API.md §1）。所有调用都经 http.ts，组件不直接碰 axios。 */
import { get, post, unwrap } from './http'
import type { UserInfo } from '../types/api'
import type { LoginData } from '../types/auth'

export async function login(username: string, password: string): Promise<LoginData> {
  const envelope = await post<LoginData>('/auth/login', { username, password })
  return unwrap(envelope)
}

export async function fetchMe(): Promise<UserInfo> {
  const envelope = await get<UserInfo>('/auth/me')
  return unwrap(envelope)
}

/** 登出失败不阻塞本地清理：会话已经在本机不可用了，不该把用户卡在页面上。 */
export async function logout(refreshToken?: string): Promise<void> {
  try {
    await post('/auth/logout', { refresh_token: refreshToken ?? null })
  } catch {
    /* 忽略：调用方无论如何都会清本地会话 */
  }
}