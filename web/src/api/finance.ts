/** 财务端接口（S5-3）：只读列表 / 详情元数据 / CSV 导出。组件不直接 import axios。 */
import { get, getBlob, unwrap } from './http'
import type {
  FinanceDetail,
  FinancePendingPage,
  FinancePendingParams,
} from '../types/finance'

function toQuery(params: FinancePendingParams): Record<string, string | number> {
  const query: Record<string, string | number> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query[key] = value as string | number
    }
  }
  return query
}

export async function listPending(
  params: FinancePendingParams = {},
): Promise<FinancePendingPage> {
  const envelope = await get<FinancePendingPage>('/finance/pending', {
    params: toQuery(params),
    silent: true,
  })
  return unwrap(envelope)
}

export async function getFinanceDetail(reportId: number): Promise<FinanceDetail> {
  const envelope = await get<FinanceDetail>(`/finance/${reportId}`, { silent: true })
  return unwrap(envelope)
}

/** CSV 导出：走鉴权后拿 Blob，由调用方触发本地下载（不解析 JSON 壳）。 */
export async function downloadPendingCsv(params: FinancePendingParams = {}): Promise<Blob> {
  return getBlob('/finance/pending/export', { params: toQuery(params), silent: true })
}