/**
 * 统一的 HTTP 封装。**所有** 后端调用都必须经过这里（AGENTS.md §10.7）：
 * 组件里禁止 `import axios` 直接用。
 *
 * 它负责三件事：
 * 1. 注入 token（`Authorization: Bearer <token>`）；
 * 2. 统一处理**错误**：401 清会话并跳登录、403 提示无权限、其余弹后端 message；
 *    业务成功时把整个 `Envelope` 交给调用方（**不吞掉 `trace_id`**——报障时要贴它）；
 * 3. 把 axios 的错误归一成 `ApiError`，调用方只需 `try/catch` 一种类型。
 *
 * 为什么不在拦截器里"解包成 data"：`trace_id` 与 `code` 同样是排障信息，
 * Debug 页要展示它；需要纯数据时用下面 `unwrap()`。
 */
import axios, { AxiosError, type AxiosRequestConfig, type AxiosInstance } from 'axios'
import { ElMessage } from 'element-plus'

import router from '../router'
import { useUserStore } from '../stores/user'
import type { Envelope } from '../types/api'

export class ApiError extends Error {
  constructor(
    public readonly code: number,
    message: string,
    public readonly status?: number,
    public readonly traceId?: string,
    /** 后端在错误响应里附带的结构化数据（如 /health 的 503 降级详情、409 的 current_version）。
     *  丢了它，调用方只能看到一个字符串，没法做"降级展示"这类事情。 */
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

http.interceptors.request.use((config) => {
  const token = useUserStore().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

function redirectToLogin(message?: string): void {
  const store = useUserStore()
  store.clear()
  const current = router.currentRoute.value
  if (current.path !== '/login') {
    void router.replace({ path: '/login', query: { redirect: current.fullPath } })
  }
  ElMessage.error(message || '登录状态已失效，请重新登录')
}

http.interceptors.response.use(
  (response) => {
    // 2xx 但业务码非 0：后端理论上不会这么返回（错误走非 2xx），仍兜住
    const body = response.data as Envelope | undefined
    if (body && typeof body.code === 'number' && body.code !== 0) {
      if (body.code === 40101) {
        redirectToLogin(body.message)
      } else if (body.code === 40301) {
        ElMessage.error('无权访问该资源')
      } else {
        ElMessage.error(body.message || '请求失败')
      }
      return Promise.reject(new ApiError(body.code, body.message, response.status, body.trace_id, body))
    }
    return response
  },
  (error: AxiosError<Envelope>) => {
    const status = error.response?.status
    const body = error.response?.data
    const traceId =
      (body && typeof body === 'object' ? body.trace_id : undefined) ??
      (error.response?.headers?.['x-trace-id'] as string | undefined)

    if (status === 401 || body?.code === 40101) {
      redirectToLogin(body?.message)
      return Promise.reject(new ApiError(40101, '登录状态已失效', status, traceId, body))
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