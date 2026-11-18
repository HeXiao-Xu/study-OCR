<script setup lang="ts">
/**
 * 概览页：当前阶段说明 + 快捷入口。
 * 不做业务展示——票据/报销单页面在 S2 之后逐张任务卡接入。
 */
import { computed } from 'vue'

import { useUserStore } from '../stores/user'

const user = useUserStore()
const who = computed(() => user.user?.real_name ?? '未登录用户')

const milestones = [
  { key: 'M0', label: '立项与可行性验证', done: true },
  { key: 'M1', label: '基础框架', done: false },
  { key: 'M2', label: '识别流水线', done: false },
  { key: 'M3', label: '密码模块', done: false },
  { key: 'M4', label: '业务闭环', done: false },
  { key: 'M5', label: '实验与评测', done: false },
]
</script>

<template>
  <el-space direction="vertical" fill size="large" class="home">
    <el-card shadow="never">
      <h2 class="home-title">study-OCR</h2>
      <p class="home-sub">
        基于 OCR 的高校报销票据识别与加密系统 —— 票据影像 → 结构化字段 → 人工确认 →
        SM3 摘要 + SM2 签名 + SM4 数字信封 → 财务端验签解密。
      </p>
      <el-alert
        type="success"
        :closable="false"
        show-icon
        title="当前进度：S0 阶段（项目启动）"
        description="后端骨架与三件套已就绪；识别链路（S1）、密码模块（S4）尚未开始。"
      />
    </el-card>

    <el-card shadow="never">
      <template #header>里程碑</template>
      <el-space wrap>
        <el-tag
          v-for="item in milestones"
          :key="item.key"
          :type="item.done ? 'success' : 'info'"
          effect="plain"
        >
          {{ item.key }} {{ item.label }}{{ item.done ? ' ✅' : '' }}
        </el-tag>
      </el-space>
    </el-card>

    <el-card shadow="never">
      <template #header>当前用户</template>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="身份">{{ who }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ user.role ?? '—' }}</el-descriptions-item>
      </el-descriptions>
      <p class="home-hint">
        想确认前后端是否真的连通，去
        <router-link to="/debug">联调自检</router-link> 页看 `/health` 结果与 trace_id。
      </p>
    </el-card>
  </el-space>
</template>

<style scoped>
.home {
  max-width: 960px;
  margin: 0 auto;
}
.home-title {
  margin: 0 0 8px;
}
.home-sub {
  margin: 0 0 16px;
  color: var(--el-text-color-regular);
  line-height: 1.8;
}
.home-hint {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
