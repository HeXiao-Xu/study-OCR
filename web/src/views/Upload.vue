<script setup lang="ts">
/**
 * 上传页（S2-10 → S3-8）：拖拽/多选 → 本地校验 → 单文件串行 / 多文件批量 → 逐项进度与重试。
 *
 * 三个刻意的取舍：
 * - **单文件仍串行**：一个文件没必要走 batch，失败原因也更直接；
 * - **多文件走 POST /invoices/batch**：服务端限单用户并发 ≤3、其余排队，前端只轮询汇总；
 * - **409 是绿色提示**：重复影像是正常业务（同一张票传了两次），给"查看已上传票据"链接。
 */
import { ElMessage, type UploadFile, type UploadInstance, type UploadRawFile } from 'element-plus'
import { computed, onBeforeUnmount, ref } from 'vue'

import { uploadBatch, uploadInvoice } from '../api/files'
import { ApiError } from '../api/http'
import { fetchBatch, retryBatchItem, waitForOcr } from '../api/invoices'
import type { BatchSummaryData, OcrStatus } from '../types/invoice'

type TaskStatus =
  | 'pending'
  | 'uploading'
  | 'recognizing'
  | 'done'
  | 'failed'
  | 'duplicate'
  | 'timeout'
  | 'local_error'

interface UploadTask {
  id: number
  file: File
  name: string
  size: number
  progress: number
  status: TaskStatus
  message: string
  invoiceId: number | null
  thumb: string | null
  ocrStatus: OcrStatus | null
  jobError: string | null
  controller: AbortController | null
}

const MAX_FILES = 30
const MAX_UPLOAD_MB = Number(import.meta.env.VITE_MAX_UPLOAD_MB ?? 20)
const ALLOWED_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'pdf'])
const ALLOWED_MIMES = new Set(['image/jpeg', 'image/png', 'application/pdf'])
const BATCH_POLL_MS = 3000
const BATCH_POLL_MAX_MS = 10 * 60 * 1000

const uploadRef = ref<UploadInstance>()
const tasks = ref<UploadTask[]>([])
const running = ref(false)
const batchId = ref<string | null>(null)
let sequence = 0
let startTimer: ReturnType<typeof setTimeout> | null = null
let batchTimer: ReturnType<typeof setInterval> | null = null
let batchStartedAt = 0

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待上传',
  uploading: '上传中',
  recognizing: '识别中',
  done: '识别完成',
  failed: '失败',
  duplicate: '已上传',
  timeout: '识别较久',
  local_error: '文件不合规',
}

function statusTagType(status: TaskStatus): 'primary' | 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'done') return 'success'
  if (status === 'uploading' || status === 'recognizing') return 'primary'
  if (status === 'duplicate' || status === 'timeout') return 'warning'
  if (status === 'failed' || status === 'local_error') return 'danger'
  return 'info'
}

const stats = computed(() => {
  const list = tasks.value
  return {
    total: list.length,
    done: list.filter((task) => task.status === 'done').length,
    failed: list.filter((task) => task.status === 'failed' || task.status === 'local_error').length,
    duplicate: list.filter((task) => task.status === 'duplicate').length,
    active: list.filter((task) => task.status === 'recognizing' || task.status === 'uploading').length,
  }
})

const overallProgress = computed(() => {
  if (!tasks.value.length) return 0
  const sum = tasks.value.reduce((total, task) => total + Math.min(100, task.progress), 0)
  return Math.round(sum / tasks.value.length)
})

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function localError(file: File): string | null {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!ALLOWED_EXTENSIONS.has(extension)) return '只支持 JPG / PNG / PDF'
  if (file.type && !ALLOWED_MIMES.has(file.type)) return '文件类型不支持（按内容再次校验）'
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `超过 ${MAX_UPLOAD_MB}MB 上限`
  if (file.size === 0) return '空文件'
  return null
}

