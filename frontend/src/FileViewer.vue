<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { readFile, request } from './api'
const props = defineProps<{ fileId?: string; filename?: string }>()
const url = ref(''), media = ref(''), error = ref(''), loading = ref(false), zoom = ref(100)
const originalName = ref('票据原件')
const expanded = ref(window.innerWidth > 900)
let generation = 0
function clear() { if (url.value) URL.revokeObjectURL(url.value); url.value = '' }
async function load() {
  const version = ++generation; clear(); error.value = ''; zoom.value = 100
  if (!props.fileId) { loading.value = false; return }
  loading.value = true
  try {
    const [blob, info] = await Promise.all([readFile(props.fileId), request<{ filename: string }>('/files/' + props.fileId)])
    if (version !== generation) return
    originalName.value = info.filename; media.value = blob.type; url.value = URL.createObjectURL(blob)
  } catch (e) { if (version === generation) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (version === generation) loading.value = false }
}
watch(() => props.fileId, load, { immediate: true })
onBeforeUnmount(() => { generation++; clear() })
</script>
<template>
  <section class="original-viewer" :aria-busy="loading">
    <div class="viewer-toolbar"><b>票据原件</b><span>{{ filename || originalName }}</span>
      <template v-if="url"><button type="button" @click="expanded = !expanded" :aria-expanded="expanded">{{ expanded ? '收起预览' : '查看原票' }}</button><button v-if="media !== 'application/pdf' && expanded" type="button" @click="zoom = Math.max(50, zoom - 25)" aria-label="缩小图片">−</button><button v-if="media !== 'application/pdf' && expanded" type="button" @click="zoom = Math.min(300, zoom + 25)" aria-label="放大图片">＋</button><a :href="url" :download="filename || originalName">下载原件</a></template>
    </div>
    <div v-if="loading" class="preview-loading" role="status">正在加载原票，请稍候…</div>
    <p v-else-if="error" role="alert">{{ error }} <button type="button" @click="load">重新加载</button></p>
    <div v-else-if="url && expanded" class="preview-body"><iframe v-if="media === 'application/pdf'" :src="url + '#toolbar=1&view=FitH'" title="发票PDF原件预览"></iframe><img v-else :src="url" alt="已归档票据原件" :style="{ width: zoom + '%', maxWidth: 'none' }"></div>
    <p v-else-if="url">原件已安全保存，需要核对时点击“查看原票”。</p>
    <div v-else class="preview-empty">上传票据后，在这里核对原件与表单。</div>
    <small>原件受登录权限保护；PDF可使用预览工具栏翻页、缩放。</small>
  </section>
</template>
<style scoped>
.original-viewer{border:1px solid #dce4ee;background:#f5f7fb;border-radius:14px;overflow:hidden;min-width:0}.viewer-toolbar{padding:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:white;border-bottom:1px solid #dce4ee}.viewer-toolbar span{font-size:12px;overflow-wrap:anywhere;flex:1}.viewer-toolbar a{color:#2463a8;font-size:13px;min-height:44px;display:flex;align-items:center}.viewer-toolbar button{padding:3px 10px;min-width:44px;min-height:44px}.preview-body{height:min(660px,calc(100vh - 220px));min-height:320px;overflow:auto}.preview-body iframe{height:100%;width:100%;border:0}.preview-body img{display:block;object-fit:contain}.preview-empty,.preview-loading{height:320px;display:grid;place-items:center;color:#526171;padding:30px}.preview-loading{background:#e9eef3}.original-viewer>small,.original-viewer>p{display:block;padding:12px;color:#526171;line-height:1.65}@media(max-width:900px){.preview-body{height:400px}}
</style>
