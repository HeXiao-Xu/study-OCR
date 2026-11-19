/**
 * 用户状态：token / refresh token / 当前用户 / 角色与能力。
 *
 * 存储决定见 D-021：access+refresh 都放 **localStorage**（前后端分离、开发跨域最简单），
 * 代价是 XSS 可读——所以全项目要求动态内容只走 Vue 插值，禁止 `v-html`。
 *
 * 这里只做状态；`/auth/me` 水合放在路由守卫（避免 store 反向 import api 造成循环依赖）。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { UserInfo, UserRole } from '../types/api'
import type { LoginData } from '../types/auth'

export const TOKEN_KEY = 'study_ocr_token'
export const REFRESH_KEY = 'study_ocr_refresh'
export const USER_KEY = 'study_ocr_user'

/** 角色 → 能力清单。菜单用它只是体验，**最终裁决永远在后端**（AGENTS §10.7）。 */
export const ROLE_PERMISSIONS: Record<UserRole, readonly string[]> = {
  submitter: [
    'invoice:read:own',
    'invoice:upload',
    'invoice:field:edit:non_amount',
    'report:create',
    'report:sign',
    'report:submit',
  ],
  reviewer: [
    'invoice:read:all',
    'invoice:field:edit:amount',
    'report:read:submitted',
    'finance:review',
    'finance:verify',
  ],
  admin: [
    'template:read',
    'template:write',
    'rule:write',
    'cert:manage',
    'audit:read',
    'user:manage',
  ],
}

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const refreshToken = ref<string>(localStorage.getItem(REFRESH_KEY) ?? '')
  const user = ref<UserInfo | null>(readUser())

  const isAuthenticated = computed(() => token.value !== '')
  const role = computed<UserRole | null>(() => user.value?.role ?? null)
  const permissions = computed<readonly string[]>(() =>
    role.value ? ROLE_PERMISSIONS[role.value] : [],
  )

  function setAccessToken(value: string): void {
    token.value = value
    localStorage.setItem(TOKEN_KEY, value)
  }

  function setRefreshToken(value: string): void {
    refreshToken.value = value
    localStorage.setItem(REFRESH_KEY, value)
  }

  function setUser(value: UserInfo | null): void {
    user.value = value
    if (value) {
      localStorage.setItem(USER_KEY, JSON.stringify(value))
    } else {
      localStorage.removeItem(USER_KEY)
    }
  }

  /** 登录成功后一次性写入 access/refresh/user。 */
  function setSession(data: LoginData): void {
    setAccessToken(data.access_token)
    setRefreshToken(data.refresh_token)
    setUser(data.user)
  }

  function clear(): void {
    token.value = ''
    refreshToken.value = ''
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
    localStorage.removeItem(USER_KEY)
  }

  function hasRole(...roles: UserRole[]): boolean {
    return role.value !== null && roles.includes(role.value)
  }

  function hasPermission(code: string): boolean {
    return permissions.value.includes(code)
  }

  return {
    token,
    refreshToken,
    user,
    isAuthenticated,
    role,
    permissions,
    setAccessToken,
    setRefreshToken,
    setUser,
    setSession,
    clear,
    hasRole,
    hasPermission,
  }
})

function readUser(): UserInfo | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UserInfo
  } catch {
    // 本地数据损坏时静默清掉，不因为一条脏数据让整个应用白屏
    localStorage.removeItem(USER_KEY)
    return null
  }
}