<script setup lang="ts">
/**
 * 财务待审（S5-3）：只读列表 + 详情抽屉 + CSV 导出。
 *
 * 与票据列表同一套约定：筛选/分页以 `route.query` 为唯一状态源（刷新/后退不丢）。
 * 这里**不显示任何密文**：附件只有文件名/大小/SHA-256 与信封元数据，明文要等 S5-4 的验签+解密。
 */
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'

import { downloadPendingCsv, decryptReport, getFinanceDetail, getPreviewFileBlob, listPending, verifyReport } from '../api/finance'
import { ApiError } from '../api/http'
import type {
  DecryptPreview,
  FinanceDetail,
  FinancePendingItem,
  FinancePendingParams,
  VerifyResult,
  VerifyStatus,
} from '../types/finance'
import {
  VERIFY_STATUS_LABEL,
  VERIFY_STATUS_TAG,
  formatDateTime,
  formatMoney,
  formatSize,
} from '../utils/format'

const route = useRoute()
const router = useRouter()

const filters = reactive({
  verifyStatus: '' as '' | VerifyStatus,
  dateRange: [] as string[],
  q: '',
  sortBy: 'submitted_at' as 'submitted_at' | 'total_amount',
  order: 'desc' as 'asc' | 'desc',
  page: 1,
  pageSize: 20,
})

const rows = ref<FinancePendingItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const exporting = ref(false)

const drawerOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<FinanceDetail | null>(null)
const detailError = ref('')

// S5-4：验签与临时预览
const verifyResult = ref<VerifyResult | null>(null)
const preview = ref<DecryptPreview | null>(null)
const verifying = ref(false)
const decrypting = ref(false)
const actionError = ref('')
const previewUrls = ref<Record<number, string>>({})
const previewLoadingId = ref<number | null>(null)

const STEP_LABEL: Record<string, string> = {
  envelope_lookup: '查信封',
  mac: '验 MAC',
  unwrap_cek: '解 CEK',
  decrypt: '解密',
  cert_check: '校验证书',
  signature: '验签',
  digest_recheck: '重算摘要',
}

const VERIFY_OPTIONS: VerifyStatus[] = ['unverified', 'ok', 'failed']

function text(value: LocationQueryValue | LocationQueryValue[] | undefined): string {
  if (Array.isArray(value)) return value[0] ?? ''
  return value ?? ''
}

function cleanQuery(query: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(query)) {
    const item = text(value as LocationQueryValue | LocationQueryValue[])
    if (item) result[key] = item
  }
  return result
}

function syncFromQuery(): void {
  const query = route.query
  const verify = text(query.verify_status)
  filters.verifyStatus = VERIFY_OPTIONS.includes(verify as VerifyStatus)
    ? (verify as VerifyStatus)
    : ''
  filters.q = text(query.q)
  filters.dateRange =
    query.submitted_from && query.submitted_to
      ? [text(query.submitted_from), text(query.submitted_to)]
      : []
  filters.sortBy = text(query.sort_by) === 'total_amount' ? 'total_amount' : 'submitted_at'
  filters.order = text(query.order) === 'asc' ? 'asc' : 'desc'
  filters.page = Math.max(1, Number(text(query.page)) || 1)
  filters.pageSize = Number(text(query.page_size)) || 20
}

