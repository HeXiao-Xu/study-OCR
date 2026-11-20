<script setup lang="ts">
/**
 * 票据详情（S3-5）：左图右表 + 框选高亮。
 *
 * 影像经 `GET /invoices/{id}/image` 带 Bearer 下载成 blob（明文仅本地调试；密文/生产返回 409，
 * 页面显示"待 S4"而不是空白）。字段框/行框都是**原图**坐标，换算只在 `ImageCanvas` + `utils/bbox.ts`。
 */
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  confirmInvoice,
  fetchInvoiceImage,
  fetchInvoiceLines,
  getInvoiceDetail,
  reocrFields,
  updateInvoiceFields,
} from '../api/invoices'
import { ApiError } from '../api/http'
import FieldTable from '../components/FieldTable.vue'
import ImageCanvas from '../components/ImageCanvas.vue'
import { useUserStore } from '../stores/user'
import type {
  ConfirmError,
  InvoiceDetail,
  InvoiceField,
  InvoiceStatus,
  InvoiceType,
  OcrLineBox,
  OcrStatus,
} from '../types/invoice'
import {
  AMOUNT_FIELDS,
  EXTRACT_STATUS_LABEL,
  INVOICE_STATUS_LABEL,
  INVOICE_STATUS_TAG,
  INVOICE_TYPE_LABEL,
  OCR_STATUS_LABEL,
  OCR_STATUS_TAG,
  fieldLabel,
  formatDateTime,
  formatMoney,
  formatPercent,
  formatSize,
} from '../utils/format'

const route = useRoute()
const store = useUserStore()

const detail = ref<InvoiceDetail | null>(null)
const reocring = ref(new Set<string>())
const confirmErrors = ref<ConfirmError[]>([])
const confirming = ref(false)
const loading = ref(false)
const error = ref('')

const editMode = ref(false)
const editable = reactive<Record<string, string>>({})
const amountConfirm = ref(false)
const saving = ref(false)

const imageUrl = ref('')
const imageError = ref('')
const page = ref(1)
const lineBoxes = ref<OcrLineBox[]>([])
const activeField = ref<string | null>(null)
const hoveredField = ref<string | null>(null)
const canvasRef = ref<InstanceType<typeof ImageCanvas> | null>(null)
const tableRef = ref<InstanceType<typeof FieldTable> | null>(null)

const FIELD_ORDER = [
  'invoice_code',
  'invoice_no',
  'invoice_date',
  'buyer_name',
  'buyer_tax_no',
  'seller_name',
  'seller_tax_no',
  'amount',
  'tax_amount',
  'total_amount',
  'total_amount_upper',
  'check_code',
  'passenger_name',
]

const invoiceId = computed(() => Number(route.params.id))

const canEdit = computed(() => {
  const invoice = detail.value?.invoice
  return Boolean(
    invoice &&
      invoice.status === 'draft' &&
      ['raw', 'need_confirm'].includes(invoice.extract_status),
  )
})

const rereadAllowed = computed(() => detail.value?.invoice.status === 'draft')

const sortedFields = computed<InvoiceField[]>(() => {
  const fields = detail.value?.fields ?? []
  return [...fields].sort((a, b) => {
    const left = FIELD_ORDER.indexOf(a.field_name)
    const right = FIELD_ORDER.indexOf(b.field_name)
    return (left === -1 ? 999 : left) - (right === -1 ? 999 : right) || a.id - b.id
  })
})

const editableNames = computed(
  () => new Set(sortedFields.value.filter((field) => fieldEditable(field)).map((field) => field.field_name)),
)

const canvasLines = computed(() =>
  lineBoxes.value.map((line) => ({
    lineNo: line.line_no,
    bbox: line.bbox,
    score: line.score,
    fieldName: line.field_name,
  })),
)

const fieldBoxes = computed(() =>
  sortedFields.value
    .filter((field) => Array.isArray(field.bbox) && field.bbox.length === 4)
    .map((field) => ({
      fieldName: field.field_name,
      label: fieldLabel(field.field_name),
      bbox: field.bbox as number[],
      level: field.review_level ?? null,
      conflict: field.conflict,
    })),
)

const hasEditableAmount = computed(() =>
  sortedFields.value.some((field) => AMOUNT_FIELDS.has(field.field_name) && fieldEditable(field)),
)

