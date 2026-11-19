<script setup lang="ts">
/**
 * 票据详情（S2-11）：字段核对 + 草稿态行内编辑。
 *
 * 影像预览按 D-049 采用占位：`GET /invoices/{id}/image` 依赖 S4 的信封/解密（S3-5/S5-4 才实现），
 * 现在临时加明文接口会绕过加密边界，所以这里只展示文件元数据并明确标注"待 S3-5"。
 */
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { getInvoiceDetail, updateInvoiceFields } from '../api/invoices'
import { ApiError } from '../api/http'
import { useUserStore } from '../stores/user'
import type { InvoiceDetail, InvoiceField, InvoiceStatus, InvoiceType, OcrStatus } from '../types/invoice'
import {
  AMOUNT_FIELDS,
  EXTRACT_STATUS_LABEL,
  INVOICE_STATUS_LABEL,
  INVOICE_STATUS_TAG,
  INVOICE_TYPE_LABEL,
  OCR_STATUS_LABEL,
  OCR_STATUS_TAG,
  confidenceNumber,
  fieldLabel,
  formatDateTime,
  formatMoney,
  formatPercent,
  formatSize,
} from '../utils/format'

const route = useRoute()
const store = useUserStore()

const detail = ref<InvoiceDetail | null>(null)
const loading = ref(false)
const error = ref('')

const editMode = ref(false)
const editable = reactive<Record<string, string>>({})
const amountConfirm = ref(false)
const saving = ref(false)

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

const sortedFields = computed<InvoiceField[]>(() => {
  const fields = detail.value?.fields ?? []
  return [...fields].sort((a, b) => {
    const left = FIELD_ORDER.indexOf(a.field_name)
    const right = FIELD_ORDER.indexOf(b.field_name)
    return (left === -1 ? 999 : left) - (right === -1 ? 999 : right) || a.id - b.id
  })
})

const hasEditableAmount = computed(() =>
  sortedFields.value.some((field) => AMOUNT_FIELDS.has(field.field_name) && fieldEditable(field)),
)

function fieldEditable(field: InvoiceField): boolean {
  if (!canEdit.value) return false
  if (AMOUNT_FIELDS.has(field.field_name) && store.role !== 'reviewer') return false
  return true
}

function displayValue(field: InvoiceField): string {
  return field.field_value ?? field.normalized_value ?? ''
}

function confidenceTag(value: string | number | null): 'success' | 'warning' | 'danger' | 'info' {
  const number = confidenceNumber(value)
  if (number === null) return 'info'
  if (number < 0.85) return 'danger'
  if (number < 0.95) return 'warning'
  return 'success'
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
    if (editMode.value) startEdit()
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

watch(invoiceId, () => {
  editMode.value = false
  void load()
}, { immediate: true })
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
          <p>核对识别字段；草稿态可修改，金额字段仅审核人可改并留痕。</p>
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

        <el-checkbox v-if="editMode && hasEditableAmount && store.role === 'reviewer'" v-model="amountConfirm" class="amount-confirm">
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
          <template #header>票据影像</template>
          <el-empty description="影像预览待 S3-5（加密信封落地后）" :image-size="80" />
          <el-descriptions :column="1" border size="small">
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
              <span class="card-sub">置信度低于 85% 标红；冲突字段不自动覆盖</span>
            </div>
          </template>
          <el-table :data="sortedFields" size="small" row-key="id">
            <el-table-column label="字段" width="130">
              <template #default="{ row }">{{ fieldLabel(row.field_name) }}</template>
            </el-table-column>
            <el-table-column label="值" min-width="180">
              <template #default="{ row }">
                <el-input
                  v-if="editMode && fieldEditable(row)"
                  v-model="editable[row.field_name]"
                  size="small"
                  :data-field="row.field_name"
                />
                <span v-else>{{ displayValue(row) || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="置信度" width="90">
              <template #default="{ row }">
                <el-tag :type="confidenceTag(row.confidence)" effect="plain" size="small">
                  {{ formatPercent(row.confidence) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="90">
              <template #default="{ row }">
                {{ row.source_line_no === null ? '—' : `第 ${row.source_line_no} 行` }}
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.conflict" type="danger" size="small" effect="plain">冲突</el-tag>
                <el-tag v-else-if="row.need_review" type="warning" size="small" effect="plain">
                  待确认
                </el-tag>
                <el-tag v-else-if="row.confirmed" type="success" size="small" effect="plain">
                  已确认
                </el-tag>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="还没有识别字段（S3-1 流水线尚未接入）" :image-size="70" />
            </template>
          </el-table>
        </el-card>
      </div>
    </template>
  </el-space>
</template>

<style scoped>
.detail-page {
  max-width: 1200px;
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
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.preview-card {
  position: sticky;
  top: 16px;
}
@media (max-width: 960px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>