function handleChange(uploadFile: UploadFile): void {
  const raw = uploadFile.raw as UploadRawFile | undefined
  uploadRef.value?.clearFiles()
  if (!raw) return

  if (tasks.value.length >= MAX_FILES) {
    ElMessage.warning(`一次最多 ${MAX_FILES} 个文件`)
    return
  }

  const problem = localError(raw)
  const isImage = (raw.type || '').startsWith('image/')
  tasks.value.push({
    id: ++sequence,
    file: raw,
    name: raw.name,
    size: raw.size,
    progress: 0,
    status: problem ? 'local_error' : 'pending',
    message: problem ?? '',
    invoiceId: null,
    thumb: isImage ? URL.createObjectURL(raw) : null,
    ocrStatus: null,
    jobError: null,
    controller: null,
  })
  scheduleStart()
}

/** el-upload 是一次选一个回调，攒 350ms 再决定走单文件还是批量。 */
function scheduleStart(): void {
  if (startTimer) clearTimeout(startTimer)
  startTimer = setTimeout(() => {
    startTimer = null
    void startSelected()
  }, 350)
}

async function startSelected(): Promise<void> {
  if (running.value) return
  const pending = tasks.value.filter((task) => task.status === 'pending')
  if (!pending.length) return
  if (pending.length === 1) {
    await runUpload(pending[0])
    return
  }
  await runBatch(pending)
}

async function runUpload(task: UploadTask): Promise<void> {
  running.value = true
  task.status = 'uploading'
  task.progress = 0
  try {
    const data = await uploadInvoice(task.file, (percent) => {
      task.progress = percent
    })
    task.invoiceId = data.invoice_id
    task.progress = 100
    task.status = 'recognizing'
    task.message = '上传成功，识别中…'
    startPolling(task)
  } catch (error) {
    if (error instanceof ApiError && error.code === 40901) {
      const payload = error.payload?.data as { invoice_id?: number | null } | null | undefined
      task.invoiceId = payload?.invoice_id ?? null
      task.progress = 100
      task.status = 'duplicate'
      // 跨账号重复时后端不给 invoice_id，直接用后端文案（不拼一个会 404 的详情链接）
      task.message = task.invoiceId
        ? '该票据已上传'
        : error.message || '该影像已被其他账号上传，不能重复提交'
      return
    }
    task.status = 'failed'
    task.message = error instanceof ApiError ? error.message : '上传失败，请重试'
  } finally {
    running.value = false
  }
}

async function runBatch(pending: UploadTask[]): Promise<void> {
  running.value = true
  pending.forEach((task) => {
    task.status = 'uploading'
    task.progress = 0
    task.message = '批量上传中…'
  })
  try {
    const data = await uploadBatch(
      pending.map((task) => task.file),
      (percent) => pending.forEach((task) => (task.progress = percent)),
    )
    batchId.value = data.batch_id
    pending.forEach((task, index) => {
      const item = data.items[index]
      if (!item) {
        task.status = 'failed'
        task.message = '批次结果缺失，请刷新后重试'
        return
      }
      task.invoiceId = item.invoice_id
      if (item.status === 'duplicate') {
        task.progress = 100
        task.status = 'duplicate'
        task.message = '该票据已上传'
      } else if (item.status === 'rejected') {
        task.status = 'failed'
        task.message = item.error ?? '上传被拒绝'
      } else {
        task.progress = 0
        task.status = 'recognizing'
        task.message = '排队识别中…'
      }
    })
    if (data.created > 0) startBatchPolling()
  } catch (error) {
    pending.forEach((task) => {
      task.status = 'failed'
      task.message = error instanceof ApiError ? error.message : '批量上传失败，请重试'
    })
  } finally {
    running.value = false
  }
}

function startPolling(task: UploadTask): void {
  if (task.invoiceId === null) return
  const controller = new AbortController()
  task.controller = controller
  void waitForOcr(
    task.invoiceId,
    (status) => {
      task.ocrStatus = status.ocr_status
      task.jobError = status.stages.find((stage) => stage.error)?.error ?? null
      task.message = `识别中… ${status.progress}%`
    },
    controller.signal,
  ).then((outcome) => {
    if (outcome === 'aborted') return
    if (outcome === 'timeout') {
      task.status = 'timeout'
      task.message = '识别时间较长，请稍后在详情页查看'
      return
    }
    if (task.ocrStatus === 'failed') {
      task.status = 'failed'
      task.message = task.jobError ? `识别失败：${task.jobError.slice(0, 80)}` : '识别失败'
    } else {
      task.status = 'done'
      task.progress = 100
      task.message = '识别完成'
    }
  })
}