function fieldEditable(field: InvoiceField): boolean {
  if (!canEdit.value) return false
  if (AMOUNT_FIELDS.has(field.field_name) && store.role !== 'reviewer') return false
  return true
}

function revokeImage(): void {
  if (imageUrl.value) {
    URL.revokeObjectURL(imageUrl.value)
    imageUrl.value = ''
  }
}

async function loadImage(): Promise<void> {
  if (!Number.isFinite(invoiceId.value)) return
  imageError.value = ''
  revokeImage()
  try {
    const blob = await fetchInvoiceImage(invoiceId.value, page.value)
    imageUrl.value = URL.createObjectURL(blob)
  } catch (cause) {
    imageError.value =
      cause instanceof ApiError && cause.status === 409
        ? '影像暂不可用（加密链路待 S4）'
        : '影像加载失败'
  }
  try {
    const data = await fetchInvoiceLines(invoiceId.value, page.value)
    lineBoxes.value = data.lines
  } catch {
    // 行框失败只降级为"只画字段框"，不阻塞字段核对
    lineBoxes.value = []
  }
}

function selectField(fieldName: string | null): void {
  activeField.value = fieldName
  if (!fieldName) return
  canvasRef.value?.scrollToField(fieldName)
  tableRef.value?.scrollToField(fieldName)
}

function onEditableUpdate(payload: { field: string; value: string }): void {
  editable[payload.field] = payload.value
}

async function load(): Promise<void> {
  if (!Number.isFinite(invoiceId.value)) {
    error.value = '无效的票据编号'
    return
  }
  loading.value = true
  error.value = ''
  try {
    detail.value = await getInvoiceDetail(invoiceId.value)
    confirmErrors.value = []
    if (editMode.value) startEdit()
    await loadImage()
  } catch (cause) {
    detail.value = null
    error.value =
      cause instanceof ApiError
        ? cause.status === 404
          ? '票据不存在或无权访问'
          : cause.message
        : '加载失败，请重试'
  } finally {
    loading.value = false
  }
}

function startEdit(): void {
  if (!detail.value) return
  amountConfirm.value = false
  for (const field of detail.value.fields) {
    if (fieldEditable(field)) {
      editable[field.field_name] = field.field_value ?? ''
    }
  }
  editMode.value = true
}

function cancelEdit(): void {
  editMode.value = false
  amountConfirm.value = false
}

async function rereadField(field: InvoiceField): Promise<void> {
  const invoice = detail.value?.invoice
  if (!invoice) return
  reocring.value.add(field.field_name)
  try {
    await reocrFields(invoice.id, [field.field_name], invoice.version)
    ElMessage.success('已提交重识别，稍后自动刷新')
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      await load()
    }
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '重识别失败')
  } finally {
    reocring.value.delete(field.field_name)
  }
}

