<script setup lang="ts">
/**
 * 根路径兜底：路由守卫通常已经把 `/` 重定向到角色工作页；
 * 这里只处理"守卫未命中"的极端情况，不再展示概览/里程碑等开发期内容。
 */
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import { useUserStore } from '../stores/user'
import { landingFor } from '../utils/landing'

const router = useRouter()
const store = useUserStore()

onMounted(() => {
  void router.replace(landingFor(store.role))
})
</script>

<template>
  <div class="landing">
    <span class="spinner" />
    <p>正在进入工作台…</p>
  </div>
</template>

<style scoped>
.landing {
  min-height: 60vh;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 14px;
  color: var(--app-muted);
  font-size: 13px;
}
.spinner {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 2px solid #dbe3ef;
  border-top-color: var(--app-brand);
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>