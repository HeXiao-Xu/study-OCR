<script setup lang="ts">
/** 404 页。路由里用通配符兜住，避免用户手输地址看到白屏。 */
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <el-result icon="warning" title="页面不存在" sub-title="检查一下地址，或者回首页。">
    <template #extra>
      <el-button type="primary" @click="router.replace('/')">回首页</el-button>
    </template>
  </el-result>
</template>