function applyBatchSummary(summary: BatchSummaryData): void {
  const byInvoice = new Map(summary.items.map((item) => [item.invoice_id, item]))
  for (const task of tasks.value) {
    if (task.invoiceId === null) continue
    const item = byInvoice.get(task.invoiceId)
    if (!item) continue
    task.progress = item.progress
    task.ocrStatus = item.ocr_status
    task.jobError = item.error
    if (item.ocr_status === 'succeeded') {
      task.progress = 100
      task.status = 'done'
      task.message = '识别完成'
    } else if (item.ocr_status === 'failed') {
      task.status = 'failed'
      task.message = item.error ? `识别失败：${item.error.slice(0, 80)}` : '识别失败'
    } else {
      task.status = 'recognizing'
      task.message = `识别中… ${item.progress}%`
    }
  }
}

function startBatchPolling(): void {
  if (!batchId.value) return
  if (batchTimer) return
  batchStartedAt = Date.now()
  const tick = async (): Promise<void> => {
    if (!batchId.value) return stopBatchPolling()
    try {
      const summary = await fetchBatch(batchId.value)
      applyBatchSummary(summary)
      const pending = (summary.counts.pending ?? 0) + (summary.counts.running ?? 0)
      if (pending === 0) stopBatchPolling()
    } catch {
      // 单次轮询失败不终止，下一次再试；超时兜底在下面
    }
    if (Date.now() - batchStartedAt > BATCH_POLL_MAX_MS) {
      stopBatchPolling()
      for (const task of tasks.value) {
        if (task.status === 'recognizing') {
          task.status = 'timeout'
          task.message = '识别时间较长，请稍后在详情页查看'
        }
      }
    }
  }
  void tick()
  batchTimer = setInterval(() => void tick(), BATCH_POLL_MS)
}

function stopBatchPolling(): void {
  if (batchTimer) {
    clearInterval(batchTimer)
    batchTimer = null
  }
}

async function retryTask(task: UploadTask): Promise<void> {
  if (!batchId.value || task.invoiceId === null) return
  try {
    await retryBatchItem(batchId.value, task.invoiceId)
    task.status = 'recognizing'
    task.progress = 0
    task.message = '已重新排队，识别中…'
    startBatchPolling()
  } catch (error) {
    task.message = error instanceof ApiError ? error.message : '重试失败，请稍后再试'
  }
}

function removeTask(task: UploadTask): void {
  task.controller?.abort()
  if (task.thumb) URL.revokeObjectURL(task.thumb)
  const index = tasks.value.findIndex((item) => item.id === task.id)
  if (index >= 0) tasks.value.splice(index, 1)
}

onBeforeUnmount(() => {
  if (startTimer) clearTimeout(startTimer)
  stopBatchPolling()
  for (const task of tasks.value) {
    task.controller?.abort()
    if (task.thumb) URL.revokeObjectURL(task.thumb)
  }
})
</script>

