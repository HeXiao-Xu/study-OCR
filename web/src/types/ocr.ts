/** 识别产物契约（S3-4）：与 backend/app/schemas/ocr.py 逐字段对应。
 *
 * 唯一来源是后端模型；本文件由 `tests/test_ocr_schema.py` 自动比对，改名/加字段会先炸测试。
 * JSON Schema 见 `docs/schemas/ocr_result.schema.json`。
 */

export interface OcrLine {
  text: string
  score: number
  /** 四点坐标 [[x,y]×4]，顺时针，校正图坐标系 */
  box: number[][]
  line_no: number
  y_center: number
  x_left: number
  height: number
}

export interface OcrResult {
  lines: OcrLine[]
  image_size: [number, number]
  engine: string
  engine_version: string
  elapsed_ms: number
  det_params: Record<string, unknown>
}

export interface ExtractedField {
  name: string
  /** 归一值（金额两位小数 / 日期 ISO / 纯数字码） */
  value: string
  /** 票面原文形态（如 ￥1,234.56）；推导值为 null */
  raw_value: string | null
  raw_text: string
  source_line_no: number | null
  confidence: number
  rule_id: string
  rule_strength: number
  anchor_score: number | null
  value_score: number | null
  agreement: number
  /** [x1,y1,x2,y2] 外接框，原图坐标系 */
  bbox: [number, number, number, number] | null
  candidates: string[]
}

export interface FieldError {
  field: string
  code: string
  message: string
  severity: 'error' | 'warning'
}

export interface ExtractionResult {
  fields: Record<string, ExtractedField>
  missing_required: string[]
  conflicts: Record<string, unknown>[]
  warnings: string[]
  ticket_type: string
}