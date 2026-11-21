<script setup lang="ts">
/**
 * 主框架：深色侧栏 + 顶部工作区标题。
 *
 * 菜单按角色渲染，但最终权限仍由接口裁决；"概览/联调自检"不再出现在业务菜单里
 * （登录后直接落到角色工作页，见 `utils/landing.ts`）。
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
  roles: UserRole[]
}

const MENU: MenuItem[] = [
  { path: '/invoices', label: '我的票据', roles: ['submitter'] },
  { path: '/upload', label: '上传票据', roles: ['submitter'] },
  { path: '/reports', label: '我的报销单', roles: ['submitter'] },
  { path: '/archive', label: '归档检索', roles: ['submitter'] },
  { path: '/invoices', label: '票据查询', roles: ['reviewer'] },
  { path: '/finance/pending', label: '报销审核', roles: ['reviewer'] },
  { path: '/archive', label: '归档检索', roles: ['reviewer'] },
  { path: '/templates', label: '票种模板', roles: ['admin'] },
  { path: '/admin/archive', label: '归档管理', roles: ['admin'] },
  { path: '/certs', label: '证书管理', roles: ['admin'] },
  { path: '/audit', label: '审计日志', roles: ['admin'] },
]

const ROLE_LABEL: Record<UserRole, string> = {
  submitter: '报销人',
  reviewer: '财务审核',
  admin: '系统管理员',
}

const visibleMenu = computed(() =>
  MENU.filter((item) => store.role !== null && item.roles.includes(store.role)),
)

const activeMenu = computed(() => {
  if (route.path.startsWith('/invoices')) return '/invoices'
  if (route.path.startsWith('/reports')) return '/reports'
  return route.path
})

const displayName = computed(() => store.user?.real_name ?? store.user?.username ?? '未登录')
const roleLabel = computed(() => (store.role ? ROLE_LABEL[store.role] : '—'))
const avatarText = computed(() => displayName.value.slice(0, 1).toUpperCase())
const pageTitle = computed(() => route.meta.title ?? '工作台')

async function handleLogout(): Promise<void> {
  await logout(store.refreshToken)
  store.clear()
  await router.replace('/login')
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="236px" class="layout-aside">
      <div class="brand">
        <div class="brand-mark">S</div>
        <div class="brand-text">
          <span class="brand-name">study-OCR</span>
          <span class="brand-sub">报销票据识别与加密</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        router
        class="layout-menu"
        background-color="transparent"
        text-color="#94a3b8"
        active-text-color="#ffffff"
      >
        <el-menu-item
          v-for="item in visibleMenu"
          :key="`${item.path}-${item.label}`"
          :index="item.path"
        >
          <span class="menu-dot" />
          <span class="menu-label">{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <div class="aside-foot">
        <span class="aside-foot-label">当前角色</span>
        <span class="aside-foot-role">{{ roleLabel }}</span>
      </div>
    </el-aside>

    <el-container class="layout-body">
      <el-header class="layout-header">
        <div class="header-left">
          <span class="header-title">{{ pageTitle }}</span>
          <span class="header-divider" />
          <span class="header-breadcrumb">票据工作台</span>
        </div>
        <div class="header-right">
          <div class="user-chip">
            <span class="avatar">{{ avatarText }}</span>
            <span class="user-meta">
              <strong>{{ displayName }}</strong>
              <em>{{ roleLabel }}</em>
            </span>
          </div>
          <el-button text class="logout" @click="handleLogout">退出登录</el-button>
        </div>
      </el-header>

      <el-main class="layout-main">
        <div class="main-inner">
          <router-view />
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
  background: var(--app-bg);
}
.layout-aside {
  background:
    radial-gradient(circle at 20% 0%, rgba(37, 99, 235, 0.22), transparent 45%),
    var(--app-sidebar);
  display: flex;
  flex-direction: column;
  padding: 20px 14px 16px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 8px 22px;
}
.brand-mark {
  width: 38px;
  height: 38px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #1d4ed8);
  color: #fff;
  font-weight: 700;
  font-size: 18px;
}
.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.brand-name {
  color: #f8fafc;
  font-size: 16px;
  font-weight: 650;
  letter-spacing: 0.3px;
}
.brand-sub {
  color: #64748b;
  font-size: 11px;
  white-space: nowrap;
}
.layout-menu {
  border-right: none;
  flex: 1;
}
.layout-menu .el-menu-item {
  height: 44px;
  line-height: 44px;
  margin: 2px 0;
  border-radius: 9px;
  font-size: 14px;
  gap: 10px;
}
.layout-menu .el-menu-item:hover {
  background: rgba(148, 163, 184, 0.12);
  color: #e2e8f0;
}
.layout-menu .el-menu-item.is-active {
  background: linear-gradient(135deg, #2563eb, #1d4ed8);
  color: #fff;
  font-weight: 600;
}
.menu-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.55;
}
.aside-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 10px 2px;
  border-top: 1px solid rgba(148, 163, 184, 0.16);
  font-size: 11px;
}
.aside-foot-label {
  color: #64748b;
}
.aside-foot-role {
  color: #cbd5e1;
}
.layout-body {
  min-width: 0;
}
.layout-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 28px;
  background: #fff;
  border-bottom: 1px solid #e6eaf2;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.header-title {
  font-size: 16px;
  font-weight: 650;
  color: #0f172a;
  white-space: nowrap;
}
.header-divider {
  width: 1px;
  height: 14px;
  background: #e2e8f0;
}
.header-breadcrumb {
  font-size: 12px;
  color: #94a3b8;
  white-space: nowrap;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 18px;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
}
.avatar {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #e0ecff;
  color: #1d4ed8;
  font-weight: 650;
  font-size: 13px;
}
.user-meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.user-meta strong {
  font-size: 13px;
  color: #0f172a;
  font-weight: 600;
}
.user-meta em {
  font-style: normal;
  font-size: 11px;
  color: #94a3b8;
}
.logout {
  color: #64748b;
}
.layout-main {
  padding: 24px 28px 40px;
}
.main-inner {
  width: 100%;
  margin: 0 auto;
}

@media (max-width: 900px) {
  .layout-aside {
    width: 72px !important;
  }
  .brand-text,
  .aside-foot-label,
  .menu-label {
    display: none;
  }
  .layout-menu .el-menu-item {
    justify-content: center;
  }
  .header-breadcrumb,
  .header-divider,
  .user-meta {
    display: none;
  }
  .layout-main {
    padding: 16px;
  }
}
</style>