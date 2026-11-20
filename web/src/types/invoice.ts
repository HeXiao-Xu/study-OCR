/** 票据相关类型（与后端 `schemas/invoice.py` 对齐，字段名一律 snake_case）。 */

export type InvoiceStatus = 'draft' | 'confirmed' | 'in_report' | 'archived' | 'void'
export type ExtractStatus = 'raw' | 'need_confirm' | 'confirmed'
export type OcrStatus = 'pending' | 'running' | 'succeeded' | 'failed'
export type InvoiceType =
  | 'vat_general'
  | 'vat_digital'
  | 'train_ticket'
  | 'flight_itinerary'
  | 'taxi_ticket'
  | 'unknown'

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

export interface LatestJob {
  job_id: number
  stage: string
  status: OcrStatus
  elapsed_ms: number | null
  retry_count: number
  error: string | null
}

/** GET /invoices/{id}/ocr-status 的阶段元素（S3-1；detect 与 recognize 合并计时，可为 null）。 */
export interface OcrStage {
  stage: string
  status: OcrStatus
  elapsed_ms: number | null
  note?: string | null
  engine?: string | null
  engine_version?: string | null
  retry_count: number
  error: string | null
}

/** 批量上传（S3-8）：POST 的逐项结果与 GET 的汇总。 */
export type BatchItemStatus = 'created' | 'duplicate' | 'rejected'

export interface BatchUploadItem {
  filename: string
  status: BatchItemStatus
  invoice_id: number | null
  job_id: number | null
  error: string | null
}

export interface BatchUploadData {
  batch_id: string
  created: number
  duplicates: number
  rejected: number
  items: BatchUploadItem[]
}

export interface BatchInvoiceItem {
  invoice_id: number
  filename: string
  ocr_status: OcrStatus
  extract_status: string
  progress: number
  error: string | null
}

export interface BatchSummaryData {
  batch_id: string
  total: number
  counts: Record<string, number>
  items: BatchInvoiceItem[]
  created_at: string
}

/** 重复检测（S3-7）：strong 会在 confirm 时 409，weak 只提示。 */
export interface DuplicateMatch {
  invoice_id: number
  kind: 'invoice_no' | 'image' | 'weak'
  invoice_code: string | null
  invoice_no: string | null
  invoice_date: string | null
  total_amount: string | null
  status: string
  created_at: string
}

export interface DuplicateCheckData {
  invoice_id: number
  level: 'strong' | 'weak' | 'none'
  matches: DuplicateMatch[]
}

/** 整单确认 422/42201 的逐字段错误（S3-6）。 */
export interface ConfirmError {
  field_name: string
  error: string
}

/** GET /invoices/{id}/lines 的行框（S3-5）：bbox 是原图坐标，不含文本。 */
export interface OcrLineBox {
  line_no: number
  bbox: number[]
  score: number | null
  field_name: string | null
}

export interface InvoiceLinesData {
  invoice_id: number
  page_no: number
  lines: OcrLineBox[]
}

/** POST /invoices/{id}/reocr 的 202 data（API.md §2）。 */
export interface ReocrData {
  invoice_id: number
  job_id: number
  fields: string[]
}

/** GET /invoices/{id}/ocr-status 的 data（API.md §2）。 */
export interface OcrStatusData {
  invoice_id: number
  ocr_status: OcrStatus
  progress: number
  stages: OcrStage[]
  started_at: string | null
  finished_at: string | null
}

/** GET /invoices/{id} 的 invoice 部分（含 join 出来的文件元数据）。 */
export interface InvoiceOut {
  id: number
  uploader_id: number
  template_id: number | null
  invoice_type: InvoiceType
  status: InvoiceStatus
  version: number
  created_at: string
  updated_at: string

  invoice_code: string | null
  invoice_no: string | null
  invoice_date: string | null
  buyer_name: string | null
  buyer_tax_no: string | null
  seller_name: string | null
  seller_tax_no: string | null
  amount: string | null
  tax_amount: string | null
  total_amount: string | null
  check_code: string | null
  ocr_status: OcrStatus
  extract_status: ExtractStatus
  overall_confidence: string | number | null
  quality_flag: string | null
  duplicate_of: number | null

  file_id: number
  filename: string
  sha256: string
  mime: string
  size: number
  page_count: number
}

export interface InvoiceField {
  id: number
  invoice_id: number
  field_name: string
  field_value: string | null
  normalized_value: string | null
  raw_text: string | null
  confidence: string | number | null
  bbox: number[] | null
  source: string
  source_page_no: number
  source_line_no: number | null
  rule_id: number | null
  need_review: boolean
  conflict: boolean
  conflict_value: string | null
  confirmed: boolean
  updated_by: number | null
  updated_at: string
  /** 服务端派生的三档（S3-2）：auto >=0.90 / review 0.60–0.90 / manual <0.60 */
  review_level?: 'auto' | 'review' | 'manual' | null
}

export interface InvoiceDetail {
  invoice: InvoiceOut
  fields: InvoiceField[]
  latest_job: LatestJob | null
}

export interface InvoiceListData {
  items: InvoiceOut[]
  total: number
  page: number
  page_size: number
}

export interface InvoiceListParams {
  status?: InvoiceStatus
  extract_status?: ExtractStatus
  invoice_type?: InvoiceType
  q?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
  sort_by?: 'created_at' | 'invoice_date' | 'total_amount'
  order?: 'asc' | 'desc'
}

export interface FieldUpdateItem {
  field_name: string
  value: string
  confirm?: boolean
}

export interface InvoiceFieldsUpdateRequest {
  version: number
  fields: FieldUpdateItem[]
  confirm_amount?: boolean
}

export interface InvoiceFieldsUpdateData {
  invoice_id: number
  version: number
  status: InvoiceStatus
  updated: Array<{ field_name: string; before: string | null; after: string }>
  needs_confirm: string[]
  conflicts: Array<{ field_name: string; reason: string; expected?: string | null; actual?: string | null }>
}