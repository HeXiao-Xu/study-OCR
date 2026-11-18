/**
 * 路由 + 全局守卫。
 *
 * 守卫只做两件现在就能确定的事：
 * 1. 未登录访问受保护页面 → 跳 /login，并把原地址塞进 `redirect`；
 * 2. 已登录访问 /login → 回首页。
 * `meta.roles` 先声明、暂不生效：角色权限在 S2-2（认证）与 S2-3（行级隔离）实装，
 * 现在写死判断会是"假的安全感"，反而误导。见文件末尾的 TODO。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useUserStore } from '../stores/user'

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 */
    public?: boolean
    /** 允许访问的角色；为空表示不限制 */
    roles?: Array<'submitter' | 'reviewer' | 'admin'>
    title?: string
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    name: 'home',
    component: () => import('../views/Home.vue'),
    meta: { title: '概览' },
  },
  {
    path: '/debug',
    name: 'debug',
    component: () => import('../views/Debug.vue'),
    meta: { title: '联调自检' },
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

router.beforeEach((to) => {
  const user = useUserStore()

  if (!to.meta.public && !user.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.path === '/login' && user.isAuthenticated) {
    return { path: '/' }
  }
  // TODO(S2-3)：meta.roles 生效——届时改为读后端 /auth/me 的角色，不放行就跳无权页。
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · study-OCR` : 'study-OCR'
})

export default router
