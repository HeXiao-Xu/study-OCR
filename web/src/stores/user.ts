/**
 * 用户状态：token 与当前用户。
 *
 * token 存 **localStorage**（决定已记入 docs/DECISIONS.md D-021）。
 * 为什么不是 httpOnly Cookie：前后端分离 + 跨域开发时 Cookie 要处理 SameSite/Secure/CORS 携带，
 * 毕设阶段成本高；localStorage 的代价是"XSS 可读"，所以要求**所有动态内容走 Vue 插值**（天然转义），
 * 禁止 `v-html` 渲染用户可控内容。S4 讨论私钥存储时会引用这条结论。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { UserInfo } from '../types/api'

export const TOKEN_KEY = 'study_ocr_token'
export const USER_KEY = 'study_ocr_user'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<UserInfo | null>(readUser())

  const isAuthenticated = computed(() => token.value !== '')
  const role = computed(() => user.value?.role ?? null)

  function setToken(value: string): void {
    token.value = value
    localStorage.setItem(TOKEN_KEY, value)
  }

  function setUser(value: UserInfo | null): void {
    user.value = value
    if (value) {
      localStorage.setItem(USER_KEY, JSON.stringify(value))
    } else {
      localStorage.removeItem(USER_KEY)
    }
  }

  /** 清空会话（401 时由 http 拦截器调用，登出时手动调用） */
  function clear(): void {
    token.value = ''
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  return { token, user, isAuthenticated, role, setToken, setUser, clear }
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