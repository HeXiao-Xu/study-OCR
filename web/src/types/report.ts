/** 报销单与归档（S5-1 ~ S5-6）前端类型；字段名与 docs/API.md 对齐。 */

export type ReportStatus =
  | 'draft'
  | 'pending_sign'
  | 'signed'
  | 'encrypted'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'archived'

export interface ReportItem {
  id: number
  invoice_id: number
  amount: string
  remark: string | null
}

export interface ReportOut {
  id: number
  report_no: string
  applicant_id: number
  department: string | null
  title: string
  reason: string | null
  status: ReportStatus | string
  total_amount: string
  item_count: number
  digest_algo: string | null
  digest_hex: string | null
  submitted_at: string | null
  archive_no: string | null
  archive_at: string | null
  created_at: string
  updated_at: string
  items: ReportItem[]
}

export interface ReportSignature {
  id: number
  form_id: number
  signer_id: number
  cert_id: number
  verifier_id: number | null
  algo: string
  digest_algo: string
  digest_hex: string
  signature_b64: string
  verify_result: string | null
  verified: boolean
  verified_at: string | null
  version: number
  status: string
  created_at: string
}

export interface ReportEnvelope {
  id: number
  form_id: number | null
  file_id: number | null
  recipient_cert_id: number
  key_algo: string
  cipher_algo: string
  iv_b64: string
  mac_b64: string
  payload_sha256: string
  recipient_cert_fp: string | null
  wrapped_key_len: number | null
  created_at: string
}

export interface ReportDetail {
  report: ReportOut
  signature: ReportSignature | null
  envelope: ReportEnvelope | null
  verify_status: 'unverified' | 'ok' | 'failed'
}

export interface ReportListParams {
  status?: ReportStatus | ''
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
  sort_by?: 'created_at' | 'submitted_at'
  order?: 'asc' | 'desc'
}

export interface ReportListData {
  items: ReportOut[]
  total: number
  page: number
  page_size: number
}

export interface SignData {
  signature_id: number
  digest_algo: string
  digest_hex: string
  algo: string
  signature_b64: string
  cert_id: number
  canonical_sha256: string
  status: string
}

export interface SubmitData {
  report_id: number
  report_no: string
  status: string
  signature_id: number
  envelope_id: number
  submitted_at: string
  duplicated: boolean
}

/** 归档检索（S5-6）：只查 archived。 */
export interface ArchiveSearchParams {
  q?: string
  date_from?: string
  date_to?: string
  amount_min?: string
  amount_max?: string
  page?: number
  page_size?: number
  sort_by?: 'archive_at' | 'total_amount' | 'submitted_at'
  order?: 'asc' | 'desc'
}

/** 管理端归档工作台行（S5-6）。 */
export interface AdminReportItem {
  report_id: number
  report_no: string
  applicant_name: string
  status: string
  total_amount: string
  item_count: number
  submitted_at: string | null
  archive_no: string | null
  archive_at: string | null
}

export interface AdminReportPage {
  items: AdminReportItem[]
  total: number
  page: number
  page_size: number
}