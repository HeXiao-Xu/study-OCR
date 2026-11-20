/** 票据读/写接口（S2-11；轮询在 S2-10 已加，S3-1 换用 ocr-status）。 */
import { get, getBlob, patch, post, unwrap } from './http'
import type {
  InvoiceDetail,
  InvoiceFieldsUpdateData,
  InvoiceFieldsUpdateRequest,
  InvoiceLinesData,
  InvoiceListData,
  InvoiceListParams,
  InvoiceOut,
  OcrStatus,
  OcrStatusData,
  ReocrData,
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

/** 整单确认（S3-6）：服务端重跑校验；失败 422/42201 时 payload.data.fields 给逐字段原因。 */
export async function confirmInvoice(invoiceId: number, version?: number): Promise<InvoiceOut> {
  const envelope = await post<InvoiceOut>(`/invoices/${invoiceId}/confirm`, { version }, {
    silent: true,
  })
  return unwrap(envelope)
}

/** 票据影像（S3-5）：带 Bearer 下载 blob，再转 objectURL 给 canvas，避免 <img src> 暴露直链。 */
export async function fetchInvoiceImage(
  invoiceId: number,
  page = 1,
  variant: 'original' | 'preprocessed' = 'original',
): Promise<Blob> {
  return getBlob(`/invoices/${invoiceId}/image`, {
    params: { page, variant },
    silent: true,
    timeout: 60_000,
  })
}

/** 行框（S3-5）：原图坐标、不含文本；失败时调用方降级为只画字段框。 */
export async function fetchInvoiceLines(
  invoiceId: number,
  page = 1,
): Promise<InvoiceLinesData> {
  const envelope = await get<InvoiceLinesData>(`/invoices/${invoiceId}/lines`, {
    params: { page },
    silent: true,
  })
  return unwrap(envelope)
}

/** 局部二次识别（S3-3，异步 202；结果由详情接口轮询可见）。 */
export async function reocrFields(
  invoiceId: number,
  fields: string[],
  version?: number,
): Promise<ReocrData> {
  const envelope = await post<ReocrData>(`/invoices/${invoiceId}/reocr`, { fields, version }, {
    silent: true,
  })
  return unwrap(envelope)
}

export type PollOutcome = 'done' | 'timeout' | 'aborted'

/**
 * 轮询识别状态：3s 起、指数退避（上限 15s）、总时长 120s。
 * S3-1 起走专属接口 `GET /invoices/{id}/ocr-status`：能拿到阶段明细与 progress，
 * 终态只看 `ocr_status`（不再同时猜 `latest_job.status`）。
 */
export async function waitForOcr(
  invoiceId: number,
  onUpdate?: (status: OcrStatusData) => void,
  signal?: AbortSignal,
  totalTimeoutMs: number = TOTAL_TIMEOUT_MS,
): Promise<PollOutcome> {
  const started = Date.now()
  let delay = START_DELAY_MS

  while (Date.now() - started < totalTimeoutMs) {
    await sleep(delay)
    if (signal?.aborted) return 'aborted'
    try {
      const envelope = await get<OcrStatusData>(`/invoices/${invoiceId}/ocr-status`, {
        silent: true,
      })
      const status = unwrap(envelope)
      if (signal?.aborted) return 'aborted'
      onUpdate?.(status)
      if (TERMINAL_STATUSES.has(status.ocr_status)) {
        return 'done'
      }
    } catch {
      // 网络抖动/瞬时 5xx：继续重试，直到总超时；不在这里弹错误
    }
    delay = Math.min(Math.round(delay * 1.5), MAX_DELAY_MS)
  }
  return 'timeout'
}