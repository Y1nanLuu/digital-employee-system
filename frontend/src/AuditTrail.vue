<script setup lang="ts">
import type { Task } from './api'
defineProps<{ task: Task }>()
const json = (value: unknown) => JSON.stringify(value, null, 2) ?? '无返回内容'
</script>
<template><section><h3>工具调用日志</h3><p v-if="!task.logs.length" class="muted">暂无工具调用记录。</p><article v-for="(log,i) in task.logs" :key="i" class="citation"><b>{{ log.tool }} · {{ log.method }} · {{ log.success ? '成功' : '失败' }}</b><p>{{ log.at }} · 耗时 {{ log.duration_ms }} ms</p><p v-if="log.error" class="field-error">{{ log.error }}</p><details><summary>请求参数</summary><pre>{{ json(log.request) }}</pre></details><details><summary>响应结果</summary><pre>{{ json(log.response) }}</pre></details></article><h3>审批记录</h3><p v-if="!task.approvals.length" class="muted">暂无人工审批记录。</p><article v-for="(record,i) in task.approvals" :key="i" class="citation"><b>{{ record.decision === 'approve' ? '通过并继续' : '拒绝并终止' }} · {{ record.actor }}</b><p>{{ record.comment }}</p><p v-if="record.manual_basis">人工依据：{{ record.manual_basis }}</p><small>{{ record.at }}</small></article></section></template>
<style scoped>pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}summary{cursor:pointer;padding:8px 0}</style>
