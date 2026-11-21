/** 证书与审计（S4-6 / S4-7）。 */
import { get, post, unwrap } from './http'
import type {
  AuditItem,
  AuditPage,
  AuditVerifyResult,
  CertItem,
  CertPage,
} from '../types/admin'

function toQuery(params: Record<string, unknown> | undefined): Record<string, string | number | boolean> {
  const query: Record<string, string | number | boolean> = {}
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') query[key] = value as string | number | boolean
  }
  return query
}

export async function listCertificates(params: {
  owner_type?: 'user' | 'server'
  revoked?: boolean
  page?: number
  page_size?: number
} = {}): Promise<CertPage> {
  const envelope = await get<CertPage>('/certs', {
    params: toQuery(params as Record<string, unknown>),
    silent: true,
  })
  return unwrap(envelope)
}

export async function revokeCertificate(certId: number, reason: string): Promise<CertItem> {
  const envelope = await post<CertItem>(`/certs/${certId}/revoke`, { reason }, { silent: true })
  return unwrap(envelope)
}

export async function listAuditLogs(params: {
  action?: string
  target_type?: string
  target_id?: number
  trace_id?: string
  from?: string
  to?: string
  page?: number
  page_size?: number
} = {}): Promise<AuditPage> {
  const envelope = await get<AuditPage>('/admin/audit', {
    params: toQuery(params as Record<string, unknown>),
    silent: true,
  })
  return unwrap(envelope)
}

export async function verifyAuditChain(): Promise<AuditVerifyResult> {
  const envelope = await get<AuditVerifyResult>('/admin/audit/verify-chain', { silent: true })
  return unwrap(envelope)
}

export type { AuditItem }