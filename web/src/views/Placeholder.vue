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
  <div class="placeholder-page">
    <div class="page-heading">
      <div>
        <h2>{{ title }}</h2>
        <p>该模块在后续任务卡实现。</p>
      </div>
    </div>
    <el-card shadow="never">
      <el-result
        icon="info"
        :title="`${title}（待实现）`"
        :sub-title="`计划在 ${plan} 实现；当前版本只完成登录、上传、票据列表与详情。`"
      />
    </el-card>
  </div>
</template>

<style scoped>
.placeholder-page {
  max-width: 1080px;
  margin: 0 auto;
}
</style>