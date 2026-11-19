<script setup lang="ts">
/**
 * 登录页（S2-9）：品牌分栏 + 真实登录。
 *
 * 错误提示交给 `http.ts` 统一处理（401 统一文案，不区分账号是否存在）；
 * 登录成功按角色落到工作页（submitter/reviewer → 票据列表，admin → 票种模板）。
 */
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { login } from '../api/auth'
import { ApiError } from '../api/http'
import { useUserStore } from '../stores/user'
import { landingFor } from '../utils/landing'

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

const capabilities = [
  { index: '01', title: '票据识别', desc: '版面分析、字段锚定与置信度输出' },
  { index: '02', title: '加密存证', desc: 'SM3 摘要、SM2 签名与 SM4 数字信封' },
  { index: '03', title: '审核留痕', desc: '财务验签解密、审核动作全链路可追溯' },
]

async function submit(): Promise<void> {
  if (!formRef.value || loading.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const data = await login(form.username.trim(), form.password)
    store.setSession(data)
    ElMessage.success(`欢迎，${data.user.real_name ?? data.user.username}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    const target =
      redirect && redirect !== '/' && redirect.startsWith('/')
        ? redirect
        : landingFor(data.user.role)
    await router.replace(target)
  } catch (error) {
    if (!(error instanceof ApiError)) {
      ElMessage.error('登录失败，请稍后重试')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="brand-panel">
      <div class="brand-top">
        <div class="brand-mark">S</div>
        <div>
          <div class="brand-name">study-OCR</div>
          <div class="brand-caption">高校报销票据识别与加密系统</div>
        </div>
      </div>

      <div class="brand-copy">
        <h1>票据从影像到<br />可信报销，一条链路。</h1>
        <p>识别、确认、签名、加密、审核，全部留痕。</p>
      </div>

      <ul class="capabilities">
        <li v-for="item in capabilities" :key="item.index">
          <span class="cap-index">{{ item.index }}</span>
          <span class="cap-body">
            <strong>{{ item.title }}</strong>
            <em>{{ item.desc }}</em>
          </span>
        </li>
      </ul>

      <div class="brand-foot">毕业设计 · 2026</div>
    </section>

    <section class="form-panel">
      <div class="form-card">
        <header class="form-head">
          <h2>欢迎回来</h2>
          <p>使用你的账号进入工作台</p>
        </header>

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
              size="large"
              placeholder="学号或工号"
              autocomplete="username"
              :disabled="loading"
            />
          </el-form-item>
          <el-form-item label="口令" prop="password">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              show-password
              placeholder="8–128 字符"
              autocomplete="current-password"
              :disabled="loading"
              @keyup.enter="submit"
            />
          </el-form-item>

          <el-button
            type="primary"
            native-type="submit"
            size="large"
            class="login-submit"
            :loading="loading"
          >
            登录
          </el-button>
        </el-form>

        <p class="form-foot">账号由管理员创建；如需开通，请联系财务信息管理员。</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(420px, 46%) 1fr;
  background: #f4f6fa;
}

/* ---------------- 左侧品牌 ---------------- */
.brand-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 48px 56px;
  color: #e2e8f0;
  background:
    radial-gradient(circle at 20% 10%, rgba(37, 99, 235, 0.35), transparent 42%),
    radial-gradient(circle at 90% 80%, rgba(14, 165, 233, 0.18), transparent 45%),
    #0f172a;
  overflow: hidden;
}
.brand-panel::after {
  content: '';
  position: absolute;
  inset: 18% -30% auto auto;
  width: 420px;
  height: 420px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 50%;
}
.brand-top {
  display: flex;
  align-items: center;
  gap: 14px;
  position: relative;
  z-index: 1;
}
.brand-mark {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #3b82f6, #1d4ed8);
  color: #fff;
  font-weight: 700;
  font-size: 20px;
}
.brand-name {
  font-size: 18px;
  font-weight: 650;
  letter-spacing: 0.4px;
}
.brand-caption {
  margin-top: 2px;
  font-size: 12px;
  color: #94a3b8;
}
.brand-copy {
  margin-top: auto;
  position: relative;
  z-index: 1;
}
.brand-copy h1 {
  margin: 0;
  font-size: clamp(30px, 3.2vw, 44px);
  line-height: 1.25;
  letter-spacing: 0.5px;
  font-weight: 650;
}
.brand-copy p {
  margin: 16px 0 0;
  color: #94a3b8;
  font-size: 14px;
}
.capabilities {
  list-style: none;
  padding: 0;
  margin: 36px 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
  position: relative;
  z-index: 1;
}
.capabilities li {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.cap-index {
  flex: none;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  color: #93c5fd;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.cap-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.cap-body strong {
  font-size: 14px;
  color: #e2e8f0;
  font-weight: 600;
}
.cap-body em {
  font-style: normal;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.6;
}
.brand-foot {
  margin-top: 28px;
  font-size: 12px;
  color: #64748b;
  position: relative;
  z-index: 1;
}

/* ---------------- 右侧表单 ---------------- */
.form-panel {
  display: grid;
  place-items: center;
  padding: 40px 24px;
}
.form-card {
  width: min(100%, 400px);
}
.form-head {
  margin-bottom: 26px;
}
.form-head h2 {
  margin: 0;
  font-size: 26px;
  font-weight: 650;
  color: #0f172a;
}
.form-head p {
  margin: 8px 0 0;
  font-size: 13px;
  color: #64748b;
}
.login-submit {
  width: 100%;
  margin-top: 6px;
  font-weight: 600;
  letter-spacing: 1px;
}
.form-foot {
  margin: 22px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: #94a3b8;
}

@media (max-width: 900px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .brand-panel {
    display: none;
  }
  .form-panel {
    padding: 48px 20px;
    background:
      radial-gradient(circle at 10% 0%, rgba(37, 99, 235, 0.1), transparent 38%),
      #f4f6fa;
  }
}
</style>