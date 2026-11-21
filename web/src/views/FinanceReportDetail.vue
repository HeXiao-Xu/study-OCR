<script setup lang="ts">
/**
 * 财务报销审核详情（S5-4/S5-5）：整页审核。
 * 验签（七步）→ 解密预览（左影像 / 右字段表）→ 通过 / 驳回。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { decryptReport, getFinanceDetail, getPreviewFileBlob, verifyReport } from '../api/finance'
import { ApiError } from '../api/http'
import type { DecryptPreview, FinanceDetail, VerifyResult } from '../types/finance'
import { fieldLabel, formatDateTime, formatMoney, formatSize } from '../utils/format'

interface PreviewItem {
  invoice_id: number
  invoice_no?: string | null
  invoice_code?: string | null
  invoice_date?: string | null
  seller_name?: string | null
  amount?: string | null
  tax_amount?: string | null
  total_amount?: string | null
  fields?: Record<string, string>
}

interface PreviewReport {
  report_no?: string
  title?: string
  reason?: string | null
  total_amount?: string
  item_count?: number
  applicant?: { username?: string; dept?: string | null }
  items?: PreviewItem[]
}

const route = useRoute()
const router = useRouter()

const detail = ref<FinanceDetail | null>(null)
const loading = ref(false)
const error = ref('')

const verifying = ref(false)
const verifyResult = ref<VerifyResult | null>(null)
const decrypting = ref(false)
const preview = ref<DecryptPreview | null>(null)
const previewReport = computed<PreviewReport>(() => (preview.value?.fields_preview ?? {}) as PreviewReport)
const activeInvoice = ref<number | null>(null)
const imageUrls = ref<Record<number, string>>({})
const imageLoading = ref(false)

const reviewing = ref(false)
const rejectOpen = ref(false)
const rejectComment = ref('')
const rejectError = ref('')

const STEP_LABEL: Record<string, string> = {
  envelope_lookup: '查信封',
  mac: '验完整性 MAC',
  unwrap_cek: '解会话密钥',
  decrypt: '解密',
  cert_check: '校验证书',
  signature: '验签名',
  digest_recheck: '重算摘要',
}

const currentItem = computed<PreviewItem | null>(() => {
  const items = previewReport.value.items ?? []
  return items.find((row) => row.invoice_id === activeInvoice.value) ?? items[0] ?? null
})

const currentFileId = computed(() => {
  const item = currentItem.value
  if (!item) return null
  const attachment = detail.value?.attachments.find((row) => row.invoice_id === item.invoice_id)
  return attachment?.file_id ?? null
})

function reportId(): number {
  return Number(route.params.id)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  verifyResult.value = null
  preview.value = null
  clearImages()
  try {
    detail.value = await getFinanceDetail(reportId())
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function clearImages(): void {
  for (const url of Object.values(imageUrls.value)) URL.revokeObjectURL(url)
  imageUrls.value = {}
  activeInvoice.value = null
}

async function runVerify(): Promise<void> {
  verifying.value = true
  error.value = ''
  try {
    verifyResult.value = await verifyReport(reportId())
    ElMessage.success('验签通过（七步全过）')
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '验签失败'
  } finally {
    verifying.value = false
  }
}

async function runDecrypt(): Promise<void> {
  if (!verifyResult.value) {
    ElMessage.warning('请先验签')
    return
  }
  decrypting.value = true
  error.value = ''
  try {
    preview.value = await decryptReport(reportId(), verifyResult.value.verify_token)
    const items = previewReport.value.items ?? []
    activeInvoice.value = items[0]?.invoice_id ?? null
    if (items[0]) await selectInvoice(items[0].invoice_id)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '解密失败'
  } finally {
    decrypting.value = false
  }
}

async function selectInvoice(name: string | number): Promise<void> {
  const invoiceId = Number(name)
  activeInvoice.value = invoiceId
  if (!preview.value || !currentFileId.value || imageUrls.value[invoiceId]) return
  imageLoading.value = true
  try {
    const blob = await getPreviewFileBlob(preview.value.preview_token, currentFileId.value)
    imageUrls.value = { ...imageUrls.value, [invoiceId]: URL.createObjectURL(blob) }
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '影像加载失败')
  } finally {
    imageLoading.value = false
  }
}

async function approve(): Promise<void> {
  if (!detail.value || !verifyResult.value) return
  reviewing.value = true
  error.value = ''
  try {
    const { reviewReport } = await import('../api/finance')
    await reviewReport(detail.value.report.id, {
      decision: 'approve',
      version: detail.value.report_version,
      verify_token: verifyResult.value.verify_token,
    })
    ElMessage.success('已通过，单据进入已审批')
    await router.push('/finance/pending')
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '审批失败'
  } finally {
    reviewing.value = false
  }
}

async function submitReject(): Promise<void> {
  if (!detail.value) return
  const comment = rejectComment.value.trim()
  if (!comment) {
    rejectError.value = '驳回必须填写意见'
    return
  }
  reviewing.value = true
  rejectError.value = ''
  try {
    const { reviewReport } = await import('../api/finance')
    await reviewReport(detail.value.report.id, {
      decision: 'reject',
      comment,
      version: detail.value.report_version,
    })
    ElMessage.success('已驳回')
    await router.push('/finance/pending')
  } catch (cause) {
    rejectError.value = cause instanceof ApiError ? cause.message : '驳回失败'
  } finally {
    reviewing.value = false
  }
}

watch(() => route.params.id, () => void load(), { immediate: true })
</script>

<template>
  <el-card v-loading="loading" shadow="never" class="page-card">
    <template #header>
      <div class="head">
        <div>
          <h3>报销审核</h3>
          <p class="sub">验签确认单据未被改、确实是申请人签的；解密后才能看影像与字段。</p>
        </div>
        <div class="head-actions">
          <el-button @click="router.push('/finance/pending')">返回列表</el-button>
          <el-button type="primary" :loading="verifying" @click="runVerify">验签</el-button>
          <el-button type="success" :loading="decrypting" :disabled="!verifyResult" @click="runDecrypt">
            解密预览
          </el-button>
          <el-button type="success" plain :loading="reviewing" :disabled="!verifyResult" @click="approve">
            通过
          </el-button>
          <el-button type="danger" plain :disabled="reviewing" @click="rejectOpen = true">驳回</el-button>
        </div>
      </div>
    </template>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <template v-if="detail">
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="单号">{{ detail.report.report_no }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ detail.applicant_name }}</el-descriptions-item>
        <el-descriptions-item label="金额">{{ formatMoney(detail.report.total_amount) }}</el-descriptions-item>
        <el-descriptions-item label="标题" :span="2">{{ detail.report.title }}</el-descriptions-item>
        <el-descriptions-item label="票据数">{{ detail.report.item_count }}</el-descriptions-item>
        <el-descriptions-item label="事由" :span="2">{{ detail.report.reason ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatDateTime(detail.report.submitted_at) }}</el-descriptions-item>
      </el-descriptions>

      <div class="explain">
        <strong>验签在做什么：</strong>
        服务端用财务私钥拆信封 → 校验完整性 MAC → 用申请人证书验证签名 →
        用库内原始数据重算摘要并逐字节比对（防“改业务行保留旧签名”）。
        七步全过才算通过，结果如下；验签凭据一次性用于“通过”。
      </div>

      <div v-if="verifyResult" class="steps">
        <el-tag v-for="step in verifyResult.steps" :key="step.step" :type="step.ok ? 'success' : 'danger'" effect="plain">
          {{ STEP_LABEL[step.step] ?? step.step }}{{ step.ok ? ' ✓' : ' ✗' }}
        </el-tag>
        <el-tag type="info" effect="plain">证书 {{ (verifyResult.cert_fp ?? '').slice(0, 12) }}…</el-tag>
      </div>

      <el-alert
        v-if="!preview"
        class="hint"
        type="info"
        :closable="false"
        show-icon
        title="验签通过后点“解密预览”：左侧看票面影像，右侧看解密后的字段表。"
      />

      <template v-else>
        <el-row :gutter="14" class="preview-body">
          <el-col :span="10">
            <div class="panel-title">票面影像</div>
            <el-tabs :model-value="activeInvoice !== null ? String(activeInvoice) : undefined" @tab-change="selectInvoice">
              <el-tab-pane
                v-for="item in previewReport.items ?? []"
                :key="item.invoice_id"
                :label="item.invoice_no ?? `票据 ${item.invoice_id}`"
                :name="String(item.invoice_id)"
              />
            </el-tabs>
            <div v-loading="imageLoading" class="image-box">
              <img v-if="activeInvoice && imageUrls[activeInvoice]" :src="imageUrls[activeInvoice]" alt="票面影像" />
              <el-empty v-else description="点上方票号加载影像" :image-size="60" />
            </div>
          </el-col>

          <el-col :span="14">
            <div class="panel-title">单据与字段</div>
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="单号">{{ previewReport.report_no ?? detail.report.report_no }}</el-descriptions-item>
              <el-descriptions-item label="申请人">{{ previewReport.applicant?.username ?? detail.applicant_name }}</el-descriptions-item>
              <el-descriptions-item label="合计">{{ formatMoney(previewReport.total_amount ?? detail.report.total_amount) }}</el-descriptions-item>
              <el-descriptions-item label="票据数">{{ previewReport.item_count ?? detail.report.item_count }}</el-descriptions-item>
              <el-descriptions-item label="事由" :span="2">{{ previewReport.reason ?? '—' }}</el-descriptions-item>
            </el-descriptions>

            <template v-if="currentItem">
              <div class="invoice-meta">
                票号 <code>{{ currentItem.invoice_no ?? '—' }}</code>
                ｜ 日期 {{ currentItem.invoice_date ?? '—' }}
                ｜ 销方 {{ currentItem.seller_name ?? '—' }}
                ｜ 合计 {{ formatMoney(currentItem.total_amount) }}
              </div>
              <el-table :data="Object.entries(currentItem.fields ?? {})" size="small" border class="field-table">
                <el-table-column label="字段" width="150">
                  <template #default="{ row }">{{ fieldLabel(row[0]) }}</template>
                </el-table-column>
                <el-table-column label="值">
                  <template #default="{ row }">{{ row[1] || '—' }}</template>
                </el-table-column>
              </el-table>
            </template>
          </el-col>
        </el-row>

        <div class="attach-meta">
          <span>附件：</span>
          <el-tag v-for="row in detail.attachments" :key="row.file_id" effect="plain">
            {{ row.filename }}（{{ formatSize(row.size) }}）
          </el-tag>
          <span class="attach-hint">以上为解密后临时预览，30 分钟后自动失效。</span>
        </div>
      </template>
    </template>

    <el-dialog v-model="rejectOpen" title="驳回单据" width="440px">
      <el-input v-model="rejectComment" type="textarea" :rows="4" maxlength="500" show-word-limit
        placeholder="例如：影像不清晰 / 金额与票据不符" />
      <el-alert v-if="rejectError" type="error" :closable="false" show-icon :title="rejectError" />
      <template #footer>
        <el-button @click="rejectOpen = false">取消</el-button>
        <el-button type="danger" :loading="reviewing" @click="submitReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.page-card { border-radius: 12px; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.head h3 { margin: 0; }
.sub { margin: 4px 0 0; font-size: 12px; color: #64748b; }
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.explain { margin: 12px 0; padding: 10px 12px; background: #f8fafc; border-radius: 8px; font-size: 13px; color: #334155; line-height: 1.7; }
.steps { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.hint { margin-bottom: 12px; }
.preview-body { margin-top: 8px; }
.panel-title { font-weight: 600; margin: 6px 0 8px; color: #0f172a; }
.image-box { min-height: 320px; display: flex; align-items: center; justify-content: center; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 8px; }
.image-box img { max-width: 100%; max-height: 520px; object-fit: contain; }
.invoice-meta { margin: 10px 0 8px; font-size: 13px; color: #334155; }
.field-table { max-height: 420px; overflow: auto; }
.attach-meta { margin-top: 14px; font-size: 12px; color: #475569; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.attach-hint { color: #94a3b8; }
</style>