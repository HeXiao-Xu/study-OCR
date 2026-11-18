import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期**不用** Vite proxy，直接跨域访问后端（CORS 由后端 Settings.cors_origins 统一配置）。
// 理由：跨域只在一处配置，前端不需要知道后端端口；生产同源部署时把 VITE_API_BASE 改成 /api/v1 即可。
// 坑（任务卡）：proxy 与 CORS 二选一，两个都配会出现"看起来是 404、其实是转了两道"的怪问题。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
