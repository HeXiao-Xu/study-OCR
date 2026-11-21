<script setup lang="ts">
/** 证书管理（S4-6）：列表 + 吊销。签发/登记仍走 scripts/register_identity.py（私钥不出服务端）。 */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { listCertificates, revokeCertificate } from '../api/admin'
import { ApiError } from '../api/http'
import type { CertItem } from '../types/admin'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()

const rows = ref<CertItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const revokingId = ref<number | null>(null)

const ownerType = ref<'user' | 'server' | ''>(
  typeof route.query.owner_type === 'string' ? (route.query.owner_type as 'user' | 'server') : '',
)
const revoked = ref<'true' | 'false' | ''>(
  typeof route.query.revoked === 'string' ? (route.query.revoked as 'true' | 'false') : '',
)

function page(): number {
  const raw = route.query.page
  const parsed = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await listCertificates({
      owner_type: ownerType.value || undefined,
      revoked: revoked.value === '' ? undefined : revoked.value === 'true',
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
    path: '/certs',
    query: {
      owner_type: ownerType.value || undefined,
      revoked: revoked.value || undefined,
      page: 1,
    },
  })
}

function goPage(next: number): void {
  router.push({ path: '/certs', query: { ...route.query, page: next } })
}

async function revoke(row: CertItem): Promise<void> {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      `吊销证书后，使用它的验签/信封校验都会失败，且不可恢复。请填写原因：`,
      `吊销 ${row.subject_dn}`,
      {
        inputPlaceholder: '1–200 字',
        inputValidator: (value: string) =>
          value && value.trim() && value.length <= 200 ? true : '原因必填（≤200 字）',
        confirmButtonText: '确认吊销',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    reason = result.value
  } catch {
    return
  }
  revokingId.value = row.id
  try {
    await revokeCertificate(row.id, reason.trim())
    ElMessage.success('已吊销')
    await load()
  } catch (cause) {
    ElMessage.error(cause instanceof ApiError ? cause.message : '吊销失败')
  } finally {
    revokingId.value = null
  }
}

watch(
  () => route.query,
  () => {
    ownerType.value =
      typeof route.query.owner_type === 'string'
        ? (route.query.owner_type as 'user' | 'server')
        : ''
    revoked.value = typeof route.query.revoked === 'string' ? (route.query.revoked as 'true' | 'false') : ''
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
          <h3>证书管理</h3>
          <p class="sub">
            签发/登记走 <code>scripts/register_identity.py</code>（私钥只存 secrets/，接口永不导出）。
          </p>
        </div>
        <span class="hint">共 {{ total }} 张</span>
      </div>
    </template>

    <div class="filters">
      <el-select v-model="ownerType" clearable placeholder="主体类型" style="width: 140px" @change="syncQuery">
        <el-option label="用户" value="user" />
        <el-option label="服务器" value="server" />
      </el-select>
      <el-select v-model="revoked" clearable placeholder="吊销状态" style="width: 140px" @change="syncQuery">
        <el-option label="有效" value="false" />
        <el-option label="已吊销" value="true" />
      </el-select>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-table v-loading="loading" :data="rows" row-key="id">
      <el-table-column prop="subject_dn" label="主体" min-width="220" show-overflow-tooltip />
      <el-table-column prop="owner_type" label="类型" width="80" />
      <el-table-column prop="key_algo" label="算法" width="90" />
      <el-table-column label="指纹" min-width="170">
        <template #default="{ row }">
          <code>{{ row.fingerprint_sha256.slice(0, 20) }}…</code>
        </template>
      </el-table-column>
      <el-table-column label="有效期至" width="115">
        <template #default="{ row }">{{ formatDateTime(row.not_after).slice(0, 10) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.revoked ? 'danger' : 'success'" effect="plain">
            {{ row.revoked ? '已吊销' : '有效' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button
            v-if="!row.revoked"
            text
            type="danger"
            :loading="revokingId === row.id"
            @click="revoke(row)"
          >
            吊销
          </el-button>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !rows.length" description="暂无证书" />

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
  gap: 10px;
  margin-bottom: 12px;
}
.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
.muted {
  color: #94a3b8;
}
</style>