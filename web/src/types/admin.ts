/** 证书与审计（S4-6 / S4-7）前端类型。 */

export interface CertItem {
  id: number
  owner_type: string
  owner_id: number | null
  subject_dn: string
  serial_no: string
  issuer_dn: string
  key_algo: string
  not_before: string
  not_after: string
  fingerprint_sha256: string
  revoked: boolean
  revoked_at: string | null
  created_at: string
}

export interface CertPage {
  items: CertItem[]
  total: number
  page: number
  page_size: number
}

export interface AuditItem {
  id: number
  trace_id: string
  actor_id: number | null
  actor_role: string | null
  action: string
  target_type: string | null
  target_id: number | null
  detail_json: Record<string, unknown> | null
  prev_hash: string
  entry_hash: string
  created_at: string
}

export interface AuditPage {
  items: AuditItem[]
  total: number
  page: number
  page_size: number
}

export interface AuditVerifyResult {
  ok: boolean
  checked_count: number
  broken_at: number | null
  broken_id: number | null
  reason: string | null
}