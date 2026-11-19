import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import { get, getRefreshCount, setUnauthorizedHandler } from './api/http'
import router from './router'
import { TOKEN_KEY } from './stores/user'

// ---------------------------------------------------------------------------
// 开发态冒烟入口：`/?dev_token=xxx` 写入 token 后把参数从地址栏抹掉。
// 用途：无人值守页面自检（S7 冷启动、演示前冒烟）直接打开受保护页面；
// 路由守卫会再用 /auth/me 水合用户，401 时走 http 的自动 refresh。
// `import.meta.env.DEV` 是编译期常量，生产构建整段被摇掉（DECISIONS D-021）。
// ---------------------------------------------------------------------------
if (import.meta.env.DEV) {
  const params = new URLSearchParams(window.location.search)
  const devToken = params.get('dev_token')
  if (devToken) {
    localStorage.setItem(TOKEN_KEY, devToken)
    params.delete('dev_token')
    const query = params.toString()
    window.history.replaceState(null, '', window.location.pathname + (query ? `?${query}` : ''))
  }
}

const app = createApp(App)

// Pinia 必须先装：http 拦截器里会取 user store
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// refresh 失败时由 http 通知这里回登录页（避免 http ↔ router 循环 import）
setUnauthorizedHandler(() => {
  const current = router.currentRoute.value
  if (current.path !== '/login') {
    void router.replace({ path: '/login', query: { redirect: current.fullPath } })
  }
})

if (import.meta.env.DEV) {
  // 仅开发态：供 scripts/ui_smoke.mjs 验证"并发 401 只刷新一次"
  ;(
    window as unknown as {
      __httpDebug: { get: typeof get; refreshCount: () => number }
    }
  ).__httpDebug = { get, refreshCount: getRefreshCount }
}

app.mount('#app')