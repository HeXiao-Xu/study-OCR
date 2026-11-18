<script setup lang="ts">
/**
 * 应用外壳：只放导航与内容容器。
 * 登录态判断放在路由守卫里，这里不做重定向，避免"渲染期改路由"引发闪烁。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useUserStore } from './stores/user'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

const showNav = computed(() => route.path !== '/login')

function logout(): void {
  user.clear()
  void router.replace('/login')
}
</script>

<template>
  <el-container class="app-shell">
    <el-header v-if="showNav" class="app-header">
      <div class="brand">
        study-OCR
        <span class="brand-sub">报销票据识别与加密系统</span>
      </div>
      <el-menu :default-active="route.path" mode="horizontal" router class="app-menu">
        <el-menu-item index="/">概览</el-menu-item>
        <el-menu-item index="/debug">联调自检</el-menu-item>
      </el-menu>
      <el-button v-if="user.isAuthenticated" text type="primary" @click="logout">退出</el-button>
    </el-header>
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}
.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
  border-bottom: 1px solid var(--el-border-color-light);
  background: #fff;
}
.brand {
  font-weight: 600;
  font-size: 18px;
  white-space: nowrap;
}
.brand-sub {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.app-menu {
  flex: 1;
  border-bottom: none;
}
.app-main {
  background: var(--el-fill-color-lighter);
}
</style>
