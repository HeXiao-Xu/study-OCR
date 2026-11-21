<script setup lang="ts">
/** 报销单详情（S5-1/S5-2）：加明细 → 签名 → 提交；提交后只读。 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { getReport, signReport, submitReport, addReportItem } from '../api/reports'
import { listInvoices } from '../api/invoices'
import { ApiError } from '../api/http'
import type { InvoiceOut } from '../types/invoice'
import type { ReportDetail } from '../types/report'
import { formatDateTime, formatMoney } from '../utils/format'

const route = useRoute()
const router = useRouter()

const detail = ref<ReportDetail | null>(null)
const loading = ref(false)
const error = ref('')

const addOpen = ref(false)
const candidates = ref<InvoiceOut[]>([])
const candidatesLoading = ref(false)
const addingId = ref<number | null>(null)

const signOpen = ref(false)
const passphrase = ref('')
const signing = ref(false)
const submitting = ref(false)

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

const status = computed(() => detail.value?.report.status ?? '')
const writable = computed(() => status.value === 'draft' || status.value === 'rejected')

function reportId(): number {
  return Number(route.params.id)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    detail.value = await getReport(reportId())
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function openAdd(): Promise<void> {
  addOpen.value = true
  candidatesLoading.value = true
  try {
    const data = await listInvoices({ status: 'confirmed', page: 1, page_size: 100 })
    candidates.value = data.items
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '票据加载失败')
  } finally {
    candidatesLoading.value = false
  }
}

async function addInvoice(invoice: InvoiceOut): Promise<void> {
  if (addingId.value !== null) return
  addingId.value = invoice.id
  try {
    await addReportItem(reportId(), {
      invoice_id: invoice.id,
      amount: invoice.total_amount ?? '0.00',
    })
    candidates.value = candidates.value.filter((row) => row.id !== invoice.id)
    ElMessage.success('已加入报销单')
    await load()
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '添加失败')
  } finally {
    addingId.value = null
  }
}

async function doSign(): Promise<void> {
  if (!passphrase.value) {
    ElMessage.warning('请输入私钥口令')
    return
  }
  signing.value = true
  try {
    await signReport(reportId(), passphrase.value)
    signOpen.value = false
    passphrase.value = ''
    ElMessage.success('签名完成')
    await load()
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '签名失败')
  } finally {
    signing.value = false
  }
}

async function doSubmit(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '提交后单据进入财务待审，内容不可再修改。确认提交？',
      '提交报销单',
      { type: 'warning', confirmButtonText: '确认提交', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    const result = await submitReport(reportId(), crypto.randomUUID())
    ElMessage.success(
      result.duplicated ? `已提交过：${result.report_no}` : `提交成功：${result.report_no}`,
    )
    await load()
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

watch(() => route.params.id, () => void load(), { immediate: true })
</script>

<template>
  <el-card v-loading="loading" shadow="never" class="page-card">
    <template #header>
      <div class="head">
        <div>
          <h3>报销单详情</h3>
          <p class="sub">签名域由服务端生成；口令只在内存停留，不回显。</p>
        </div>
        <div class="head-actions">
          <el-button @click="router.push('/reports')">返回列表</el-button>
          <el-button v-if="writable" type="primary" @click="openAdd">添加票据</el-button>
          <el-button v-if="writable" :disabled="!detail?.report.item_count" @click="signOpen = true">
            签名
          </el-button>
          <el-button v-if="status === 'signed'" type="success" :loading="submitting" @click="doSubmit">
            提交
          </el-button>
        </div>
      </div>
    </template>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <template v-if="detail">
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="单号">{{ detail.report.report_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag effect="plain">{{ STATUS_LABEL[detail.report.status] ?? detail.report.status }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="金额">{{ formatMoney(detail.report.total_amount) }}</el-descriptions-item>
        <el-descriptions-item label="标题" :span="2">{{ detail.report.title }}</el-descriptions-item>
        <el-descriptions-item label="票据数">{{ detail.report.item_count }}</el-descriptions-item>
        <el-descriptions-item label="事由" :span="3">{{ detail.report.reason ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="创建">{{ formatDateTime(detail.report.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="提交">{{ formatDateTime(detail.report.submitted_at) }}</el-descriptions-item>
        <el-descriptions-item label="归档号">{{ detail.report.archive_no ?? '—' }}</el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="status === 'signed'"
        class="tip"
        type="warning"
        :closable="false"
        show-icon
        title="已签名：确认无误后点“提交”，服务端会封装信封并删除明文对象。"
      />
      <el-alert
        v-if="status === 'submitted' || status === 'approved'"
        class="tip"
        type="success"
        :closable="false"
        show-icon
        title="已提交：财务端可做验签/解密/审核，本页只读。"
      />
      <el-alert
        v-if="status === 'rejected'"
        class="tip"
        type="error"
        :closable="false"
        show-icon
        title="已驳回：可修改明细后重新签名、再次提交。"
      />

      <h4 class="section">明细</h4>
      <el-table :data="detail.report.items" size="small" border>
        <el-table-column prop="invoice_id" label="票据 ID" width="100" />
        <el-table-column label="金额" width="130">
          <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="160" />
      </el-table>
      <el-empty v-if="!detail.report.items.length" description="还没有票据" :image-size="60" />

      <h4 class="section">签名与信封</h4>
      <el-descriptions v-if="detail.signature" :column="2" border size="small">
        <el-descriptions-item label="算法">{{ detail.signature.algo }}</el-descriptions-item>
        <el-descriptions-item label="摘要">{{ detail.signature.digest_hex.slice(0, 24) }}…</el-descriptions-item>
        <el-descriptions-item label="验签结论">{{ detail.signature.verify_result ?? '未验' }}</el-descriptions-item>
        <el-descriptions-item label="签名版本">v{{ detail.signature.version }}</el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="尚未签名" :image-size="60" />
      <el-descriptions v-if="detail.envelope" :column="2" border size="small">
        <el-descriptions-item label="信封算法">
          {{ detail.envelope.key_algo }} / {{ detail.envelope.cipher_algo }}
        </el-descriptions-item>
        <el-descriptions-item label="载荷摘要">
          <code>{{ detail.envelope.payload_sha256.slice(0, 24) }}…</code>
        </el-descriptions-item>
      </el-descriptions>

      <el-dialog v-model="addOpen" title="添加已确认票据" width="720px">
        <el-table v-loading="candidatesLoading" :data="candidates" size="small" border>
          <el-table-column prop="invoice_no" label="票号" width="150" />
          <el-table-column prop="seller_name" label="销方" min-width="140" show-overflow-tooltip />
          <el-table-column label="金额" width="110">
            <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button text type="primary" :loading="addingId === row.id" @click="addInvoice(row)">
                添加
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!candidatesLoading && !candidates.length" description="没有可添加的已确认票据" />
      </el-dialog>

      <el-dialog v-model="signOpen" title="签名单据" width="440px">
        <el-form label-position="top">
          <el-form-item label="私钥口令">
            <el-input v-model="passphrase" type="password" show-password placeholder="仅本次使用，不回显" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="signOpen = false">取消</el-button>
          <el-button type="primary" :loading="signing" @click="doSign">签名</el-button>
        </template>
      </el-dialog>
    </template>
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
  gap: 12px;
}
.head h3 {
  margin: 0;
}
.sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: #64748b;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.tip {
  margin-top: 12px;
}
.section {
  margin: 18px 0 8px;
}
</style>