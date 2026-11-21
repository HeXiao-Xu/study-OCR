<script setup lang="ts">
/**
 * 财务审核用单张票据预览（S5-4）：与提交人确认页一致——
 * 左图（画框/缩放/拖拽/翻页）+ 右字段表（hover 高亮、点击定位）。
 * 数据：财务解密后的影像 + 票据库里的字段/行框（reviewer 可见）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import ImageCanvas from '../components/ImageCanvas.vue'
import { getPreviewFileBlob } from '../api/finance'
import { fetchInvoiceLines, getInvoiceDetail } from '../api/invoices'
import { ApiError } from '../api/http'
import type { InvoiceField, OcrLineBox } from '../types/invoice'
import { confidenceNumber, fieldLabel, formatPercent, formatMoney } from '../utils/format'

const props = defineProps<{
  invoiceId: number
  fileId: number | null
  previewToken: string
}>()

const loading = ref(false)
const error = ref('')
const fields = ref<InvoiceField[]>([])
const lines = ref<OcrLineBox[]>([])
const pageCount = ref(1)
const page = ref(1)
const imageUrl = ref('')
const imageError = ref('')
const activeField = ref<string | null>(null)
const hoveredField = ref<string | null>(null)
const canvasRef = ref<InstanceType<typeof ImageCanvas> | null>(null)
const tableWrap = ref<HTMLDivElement | null>(null)

const fieldBoxes = computed(() =>
  fields.value
    .filter((field) => (field.bbox?.length ?? 0) === 4)
    .map((field) => ({
      fieldName: field.field_name,
      label: fieldLabel(field.field_name),
      bbox: field.bbox as number[],
      level: field.review_level ?? null,
      conflict: Boolean(field.conflict),
    })),
)

const canvasLines = computed(() =>
  lines.value.map((line) => ({
    lineNo: line.line_no,
    bbox: line.bbox,
    score: line.score,
    fieldName: line.field_name ?? null,
  })),
)

const totalAmount = computed(() => {
  const field = fields.value.find((row) => row.field_name === 'total_amount')
  return field?.field_value ?? null
})

function revoke(): void {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
}

async function loadImage(): Promise<void> {
  if (!props.fileId) {
    imageError.value = '找不到附件'
    return
  }
  imageError.value = ''
  try {
    const blob = await getPreviewFileBlob(props.previewToken, props.fileId, page.value)
    revoke()
    imageUrl.value = URL.createObjectURL(blob)
  } catch (cause) {
    revoke()
    imageError.value = cause instanceof ApiError ? cause.message : '影像加载失败'
  }
}

async function loadLines(): Promise<void> {
  try {
    const data = await fetchInvoiceLines(props.invoiceId, page.value)
    lines.value = data.lines
  } catch {
    lines.value = []
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const detail = await getInvoiceDetail(props.invoiceId)
    fields.value = detail.fields
    pageCount.value = Math.max(1, detail.invoice.page_count ?? 1)
    page.value = 1
    await Promise.all([loadImage(), loadLines()])
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '票据字段加载失败'
  } finally {
    loading.value = false
  }
}

async function changePage(next: number): Promise<void> {
  page.value = next
  await Promise.all([loadImage(), loadLines()])
}

function selectField(fieldName: string | null): void {
  activeField.value = fieldName
  if (!fieldName) return
  canvasRef.value?.scrollToField(fieldName)
  const index = fields.value.findIndex((row) => row.field_name === fieldName)
  if (index >= 0) {
    const rows = tableWrap.value?.querySelectorAll('.field-table .el-table__body tr')
    rows?.[index]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
}

function rowClass({ row }: { row: InvoiceField }): string {
  const classes: string[] = []
  if (row.conflict || row.review_level === 'manual') classes.push('row-danger')
  else if (row.review_level === 'review') classes.push('row-warning')
  if (row.field_name === activeField.value) classes.push('row-active')
  if (row.field_name === hoveredField.value) classes.push('row-hovered')
  return classes.join(' ')
}

function valueOf(field: InvoiceField): string {
  return field.field_value ?? field.normalized_value ?? ''
}

function confidenceType(field: InvoiceField): 'success' | 'warning' | 'danger' | 'info' {
  if (field.review_level === 'manual') return 'danger'
  if (field.review_level === 'review') return 'warning'
  if (field.review_level === 'auto') return 'success'
  const value = confidenceNumber(field.confidence)
  if (value === null) return 'info'
  if (value < 0.6) return 'danger'
  if (value < 0.9) return 'warning'
  return 'success'
}

watch(() => props.invoiceId, () => void load(), { immediate: true })
onBeforeUnmount(revoke)
</script>

<template>
  <el-card v-loading="loading" shadow="never" class="pane">
    <template #header>
      <div class="pane-head">
        <strong>票号 {{ fields.find((row) => row.field_name === 'invoice_no')?.field_value ?? invoiceId }}</strong>
        <span>价税合计 {{ formatMoney(totalAmount) }}</span>
        <span class="muted">影像为财务解密后的临时预览（30 分钟）</span>
      </div>
    </template>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
    <el-alert v-if="imageError" type="warning" :closable="false" show-icon :title="imageError" />

    <el-row :gutter="12" class="pane-body">
      <el-col :span="12">
        <ImageCanvas
          v-if="imageUrl"
          ref="canvasRef"
          :src="imageUrl"
          :field-boxes="fieldBoxes"
          :line-boxes="canvasLines"
          :active-field="activeField"
          :hovered-field="hoveredField"
          :page="page"
          :page-count="pageCount"
          @select-field="selectField"
          @hover-field="hoveredField = $event"
          @update:page="changePage"
        />
        <el-empty v-else :description="imageError || '影像加载中…'" :image-size="70" />
      </el-col>

      <el-col :span="12">
        <div ref="tableWrap">
          <el-table
            :data="fields"
            size="small"
            border
            class="field-table"
            :row-class-name="rowClass"
            @row-click="(row: InvoiceField) => selectField(row.field_name)"
            @cell-mouse-enter="(row: InvoiceField) => (hoveredField = row.field_name)"
            @cell-mouse-leave="() => (hoveredField = null)"
          >
            <el-table-column label="字段" width="120">
              <template #default="{ row }">{{ fieldLabel(row.field_name) }}</template>
            </el-table-column>
            <el-table-column label="值" min-width="150">
              <template #default="{ row }">{{ valueOf(row) || '—' }}</template>
            </el-table-column>
            <el-table-column label="置信度" width="90">
              <template #default="{ row }">
                <el-tag :type="confidenceType(row)" effect="plain">
                  {{ formatPercent(row.confidence) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="source" label="来源" width="80" />
          </el-table>
        </div>
      </el-col>
    </el-row>
  </el-card>
</template>

<style scoped>
.pane {
  border-radius: 10px;
  margin-bottom: 14px;
}
.pane-head {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 13px;
}
.muted {
  color: #94a3b8;
  font-size: 12px;
}
.pane-body {
  margin-top: 4px;
}
.field-table {
  max-height: 560px;
  overflow: auto;
}
:deep(.row-danger) {
  background: #fef2f2;
}
:deep(.row-warning) {
  background: #fffbeb;
}
:deep(.row-active) {
  outline: 2px solid #2563eb;
  outline-offset: -2px;
}
:deep(.row-hovered) {
  background: #eff6ff;
}
</style>