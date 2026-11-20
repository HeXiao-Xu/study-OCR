/** 文件上传接口（走统一 http；409 的 invoice_id 在 ApiError.payload 里）。 */
import http, { unwrap, type HttpRequestConfig } from './http'
import type { Envelope } from '../types/api'
import type { BatchUploadData, UploadData } from '../types/invoice'

const MAX_UPLOAD_TIMEOUT_MS = 60_000
const MAX_BATCH_TIMEOUT_MS = 120_000

export function uploadInvoice(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadData> {
  const form = new FormData()
  // 只传文件本身；文件名由后端清洗后存展示字段，绝不拿来拼路径
  form.append('file', file)

  const config: HttpRequestConfig = {
    // 上传接口的错误由上传页逐文件展示（409 是绿色提示），不让拦截器再弹全局红条
    silent: true,
    timeout: MAX_UPLOAD_TIMEOUT_MS,
    onUploadProgress: (event) => {
      if (event.total && event.total > 0) {
        onProgress?.(Math.round((event.loaded * 100) / event.total))
      }
    },
  }

  return http
    .post<Envelope<UploadData>>('/invoices/upload', form, config)
    .then((response) => unwrap(response.data))
}

/** 批量上传（S3-8）：一次 multipart 传多个文件；返回逐项结果（去重/被拒按项返回）。 */
export function uploadBatch(
  files: File[],
  onProgress?: (percent: number) => void,
): Promise<BatchUploadData> {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))

  const config: HttpRequestConfig = {
    silent: true,
    timeout: MAX_BATCH_TIMEOUT_MS,
    onUploadProgress: (event) => {
      if (event.total && event.total > 0) {
        onProgress?.(Math.round((event.loaded * 100) / event.total))
      }
    },
  }

  return http
    .post<Envelope<BatchUploadData>>('/invoices/batch', form, config)
    .then((response) => unwrap(response.data))
}
