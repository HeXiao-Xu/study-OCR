<script setup lang="ts">
/** 403：角色没有这个页面的权限（前端体验层；接口仍会独立裁决）。 */
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <el-result icon="warning" title="无权访问" sub-title="当前角色没有这个页面的权限。">
    <template #extra>
      <el-button type="primary" @click="router.replace('/')">回概览</el-button>
      <el-button @click="router.back()">返回上一页</el-button>
    </template>
  </el-result>
</template>