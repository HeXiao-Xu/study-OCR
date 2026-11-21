/** 财务端接口（S5-3）：只读列表 / 详情元数据 / CSV 导出。组件不直接 import axios。 */
import { get, getBlob, post, unwrap } from './http'
import type {
  DecryptPreview,
  FinanceDetail,
  FinancePendingPage,
  FinancePendingParams,
  ReviewPayload,
  ReviewResult,
  VerifyResult,
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

/** 验签（S5-4）：七步校验；财务私钥口令在服务端环境变量里，前端不接触。 */
export async function verifyReport(reportId: number): Promise<VerifyResult> {
  const envelope = await post<VerifyResult>(
    `/finance/reports/${reportId}/verify`,
    {},
    { silent: true },
  )
  return unwrap(envelope)
}

/** 解密到临时预览（30min）：verify_token 换 preview_token。 */
export async function decryptReport(
  reportId: number,
  verifyToken: string,
): Promise<DecryptPreview> {
  const envelope = await post<DecryptPreview>(
    `/finance/reports/${reportId}/decrypt`,
    { verify_token: verifyToken },
    { silent: true },
  )
  return unwrap(envelope)
}

/** 预览影像（明文，30min 内可重复读）。 */
export async function getPreviewFileBlob(
  previewToken: string,
  fileId: number,
): Promise<Blob> {
  return getBlob(`/finance/preview/${previewToken}/files/${fileId}`, { silent: true })
}

/** 审核决策（S5-5）：approve 必须带未消费的 verify_token；reject 必须带意见。 */
export async function reviewReport(
  reportId: number,
  payload: ReviewPayload,
): Promise<ReviewResult> {
  const envelope = await post<ReviewResult>(
    `/finance/reports/${reportId}/review`,
    payload,
    { silent: true },
  )
  return unwrap(envelope)
}