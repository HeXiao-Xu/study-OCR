import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import { TOKEN_KEY } from './stores/user'

// ---------------------------------------------------------------------------
// 开发态冒烟入口：`/?dev_token=xxx` 写入占位 token 后把参数从地址栏抹掉。
// 用途：无人值守的页面自检（S7 冷启动检查、演示前冒烟）需要直接打开受保护页面。
// `import.meta.env.DEV` 是编译期常量，生产构建时整段会被摇掉（见 DECISIONS D-021）。
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

app.mount('#app')
