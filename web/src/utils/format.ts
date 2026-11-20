/** 票据展示层格式化：金额、时间、票种/状态标签。组件里不各写一套。 */
import type { ExtractStatus, InvoiceStatus, InvoiceType, OcrStatus } from '../types/invoice'

export const INVOICE_TYPE_LABEL: Record<InvoiceType, string> = {
  vat_general: '增值税电子普通发票',
  vat_digital: '数电票（电子发票）',
  train_ticket: '铁路电子客票',
  flight_itinerary: '航空运输电子客票行程单',
  taxi_ticket: '出租车发票',
  unknown: '未识别票种',
}

export const INVOICE_STATUS_LABEL: Record<InvoiceStatus, string> = {
  draft: '草稿',
  confirmed: '已确认',
  in_report: '已入报销单',
  archived: '已归档',
  void: '已作废',
}

export const INVOICE_STATUS_TAG: Record<
  InvoiceStatus,
  'info' | 'success' | 'primary' | 'warning' | 'danger'
> = {
  draft: 'info',
  confirmed: 'success',
  in_report: 'primary',
  archived: 'warning',
  void: 'danger',
}

export const EXTRACT_STATUS_LABEL: Record<ExtractStatus, string> = {
  raw: '未抽取',
  need_confirm: '待确认',
  confirmed: '已确认',
}

export const OCR_STATUS_LABEL: Record<OcrStatus, string> = {
  pending: '排队中',
  running: '识别中',
  succeeded: '识别完成',
  failed: '识别失败',
}

export const OCR_STATUS_TAG: Record<OcrStatus, 'info' | 'primary' | 'success' | 'danger'> = {
  pending: 'info',
  running: 'primary',
  succeeded: 'success',
  failed: 'danger',
}

export const FIELD_LABELS: Record<string, string> = {
  invoice_code: '发票代码',
  invoice_no: '发票号码',
  invoice_date: '开票日期',
  buyer_name: '购买方名称',
  buyer_tax_no: '购买方税号',
  seller_name: '销售方名称',
  seller_tax_no: '销售方税号',
  amount: '金额',
  tax_amount: '税额',
  total_amount: '价税合计',
  total_amount_upper: '大写金额',
  check_code: '校验码',
  passenger_name: '乘车人',
}

export const AMOUNT_FIELDS = new Set(['amount', 'tax_amount', 'total_amount'])

export function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? name
}

/** 后端金额是两位小数字符串；null 显示破折号。 */
export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : String(value)
}

/** 置信度可能是 "0.9800" 或 0.98；统一显示百分比。 */
export function formatPercent(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return `${(number * 100).toFixed(0)}%`
}

export function confidenceNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** 后端时间是 ISO-8601（UTC），展示统一按 Asia/Shanghai。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : DATE_TIME_FORMATTER.format(date)
}

export function formatSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}