/** 票种模板与规则类型（与 `schemas/template.py` 对齐）。 */

export interface TemplateOut {
  id: number
  type_code: string
  type_name: string
  match_keywords: string[] | null
  version: number
  active: boolean
  created_at: string
  updated_at: string
}

export interface TemplateDetail extends TemplateOut {
  field_specs_json: Record<string, unknown>
}

export interface TemplateRule {
  id: number
  field_name: string
  pattern: string | null
  validator: string | null
  error_msg: string | null
  priority: number
  active: boolean
}

export interface TemplateDetailData {
  template: TemplateDetail
  rules: TemplateRule[]
}

export interface TemplateRuleInput {
  field_name: string
  pattern?: string | null
  validator?: string | null
  error_msg?: string | null
  priority?: number
  active?: boolean
}

export interface TemplatePayload {
  type_code: string
  type_name: string
  match_keywords: string[]
  field_specs_json: Record<string, unknown>
  active: boolean
  rules: TemplateRuleInput[]
}

export interface TemplateUpdatePayload extends TemplatePayload {
  version: number
}

export interface TemplateListData {
  items: TemplateOut[]
  total: number
  page: number
  page_size: number
}

export interface TemplateTestField {
  field_name: string
  value: string
  confidence: number
  bbox: number[] | null
  source: string
}

export interface TemplateTestData {
  template_id: number
  type_code: string
  matched_keywords: string[]
  overall_confidence: number
  elapsed_ms: number
  fields: TemplateTestField[]
}

export const TICKET_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'vat_general', label: '增值税电子普通发票' },
  { value: 'vat_digital', label: '数电票' },
  { value: 'train_ticket', label: '铁路电子客票' },
  { value: 'flight_itinerary', label: '航空行程单' },
  { value: 'taxi_ticket', label: '出租车发票' },
  { value: 'unknown', label: '未识别票种' },
]

export const VALIDATOR_OPTIONS = [
  { value: 'amount', label: '金额' },
  { value: 'date', label: '日期' },
  { value: 'tax_no', label: '税号' },
  { value: 'code_no', label: '代码/号码' },
  { value: 'uppercase_amount', label: '大写金额' },
]