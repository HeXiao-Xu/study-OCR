/**
 * 统一的 HTTP 封装。**所有** 后端调用都必须经过这里（AGENTS.md §10.7）：
 * 组件里禁止 `import axios` 直接用。
 *
 * 三件事：
 * 1. 注入 access token（`Authorization: Bearer <token>`）；
 * 2. **401 自动刷新一次**：并发的多个 401 复用**同一个** refresh Promise（任务卡的"刷新风暴"坑）；
 *    刷新失败才清会话、跳登录、提示一次；
 * 3. 把 axios 错误归一成 `ApiError`，调用方只处理一种类型；成功时返回整个 `Envelope`
 *    （不吞 `trace_id`，报障要贴）。
 *
 * 不解析 JWT 的 exp 判断过期：完全依赖 401，避免前后端时钟偏差。
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

import { useUserStore } from '../stores/user'
import type { Envelope } from '../types/api'
import type { AuthTokens } from '../types/auth'

export class ApiError extends Error {
  constructor(
    public readonly code: number,
    message: string,
    public readonly status?: number,
    public readonly traceId?: string,
    /** 后端错误响应里的结构化 data（409 的 current_version、503 的降级详情等）。 */
    public readonly payload?: Envelope | null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api/v1'

const http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 15_000,
})

/** 专用于 refresh：**不挂业务拦截器**，避免 401 → refresh → 401 的递归。 */
const refreshClient: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 15_000,
})

interface RetryableConfig extends AxiosRequestConfig {
  _retried?: boolean
}

/** 由 main.ts 注册：刷新失败时如何回登录页（避免 http ↔ router 的循环 import）。 */
type UnauthorizedHandler = (message?: string) => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

let lastAuthMessageAt = 0

function handleUnauthorized(message?: string): void {
  const store = useUserStore()
  store.clear()
  if (unauthorizedHandler) {
    unauthorizedHandler(message)
  } else if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
  const now = Date.now()
  if (now - lastAuthMessageAt > 1500) {
    lastAuthMessageAt = now
    ElMessage.error(message || '登录状态已失效，请重新登录')
  }
}

// --------------------------------------------------------------------------
// refresh 单飞：并发 401 只发一次 /auth/refresh（后端是轮换式的，重复用旧 token 会 401）
// --------------------------------------------------------------------------
let refreshPromise: Promise<string> | null = null
let refreshCount = 0

/** DEV 冒烟用：累计的 refresh 请求次数（验证并发去重）。 */
export function getRefreshCount(): number {
  return refreshCount
}

async function performRefresh(): Promise<string> {
  const store = useUserStore()
  if (!store.refreshToken) {
    throw new Error('no refresh token')
  }
  refreshCount += 1
  const response = await refreshClient.post<Envelope<AuthTokens>>('/auth/refresh', {
    refresh_token: store.refreshToken,
  })
  const body = response.data
  if (body.code !== 0 || !body.data) {
    throw new ApiError(body.code, body.message, response.status, body.trace_id, body)
  }
  store.setAccessToken(body.data.access_token)
  store.setRefreshToken(body.data.refresh_token)
  return body.data.access_token
}

function refreshAccessToken(): Promise<string> {
  if (!refreshPromise) {
    const promise = performRefresh()
    refreshPromise = promise
    // 结束后复位，下一次过期还能再刷新；catch 防止未处理的 rejection
    void promise
      .finally(() => {
        if (refreshPromise === promise) refreshPromise = null
      })
      .catch(() => undefined)
  }
  return refreshPromise
}

// --------------------------------------------------------------------------
// 拦截器
// --------------------------------------------------------------------------
http.interceptors.request.use((config) => {
  const token = useUserStore().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => {
    const body = response.data as Envelope | undefined
    if (body && typeof body.code === 'number' && body.code !== 0) {
      if (body.code === 40101) {
        handleUnauthorized(body.message)
      } else if (body.code === 40301) {
        ElMessage.error('无权访问该资源')
      } else {
        ElMessage.error(body.message || '请求失败')
      }
      return Promise.reject(new ApiError(body.code, body.message, response.status, body.trace_id, body))
    }
    return response
  },
  async (error: AxiosError<Envelope>) => {
    const status = error.response?.status
    const body = error.response?.data
    const config = error.config as RetryableConfig | undefined
    const traceId =
      (body && typeof body === 'object' ? body.trace_id : undefined) ??
      (error.response?.headers?.['x-trace-id'] as string | undefined)

    const isAuthEndpoint = Boolean(
      config?.url && (config.url.includes('/auth/login') || config.url.includes('/auth/refresh')),
    )
    const unauthorized = status === 401 || body?.code === 40101

    if (unauthorized && config && !config._retried && !isAuthEndpoint) {
      config._retried = true
      try {
        const token = await refreshAccessToken()
        const headers = (config.headers ?? {}) as Record<string, string>
        headers.Authorization = `Bearer ${token}`
        config.headers = headers
        return await http.request(config)
      } catch {
        handleUnauthorized(body?.message)
        return Promise.reject(
          new ApiError(40101, '登录状态已失效', status, traceId, body ?? null),
        )
      }
    }

    if (unauthorized) {
      handleUnauthorized(body?.message)
      return Promise.reject(new ApiError(40101, '登录状态已失效', status, traceId, body ?? null))
    }
    if (status === 403) {
      ElMessage.error('无权访问该资源')
      return Promise.reject(new ApiError(40301, '无权访问该资源', status, traceId, body))
    }

    const message =
      (body && typeof body === 'object' && body.message) ||
      (status ? `请求失败（HTTP ${status}）` : '网络异常：后端服务未启动或不可达')
    ElMessage.error(message)
    return Promise.reject(new ApiError(body?.code ?? status ?? -1, message, status, traceId, body))
  },
)

// --------------------------------------------------------------------------
// 便捷方法
// --------------------------------------------------------------------------
export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<Envelope<T>> {
  const response = await http.get<Envelope<T>>(url, config)
  return response.data
}

export async function post<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<Envelope<T>> {
  const response = await http.post<Envelope<T>>(url, data, config)
  return response.data
}

export async function patch<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<Envelope<T>> {
  const response = await http.patch<Envelope<T>>(url, data, config)
  return response.data
}

export async function del<T>(url: string, config?: AxiosRequestConfig): Promise<Envelope<T>> {
  const response = await http.delete<Envelope<T>>(url, config)
  return response.data
}

/** 只要数据、不关心 code/trace_id 的场景用这个（仍然已做过错误处理） */
export function unwrap<T>(envelope: Envelope<T>): T {
  if (envelope.data === null) {
    throw new ApiError(envelope.code, envelope.message, undefined, envelope.trace_id)
  }
  return envelope.data
}

export default http