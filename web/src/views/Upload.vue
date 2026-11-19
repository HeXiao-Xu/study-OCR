<script setup lang="ts">
/**
 * 上传页（S2-10）：拖拽/多选 → 本地校验 → 串行上传 → 逐文件状态与进度 → 识别状态轮询。
 *
 * 三个刻意的取舍：
 * - **串行上传**：一次 20 个同时打队列会把 OCR worker 压爆，逐文件更能看清失败原因；
 * - **409 是绿色提示**：重复影像是正常业务（同一张票传了两次），给"查看已上传票据"链接；
 * - **轮询用详情接口**（`GET /invoices/{id}` 的 `ocr_status`/`latest_job`）：`ocr-status` 专属接口
 *   要到 S3-1 才有，这里不改后端（D-048）。
 */
import { ElMessage, type UploadFile, type UploadInstance, type UploadRawFile } from 'element-plus'
import { computed, onBeforeUnmount, ref } from 'vue'

import { uploadInvoice } from '../api/files'
import { ApiError } from '../api/http'
import { waitForOcr } from '../api/invoices'
import type { OcrStatus } from '../types/invoice'

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
  jobStatus: OcrStatus | null
  jobError: string | null
  controller: AbortController | null
}

const MAX_FILES = 20
const MAX_UPLOAD_MB = Number(import.meta.env.VITE_MAX_UPLOAD_MB ?? 20)
const ALLOWED_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'pdf'])
const ALLOWED_MIMES = new Set(['image/jpeg', 'image/png', 'application/pdf'])

const uploadRef = ref<UploadInstance>()
const tasks = ref<UploadTask[]>([])
const running = ref(false)
const queue: UploadTask[] = []
let sequence = 0

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '等待上传',
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
  const task: UploadTask = {
    id: ++sequence,
    file: raw,
    name: raw.name,
    size: raw.size,
    progress: problem ? 0 : 0,
    status: problem ? 'local_error' : 'pending',
    message: problem ?? '',
    invoiceId: null,
    thumb: isImage ? URL.createObjectURL(raw) : null,
    ocrStatus: null,
    jobStatus: null,
    jobError: null,
    controller: null,
  }
  tasks.value.push(task)
  // push 进去后要拿到 Vue 的**响应式代理**再改；直接改原始 task 对象不会触发视图更新
  const current = tasks.value[tasks.value.length - 1]
  if (!problem) {
    queue.push(current)
    void processQueue()
  }
}

function handleExceed(): void {
  ElMessage.warning(`一次最多 ${MAX_FILES} 个文件，已忽略多出的文件`)
}

async function processQueue(): Promise<void> {
  if (running.value) return
  running.value = true
  try {
    while (queue.length) {
      const task = queue.shift()
      if (task) await runUpload(task)
    }
  } finally {
    running.value = false
  }
}

async function runUpload(task: UploadTask): Promise<void> {
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
      task.message = '该票据已上传'
      return
    }
    task.status = 'failed'
    task.message = error instanceof ApiError ? error.message : '上传失败，请重试'
  }
}

function startPolling(task: UploadTask): void {
  if (task.invoiceId === null) return
  const controller = new AbortController()
  task.controller = controller
  void waitForOcr(
    task.invoiceId,
    (detail) => {
      task.ocrStatus = detail.invoice.ocr_status
      task.jobStatus = detail.latest_job?.status ?? null
      task.jobError = detail.latest_job?.error ?? null
    },
    controller.signal,
  ).then((outcome) => {
    if (outcome === 'aborted') return
    if (outcome === 'timeout') {
      task.status = 'timeout'
      task.message = '识别时间较长，请稍后在详情页查看'
      return
    }
    if (task.ocrStatus === 'failed' || task.jobStatus === 'failed') {
      task.status = 'failed'
      task.message = task.jobError
        ? `识别失败：${task.jobError.slice(0, 80)}`
        : '识别失败，请稍后在详情页查看'
    } else {
      task.status = 'done'
      task.message = '识别完成'
    }
  })
}

function removeTask(task: UploadTask): void {
  task.controller?.abort()
  if (task.thumb) URL.revokeObjectURL(task.thumb)
  const index = tasks.value.findIndex((item) => item.id === task.id)
  if (index >= 0) tasks.value.splice(index, 1)
  const queued = queue.indexOf(task)
  if (queued >= 0) queue.splice(queued, 1)
}

onBeforeUnmount(() => {
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
        :on-exceed="handleExceed"
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
        description="表格里跟踪进度与识别状态；轮询最多 120 秒，超时会提示到详情页查看。"
        class="upload-alert"
      />
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>上传队列</span>
          <span class="card-sub">
            共 {{ stats.total }} ｜ 完成 {{ stats.done }} ｜ 失败 {{ stats.failed }} ｜ 重复
            {{ stats.duplicate }}
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

        <el-table-column label="操作" width="90">
          <template #default="{ row }">
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