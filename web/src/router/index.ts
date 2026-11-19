/**
 * 路由 + 全局守卫。
 *
 * 权限边界（AGENTS §0/§10.7）：
 * - 未登录访问受保护页 → `/login?redirect=...`；
 * - 已登录但本地没有 user（刷新页面只留 token）→ 先调 `/auth/me` 水合；
 * - `meta.roles` 生效，无权限 → `/403`；
 * - 前端隐藏菜单/路由只是体验，**真正的裁决在接口**（403/404）。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { fetchMe } from '../api/auth'
import { useUserStore } from '../stores/user'
import type { UserRole } from '../types/api'
import { landingFor } from '../utils/landing'

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 */
    public?: boolean
    /** 允许访问的角色；为空表示不限制（仍需登录） */
    roles?: UserRole[]
    title?: string
    /** 尚未实现的业务页，占位页展示的说明 */
    placeholder?: string
  }
}

const Layout = () => import('../layout/MainLayout.vue')
const Placeholder = () => import('../views/Placeholder.vue')

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/403',
    name: 'forbidden',
    component: () => import('../views/Forbidden.vue'),
    meta: { title: '无权访问' },
  },
  {
    path: '/',
    component: Layout,
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('../views/Home.vue'),
        meta: { title: '概览' },
      },
      {
        path: 'invoices',
        name: 'invoices',
        component: () => import('../views/InvoiceList.vue'),
        meta: { title: '票据列表', roles: ['submitter', 'reviewer'] },
      },
      {
        path: 'invoices/:id',
        name: 'invoice-detail',
        component: () => import('../views/InvoiceDetail.vue'),
        meta: { title: '票据详情', roles: ['submitter', 'reviewer'] },
      },
      {
        path: 'upload',
        name: 'upload',
        component: () => import('../views/Upload.vue'),
        meta: { title: '上传票据', roles: ['submitter'] },
      },
      {
        path: 'review',
        name: 'review',
        component: Placeholder,
        meta: { title: '待核对', roles: ['reviewer'], placeholder: 'S3' },
      },
      {
        path: 'finance/pending',
        name: 'finance-pending',
        component: Placeholder,
        meta: { title: '待审核', roles: ['reviewer'], placeholder: 'S5' },
      },
      {
        path: 'templates',
        name: 'templates',
        component: Placeholder,
        meta: { title: '票种模板', roles: ['admin'], placeholder: '后端接口已在 S2-8 完成，管理端页面尚未排期' },
      },
      {
        path: 'rules',
        name: 'rules',
        component: Placeholder,
        meta: { title: '字段规则', roles: ['admin'], placeholder: '规则随模板维护，后端接口已在 S2-8 完成；管理端页面尚未排期' },
      },
      {
        path: 'certs',
        name: 'certs',
        component: Placeholder,
        meta: { title: '证书管理', roles: ['admin'], placeholder: 'S4-6' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: Placeholder,
        meta: { title: '审计日志', roles: ['admin'], placeholder: 'S4-7' },
      },
      {
        path: 'debug',
        name: 'debug',
        component: () => import('../views/Debug.vue'),
        meta: { title: '联调自检' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFound.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const user = useUserStore()

  if (to.meta.public) {
    if (to.path === '/login' && user.isAuthenticated) {
      return { path: '/' }
    }
    return true
  }

  if (!user.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // 刷新页面后只有 token：用 /auth/me 水合（401 会由 http 自动 refresh 一次）
  if (!user.user) {
    try {
      user.setUser(await fetchMe())
    } catch {
      user.clear()
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }

  if (to.meta.roles?.length && user.role && !to.meta.roles.includes(user.role)) {
    return { path: '/403' }
  }
  // 概览不再作为落地页：登录后直接进角色工作页
  if (to.path === '/' && user.role) {
    return { path: landingFor(user.role) }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · study-OCR` : 'study-OCR'
})

export default router