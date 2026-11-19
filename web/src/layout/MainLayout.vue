<script setup lang="ts">
/**
 * 主框架：侧边栏按角色渲染，顶栏显示当前用户与退出。
 *
 * 菜单只是"体验"——真正的授权在接口（403/404）。这里按 `meta.roles` 过滤，
 * 与 router 的守卫共用同一份角色定义，避免两处漂移。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { logout } from '../api/auth'
import { useUserStore } from '../stores/user'
import type { UserRole } from '../types/api'

const route = useRoute()
const router = useRouter()
const store = useUserStore()

interface MenuItem {
  path: string
  label: string
  roles?: UserRole[]
}

const MENU: MenuItem[] = [
  { path: '/', label: '概览' },
  { path: '/invoices', label: '我的票据', roles: ['submitter'] },
  { path: '/invoices', label: '票据查询', roles: ['reviewer'] },
  { path: '/upload', label: '上传票据', roles: ['submitter'] },
  { path: '/review', label: '待核对', roles: ['reviewer'] },
  { path: '/finance/pending', label: '待审核', roles: ['reviewer'] },
  { path: '/templates', label: '票种模板', roles: ['admin'] },
  { path: '/rules', label: '字段规则', roles: ['admin'] },
  { path: '/certs', label: '证书管理', roles: ['admin'] },
  { path: '/audit', label: '审计日志', roles: ['admin'] },
  { path: '/debug', label: '联调自检' },
]

const ROLE_LABEL: Record<UserRole, string> = {
  submitter: '报销人',
  reviewer: '财务审核',
  admin: '系统管理员',
}

const visibleMenu = computed(() =>
  MENU.filter((item) => !item.roles || (store.role !== null && item.roles.includes(store.role))),
)

const displayName = computed(() => store.user?.real_name ?? store.user?.username ?? '未登录')
const roleLabel = computed(() => (store.role ? ROLE_LABEL[store.role] : '—'))

async function handleLogout(): Promise<void> {
  await logout(store.refreshToken)
  store.clear()
  await router.replace('/login')
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px" class="layout-aside">
      <div class="brand">
        <span class="brand-title">study-OCR</span>
        <span class="brand-sub">报销票据识别与加密系统</span>
      </div>
      <el-menu :default-active="route.path" router class="layout-menu">
        <el-menu-item v-for="item in visibleMenu" :key="item.path" :index="item.path">
          {{ item.label }}
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout-header">
        <div class="header-user">
          <span class="header-name">{{ displayName }}</span>
          <el-tag size="small" type="info">{{ roleLabel }}</el-tag>
        </div>
        <el-button text type="primary" @click="handleLogout">退出登录</el-button>
      </el-header>
      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}
.layout-aside {
  background: #fff;
  border-right: 1px solid var(--el-border-color-light);
  display: flex;
  flex-direction: column;
}
.brand {
  padding: 18px 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.brand-title {
  font-size: 18px;
  font-weight: 600;
}
.brand-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.layout-menu {
  border-right: none;
  flex: 1;
}
.layout-header {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  background: #fff;
  border-bottom: 1px solid var(--el-border-color-light);
}
.header-user {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header-name {
  font-size: 14px;
}
.layout-main {
  background: var(--el-fill-color-lighter);
}
</style>