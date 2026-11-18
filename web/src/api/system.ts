/** 系统类接口（与 API.md §9 对应）。 */
import { get } from './http'
import type { Envelope, HealthData } from '../types/api'

/** GET /health —— 返回 DB / Redis / MinIO 三项状态 */
export function fetchHealth(): Promise<Envelope<HealthData>> {
  return get<HealthData>('/health')
}
