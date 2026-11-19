<script setup lang="ts">
/**
 * 尚未实现的业务页占位（S2-10/S2-11/S5 等）。
 * 明确写"待实现"，不放假数据——避免看起来能点、点开却是演示数据。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => route.meta.title ?? '功能页')
const plan = computed(() => route.meta.placeholder ?? '后续任务')
</script>

<template>
  <el-result
    icon="info"
    :title="`${title}（待实现）`"
    :sub-title="`该页面计划在 ${plan} 实现；当前版本只完成登录与主框架。`"
  >
    <template #extra>
      <router-link to="/">回概览</router-link>
    </template>
  </el-result>
</template>