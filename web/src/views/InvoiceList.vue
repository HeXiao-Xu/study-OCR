<script setup lang="ts">
/**
 * 票据列表（S2-11）：筛选/分页/排序全部同步到 URL query（刷新、前进后退都不丢）。
 *
 * 状态源只有一个：`route.query`。控件改动只负责改 query，`watch` 统一拉数据，
 * 这样浏览器后退也能回到上一组筛选，不需要在两个方向同步状态。
 */
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'

import { listInvoices } from '../api/invoices'
import { ApiError } from '../api/http'
import { useUserStore } from '../stores/user'
import type {
  ExtractStatus,
  InvoiceListParams,
  InvoiceOut,
  InvoiceStatus,
  InvoiceType,
} from '../types/invoice'
import {
  EXTRACT_STATUS_LABEL,
  INVOICE_STATUS_LABEL,
  INVOICE_STATUS_TAG,
  INVOICE_TYPE_LABEL,
  OCR_STATUS_LABEL,
  OCR_STATUS_TAG,
  formatDateTime,
  formatMoney,
} from '../utils/format'

const route = useRoute()
const router = useRouter()
const store = useUserStore()

const filters = reactive({
  status: '',
  extract_status: '',
  review_status: '',
  invoice_type: '',
  q: '',
  dateRange: [] as string[],
  sortBy: 'created_at',
  order: 'desc' as 'asc' | 'desc',
  page: 1,
  pageSize: 20,
})

const rows = ref<InvoiceOut[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

const INVOICE_STATUSES: InvoiceStatus[] = ['draft', 'confirmed', 'in_report', 'archived', 'void']
const EXTRACT_STATUSES: ExtractStatus[] = ['raw', 'need_confirm', 'confirmed']
const INVOICE_TYPES: InvoiceType[] = [
  'vat_general',
  'vat_digital',
  'train_ticket',
  'flight_itinerary',
  'taxi_ticket',
  'unknown',
]

function text(value: LocationQueryValue | LocationQueryValue[] | undefined): string {
  if (Array.isArray(value)) return value[0] ?? ''
  return value ?? ''
}

function syncFromQuery(): void {
  const query = route.query
  filters.status = text(query.status)
  filters.extract_status = text(query.extract_status)
  filters.review_status = text(query.review_status)
  filters.invoice_type = text(query.invoice_type)
  filters.q = text(query.q)
  filters.dateRange =
    query.date_from && query.date_to ? [text(query.date_from), text(query.date_to)] : []
  filters.sortBy = text(query.sort_by) || 'created_at'
  filters.order = text(query.order) === 'asc' ? 'asc' : 'desc'
  filters.page = Math.max(1, Number(text(query.page)) || 1)
  filters.pageSize = Number(text(query.page_size)) || 20
}

function buildParams(): InvoiceListParams {
  const params: InvoiceListParams = {
    page: filters.page,
    page_size: filters.pageSize,
    sort_by: filters.sortBy as InvoiceListParams['sort_by'],
    order: filters.order,
  }
  if (filters.status) params.status = filters.status as InvoiceStatus
  if (filters.extract_status) params.extract_status = filters.extract_status as ExtractStatus
  if (filters.review_status) {
    params.review_status = filters.review_status as 'pending' | 'reviewed'
  }
  if (filters.invoice_type) params.invoice_type = filters.invoice_type as InvoiceType
  if (filters.q.trim()) params.q = filters.q.trim()
  if (filters.dateRange.length === 2) {
    params.date_from = filters.dateRange[0]
    params.date_to = filters.dateRange[1]
  }
  return params
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listInvoices(buildParams())
    rows.value = data.items
    total.value = data.total
  } catch (cause) {
    rows.value = []
    total.value = 0
    error.value = cause instanceof ApiError ? cause.message : '加载失败，请重试'
  } finally {
    loading.value = false
  }
}

function navigate(patch: Record<string, string | number | undefined>, resetPage = false): void {
  const merged: Record<string, string> = {}
  for (const [key, value] of Object.entries(route.query)) {
    const item = text(value)
    if (item) merged[key] = item
  }
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined || value === '') {
      delete merged[key]
    } else {
      merged[key] = String(value)
    }
  }
  if (resetPage) {
    if (merged.page === '1') delete merged.page
    else merged.page = '1'
  }
  if (JSON.stringify(merged) === JSON.stringify(cleanQuery(route.query))) {
    void load()
    return
  }
  void router.replace({ query: merged })
}

function cleanQuery(query: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(query)) {
    const item = text(value as LocationQueryValue | LocationQueryValue[])
    if (item) result[key] = item
  }
  return result
}

function applyFilters(): void {
  navigate(
    {
      status: filters.status,
      extract_status: filters.extract_status,
      review_status: filters.review_status,
      invoice_type: filters.invoice_type,
      q: filters.q.trim(),
      date_from: filters.dateRange[0] ?? '',
      date_to: filters.dateRange[1] ?? '',
      sort_by: filters.sortBy,
      order: filters.order,
      page_size: filters.pageSize,
    },
    true,
  )
}

function resetFilters(): void {
  if (Object.keys(cleanQuery(route.query)).length === 0) {
    void load()
    return
  }
  void router.replace({ query: {} })
}

