<script setup lang="ts">
/** 管理端归档工作台（S5-6）：approved 列表 → 归档（不可逆）；已归档可查。 */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { archiveReport, listAdminReports } from '../api/reports'
import { ApiError } from '../api/http'
import type { AdminReportItem } from '../types/report'
import { formatDateTime, formatMoney } from '../utils/format'

const route = useRoute()
const router = useRouter()

const tab = ref<'approved' | 'archived'>(
  route.query.tab === 'archived' ? 'archived' : 'approved',
)
const q = ref(typeof route.query.q === 'string' ? route.query.q : '')
const rows = ref<AdminReportItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const archivingId = ref<number | null>(null)

function page(): number {
  const raw = route.query.page
  const parsed = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listAdminReports({
      status: tab.value,
      q: q.value || undefined,
      page: page(),
      page_size: 10,
    })
    rows.value = data.items
    total.value = data.total
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function syncQuery(): void {
  router.push({
    path: '/admin/archive',
    query: { tab: tab.value, q: q.value || undefined, page: 1 },
  })
}

function goPage(next: number): void {
  router.push({ path: '/admin/archive', query: { ...route.query, page: next } })
}

async function doArchive(row: AdminReportItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `归档 ${row.report_no} 后不可修改、不可撤销。确认归档？`,
      '归档确认',
      { type: 'warning', confirmButtonText: '确认归档', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  archivingId.value = row.report_id
  try {
    await archiveReport(row.report_id)
    ElMessage.success(`已归档 ${row.report_no}`)
    await load()
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '归档失败')
  } finally {
    archivingId.value = null
  }
}

watch(
  () => route.query,
  () => {
    tab.value = route.query.tab === 'archived' ? 'archived' : 'approved'
    q.value = typeof route.query.q === 'string' ? route.query.q : ''
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="head">
        <div>
          <h3>归档管理</h3>
          <p class="sub">审批通过的单据在此归档；归档后只读、可检索、可导出。</p>
        </div>
        <span class="hint">共 {{ total }} 张</span>
      </div>
    </template>

    <div class="filters">
      <el-radio-group v-model="tab" @change="syncQuery">
        <el-radio-button value="approved">待归档</el-radio-button>
        <el-radio-button value="archived">已归档</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" placeholder="单号 / 申请人" clearable style="width: 180px" />
      <el-button type="primary" @click="syncQuery">查询</el-button>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-table v-loading="loading" :data="rows" row-key="report_id">
      <el-table-column prop="report_no" label="单号" width="165" />
      <el-table-column prop="applicant_name" label="申请人" width="120" />
      <el-table-column label="金额" width="110">
        <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
      </el-table-column>
      <el-table-column prop="item_count" label="票据数" width="80" />
      <el-table-column label="提交时间" width="165">
        <template #default="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
      </el-table-column>
      <el-table-column label="归档号" width="150">
        <template #default="{ row }">{{ row.archive_no ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="归档时间" width="165">
        <template #default="{ row }">{{ formatDateTime(row.archive_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button
            v-if="tab === 'approved'"
            text
            type="danger"
            :loading="archivingId === row.report_id"
            @click="doArchive(row)"
          >
            归档
          </el-button>
          <span v-else class="done">已归档</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !rows.length" description="暂无数据" />

    <el-pagination
      class="pager"
      layout="prev, pager, next"
      :total="total"
      :page-size="10"
      :current-page="page()"
      @current-change="goPage"
    />
  </el-card>
</template>

<style scoped>
.page-card {
  border-radius: 12px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head h3 {
  margin: 0;
}
.sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: #64748b;
}
.hint {
  font-size: 12px;
  color: #64748b;
}
.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
.done {
  font-size: 12px;
  color: #16a34a;
}
</style>