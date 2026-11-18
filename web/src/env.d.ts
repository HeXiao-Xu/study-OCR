/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 前缀，例如 http://127.0.0.1:8000/api/v1 或 /api/v1 */
  readonly VITE_API_BASE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
