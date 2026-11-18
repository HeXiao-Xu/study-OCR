<script setup lang="ts">
/**
 * 登录页（S0-6 占位版）。
 *
 * 真实登录在 **S2-2** 接入（POST /auth/login → 存 token → 拉 /auth/me）。
 * 这里刻意**不发请求**，避免出现一个"点了没反应"的假表单；
 * 开发态提供一个占位按钮，用于把路由守卫与拦截器的 401 分支跑通。
 */
import { ElMessage } from 'element-plus'
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useUserStore } from '../stores/user'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

const username = ref('')
const password = ref('')
const loading = ref(false)

const isDev = import.meta.env.DEV

function submitDisabled(): boolean {
  return username.value.trim() === '' || password.value === ''
}

/** 仅开发态：塞一个占位 token，让守卫放行（S2-2 会删掉这段） */
function devSignIn(): void {
  loading.value = true
  user.setToken('dev-placeholder-token')
  user.setUser({
    id: 0,
    username: username.value.trim() || 'dev',
    real_name: '开发占位用户',
    dept: null,
    role: 'submitter',
    status: 'active',
    created_at: new Date().toISOString(),
  })
  loading.value = false
  ElMessage.success('已写入占位 token（仅开发态）')
  const redirect = (route.query.redirect as string | undefined) ?? '/'
  void router.replace(redirect)
}
</script>

<template>
  <el-row justify="center" class="login-wrap">
    <el-col :xs="22" :sm="16" :md="10" :lg="8">
      <el-card shadow="never">
        <template #header>
          <div class="login-header">
            <strong>登录</strong>
            <el-tag size="small" type="info">登录接口待 S2-2 接入</el-tag>
          </div>
        </template>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="当前是 S0-6 骨架阶段"
          description="登录、角色与权限在 S2-2 / S2-3 实装；本页暂不调用后端。"
          class="login-alert"
        />

        <el-form label-position="top" @submit.prevent>
          <el-form-item label="用户名">
            <el-input v-model="username" placeholder="学号或工号" autocomplete="username" />
          </el-form-item>
          <el-form-item label="口令">
            <el-input
              v-model="password"
              type="password"
              show-password
              placeholder="8–128 字符"
              autocomplete="current-password"
            />
          </el-form-item>
        </el-form>

        <el-button type="primary" class="login-submit" disabled :loading="loading">
          登录（S2-2 开放）
        </el-button>

        <el-divider v-if="isDev" content-position="left">开发态</el-divider>
        <el-button v-if="isDev" :disabled="submitDisabled()" class="login-submit" @click="devSignIn">
          写入占位 token 并进入
        </el-button>
        <p v-if="isDev" class="login-hint">
          这段只在 `npm run dev` 下出现（`import.meta.env.DEV`），生产构建会被摇掉。
        </p>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.login-wrap {
  padding-top: 8vh;
}
.login-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.login-alert {
  margin-bottom: 16px;
}
.login-submit {
  width: 100%;
  margin-top: 4px;
}
.login-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>