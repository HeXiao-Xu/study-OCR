<script setup lang="ts">
/** 归档检索（S5-6）：submitter 本人 / reviewer 全部；查归档号/申请人/归档日/金额。 */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { exportArchiveBlob, getReport, listArchivedReports } from '../api/reports'
import { ApiError } from '../api/http'
import type { ReportDetail, ReportOut } from '../types/report'
import { formatDateTime, formatMoney } from '../utils/format'

const route = useRoute()
const router = useRouter()

const rows = ref<ReportOut[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const filters = ref({
  q: typeof route.query.q === 'string' ? route.query.q : '',
  date_from: typeof route.query.date_from === 'string' ? route.query.date_from : '',
  date_to: typeof route.query.date_to === 'string' ? route.query.date_to : '',
  amount_min: typeof route.query.amount_min === 'string' ? route.query.amount_min : '',
  amount_max: typeof route.query.amount_max === 'string' ? route.query.amount_max : '',
})

const drawerOpen = ref(false)
const detail = ref<ReportDetail | null>(null)
const detailLoading = ref(false)

const exportOpen = ref(false)
const exportTarget = ref<ReportOut | null>(null)
const passphrase = ref('')
const exporting = ref(false)

function page(): number {
  const raw = route.query.page
  const parsed = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listArchivedReports({ ...filters.value, page: page(), page_size: 10 })
    rows.value = data.items
    total.value = data.total
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function search(): void {
  router.push({
    path: '/archive',
    query: {
      ...Object.fromEntries(Object.entries(filters.value).filter(([, value]) => value)),
      page: 1,
    },
  })
}

function goPage(next: number): void {
  router.push({ path: '/archive', query: { ...route.query, page: next } })
}

async function openDetail(row: ReportOut): Promise<void> {
  drawerOpen.value = true
  detail.value = null
  detailLoading.value = true
  try {
    detail.value = await getReport(row.id)
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

function openExport(row: ReportOut): void {
  exportTarget.value = row
  passphrase.value = ''
  exportOpen.value = true
}

async function doExport(): Promise<void> {
  if (!exportTarget.value) return
  if (passphrase.value.length < 8) {
    ElMessage.warning('口令至少 8 位')
    return
  }
  exporting.value = true
  try {
    const blob = await exportArchiveBlob(exportTarget.value.id, passphrase.value)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${exportTarget.value.report_no}-archive.zip.enc`
    anchor.click()
    URL.revokeObjectURL(url)
    exportOpen.value = false
    ElMessage.success('归档包已下载（用 scripts/open_archive.py 解开）')
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

watch(
  () => route.query,
  () => {
    filters.value = {
      q: typeof route.query.q === 'string' ? route.query.q : '',
      date_from: typeof route.query.date_from === 'string' ? route.query.date_from : '',
      date_to: typeof route.query.date_to === 'string' ? route.query.date_to : '',
      amount_min: typeof route.query.amount_min === 'string' ? route.query.amount_min : '',
      amount_max: typeof route.query.amount_max === 'string' ? route.query.amount_max : '',
    }
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
          <h3>归档检索</h3>
          <p class="sub">只列已归档单据；导出为口令加密归档包（不含影像明文）。</p>
        </div>
        <span class="hint">共 {{ total }} 张</span>
      </div>
    </template>

    <div class="filters">
      <el-input v-model="filters.q" placeholder="单号 / 申请人" clearable style="width: 180px" />
      <el-date-picker v-model="filters.date_from" type="date" placeholder="归档起" value-format="YYYY-MM-DD" />
      <el-date-picker v-model="filters.date_to" type="date" placeholder="归档止" value-format="YYYY-MM-DD" />
      <el-input v-model="filters.amount_min" placeholder="金额 ≥" style="width: 110px" />
      <el-input v-model="filters.amount_max" placeholder="金额 ≤" style="width: 110px" />
      <el-button type="primary" @click="search">查询</el-button>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-table v-loading="loading" :data="rows" row-key="id" class="table">
      <el-table-column prop="archive_no" label="归档号" width="150" />
      <el-table-column prop="report_no" label="单号" width="165" />
      <el-table-column prop="title" label="标题" min-width="170" show-overflow-tooltip />
      <el-table-column label="金额" width="110">
        <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
      </el-table-column>
      <el-table-column prop="item_count" label="票据数" width="80" />
      <el-table-column label="归档时间" width="165">
        <template #default="{ row }">{{ formatDateTime(row.archive_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <el-button text type="primary" @click="openDetail(row)">详情</el-button>
          <el-button text type="success" @click="openExport(row)">导出</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !rows.length" description="没有归档单据" />

    <el-pagination
      class="pager"
      layout="prev, pager, next"
      :total="total"
      :page-size="10"
      :current-page="page()"
      @current-change="goPage"
    />

    <el-drawer v-model="drawerOpen" title="归档详情" size="46%">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="归档号">{{ detail.report.archive_no }}</el-descriptions-item>
            <el-descriptions-item label="单号">{{ detail.report.report_no }}</el-descriptions-item>
            <el-descriptions-item label="标题" :span="2">{{ detail.report.title }}</el-descriptions-item>
            <el-descriptions-item label="金额">{{ formatMoney(detail.report.total_amount) }}</el-descriptions-item>
            <el-descriptions-item label="票据数">{{ detail.report.item_count }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ detail.report.status }}</el-descriptions-item>
            <el-descriptions-item label="验签">{{ detail.verify_status }}</el-descriptions-item>
            <el-descriptions-item label="归档时间" :span="2">
              {{ formatDateTime(detail.report.archive_at) }}
            </el-descriptions-item>
          </el-descriptions>
          <h4 class="section">明细</h4>
          <el-table :data="detail.report.items" size="small" border>
            <el-table-column prop="invoice_id" label="票据 ID" width="100" />
            <el-table-column label="金额">
              <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
            </el-table-column>
            <el-table-column prop="remark" label="备注" />
          </el-table>
          <h4 class="section">签名</h4>
          <el-descriptions v-if="detail.signature" :column="2" border size="small">
            <el-descriptions-item label="算法">{{ detail.signature.algo }}</el-descriptions-item>
            <el-descriptions-item label="摘要">
              <code>{{ detail.signature.digest_hex.slice(0, 24) }}…</code>
            </el-descriptions-item>
          </el-descriptions>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="exportOpen" title="导出归档包" width="440px">
      <p class="dialog-tip">设置解包口令（8–128 位）。归档包含清单/签名/信封元数据，不含影像明文。</p>
      <el-input v-model="passphrase" type="password" show-password placeholder="解包口令（至少 8 位）" />
      <template #footer>
        <el-button @click="exportOpen = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="doExport">下载</el-button>
      </template>
    </el-dialog>
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
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
.section {
  margin: 16px 0 8px;
}
.dialog-tip {
  margin: 0 0 10px;
  font-size: 12px;
  color: #64748b;
}
</style>