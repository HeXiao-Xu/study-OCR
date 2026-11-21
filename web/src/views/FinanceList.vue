<script setup lang="ts">
/** 财务报销审核列表（S5-3）：筛选项走 route.query；点单号进整页审核。 */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { downloadPendingCsv, listPending } from '../api/finance'
import { ApiError } from '../api/http'
import type { FinancePendingItem, FinancePendingParams, VerifyStatus } from '../types/finance'
import { formatDateTime, formatMoney } from '../utils/format'

const route = useRoute()
const router = useRouter()

const rows = ref<FinancePendingItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const exporting = ref(false)

const VERIFY_LABEL: Record<VerifyStatus, string> = {
  unverified: '未验签',
  ok: '已验签',
  failed: '验签失败',
}
const VERIFY_TAG: Record<VerifyStatus, 'info' | 'success' | 'danger'> = {
  unverified: 'info',
  ok: 'success',
  failed: 'danger',
}

function verifyLabel(value: string): string {
  return VERIFY_LABEL[value as VerifyStatus] ?? value
}

function verifyTag(value: string): 'info' | 'success' | 'danger' {
  return VERIFY_TAG[value as VerifyStatus] ?? 'info'
}

function text(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] ?? '')
  return value == null ? '' : String(value)
}

function page(): number {
  const parsed = Number(text(route.query.page))
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

function currentParams(): FinancePendingParams {
  return {
    verify_status: (text(route.query.verify_status) || undefined) as VerifyStatus | undefined,
    submitted_from: text(route.query.submitted_from) || undefined,
    submitted_to: text(route.query.submitted_to) || undefined,
    q: text(route.query.q) || undefined,
    page: page(),
    page_size: 10,
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listPending(currentParams())
    rows.value = data.items
    total.value = data.total
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function setQuery(patch: Record<string, string | number | undefined>): void {
  const next: Record<string, string | number> = {}
  for (const [key, value] of Object.entries({ ...route.query, ...patch })) {
    if (value !== undefined && value !== '') next[key] = value as string | number
  }
  router.push({ path: '/finance/pending', query: next })
}

async function exportCsv(): Promise<void> {
  exporting.value = true
  try {
    const blob = await downloadPendingCsv(currentParams())
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `finance-pending-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '导出失败'
  } finally {
    exporting.value = false
  }
}

watch(() => route.query, () => void load(), { immediate: true })
</script>

<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="head">
        <div>
          <h3>报销审核</h3>
          <p class="sub">点单号进入整页审核：验签 → 解密预览（左影像右字段）→ 通过/驳回。</p>
        </div>
        <el-button :loading="exporting" @click="exportCsv">导出 CSV</el-button>
      </div>
    </template>

    <div class="filters">
      <el-select
        :model-value="text(route.query.verify_status)"
        placeholder="验签状态"
        clearable
        style="width: 140px"
        @update:model-value="(value: string) => setQuery({ verify_status: value, page: 1 })"
      >
        <el-option label="未验签" value="unverified" />
        <el-option label="已验签" value="ok" />
        <el-option label="验签失败" value="failed" />
      </el-select>
      <el-date-picker
        :model-value="text(route.query.submitted_from)"
        type="date"
        placeholder="提交起"
        value-format="YYYY-MM-DD"
        @update:model-value="(value: string) => setQuery({ submitted_from: value, page: 1 })"
      />
      <el-date-picker
        :model-value="text(route.query.submitted_to)"
        type="date"
        placeholder="提交止"
        value-format="YYYY-MM-DD"
        @update:model-value="(value: string) => setQuery({ submitted_to: value, page: 1 })"
      />
      <el-input
        :model-value="text(route.query.q)"
        placeholder="单号 / 申请人"
        clearable
        style="width: 190px"
        @update:model-value="(value: string) => setQuery({ q: value })"
      />
      <span class="hint">共 {{ total }} 张</span>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-table v-loading="loading" :data="rows" row-key="report_id">
      <el-table-column label="单号" min-width="200">
        <template #default="{ row }">
          <el-button text type="primary" @click="router.push(`/finance/pending/${row.report_id}`)">
            {{ row.report_no }}
          </el-button>
        </template>
      </el-table-column>
      <el-table-column prop="applicant_name" label="申请人" min-width="120" />
      <el-table-column label="金额" width="120">
        <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
      </el-table-column>
      <el-table-column prop="item_count" label="票据数" width="80" />
      <el-table-column label="提交时间" min-width="170">
        <template #default="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
      </el-table-column>
      <el-table-column label="验签" width="100">
        <template #default="{ row }">
          <el-tag :type="verifyTag(row.verify_status)" effect="plain">
            {{ verifyLabel(row.verify_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button text type="primary" @click="router.push(`/finance/pending/${row.report_id}`)">
            审核
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !rows.length" description="没有待审核单据（单张票据需先建报销单并签名提交）" />

    <el-pagination
      class="pager"
      layout="prev, pager, next"
      :total="total"
      :page-size="10"
      :current-page="page()"
      @current-change="(value: number) => setQuery({ page: value })"
    />
  </el-card>
</template>

<style scoped>
.page-card { border-radius: 12px; width: 100%; }
.head { display: flex; align-items: center; justify-content: space-between; }
.head h3 { margin: 0; }
.sub { margin: 4px 0 0; font-size: 12px; color: #64748b; }
.filters { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.hint { font-size: 12px; color: #64748b; }
.pager { margin-top: 14px; justify-content: flex-end; }
</style>