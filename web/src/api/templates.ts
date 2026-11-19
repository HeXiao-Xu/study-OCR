/** 模板管理接口（S2-8 后端；S2-12 后补管理端页面）。 */
import { get, post, put, unwrap } from './http'
import type {
  TemplateDetailData,
  TemplateListData,
  TemplateOut,
  TemplatePayload,
  TemplateTestData,
  TemplateUpdatePayload,
} from '../types/template'

export interface TemplateListParams {
  active?: boolean
  type_code?: string
  page?: number
  page_size?: number
  sort_by?: 'updated_at' | 'version' | 'type_code'
  order?: 'asc' | 'desc'
}

export async function listTemplates(params: TemplateListParams = {}): Promise<TemplateListData> {
  const query: Record<string, string | number | boolean> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query[key] = value
  }
  const envelope = await get<TemplateListData>('/templates', { params: query, silent: true })
  return unwrap(envelope)
}

export async function getTemplate(templateId: number): Promise<TemplateDetailData> {
  const envelope = await get<TemplateDetailData>(`/templates/${templateId}`, { silent: true })
  return unwrap(envelope)
}

export async function createTemplate(payload: TemplatePayload): Promise<TemplateOut> {
  const envelope = await post<TemplateOut>('/templates', payload, { silent: true })
  return unwrap(envelope)
}

export async function updateTemplate(
  templateId: number,
  payload: TemplateUpdatePayload,
): Promise<TemplateOut> {
  const envelope = await put<TemplateOut>(`/templates/${templateId}`, payload, { silent: true })
  return unwrap(envelope)
}

export async function testTemplate(
  templateId: number,
  file: File,
): Promise<TemplateTestData> {
  const form = new FormData()
  form.append('file', file)
  const envelope = await post<TemplateTestData>(
    `/templates/${templateId}/test`,
    form,
    { silent: true, timeout: 60_000 },
  )
  return unwrap(envelope)
}