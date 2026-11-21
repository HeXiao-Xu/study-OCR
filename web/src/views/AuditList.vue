<script setup lang="ts">
/** 审计日志（S4-7）：列表 + 哈希链校验。 */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { listAuditLogs, verifyAuditChain } from '../api/admin'
import { ApiError } from '../api/http'
import type { AuditItem, AuditVerifyResult } from '../types/admin'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()

const rows = ref<AuditItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const action = ref(typeof route.query.action === 'string' ? route.query.action : '')
const traceId = ref(typeof route.query.trace_id === 'string' ? route.query.trace_id : '')
const chain = ref<AuditVerifyResult | null>(null)
const verifying = ref(false)

const ACTION_OPTIONS = [
  'report_create',
  'sign',
  'seal',
  'submit',
  'verify',
  'decrypt',
  'review',
  'archive',
  'revoke_cert',
  'login_failed',
  'logout',
  'forbidden',
]

function page(): number {
  const raw = route.query.page
  const parsed = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listAuditLogs({
      action: action.value || undefined,
      trace_id: traceId.value || undefined,
      page: page(),
      page_size: 20,
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
    path: '/audit',
    query: { action: action.value || undefined, trace_id: traceId.value || undefined, page: 1 },
  })
}

function goPage(next: number): void {
  router.push({ path: '/audit', query: { ...route.query, page: next } })
}

async function runVerify(): Promise<void> {
  verifying.value = true
  try {
    chain.value = await verifyAuditChain()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '链校验失败'
  } finally {
    verifying.value = false
  }
}

watch(
  () => route.query,
  () => {
    action.value = typeof route.query.action === 'string' ? route.query.action : ''
    traceId.value = typeof route.query.trace_id === 'string' ? route.query.trace_id : ''
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
          <h3>审计日志</h3>
          <p class="sub">追加写 + HMAC-SM3 哈希链；可随时复算全链验证未被篡改。</p>
        </div>
        <el-button type="primary" :loading="verifying" @click="runVerify">校验哈希链</el-button>
      </div>
    </template>

    <el-alert
      v-if="chain"
      :type="chain.ok ? 'success' : 'error'"
      :closable="false"
      show-icon
      :title="
        chain.ok
          ? `哈希链完整：${chain.checked_count} 行`
          : `链在 #${chain.broken_id ?? '?'}（第 ${chain.broken_at ?? '?'} 行）断裂：${chain.reason ?? ''}`
      "
    />

    <div class="filters">
      <el-select v-model="action" clearable placeholder="动作" style="width: 170px" @change="syncQuery">
        <el-option v-for="item in ACTION_OPTIONS" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input v-model="traceId" placeholder="trace_id 精确匹配" clearable style="width: 260px" />
      <el-button @click="syncQuery">查询</el-button>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="id" label="#" width="70" />
      <el-table-column prop="action" label="动作" width="130" />
      <el-table-column prop="actor_role" label="角色" width="100" />
      <el-table-column label="目标" width="150">
        <template #default="{ row }">{{ row.target_type ?? '—' }} / {{ row.target_id ?? '—' }}</template>
      </el-table-column>
      <el-table-column prop="trace_id" label="trace_id" min-width="220" show-overflow-tooltip />
      <el-table-column label="时间" width="165">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column type="expand">
        <template #default="{ row }">
          <pre class="detail">{{ JSON.stringify(row.detail_json, null, 2) }}</pre>
          <p class="hash">entry_hash: {{ row.entry_hash }}</p>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !rows.length" description="暂无审计记录" />

    <el-pagination
      class="pager"
      layout="prev, pager, next"
      :total="total"
      :page-size="20"
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
.filters {
  display: flex;
  gap: 10px;
  margin: 12px 0;
}
.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
.detail {
  margin: 0;
  padding: 10px 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
  overflow: auto;
}
.hash {
  margin: 8px 0 0;
  font-size: 12px;
  color: #64748b;
  word-break: break-all;
}
</style>