async function confirmAll(): Promise<void> {
  const invoice = detail.value?.invoice
  if (!invoice || confirming.value) return
  try {
    await ElMessageBox.confirm(
      '确认后票据进入「已确认」，之后修改字段需要重新走草稿流程。是否继续？',
      '确认整单',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  confirmErrors.value = []
  confirming.value = true
  try {
    await confirmInvoice(invoice.id, invoice.version)
    ElMessage.success('已确认整单')
    await load()
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 42201) {
      const payload = cause.payload?.data as { fields?: ConfirmError[] } | null | undefined
      confirmErrors.value = payload?.fields ?? []
      ElMessage.error('整单校验未通过，请按提示处理')
    } else if (cause instanceof ApiError && cause.code === 40900) {
      ElMessage.error('版本冲突：票据已被修改，已为你刷新')
      await load()
    } else {
      ElMessage.error(cause instanceof ApiError ? cause.message : '确认失败，请重试')
    }
  } finally {
    confirming.value = false
  }
}

async function save(): Promise<void> {
  if (!detail.value || saving.value) return
  const changed = sortedFields.value.filter(
    (field) => fieldEditable(field) && (editable[field.field_name] ?? '') !== (field.field_value ?? ''),
  )
  if (!changed.length) {
    ElMessage.info('没有修改任何字段')
    return
  }

  const amountChanged = changed.some((field) => AMOUNT_FIELDS.has(field.field_name))
  if (amountChanged && store.role === 'reviewer' && !amountConfirm.value) {
    ElMessage.warning('修改金额字段必须勾选"确认修改金额"')
    return
  }

  saving.value = true
  try {
    const result = await updateInvoiceFields(detail.value.invoice.id, {
      version: detail.value.invoice.version,
      fields: changed.map((field) => ({
        field_name: field.field_name,
        value: editable[field.field_name] ?? '',
        confirm: true,
      })),
      confirm_amount: amountChanged && store.role === 'reviewer' ? true : undefined,
    })
    ElMessage.success(`已保存 ${result.updated.length} 个字段`)
    editMode.value = false
    amountConfirm.value = false
    await load()
  } catch (cause) {
    if (cause instanceof ApiError) {
      if (cause.code === 40900) {
        ElMessage.error('版本冲突：票据已被修改，已为你刷新，请重试')
        await load()
      } else if (cause.code === 42201) {
        ElMessage.error(cause.message || '修改后仍存在未决冲突')
      } else {
        ElMessage.error(cause.message)
      }
    } else {
      ElMessage.error('保存失败，请重试')
    }
  } finally {
    saving.value = false
  }
}

watch(
  invoiceId,
  () => {
    editMode.value = false
    page.value = 1
    activeField.value = null
    hoveredField.value = null
    lineBoxes.value = []
    void load()
  },
  { immediate: true },
)

onBeforeUnmount(revokeImage)
</script>

<template>
  <el-space direction="vertical" fill size="large" class="detail-page" v-loading="loading">
    <el-result
      v-if="error"
      icon="warning"
      :title="error"
      sub-title="票据不存在，或当前账号无权访问。"
    >
      <template #extra>
        <router-link to="/invoices">
          <el-button type="primary">回票据列表</el-button>
        </router-link>
      </template>
    </el-result>

    <template v-else-if="detail">
      <div class="page-heading">
        <div>
          <h2>票据详情</h2>
          <p>左图右表核对：hover 字段高亮图上框、点框定位字段；草稿态可修改，金额字段仅审核人可改并留痕。</p>
        </div>
      </div>

      <el-card shadow="never">
        <template #header>
          <div class="card-header">
            <div class="title-group">
              <span>票据详情</span>
              <el-tag :type="INVOICE_STATUS_TAG[detail.invoice.status as InvoiceStatus]" effect="plain">
                {{ INVOICE_STATUS_LABEL[detail.invoice.status as InvoiceStatus] }}
              </el-tag>
              <el-tag :type="OCR_STATUS_TAG[detail.invoice.ocr_status as OcrStatus]" effect="plain">
                {{ OCR_STATUS_LABEL[detail.invoice.ocr_status as OcrStatus] }}
              </el-tag>
            </div>
            <div class="actions">
              <el-button @click="load">刷新</el-button>
              <el-button v-if="canEdit && !editMode" type="primary" @click="startEdit">
                编辑字段
              </el-button>
              <el-button
                v-if="canEdit && !editMode && store.role === 'submitter'"
                type="success"
                :loading="confirming"
                @click="confirmAll"
              >
                确认整单
              </el-button>
              <template v-if="editMode">
                <el-button type="primary" :loading="saving" @click="save">保存修改</el-button>
                <el-button :disabled="saving" @click="cancelEdit">取消</el-button>
              </template>
            </div>
          </div>
        </template>

        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="票种">
            {{ INVOICE_TYPE_LABEL[detail.invoice.invoice_type as InvoiceType] }}
          </el-descriptions-item>
          <el-descriptions-item label="价税合计">
            {{ formatMoney(detail.invoice.total_amount) }}
          </el-descriptions-item>
          <el-descriptions-item label="开票日期">
            {{ detail.invoice.invoice_date ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="发票代码 / 号码">
            {{ detail.invoice.invoice_code ?? '—' }} / {{ detail.invoice.invoice_no ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="抽取状态">
            {{ EXTRACT_STATUS_LABEL[detail.invoice.extract_status] }}
          </el-descriptions-item>
          <el-descriptions-item label="整体置信度">
            {{ formatPercent(detail.invoice.overall_confidence) }}
          </el-descriptions-item>
          <el-descriptions-item label="上传时间">
            {{ formatDateTime(detail.invoice.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="数据版本">
            v{{ detail.invoice.version }}
          </el-descriptions-item>
          <el-descriptions-item label="质量标记">
            {{ detail.invoice.quality_flag ?? '—' }}
          </el-descriptions-item>
        </el-descriptions>

        <el-checkbox
          v-if="editMode && hasEditableAmount && store.role === 'reviewer'"
          v-model="amountConfirm"
          class="amount-confirm"
        >
          确认修改金额（金额类字段的改动会留痕）
        </el-checkbox>
        <p v-if="editMode" class="edit-hint">
          提交人只能修改非金额字段；金额字段仅审核人可改，并需勾选确认。
        </p>
      </el-card>

      <el-alert
        v-if="detail.latest_job"
        :type="detail.latest_job.status === 'failed' ? 'error' : 'info'"
        :closable="false"
        show-icon
        :title="`最近识别任务 #${detail.latest_job.job_id} · ${OCR_STATUS_LABEL[detail.latest_job.status]}`"
        :description="detail.latest_job.error ?? `阶段：${detail.latest_job.stage}；重试 ${detail.latest_job.retry_count} 次`"
      />

      <div class="detail-grid">
        <el-card shadow="never" class="preview-card">
          <template #header>
            <div class="card-header">
              <span>票据影像</span>
              <span class="card-sub">线条：字段框（绿/黄/红）＋文字行框（灰）</span>
            </div>
          </template>
          <ImageCanvas
            v-if="imageUrl"
            ref="canvasRef"
            :src="imageUrl"
            :field-boxes="fieldBoxes"
            :line-boxes="canvasLines"
            :active-field="activeField"
            :hovered-field="hoveredField"
            :page="page"
            :page-count="detail.invoice.page_count"
            @select-field="selectField"
            @hover-field="hoveredField = $event"
            @update:page="page = $event; loadImage()"
          />
          <el-empty v-else :description="imageError || '影像加载中…'" :image-size="80" />
          <el-descriptions :column="1" border size="small" class="file-meta">
            <el-descriptions-item label="文件名">{{ detail.invoice.filename }}</el-descriptions-item>
            <el-descriptions-item label="类型">{{ detail.invoice.mime }}</el-descriptions-item>
            <el-descriptions-item label="大小">
              {{ formatSize(detail.invoice.size) }}
            </el-descriptions-item>
            <el-descriptions-item label="页数">{{ detail.invoice.page_count }}</el-descriptions-item>
            <el-descriptions-item label="sha256">
              {{ detail.invoice.sha256.slice(0, 12) }}…
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card shadow="never" class="fields-card">
          <template #header>
            <div class="card-header">
              <span>识别字段（{{ sortedFields.length }}）</span>
              <span class="card-sub">低于 90% 黄底、低于 60% 或冲突红底；冲突不自动覆盖</span>
            </div>
          </template>
          <el-alert
            v-if="confirmErrors.length"
            type="error"
            :closable="false"
            show-icon
            title="整单校验未通过"
            class="confirm-errors"
          >
            <ul class="confirm-error-list">
              <li v-for="item in confirmErrors" :key="item.field_name">
                <strong>{{ fieldLabel(item.field_name) }}</strong>：{{ item.error }}
              </li>
            </ul>
          </el-alert>
          <FieldTable
            ref="tableRef"
            :fields="sortedFields"
            :edit-mode="editMode"
            :editable="editable"
            :editable-names="editableNames"
            :reocring="reocring"
            :active-field="activeField"
            :hovered-field="hoveredField"
            :reread-allowed="rereadAllowed"
            @hover-field="hoveredField = $event"
            @select-field="selectField"
            @reread="rereadField"
            @update:editable="onEditableUpdate"
          />
        </el-card>
      </div>
    </template>
  </el-space>
</template>

<style scoped>
.detail-page {
  width: 100%;
  max-width: none;
  margin: 0 auto;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.actions {
  display: flex;
  gap: 8px;
}
.card-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.amount-confirm {
  margin-top: 12px;
}
.edit-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.detail-grid {
  display: grid;
  grid-template-columns: minmax(380px, 1.05fr) minmax(460px, 1fr);
  gap: 16px;
  align-items: start;
}
.preview-card {
  min-width: 0;
}
.fields-card {
  min-width: 0;
}
.file-meta {
  margin-top: 12px;
}
.confirm-errors {
  margin-bottom: 12px;
}
.confirm-error-list {
  margin: 6px 0 0;
  padding-left: 18px;
  line-height: 1.6;
}
@media (max-width: 1100px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>