function buildParams(withPaging = true): FinancePendingParams {
  const params: FinancePendingParams = {
    sort_by: filters.sortBy,
    order: filters.order,
  }
  if (filters.verifyStatus) params.verify_status = filters.verifyStatus
  if (filters.q.trim()) params.q = filters.q.trim()
  if (filters.dateRange.length === 2) {
    params.submitted_from = filters.dateRange[0]
    params.submitted_to = filters.dateRange[1]
  }
  if (withPaging) {
    params.page = filters.page
    params.page_size = filters.pageSize
  }
  return params
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listPending(buildParams())
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

function applyFilters(): void {
  navigate(
    {
      verify_status: filters.verifyStatus,
      q: filters.q.trim(),
      submitted_from: filters.dateRange[0] ?? '',
      submitted_to: filters.dateRange[1] ?? '',
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

async function openDetail(row: FinancePendingItem): Promise<void> {
  drawerOpen.value = true
  detail.value = null
  detailError.value = ''
  resetCryptoState()
  detailLoading.value = true
  try {
    detail.value = await getFinanceDetail(row.report_id)
  } catch (cause) {
    detailError.value = cause instanceof ApiError ? cause.message : '详情加载失败'
  } finally {
    detailLoading.value = false
  }
}

function resetCryptoState(): void {
  for (const url of Object.values(previewUrls.value)) URL.revokeObjectURL(url)
  previewUrls.value = {}
  verifyResult.value = null
  preview.value = null
  actionError.value = ''
}

function onDrawerClosed(): void {
  resetCryptoState()
}

async function runVerify(): Promise<void> {
  if (!detail.value || verifying.value) return
  verifying.value = true
  actionError.value = ''
  preview.value = null
  try {
    verifyResult.value = await verifyReport(detail.value.report.id)
  } catch (cause) {
    actionError.value =
      cause instanceof ApiError
        ? `${cause.message}${cause.payload?.data ? `（${JSON.stringify(cause.payload.data)}）` : ''}`
        : '验签失败'
  } finally {
    verifying.value = false
  }
}

async function runDecrypt(): Promise<void> {
  if (!detail.value || !verifyResult.value || decrypting.value) return
  decrypting.value = true
  actionError.value = ''
  try {
    preview.value = await decryptReport(
      detail.value.report.id,
      verifyResult.value.verify_token,
    )
  } catch (cause) {
    actionError.value = cause instanceof ApiError ? cause.message : '解密失败'
  } finally {
    decrypting.value = false
  }
}

async function showPreviewImage(fileId: number): Promise<void> {
  if (!preview.value || previewUrls.value[fileId] || previewLoadingId.value !== null) return
  previewLoadingId.value = fileId
  try {
    const blob = await getPreviewFileBlob(preview.value.preview_token, fileId)
    previewUrls.value = {
      ...previewUrls.value,
      [fileId]: URL.createObjectURL(blob),
    }
  } catch (cause) {
    actionError.value = cause instanceof ApiError ? cause.message : '影像加载失败'
  } finally {
    previewLoadingId.value = null
  }
}

async function exportCsv(): Promise<void> {
  exporting.value = true
  try {
    const blob = await downloadPendingCsv(buildParams(false))
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `finance-pending-${stamp}.csv`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '导出失败，请重试'
  } finally {
    exporting.value = false
  }
}

const hasFilters = computed(() => Object.keys(cleanQuery(route.query)).length > 0)

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
        <h2>待审核</h2>
        <p>已提交的报销单；验签状态来自提交时的系统自动验签，详情只展示元数据（明文待 S5-4）。</p>
      </div>
      <div class="heading-right">
        <span class="heading-total">共 {{ total }} 单</span>
        <el-button :loading="exporting" @click="exportCsv">导出 CSV</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <div class="filters">
        <el-input
          v-model="filters.q"
          placeholder="单号 / 申请人"
          clearable
          class="filter-q"
          @keyup.enter="applyFilters"
        />
        <el-select v-model="filters.verifyStatus" placeholder="验签状态" clearable class="filter-item">
          <el-option
            v-for="item in VERIFY_OPTIONS"
            :key="item"
            :label="VERIFY_STATUS_LABEL[item]"
            :value="item"
          />
        </el-select>
        <el-date-picker
          v-model="filters.dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="提交起始"
          end-placeholder="提交结束"
          class="filter-date"
        />
        <el-select v-model="filters.sortBy" class="filter-small">
          <el-option label="按提交时间" value="submitted_at" />
          <el-option label="按金额" value="total_amount" />
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

      <el-table v-loading="loading" :data="rows" stripe row-key="report_id">
        <el-table-column label="单号" min-width="190">
          <template #default="{ row }">
            <el-button text type="primary" class="number-link" @click="openDetail(row)">
              {{ row.report_no }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column prop="applicant_name" label="申请人" width="130" />
        <el-table-column label="金额" width="140" align="right">
          <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="item_count" label="票据数" width="90" align="center" />
        <el-table-column label="提交时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
        </el-table-column>
        <el-table-column label="验签状态" width="110">
          <template #default="{ row }">
            <el-tag :type="VERIFY_STATUS_TAG[row.verify_status as VerifyStatus]" effect="plain">
              {{ VERIFY_STATUS_LABEL[row.verify_status as VerifyStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" @click="openDetail(row)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有符合条件的待审核单据" />
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

    <el-drawer
      v-model="drawerOpen"
      title="单据详情"
      size="46%"
      destroy-on-close
      @closed="onDrawerClosed"
    >
      <div v-loading="detailLoading" class="drawer-body">
        <el-alert
          v-if="detailError"
          type="error"
          :closable="false"
          show-icon
          :title="detailError"
        />
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="单号">{{ detail.report.report_no }}</el-descriptions-item>
            <el-descriptions-item label="申请人">{{ detail.applicant_name }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ detail.report.status }}</el-descriptions-item>
            <el-descriptions-item label="验签状态">
              <el-tag :type="VERIFY_STATUS_TAG[detail.verify_status]" effect="plain">
                {{ VERIFY_STATUS_LABEL[detail.verify_status] }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="金额">
              {{ formatMoney(detail.report.total_amount) }}
            </el-descriptions-item>
            <el-descriptions-item label="票据数">{{ detail.report.item_count }}</el-descriptions-item>
            <el-descriptions-item label="事由" :span="2">
              {{ detail.report.reason ?? '—' }}
            </el-descriptions-item>
          </el-descriptions>

          <h4 class="drawer-title">签名</h4>
          <el-descriptions v-if="detail.signature" :column="2" border size="small">
            <el-descriptions-item label="算法">{{ detail.signature.algo }}</el-descriptions-item>
            <el-descriptions-item label="摘要算法">
              {{ detail.signature.digest_algo }}
            </el-descriptions-item>
            <el-descriptions-item label="摘要" :span="2">
              <code class="mono">{{ detail.signature.digest_hex }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="证书 ID">{{ detail.signature.cert_id }}</el-descriptions-item>
            <el-descriptions-item label="验签结果">
              {{ detail.signature.verify_result ?? '未验' }}
            </el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="没有有效签名" :image-size="60" />

          <h4 class="drawer-title">数字信封</h4>
          <el-descriptions v-if="detail.envelope" :column="2" border size="small">
            <el-descriptions-item label="密钥算法">{{ detail.envelope.key_algo }}</el-descriptions-item>
            <el-descriptions-item label="对称算法">
              {{ detail.envelope.cipher_algo }}
            </el-descriptions-item>
            <el-descriptions-item label="载荷摘要" :span="2">
              <code class="mono">{{ detail.envelope.payload_sha256 }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="收方证书指纹" :span="2">
              <code class="mono">{{ detail.envelope.recipient_cert_fp ?? '—' }}</code>
            </el-descriptions-item>
          </el-descriptions>
          <el-alert v-else description="没有单据级信封" :image-size="60" />

          <h4 class="drawer-title">验签与临时预览（S5-4）</h4>
          <div class="crypto-actions">
            <el-button type="primary" :loading="verifying" @click="runVerify">验签</el-button>
            <el-button
              type="success"
              :loading="decrypting"
              :disabled="!verifyResult"
              @click="runDecrypt"
            >
              解密预览
            </el-button>
            <span v-if="verifyResult" class="crypto-hint">
              验签通过（凭据 {{ verifyResult.verify_token_expires_in }}s 有效）
            </span>
          </div>

          <el-alert
            v-if="actionError"
            type="error"
            :closable="false"
            show-icon
            :title="actionError"
            class="list-error"
          />

          <div v-if="verifyResult" class="step-tags">
            <el-tag
              v-for="step in verifyResult.steps"
              :key="step.step"
              :type="step.ok ? 'success' : 'danger'"
              effect="plain"
            >
              {{ STEP_LABEL[step.step] ?? step.step }}{{ step.ok ? ' ✓' : ' ✗' }}
            </el-tag>
            <el-tag v-if="verifyResult.cert_fp" type="info" effect="plain">
              证书 {{ verifyResult.cert_fp.slice(0, 12) }}…
            </el-tag>
          </div>

          <template v-if="preview">
            <el-descriptions :column="2" border size="small" class="preview-meta">
              <el-descriptions-item label="预览凭据有效期">
                {{ formatDateTime(preview.expires_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="载荷大小">
                {{ formatSize(preview.size) }}
              </el-descriptions-item>
            </el-descriptions>
            <div class="preview-title">解密后的字段（report-v1）</div>
            <pre class="preview-json">{{ JSON.stringify(preview.fields_preview, null, 2) }}</pre>
          </template>

          <h4 class="drawer-title">明细与附件</h4>
          <el-table :data="detail.attachments" size="small" border row-key="file_id">
            <el-table-column prop="invoice_id" label="票据 ID" width="90" />
            <el-table-column prop="filename" label="文件名" min-width="130" show-overflow-tooltip />
            <el-table-column label="大小" width="100">
              <template #default="{ row }">{{ formatSize(row.size) }}</template>
            </el-table-column>
            <el-table-column label="SHA-256" min-width="150">
              <template #default="{ row }">
                <code class="mono">{{ String(row.sha256).slice(0, 12) }}…</code>
              </template>
            </el-table-column>
            <el-table-column label="附件信封" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.envelope" type="success" effect="plain">已封装</el-tag>
                <el-tag v-else type="info" effect="plain">无</el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="preview" label="预览" width="100">
              <template #default="{ row }">
                <el-button
                  v-if="!previewUrls[row.file_id]"
                  text
                  type="primary"
                  :loading="previewLoadingId === row.file_id"
                  @click="showPreviewImage(row.file_id)"
                >
                  查看影像
                </el-button>
                <span v-else class="preview-ready">已加载</span>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="preview" class="preview-images">
            <el-image
              v-for="(url, fileId) in previewUrls"
              :key="fileId"
              :src="url"
              fit="contain"
              class="preview-image"
            />
          </div>

          <el-alert
            class="drawer-hint"
            type="info"
            :closable="false"
            show-icon
            title="明文只在 30 分钟的临时预览对象里；过期后需重新验签与解密。"
          />
        </template>
      </div>
    </el-drawer>
  </el-space>
</template>

<style scoped>
.list-page {
  width: 100%;
  max-width: none;
  margin: 0 auto;
}
.heading-right {
  display: flex;
  align-items: center;
  gap: 12px;
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
  width: 140px;
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
  padding: 0;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.drawer-body {
  min-height: 200px;
}
.drawer-title {
  margin: 18px 0 10px;
  font-size: 14px;
  color: #0f172a;
}
.drawer-hint {
  margin-top: 14px;
}
.crypto-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.crypto-hint {
  font-size: 12px;
  color: #16a34a;
}
.step-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.preview-meta {
  margin: 10px 0;
}
.preview-title {
  font-size: 13px;
  color: #334155;
  margin: 8px 0 6px;
}
.preview-json {
  max-height: 220px;
  overflow: auto;
  padding: 10px 12px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.5;
}
.preview-ready {
  font-size: 12px;
  color: #16a34a;
}
.preview-images {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
}
.preview-image {
  width: 220px;
  height: 150px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}
.mono {
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>