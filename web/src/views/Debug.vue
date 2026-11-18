<script setup lang="ts">
/**
 * 联调自检页（S0-6 的核心验收点）。
 *
 * 它要证明三件事：
 * 1. 前端能通过 http 封装打到后端（不是浏览器裸 fetch）；
 * 2. 统一的 `{code,message,data,trace_id}` 壳被正确消费，**trace_id 展示出来**；
 * 3. 错误路径也走同一套封装：触发一个 404，看是否弹出后端 message 而不是白屏。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { API_BASE, ApiError, get } from '../api/http'
import { fetchHealth } from '../api/system'
import type { Envelope, HealthData } from '../types/api'

const loading = ref(false)
const lastTraceId = ref('-')
const envelope = ref<Envelope<HealthData> | null>(null)
const errorText = ref('')
const fetchedAt = ref('')

const health = computed(() => envelope.value?.data ?? null)
const statusTag = computed(() => {
  if (!health.value) return { type: 'info' as const, text: '未请求' }
  return health.value.status === 'ok'
    ? { type: 'success' as const, text: 'ok' }
    : { type: 'warning' as const, text: 'degraded' }
})

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const body = await fetchHealth()
    envelope.value = body
    lastTraceId.value = body.trace_id
    fetchedAt.value = new Date().toLocaleTimeString()
  } catch (error) {
    if (error instanceof ApiError) {
      // 503 降级是**有内容的错误**：后端仍返回了完整信封，要展示出来而不是当白屏处理
      const payload = error.payload
      const degraded = payload?.data as HealthData | null | undefined
      const isDegraded = error.status === 503 && !!degraded && typeof degraded === 'object' && 'checks' in degraded
      if (isDegraded && payload) {
        envelope.value = {
          code: payload.code,
          message: payload.message,
          data: degraded as HealthData,
          trace_id: payload.trace_id,
        }
        errorText.value = '后端处于降级状态（503 degraded）：个别依赖不可用，详见下表。'
      } else {
        envelope.value = null
        errorText.value = `${error.message}（code=${error.code}${error.traceId ? `，trace_id=${error.traceId}` : ''}）`
        lastTraceId.value = error.traceId ?? '-'
        return
      }
    } else {
      envelope.value = null
      errorText.value = String(error)
      return
    }
    lastTraceId.value = error.traceId ?? '-'
  } finally {
    loading.value = false
  }
}

/** 故意打一个不存在的接口：验证拦截器把后端 message 弹出来，而不是静默失败 */
async function triggerNotFound(): Promise<void> {
  try {
    await get('/definitely-not-exists')
  } catch (error) {
    if (error instanceof ApiError) {
      lastTraceId.value = error.traceId ?? '-'
    }
    // 提示由 http 拦截器统一弹出，这里不再重复 ElMessage
  }
}

async function copyTraceId(): Promise<void> {
  if (!lastTraceId.value || lastTraceId.value === '-') return
  try {
    await navigator.clipboard.writeText(lastTraceId.value)
    ElMessage.success('trace_id 已复制')
  } catch {
    ElMessage.warning('浏览器不允许写剪贴板，请手动选中复制')
  }
}

onMounted(load)
</script>

<template>
  <el-space direction="vertical" fill size="large" class="debug-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>后端连通性</span>
          <el-tag :type="statusTag.type" size="small">{{ statusTag.text }}</el-tag>
        </div>
      </template>

      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="请求地址">
          <code>{{ API_BASE }}/health</code>
        </el-descriptions-item>
        <el-descriptions-item label="上次请求">
          {{ fetchedAt || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="trace_id">
          <code>{{ lastTraceId }}</code>
          <el-button
            v-if="lastTraceId && lastTraceId !== '-'"
            link
            type="primary"
            size="small"
            @click="copyTraceId"
          >
            复制
          </el-button>
        </el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="errorText"
        class="debug-alert"
        type="error"
        :closable="false"
        show-icon
        :title="errorText"
      />

      <el-table
        v-if="health"
        class="debug-table"
        :data="Object.entries(health.checks).map(([name, item]) => ({ name, ...item }))"
        size="small"
        border
      >
        <el-table-column prop="name" label="依赖" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.ok ? 'success' : 'danger'" size="small">
              {{ row.ok ? 'ok' : 'fail' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="detail" label="说明" />
      </el-table>

      <el-space class="debug-actions">
        <el-button type="primary" :loading="loading" @click="load">重新检查</el-button>
        <el-button @click="triggerNotFound">触发一个 404（看统一错误提示）</el-button>
      </el-space>

      <el-collapse v-if="envelope" class="debug-raw">
        <el-collapse-item title="原始响应（统一壳）" name="raw">
          <pre>{{ JSON.stringify(envelope, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-card shadow="never">
      <template #header>这张页面同时在验证什么</template>
      <ul class="debug-list">
        <li>请求走 <code>src/api/http.ts</code> 封装：自动注入 token、统一解包响应壳；</li>
        <li>
          后端返回的 <code>trace_id</code> 被原样展示——报障时把它贴给后端，能在日志里
          <code>grep</code> 出整条链路（S0-5 已打通）；
        </li>
        <li>
          「触发一个 404」验证错误路径：拦截器会弹出后端 message（<code>资源不存在</code>），
          并把 <code>trace_id</code> 回填到上面；
        </li>
        <li>3 个依赖任一为 fail 时，后端返回 503 + <code>status=degraded</code>，这里会显示黄色标签。</li>
      </ul>
    </el-card>
  </el-space>
</template>

<style scoped>
.debug-page {
  max-width: 960px;
  margin: 0 auto;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.debug-alert,
.debug-table,
.debug-actions,
.debug-raw {
  margin-top: 16px;
}
.debug-raw pre {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.debug-list {
  margin: 0;
  padding-left: 20px;
  line-height: 1.9;
}
</style>