/** 票据相关类型（与后端 `schemas/invoice.py` 对齐，字段名一律 snake_case）。 */

export interface UploadDedup {
  hit: boolean
  invoice_id: number | null
}

/** POST /invoices/upload 的 202 data（API.md §2）。 */
export interface UploadData {
  invoice_id: number
  file_id: number
  job_id: number
  sha256: string
  status: string
  ocr_status: string
  dedup: UploadDedup
}

export type OcrStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface LatestJob {
  job_id: number
  stage: string
  status: OcrStatus
  elapsed_ms: number | null
  retry_count: number
  error: string | null
}

/** GET /invoices/{id} 里轮询需要的最小字段。 */
export interface InvoiceSummary {
  id: number
  invoice_type: string
  status: string
  ocr_status: OcrStatus
  extract_status: string
  total_amount: string | null
}

export interface InvoiceDetail {
  invoice: InvoiceSummary
  fields: unknown[]
  latest_job: LatestJob | null
}