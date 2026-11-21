/** 财务端类型（S5-3，按 docs/API.md §5 的字段手写；不返回任何密文/对象名）。 */

export type VerifyStatus = 'unverified' | 'ok' | 'failed'

export interface FinancePendingItem {
  report_id: number
  report_no: string
  applicant_id: number
  applicant_name: string
  /** 两位小数字符串（后端 Decimal → string） */
  total_amount: string
  item_count: number
  submitted_at: string
  verify_status: VerifyStatus
}

export interface FinancePendingPage {
  items: FinancePendingItem[]
  total: number
  page: number
  page_size: number
}

export interface FinancePendingParams {
  verify_status?: VerifyStatus
  submitted_from?: string
  submitted_to?: string
  q?: string
  sort_by?: 'submitted_at' | 'total_amount'
  order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

/** 信封元数据（`EnvelopeOut`）：只有算法/IV/MAC/摘要与派生字段，**没有**密文与会话密钥。 */
export interface FinanceEnvelopeOut {
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

export interface FinanceSignatureOut {
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

export interface FinanceReportItemOut {
  id: number
  invoice_id: number
  amount: string
  remark: string | null
}

export interface FinanceReportOut {
  id: number
  report_no: string
  applicant_id: number
  department: string | null
  title: string
  reason: string | null
  status: string
  total_amount: string
  item_count: number
  digest_algo: string | null
  digest_hex: string | null
  submitted_at: string | null
  created_at: string
  updated_at: string
  items: FinanceReportItemOut[]
}

export interface FinanceAttachment {
  file_id: number
  invoice_id: number
  filename: string
  mime: string
  size: number
  sha256: string
  page_count: number
  envelope: FinanceEnvelopeOut | null
}

export interface FinanceDetail {
  report: FinanceReportOut
  applicant_name: string
  signature: FinanceSignatureOut | null
  envelope: FinanceEnvelopeOut | null
  verify_status: VerifyStatus
  attachments: FinanceAttachment[]
}