<script setup lang="ts">
/** 我的报销单（S5-1）：列表 + 新建；明细/签名/提交在详情页。 */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { createReport, listMyReports } from '../api/reports'
import { ApiError } from '../api/http'
import type { ReportListParams, ReportOut } from '../types/report'
import { formatDateTime, formatMoney } from '../utils/format'

const route = useRoute()
const router = useRouter()

const rows = ref<ReportOut[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

const createOpen = ref(false)
const creating = ref(false)
const form = ref({ title: '', reason: '' })

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending_sign: '待签名',
  signed: '待提交',
  encrypted: '加密中',
  submitted: '已提交',
  approved: '已通过',
  rejected: '已驳回',
  archived: '已归档',
}

function queryNumber(key: string, fallback: number): number {
  const raw = route.query[key]
  const value = Array.isArray(raw) ? raw[0] : raw
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function currentQuery(): ReportListParams {
  return {
    status: (typeof route.query.status === 'string'
      ? route.query.status
      : '') as ReportListParams['status'],
    page: queryNumber('page', 1),
    page_size: 10,
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listMyReports(currentQuery())
    rows.value = data.items
    total.value = data.total
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function setQuery(patch: Record<string, string | number>): void {
  const next = { ...route.query, ...patch }
  for (const key of Object.keys(next)) {
    if (next[key] === '' || next[key] === undefined) delete next[key]
  }
  router.push({ path: '/reports', query: next })
}

async function submitCreate(): Promise<void> {
  if (creating.value) return
  if (!form.value.title.trim() || !form.value.reason.trim()) {
    ElMessage.warning('标题与事由都必填')
    return
  }
  creating.value = true
  try {
    const report = await createReport({
      title: form.value.title.trim(),
      reason: form.value.reason.trim(),
    })
    createOpen.value = false
    ElMessage.success(`已创建 ${report.report_no}`)
    await router.push(`/reports/${report.id}`)
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '创建失败')
  } finally {
    creating.value = false
  }
}

watch(
  () => route.query,
  () => {
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="page-head">
        <div>
          <h3>我的报销单</h3>
          <p class="sub">建单 → 添加已确认票据 → 签名 → 提交财务</p>
        </div>
        <el-button type="primary" @click="createOpen = true">新建报销单</el-button>
      </div>
    </template>

    <div class="filters">
      <el-select
        :model-value="(route.query.status as string) ?? ''"
        placeholder="全部状态"
        clearable
        style="width: 160px"
        @update:model-value="(value: string) => setQuery({ status: value ?? '', page: 1 })"
      >
        <el-option label="草稿" value="draft" />
        <el-option label="待签名" value="pending_sign" />
        <el-option label="待提交" value="signed" />
        <el-option label="已提交" value="submitted" />
        <el-option label="已通过" value="approved" />
        <el-option label="已驳回" value="rejected" />
        <el-option label="已归档" value="archived" />
      </el-select>
      <span class="hint">共 {{ total }} 张</span>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-table
      v-loading="loading"
      :data="rows"
      row-key="id"
      class="table"
      @row-click="(row: ReportOut) => router.push(`/reports/${row.id}`)"
    >
      <el-table-column prop="report_no" label="单号" width="170" />
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag effect="plain">{{ STATUS_LABEL[row.status] ?? row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="金额" width="120">
        <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
      </el-table-column>
      <el-table-column prop="item_count" label="票据数" width="80" />
      <el-table-column label="提交时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="80">
        <template #default="{ row }">
          <el-button text type="primary" @click.stop="router.push(`/reports/${row.id}`)">
            查看
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      class="pager"
      layout="prev, pager, next"
      :total="total"
      :page-size="10"
      :current-page="queryNumber('page', 1)"
      @current-change="(page: number) => setQuery({ page })"
    />

    <el-dialog v-model="createOpen" title="新建报销单" width="460px">
      <el-form label-position="top">
        <el-form-item label="标题（必填）">
          <el-input v-model="form.title" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="事由（必填）">
          <el-input v-model="form.reason" type="textarea" :rows="3" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.page-card {
  border-radius: 12px;
}
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-head h3 {
  margin: 0;
}
.sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: #64748b;
}
.filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.hint {
  font-size: 12px;
  color: #64748b;
}
.table {
  cursor: pointer;
}
.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
</style>