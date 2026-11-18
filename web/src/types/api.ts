/**
 * 后端统一响应壳（API.md §0）。
 * 后端**永远**返回这个结构，包括错误：`{code, message, data, trace_id}`。
 */
export interface Envelope<T = unknown> {
  code: number
  message: string
  data: T | null
  trace_id: string
}

export interface HealthCheckItem {
  ok: boolean
  detail: string
}

export interface HealthData {
  status: 'ok' | 'degraded'
  version: string
  ts: string
  checks: Record<string, HealthCheckItem>
}

export interface UserInfo {
  id: number
  username: string
  real_name: string
  dept: string | null
  role: 'submitter' | 'reviewer' | 'admin'
  status: 'active' | 'disabled' | 'locked'
  created_at: string
}