<template>
  <el-space direction="vertical" fill size="large" class="upload-page">
    <div class="page-heading">
      <div>
        <h2>上传票据</h2>
        <p>拖拽或选择票据影像，上传成功后自动排队识别。</p>
      </div>
      <span class="heading-hint">单文件 ≤ {{ MAX_UPLOAD_MB }}MB · 一次最多 {{ MAX_FILES }} 个</span>
    </div>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>选择文件</span>
          <span class="card-sub">JPG / PNG / PDF；后端会按魔数复核</span>
        </div>
      </template>

      <el-upload
        ref="uploadRef"
        drag
        multiple
        :auto-upload="false"
        :show-file-list="false"
        :limit="MAX_FILES"
        accept=".jpg,.jpeg,.png,.pdf"
        :on-change="handleChange"
        :on-exceed="() => ElMessage.warning(`一次最多 ${MAX_FILES} 个文件`)"
        class="uploader"
      >
        <div class="drop-zone">
          <div class="drop-icon">↑</div>
          <div class="drop-title">把票据拖到这里，或点击选择文件</div>
          <div class="drop-sub">改扩展名会被后端的魔数校验拒绝</div>
        </div>
      </el-upload>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="上传后自动排队识别"
        description="多文件走批量接口：服务端单用户最多同时识别 3 个，其余排队；页面每 3 秒刷新逐项进度。"
        class="upload-alert"
      />
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>上传队列</span>
          <span class="card-sub">
            共 {{ stats.total }} ｜ 完成 {{ stats.done }} ｜ 识别中 {{ stats.active }} ｜ 失败
            {{ stats.failed }} ｜ 重复 {{ stats.duplicate }}
          </span>
        </div>
      </template>

      <el-progress :percentage="overallProgress" :stroke-width="10" class="overall" />

      <el-empty v-if="!tasks.length" description="还没有选择文件" />

      <el-table v-else :data="tasks" size="small" row-key="id">
        <el-table-column label="文件" min-width="260">
          <template #default="{ row }">
            <div class="file-cell">
              <img v-if="row.thumb" :src="row.thumb" class="thumb" alt="预览" />
              <span v-else class="thumb thumb-pdf">PDF</span>
              <div class="file-meta">
                <span class="file-name">{{ row.name }}</span>
                <span class="file-size">{{ formatSize(row.size) }}</span>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="进度" width="180">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :stroke-width="8" />
          </template>
        </el-table-column>

        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="plain">
              {{ STATUS_LABEL[row.status as TaskStatus] }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="说明" min-width="240">
          <template #default="{ row }">
            <span :class="{ 'text-danger': row.status === 'failed' || row.status === 'local_error' }">
              {{ row.message || '—' }}
            </span>
            <router-link
              v-if="row.status === 'duplicate' && row.invoiceId"
              :to="`/invoices/${row.invoiceId}`"
              class="detail-link"
            >
              查看已上传票据
            </router-link>
            <router-link
              v-else-if="row.invoiceId && (row.status === 'done' || row.status === 'recognizing')"
              :to="`/invoices/${row.invoiceId}`"
              class="detail-link"
            >
              查看详情
            </router-link>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'failed' && row.invoiceId && batchId"
              text
              type="primary"
              @click="retryTask(row)"
            >
              重试
            </el-button>
            <el-button
              text
              type="danger"
              :disabled="row.status === 'uploading'"
              @click="removeTask(row)"
            >
              移除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </el-space>
</template>

<style scoped>
.upload-page {
  width: 100%;
  max-width: none;
  margin: 0 auto;
}
.heading-hint {
  font-size: 12px;
  color: var(--app-muted);
  white-space: nowrap;
}
.card-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.card-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.uploader {
  width: 100%;
}
.uploader :deep(.el-upload) {
  width: 100%;
}
.uploader :deep(.el-upload-dragger) {
  width: 100%;
  border-radius: 10px;
  border: 1px dashed #cbd5e1;
  background: #fbfcfe;
  padding: 0;
  transition:
    border-color 0.2s ease,
    background 0.2s ease;
}
.uploader :deep(.el-upload-dragger:hover) {
  border-color: var(--app-brand);
  background: #f5f8ff;
}
.drop-zone {
  padding: 40px 16px;
  text-align: center;
}
.drop-icon {
  width: 46px;
  height: 46px;
  margin: 0 auto 14px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #e8f0ff;
  color: #1d4ed8;
  font-size: 22px;
  font-weight: 600;
}
.drop-title {
  font-size: 15px;
  color: var(--el-text-color-regular);
  font-weight: 550;
}
.drop-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.upload-alert {
  margin-top: 16px;
}
.overall {
  margin-bottom: 12px;
}
.file-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
.thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-light);
}
.thumb-pdf {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.file-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.file-name {
  word-break: break-all;
}
.file-size {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.detail-link {
  margin-left: 8px;
  font-size: 12px;
}
.text-danger {
  color: var(--el-color-danger);
}
</style>