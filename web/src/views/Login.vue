<script setup lang="ts">
/**
 * 登录页（S2-9）：Element Plus 表单 + 前端校验 → POST /auth/login → 存会话 → 跳 redirect。
 *
 * 错误提示交给 `http.ts` 统一处理（401 统一文案"用户名或密码错误"，不区分账号是否存在）；
 * 这里只兜住非 ApiError 的意外，避免双弹窗。
 */
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { login } from '../api/auth'
import { ApiError } from '../api/http'
import { useUserStore } from '../stores/user'

const route = useRoute()
const router = useRouter()
const store = useUserStore()

const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const loading = ref(false)

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '用户名 3–64 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入口令', trigger: 'blur' },
    { min: 8, max: 128, message: '口令 8–128 个字符', trigger: 'blur' },
  ],
}

async function submit(): Promise<void> {
  if (!formRef.value || loading.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const data = await login(form.username.trim(), form.password)
    store.setSession(data)
    ElMessage.success(`欢迎，${data.user.real_name ?? data.user.username}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect.startsWith('/') ? redirect : '/')
  } catch (error) {
    // 后端错误（含 401 用户名/密码错误）已由 http.ts 弹出；非 ApiError 才是意外
    if (!(error instanceof ApiError)) {
      ElMessage.error('登录失败，请稍后重试')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-row justify="center" class="login-wrap">
    <el-col :xs="22" :sm="16" :md="10" :lg="8">
      <el-card shadow="never">
        <template #header>
          <div class="login-header">
            <strong>登录</strong>
            <span class="login-sub">报销票据识别与加密系统</span>
          </div>
        </template>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent="submit"
        >
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="form.username"
              placeholder="学号或工号"
              autocomplete="username"
              :disabled="loading"
            />
          </el-form-item>
          <el-form-item label="口令" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="8–128 字符"
              autocomplete="current-password"
              :disabled="loading"
            />
          </el-form-item>

          <el-button
            type="primary"
            native-type="submit"
            class="login-submit"
            :loading="loading"
          >
            登录
          </el-button>
        </el-form>

        <p class="login-hint">
          账号由管理员创建；提交人可自助注册（如需开放），审核人与管理员由初始化脚本创建。
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
  align-items: baseline;
  justify-content: space-between;
}
.login-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.login-submit {
  width: 100%;
  margin-top: 4px;
}
.login-hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
</style>