function changePage(page: number): void {
  navigate({ page }, false)
}

function changePageSize(size: number): void {
  navigate({ page_size: size }, true)
}

function invoiceNumber(row: InvoiceOut): string {
  if (row.invoice_code && row.invoice_no) return `${row.invoice_code} / ${row.invoice_no}`
  return row.invoice_no ?? row.invoice_code ?? `INV-${row.id}`
}

const hasFilters = computed(() => Object.keys(cleanQuery(route.query)).length > 0)

const pageTitle = computed(() => {
  if (route.query.review_status === 'pending') return '待复核'
  if (route.query.review_status === 'reviewed') return '已复核'
  return store.role === 'reviewer' ? '票据查询' : '我的票据'
})

const pageHint = computed(() =>
  route.query.review_status === 'pending'
    ? '提交人已整单确认、等待审核人复核的票据；复核通过后状态变为「已复核」。'
    : '筛选条件会同步到地址栏，刷新或后退不会丢。',
)

watch(
  () => route.query,
  () => {
    syncFromQuery()
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <el-space direction="vertical" fill size="large" class="list-page">
    <div class="page-heading">
      <div>
        <h2>{{ pageTitle }}</h2>
        <p>{{ pageHint }}</p>
      </div>
      <span class="heading-total">共 {{ total }} 张</span>
    </div>

    <el-card shadow="never">
      <div class="filters">
        <el-input
          v-model="filters.q"
          placeholder="发票号 / 销售方名称"
          clearable
          class="filter-q"
          @keyup.enter="applyFilters"
        />
        <el-select v-model="filters.status" placeholder="票据状态" clearable class="filter-item">
          <el-option
            v-for="item in INVOICE_STATUSES"
            :key="item"
            :label="INVOICE_STATUS_LABEL[item]"
            :value="item"
          />
        </el-select>
        <el-select
          v-model="filters.extract_status"
          placeholder="抽取状态"
          clearable
          class="filter-item"
        >
          <el-option
            v-for="item in EXTRACT_STATUSES"
            :key="item"
            :label="EXTRACT_STATUS_LABEL[item]"
            :value="item"
          />
        </el-select>
        <el-select v-model="filters.review_status" placeholder="复核状态" clearable class="filter-item">
          <el-option label="待复核" value="pending" />
          <el-option label="已复核" value="reviewed" />
        </el-select>
        <el-select v-model="filters.invoice_type" placeholder="票种" clearable class="filter-item">
          <el-option
            v-for="item in INVOICE_TYPES"
            :key="item"
            :label="INVOICE_TYPE_LABEL[item]"
            :value="item"
          />
        </el-select>
        <el-date-picker
          v-model="filters.dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开票起始"
          end-placeholder="开票结束"
          class="filter-date"
        />
        <el-select v-model="filters.sortBy" class="filter-small">
          <el-option label="按上传时间" value="created_at" />
          <el-option label="按开票日期" value="invoice_date" />
          <el-option label="按价税合计" value="total_amount" />
        </el-select>
        <el-select v-model="filters.order" class="filter-tiny">
          <el-option label="降序" value="desc" />
          <el-option label="升序" value="asc" />
        </el-select>
        <el-button type="primary" @click="applyFilters">查询</el-button>
        <el-button :disabled="!hasFilters" @click="resetFilters">重置</el-button>
      </div>

      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        :title="error"
        class="list-error"
      >
        <el-button text type="primary" @click="load">重试</el-button>
      </el-alert>

      <el-table v-loading="loading" :data="rows" stripe row-key="id">
        <el-table-column label="单号" min-width="220">
          <template #default="{ row }">
            <router-link :to="`/invoices/${row.id}`" class="number-link">
              {{ invoiceNumber(row) }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column label="票种" min-width="170">
          <template #default="{ row }">
            {{ INVOICE_TYPE_LABEL[row.invoice_type as InvoiceType] }}
          </template>
        </el-table-column>
        <el-table-column label="价税合计" width="130" align="right">
          <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="INVOICE_STATUS_TAG[row.status as InvoiceStatus]" effect="plain">
              {{ INVOICE_STATUS_LABEL[row.status as InvoiceStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="识别" width="110">
          <template #default="{ row }">
            <el-tag :type="OCR_STATUS_TAG[row.ocr_status as keyof typeof OCR_STATUS_TAG]" effect="plain">
              {{ OCR_STATUS_LABEL[row.ocr_status as keyof typeof OCR_STATUS_LABEL] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <router-link :to="`/invoices/${row.id}`">
              <el-button text type="primary">查看</el-button>
            </router-link>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有符合条件的票据" />
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          :current-page="filters.page"
          :page-size="filters.pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="changePage"
          @size-change="changePageSize"
        />
      </div>
    </el-card>
  </el-space>
</template>

<style scoped>
.list-page {
  width: 100%;
  max-width: none;
  margin: 0 auto;
}
.heading-total {
  font-size: 12px;
  color: var(--app-muted);
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}
.filter-q {
  width: 220px;
}
.filter-item {
  width: 150px;
}
.filter-date {
  width: 240px;
}
.filter-small {
  width: 130px;
}
.filter-tiny {
  width: 90px;
}
.list-error {
  margin-bottom: 12px;
}
.number-link {
  color: var(--el-color-primary);
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>