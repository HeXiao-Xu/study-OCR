/** 报销单 / 归档 / 管理端归档工作台（S5-1 ~ S5-6）。 */
import { get, post, postBlob, unwrap } from './http'
import type {
  AdminReportPage,
  ArchiveSearchParams,
  ReportDetail,
  ReportListData,
  ReportListParams,
  ReportOut,
  SignData,
  SubmitData,
} from '../types/report'

function toQuery(params: Record<string, unknown> | undefined): Record<string, string | number> {
  const query: Record<string, string | number> = {}
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') {
      query[key] = value as string | number
    }
  }
  return query
}

export async function listMyReports(params: ReportListParams = {}): Promise<ReportListData> {
  const envelope = await get<ReportListData>('/reports/mine', {
    params: toQuery(params as Record<string, unknown>),
    silent: true,
  })
  return unwrap(envelope)
}

export async function createReport(payload: {
  title: string
  reason: string
}): Promise<ReportOut> {
  const envelope = await post<ReportOut>('/reports', payload, { silent: true })
  return unwrap(envelope)
}

export async function getReport(reportId: number): Promise<ReportDetail> {
  const envelope = await get<ReportDetail>(`/reports/${reportId}`, { silent: true })
  return unwrap(envelope)
}

export async function addReportItem(
  reportId: number,
  payload: { invoice_id: number; amount: string; remark?: string },
): Promise<ReportOut> {
  const envelope = await post<ReportOut>(`/reports/${reportId}/items`, payload, { silent: true })
  return unwrap(envelope)
}

export async function signReport(reportId: number, passphrase: string): Promise<SignData> {
  const envelope = await post<SignData>(
    `/reports/${reportId}/sign`,
    { private_key_passphrase: passphrase },
    { silent: true },
  )
  return unwrap(envelope)
}

export async function submitReport(
  reportId: number,
  idempotencyKey: string,
): Promise<SubmitData> {
  const envelope = await post<SubmitData>(
    `/reports/${reportId}/submit`,
    { idempotency_key: idempotencyKey },
    { silent: true },
  )
  return unwrap(envelope)
}

/** 归档检索（submitter 本人 / reviewer 全部）。 */
export async function listArchivedReports(
  params: ArchiveSearchParams = {},
): Promise<ReportListData> {
  const envelope = await get<ReportListData>('/reports', {
    params: { status: 'archived', ...toQuery(params as Record<string, unknown>) },
    silent: true,
  })
  return unwrap(envelope)
}

/** 导出归档包：POST 拿 blob（口令在 body，不放 URL）。 */
export async function exportArchiveBlob(reportId: number, passphrase: string): Promise<Blob> {
  return postBlob(`/reports/${reportId}/export`, { passphrase }, { silent: true, timeout: 60_000 })
}

/** 管理端归档工作台（approved / archived）。 */
export async function listAdminReports(params: {
  status?: 'approved' | 'archived'
  q?: string
  page?: number
  page_size?: number
} = {}): Promise<AdminReportPage> {
  const envelope = await get<AdminReportPage>('/admin/reports', {
    params: toQuery(params as Record<string, unknown>),
    silent: true,
  })
  return unwrap(envelope)
}

export async function archiveReport(reportId: number): Promise<void> {
  await post(`/admin/reports/${reportId}/archive`, { confirm: true }, { silent: true })
}