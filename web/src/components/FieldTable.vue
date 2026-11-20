<script setup lang="ts">
/**
 * 字段表（S3-5）：从详情页抽出的表格 + 行底色 + 与影像画布的联动。
 *
 * 行底色口径（D-064）：`review_level=manual` 或 `conflict` → 红底（必须人工）；
 * `review` → 黄底（待核对）；冲突行把两个候选都摆出来，**不自动替换**。
 * 表格只负责展示与事件，编辑/保存/重识别仍由详情页管理。
 */
import { computed, nextTick } from 'vue'

import type { InvoiceField } from '../types/invoice'
import {
  AMOUNT_FIELDS,
  fieldLabel,
  formatPercent,
  confidenceNumber,
} from '../utils/format'

const props = defineProps<{
  fields: InvoiceField[]
  editMode: boolean
  editable: Record<string, string>
  editableNames: Set<string>
  activeField: string | null
  hoveredField: string | null
}>()

const emit = defineEmits<{
  'hover-field': [fieldName: string | null]
  'select-field': [fieldName: string]
  'update:editable': [payload: { field: string; value: string }]
}>()

function rowClass({ row }: { row: InvoiceField }): string {
  const classes: string[] = []
  if (row.conflict || row.review_level === 'manual') classes.push('row-danger')
  else if (row.review_level === 'review') classes.push('row-warning')
  if (row.field_name === props.activeField) classes.push('row-active')
  if (row.field_name === props.hoveredField) classes.push('row-hovered')
  return classes.join(' ')
}

function displayValue(field: InvoiceField): string {
  return field.field_value ?? field.normalized_value ?? ''
}

function confidenceTag(row: InvoiceField): 'success' | 'warning' | 'danger' | 'info' {
  if (row.review_level === 'manual') return 'danger'
  if (row.review_level === 'review') return 'warning'
  if (row.review_level === 'auto') return 'success'
  const number = confidenceNumber(row.confidence)
  if (number === null) return 'info'
  if (number < 0.6) return 'danger'
  if (number < 0.9) return 'warning'
  return 'success'
}

function isEditable(field: InvoiceField): boolean {
  return props.editMode && props.editableNames.has(field.field_name)
}

function onInput(field: InvoiceField, value: string): void {
  emit('update:editable', { field: field.field_name, value })
}

/** 父组件点画布上的框后调用：把对应行滚到视口中间并闪烁一次。 */
async function scrollToField(fieldName: string): Promise<void> {
  await nextTick()
  const rows = document.querySelectorAll('.field-table .el-table__body tr')
  const index = props.fields.findIndex((field) => field.field_name === fieldName)
  const row = rows[index] as HTMLElement | undefined
  if (!row) return
  row.scrollIntoView({ block: 'center', behavior: 'smooth' })
  row.classList.add('row-flash')
  window.setTimeout(() => row.classList.remove('row-flash'), 700)
}

const amountEditable = computed(() =>
  props.fields.some(
    (field) => AMOUNT_FIELDS.has(field.field_name) && props.editableNames.has(field.field_name),
  ),
)

defineExpose({ scrollToField, amountEditable })
</script>

<template>
  <el-table
    class="field-table"
    :data="fields"
    size="small"
    row-key="id"
    :row-class-name="rowClass"
    @cell-mouse-enter="(_row: InvoiceField) => emit('hover-field', _row.field_name)"
    @cell-mouse-leave="() => emit('hover-field', null)"
    @row-click="(row: InvoiceField) => emit('select-field', row.field_name)"
  >
    <el-table-column label="字段" width="130">
      <template #default="{ row }">{{ fieldLabel(row.field_name) }}</template>
    </el-table-column>
    <el-table-column label="值" min-width="180">
      <template #default="{ row }">
        <el-input
          v-if="isEditable(row)"
          :model-value="editable[row.field_name]"
          size="small"
          :data-field="row.field_name"
          @update:model-value="(value: string) => onInput(row, value)"
        />
        <template v-else>
          <span>{{ displayValue(row) || '—' }}</span>
          <div v-if="row.conflict" class="row-note danger-note">
            冲突候选：{{ row.field_value }} / {{ row.conflict_value }}，未自动替换
          </div>
          <div v-else-if="row.review_level === 'manual'" class="row-note danger-note">
            必须人工核对
          </div>
        </template>
      </template>
    </el-table-column>
    <el-table-column label="置信度" width="90">
      <template #default="{ row }">
        <el-tag :type="confidenceTag(row)" effect="plain" size="small">
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
        <el-tag v-else-if="row.need_review" type="warning" size="small" effect="plain">待确认</el-tag>
        <el-tag v-else-if="row.confirmed" type="success" size="small" effect="plain">已确认</el-tag>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <template #empty>
      <el-empty description="还没有识别字段（OCR 流水线尚未产出）" :image-size="70" />
    </template>
  </el-table>
</template>

<style scoped>
.field-table :deep(.row-warning) {
  --el-table-tr-bg-color: #fdf6ec;
}
.field-table :deep(.row-danger) {
  --el-table-tr-bg-color: #fef0f0;
}
.field-table :deep(.row-hovered) {
  --el-table-tr-bg-color: #f0f7ff;
}
.field-table :deep(.row-active) {
  --el-table-tr-bg-color: #fdf6ec;
  box-shadow: inset 3px 0 0 var(--el-color-warning);
}
.field-table :deep(.row-flash) {
  animation: row-flash 0.7s ease-in-out;
}
@keyframes row-flash {
  0%,
  100% {
    background-color: transparent;
  }
  40% {
    background-color: #faecd8;
  }
}
.row-note {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.4;
}
.danger-note {
  color: var(--el-color-danger);
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>