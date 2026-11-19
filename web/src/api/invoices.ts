/** 票据读/写接口（S2-11；轮询在 S2-10 已加）。 */
import { get, patch, unwrap } from './http'
import type {
  InvoiceDetail,
  InvoiceFieldsUpdateData,
  InvoiceFieldsUpdateRequest,
  InvoiceListData,
  InvoiceListParams,
  OcrStatus,
} from '../types/invoice'

const TERMINAL_STATUSES = new Set<OcrStatus>(['succeeded', 'failed'])
const START_DELAY_MS = 3000
const MAX_DELAY_MS = 15_000
const TOTAL_TIMEOUT_MS = 120_000

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export async function listInvoices(params: InvoiceListParams = {}): Promise<InvoiceListData> {
  const query: Record<string, string | number> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query[key] = value as string | number
    }
  }
  const envelope = await get<InvoiceListData>('/invoices', { params: query, silent: true })
  return unwrap(envelope)
}

export async function getInvoiceDetail(invoiceId: number): Promise<InvoiceDetail> {
  const envelope = await get<InvoiceDetail>(`/invoices/${invoiceId}`, { silent: true })
  return unwrap(envelope)
}

export async function updateInvoiceFields(
  invoiceId: number,
  payload: InvoiceFieldsUpdateRequest,
): Promise<InvoiceFieldsUpdateData> {
  const envelope = await patch<InvoiceFieldsUpdateData>(`/invoices/${invoiceId}/fields`, payload, {
    silent: true,
  })
  return unwrap(envelope)
}

export type PollOutcome = 'done' | 'timeout' | 'aborted'

/**
 * 轮询识别状态：3s 起、指数退避（上限 15s）、总时长 120s。
 * 终态判定看 `invoice.ocr_status` 或 `latest_job.status`——S3-1 的 ocr-status 接口上线后
 * 只需要换掉这个函数，页面不用动。
 */
export async function waitForOcr(
  invoiceId: number,
  onUpdate?: (detail: InvoiceDetail) => void,
  signal?: AbortSignal,
  totalTimeoutMs: number = TOTAL_TIMEOUT_MS,
): Promise<PollOutcome> {
  const started = Date.now()
  let delay = START_DELAY_MS

  while (Date.now() - started < totalTimeoutMs) {
    await sleep(delay)
    if (signal?.aborted) return 'aborted'
    try {
      const detail = await getInvoiceDetail(invoiceId)
      if (signal?.aborted) return 'aborted'
      onUpdate?.(detail)
      const jobStatus = detail.latest_job?.status
      if (
        TERMINAL_STATUSES.has(detail.invoice.ocr_status) ||
        (jobStatus !== undefined && TERMINAL_STATUSES.has(jobStatus))
      ) {
        return 'done'
      }
    } catch {
      // 网络抖动/瞬时 5xx：继续重试，直到总超时；不在这里弹错误
    }
    delay = Math.min(Math.round(delay * 1.5), MAX_DELAY_MS)
  }
  return 'timeout'
}