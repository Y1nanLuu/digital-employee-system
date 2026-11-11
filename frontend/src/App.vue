<script setup lang="ts">
import AuditTrail from './AuditTrail.vue'
import ReviewSummary from './ReviewSummary.vue'
import ReceiptIntake from './ReceiptIntake.vue'
import FileViewer from './FileViewer.vue'
import { computed, reactive, ref, watch } from 'vue'
import { ApiError, request, setCredentials, uploadInvoice, type ApprovalRequest, type Claim, type CorrectedFields, type InvoiceSuggestion, type ParseSuggestion, type Task, type User } from './api'

type View = 'overview' | 'submit' | 'tasks'
interface Metrics {
  total: number
  completed: number
  completion_rate: number | null
  manual_rate: number | null
  average_processing_seconds: number | null
  average_end_to_end_seconds: number | null
}
type Form = Pick<Claim, 'project_id' | 'category' | 'amount' | 'expense_date' | 'description'>
const labels: Record<string, string> = { queued: '待执行', running: '智能初审中', awaiting_approval: '待审批', returned: '已驳回 · 待修改', writing_back: '结果写回中', succeeded: '已通过', rejected: '初审未通过', failed: '处理异常',cancelled:'已撤销' }
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const defaultForm: Form = { project_id: 'P001', category: '交通费', amount: '', expense_date: today, description: '' }
const username = ref('employee'), password = ref('demo123'), user = ref<User | null>(null)
const tasks = ref<Task[]>([]), selected = ref<Task | null>(null), metrics = ref<Metrics | null>(null)
const view = ref<View>('overview'), busy = ref(false), loadingTasks = ref(false), loadingDetail = ref(false), error = ref(''), success = ref('')
const metricsError = ref('')
const editTask = ref<Task | null>(null)
const rejectDialog = ref<HTMLDialogElement | null>(null)
const rejectTarget = ref('')
function askReject() { if (!selected.value || !comment.value.trim()) return; rejectTarget.value = selected.value.id; rejectDialog.value?.showModal() }
function confirmReject() { rejectDialog.value?.close(); if (selected.value?.id === rejectTarget.value) approve('return') }
const withdrawDialog = ref<HTMLDialogElement | null>(null), withdrawTarget = ref('')
function askWithdraw() { if (selected.value) { withdrawTarget.value = selected.value.id; withdrawDialog.value?.showModal() } }
function confirmWithdraw() { withdrawDialog.value?.close(); if (selected.value?.id !== withdrawTarget.value) return; run(async()=>{ selected.value = await request<Task>('/tasks/'+withdrawTarget.value+'/withdraw','POST'); await refresh(); success.value='申请已撤销，历史材料继续保留。' }) }
function beginResubmit() { if (selected.value?.status === 'returned' && !busy.value) { editTask.value = selected.value; view.value = 'submit' } }
async function receiptSubmitted(task: Task) { selected.value = task; await refresh() }
const note = ref<ParseSuggestion | null>(null)
const invoiceNote = ref<InvoiceSuggestion | null>(null)
const uploading = ref(false)
const parsing = ref(false)
let parseController: AbortController | null = null
const confirmed = ref(false), comment = ref(''), basis = ref(''), query = ref(''), statusFilter = ref('')
const fromDate = ref(''), toDate = ref(''), sortOrder = ref('newest')
const conflictAcknowledged = ref(false), confirmationNote = ref(''), reviewedFields = ref(false), correcting = ref(false), correctionReason = ref('')
const form = reactive<Form>({ ...defaultForm }), correction = reactive<Form>({ ...defaultForm }), touched = reactive<Record<string, boolean>>({})
const pending = ref<{ key: string; payload: Claim } | null>(null)
let parseVersion = 0, detailVersion = 0, sessionVersion = 0
const isEmployee = computed(() => user.value?.role === 'employee')
const storageKey = (kind: string) => 'employee-' + kind + ':' + user.value?.username
const filteredTasks = computed(() => tasks.value.filter(task => {
  const needle = query.value.trim().toLowerCase()
  const day=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date(task.created_at))
  const matchesStatus=!statusFilter.value || (statusFilter.value==='awaiting_approval' ? ['queued','running','awaiting_approval','writing_back'].includes(task.status) : task.status === statusFilter.value)
  return (!fromDate.value || day >= fromDate.value) && (!toDate.value || day <= toDate.value) && matchesStatus && (!needle || (task.id + ' ' + task.business_no + ' ' + task.payload.category + ' ' + task.payload.description + ' ' + task.payload.project_id).toLowerCase().includes(needle))
}).sort((a,b)=>sortOrder.value==='oldest' ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at)))
watch(filteredTasks,list=>{ if(selected.value && !list.some(t=>t.id===selected.value?.id)){selected.value=null;resetApproval()} })
function taskNo(task:Task) { return task.business_no || task.id }
function clearFilters() { query.value='';statusFilter.value='';fromDate.value='';toDate.value='';sortOrder.value='newest' }
const displayName = computed(() => user.value?.name || user.value?.username || '')
const summary = computed(() => ({ total: tasks.value.length, active: tasks.value.filter(t => ['queued','running','writing_back'].includes(t.status)).length, pending: tasks.value.filter(t => t.status === 'awaiting_approval').length, done: tasks.value.filter(t => ['succeeded','rejected','failed'].includes(t.status)).length }))
const errors = computed(() => ({
  amount: !/^\d+(?:\.\d{1,2})?$/.test(form.amount) || Number(form.amount) <= 0 || Number(form.amount) > 5000 ? '请输入大于0且不超过5000的普通十进制金额，最多两位小数' : '',
  expense_date: !/^\d{4}-\d{2}-\d{2}$/.test(form.expense_date) || form.expense_date > today ? '请选择合法且不晚于今天的日期' : '',
  description: !form.description.trim() || form.description.trim().length > 500 ? '说明须为1至500字符' : ''
}))
const formValid = computed(() => Object.values(errors.value).every(value => !value))
function money(value: string) { return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(Number(value)) }
function dateTime(value: string) { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
function resultText(task: Task) { return (task.status === 'returned' ? task.result.return_reason || '请按退回意见修改后重新提交。' : '') + (task.status === 'failed' && task.result.decision ? '审核决定已产生，结果写回尚未确认。' : '') + (task.result.error || task.result.reason || labels[task.status]) }
function resultDecision(task: Task) { return task.result.decision || '' }
function markAllTouched() { Object.assign(touched, { amount: true, expense_date: true, description: true }) }
function navigate(next: View) { if (busy.value || loadingDetail.value) return; view.value = next; if (next === 'submit') editTask.value = null; error.value = ''; success.value = ''; if (next !== 'tasks') query.value = '' }
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; success.value = ''
  try { await action() } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}
async function refresh() {
  loadingTasks.value = true
  const generation = sessionVersion
  try {
    const list = await request<Task[]>('/tasks')
    if (generation !== sessionVersion) return
    tasks.value = list
    if (selected.value && !loadingDetail.value) selected.value = list.find(t => t.id === selected.value?.id) || null
    if (user.value?.role !== 'employee') await refreshMetrics()
  } finally { loadingTasks.value = false }
}
async function refreshMetrics() {
  metricsError.value = ''
  try {
    metrics.value = await request<Metrics>('/metrics')
  } catch (e) {
    metrics.value = null
    metricsError.value = e instanceof Error ? e.message : String(e)
  }
}
function resetApproval() { comment.value = ''; basis.value = ''; correcting.value = false; reviewedFields.value = false; correctionReason.value = '' }
function login() { run(async () => {
  setCredentials(username.value, password.value)
  const identity = await request<User>('/me')
  sessionVersion++; user.value = identity; view.value = 'tasks'; selected.value = null; note.value = null
  clearFilters();statusFilter.value=identity.role==='employee' ? '' : 'awaiting_approval'
  const draftText = localStorage.getItem(storageKey('claim-draft'))
  Object.assign(form, defaultForm); pending.value = null
  if (identity.role === 'employee') {
    try {
      const draft = JSON.parse(draftText || 'null')
      if (draft) Object.assign(form, draft)
      pending.value = JSON.parse(localStorage.getItem(storageKey('pending')) || 'null')
    } catch { localStorage.removeItem(storageKey('pending')) }
  }
  confirmed.value = false
  await refresh()
  if (identity.role !== 'employee') { selected.value=tasks.value.find(t=>t.status==='awaiting_approval') || null; resetApproval() }
}) }
function logout() {
  if (busy.value) return
  sessionVersion++; detailVersion++; cancelParsing(); loadingDetail.value = false
  user.value = null; tasks.value = []; selected.value = null; metrics.value = null; metricsError.value = ''; pending.value = null
  setCredentials('', ''); error.value = ''; success.value = ''; note.value = null
  Object.assign(form, defaultForm); resetApproval()
}
function cancelParsing() { parseVersion++; parseController?.abort(); parseController = null; parsing.value = false }
async function parse() {
  if (busy.value || parsing.value) return
  if (!form.description.trim()) { error.value = '请先填写报销说明'; return }
  const description = form.description.trim(), version = ++parseVersion
  const controller = new AbortController()
  parseController = controller; parsing.value = true; note.value = null; error.value = ''
  try {
    const response = await request<ParseSuggestion>('/parse', 'POST', { description }, undefined, controller.signal)
    if (version === parseVersion && description === form.description.trim()) note.value = response
  } catch (e) {
    if (version === parseVersion) error.value = e instanceof Error ? e.message : String(e)
  } finally { if (version === parseVersion) { parsing.value = false; parseController = null } }
}
async function uploadPdf(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!/\.pdf$/i.test(file.name)) { error.value = '只支持 PDF 格式的电子发票'; return }
  if (file.size > 5 * 1024 * 1024) { error.value = '发票文件超过 5 MB，请压缩后重试'; return }
  uploading.value = true; error.value = ''; success.value = ''; invoiceNote.value = null
  try {
    const response = await uploadInvoice(file)
    invoiceNote.value = response
    note.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally { uploading.value = false }
}
function fillFromInvoice() {
  const suggestion = invoiceNote.value
  if (!suggestion) return
  if (suggestion.suggested_amount) form.amount = suggestion.suggested_amount
  if (suggestion.suggested_category) form.category = suggestion.suggested_category
  const hints = suggestion.invoice?.hints || {}
  if (hints.expense_date && /^\d{4}-\d{2}-\d{2}$/.test(hints.expense_date) && hints.expense_date <= today) form.expense_date = hints.expense_date
  if (!form.description.trim()) {
    const parts = [
      hints.invoice_no ? `发票号 ${hints.invoice_no}` : '',
      suggestion.suggested_amount ? `金额 ${suggestion.suggested_amount} 元` : '',
      suggestion.suggested_category || ''
    ].filter(Boolean)
    if (parts.length) form.description = parts.join('，') + '，由发票 PDF 提取'
  }
  confirmed.value = false
}
async function sendSubmission(payload: Claim, key: string) {
  pending.value = { payload, key }
  localStorage.setItem(storageKey('pending'), JSON.stringify(pending.value))
  let task: Task
  try { task = await request<Task>('/tasks', 'POST', payload, key) }
  catch (e) {
    if (e instanceof ApiError && [400, 401, 403, 404, 422].includes(e.status)) {
      pending.value = null; localStorage.removeItem(storageKey('pending'))
    }
    throw e
  }
  selected.value = task
  localStorage.removeItem(storageKey('pending')); pending.value = null
  Object.assign(form, defaultForm); confirmed.value = false; note.value = null; invoiceNote.value = null
  Object.keys(touched).forEach(key => delete touched[key])
  localStorage.removeItem(storageKey('claim-draft'))
  await refresh(); view.value = 'tasks'; success.value = '报销已提交，任务编号 ' + task.id
}
function submit() { run(async () => {
  if (parsing.value) cancelParsing()
  markAllTouched()
  if (!formValid.value) throw new Error('请修正表单中的错误后再提交')
  if (!confirmed.value) throw new Error('请核对字段并勾选确认')
  const payload: Claim = { ...form, amount: Number(form.amount).toFixed(2), description: form.description.trim(), confirmed: true,
    ...(invoiceNote.value?.invoice_id ? { invoice_id: invoiceNote.value.invoice_id } : {}),
    ...(!invoiceNote.value && note.value?.parse_id ? { parse_id: note.value.parse_id } : {}),
    ...(conflictAcknowledged.value ? { conflict_acknowledged: true, confirmation_note: confirmationNote.value.trim() } : {}) }
  if (conflictAcknowledged.value && !confirmationNote.value.trim()) throw new Error('请说明最终申报金额或类别的确认依据')
  if (pending.value && JSON.stringify(pending.value.payload) !== JSON.stringify(payload)) throw new Error('上次提交结果尚未确认，请先重试原申请或查询任务列表，再明确发起新申请')
  await sendSubmission(payload, pending.value?.key || crypto.randomUUID())
}) }
function retryPending() { run(async () => { if (pending.value) await sendSubmission(pending.value.payload, pending.value.key) }) }
function startNewApplication() { pending.value = null; localStorage.removeItem(storageKey('pending')); success.value = '已转为新申请，请重新核对字段后提交'; confirmed.value = false }
function approve(decision: 'approve' | 'reject' | 'return') { run(async () => {
  if (!selected.value || loadingDetail.value) throw new Error('请等待详情加载')
  if (user.value?.role !== 'approver' || selected.value.status !== 'awaiting_approval') throw new Error('当前任务不可审批')
  if (!comment.value.trim()) throw new Error('请填写审批或退回意见')
  if (decision === 'approve' && !reviewedFields.value) throw new Error('请确认已核对原件、字段和制度')
  const target = selected.value.id
  const payload: ApprovalRequest = { decision, comment: comment.value.trim(), manual_basis: basis.value.trim(), reviewed_fields: reviewedFields.value }
  try {
    const result = await request<Task>('/tasks/' + target + '/approval', 'POST', payload)
    selected.value = result; tasks.value = tasks.value.map(t => t.id === target ? result : t); resetApproval()
    if (result.status === 'failed') error.value = resultText(result)
    else success.value = '决定已记录，当前状态：' + labels[result.status]
    await refreshMetrics()
  } catch (e) { if (e instanceof ApiError && e.status === 409) await refresh(); throw e }
}) }
async function choose(task: Task) {
  if (busy.value || loadingDetail.value) return
  view.value = 'tasks'
  const version = ++detailVersion
  selected.value = null; resetApproval(); loadingDetail.value = true; error.value = ''
  try {
    const detail = await request<Task>('/tasks/' + task.id)
    if (version === detailVersion) { selected.value = detail; Object.assign(correction, detail.payload) }
  } catch (e) { if (version === detailVersion) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (version === detailVersion) loadingDetail.value = false }
}
function percent(value: number | null | undefined) { return value == null ? '暂无数据' : (value * 100).toFixed(1) + '%' }
function seconds(value: number | null | undefined) { return value == null ? '暂无数据' : value.toFixed(2) + ' 秒' }
watch(form, value => {
  confirmed.value = false; conflictAcknowledged.value = false; confirmationNote.value = ''
  if (user.value?.role === 'employee') localStorage.setItem(storageKey('claim-draft'), JSON.stringify(value))
}, { deep: true, flush: 'sync' })
watch(() => form.description, () => { cancelParsing(); note.value = null }, { flush: 'sync' })
</script>

<template>
  <div v-if="!user" class="login-shell"><section class="login-card"><div class="brand large"><span>智</span><div><b>智报</b><small>数字员工费用工作台</small></div></div><div class="login-copy"><p class="eyebrow">EXPENSE ASSISTANT</p><h1>欢迎回来</h1><p>登录后提交报销、追踪初审进度并查看制度依据。</p></div><p v-if="error" role="alert" class="error">{{ error }}</p><form @submit.prevent="login" class="login-form"><label>演示身份<select v-model="username"><option value="employee">员工 · 报销申请</option><option value="approver">审批人 · 人工复核</option><option value="admin">管理员 · 运行指标</option></select></label><label>密码<input v-model="password" type="password" required autocomplete="current-password"></label><button class="primary full" :disabled="busy">{{ busy ? '正在登录…' : '进入工作台' }}</button></form><p class="login-hint">本地默认密码 demo123。本系统使用模拟制度与预算，不执行真实资金支付。</p></section></div>
  <div v-else class="app-shell"><aside class="sidebar"><div class="brand"><span>智</span><div><b>智报</b><small>数字员工工作台</small></div></div><nav><button v-if="isEmployee" :disabled="busy || loadingDetail" :class="{ active: view === 'submit' }" @click="navigate('submit')"><i>＋</i>发起报销</button><button v-if="!isEmployee" :disabled="busy || loadingDetail" :class="{ active: view === 'overview' }" @click="navigate('overview')"><i>◫</i>运行指标</button><button :disabled="busy || loadingDetail" :class="{ active: view === 'tasks' }" @click="navigate('tasks')"><i>☷</i>{{ isEmployee ? '我的报销' : '审批工作台' }}</button></nav><div class="sidebar-note"><i></i><span>费用报销初审</span><small>制度版本以任务依据为准</small></div></aside>
    <main class="workspace"><header class="topbar"><div><small>数字员工工作台</small><b> / {{ view === 'overview' ? '运行指标' : view === 'submit' ? '发起报销' : isEmployee ? '我的报销' : '审批工作台' }}</b></div><div class="account"><span>{{ displayName.slice(0, 1) }}</span><div><b>{{ displayName }}</b><small>{{ user.role === 'employee' ? '员工' : user.role === 'approver' ? '审批人' : '管理员' }}</small></div><button @click="logout" :disabled="busy">退出</button></div></header><p v-if="error" role="alert" class="error page-error">{{ error }}<button @click="error = ''" aria-label="关闭">×</button></p><p v-if="success" role="status" class="success page-error">{{ success }}<button @click="success = ''" aria-label="关闭">×</button></p>
      <div v-if="view === 'overview'" class="page"><section class="hero"><div><p class="eyebrow">EXPENSE ASSISTANT</p><h1>{{ isEmployee ? `下午好，${displayName}` : `${displayName}工作台` }}</h1><p>{{ isEmployee ? '数字员工会核对制度、预算和字段信息，并给出可追溯的初审意见。' : '查看真实任务、审批记录和系统运行指标。' }}</p></div><button v-if="isEmployee" class="primary" @click="navigate('submit')">＋ 发起一笔报销</button></section>
        <section v-if="isEmployee" class="stats"><article><i>◇</i><div><strong>{{ summary.total }}</strong><small>累计提交</small></div></article><article><i>◷</i><div><strong>{{ summary.pending }}</strong><small>待人工审批</small></div></article><article><i>◎</i><div><strong>{{ summary.active }}</strong><small>处理中</small></div></article><article><i>✓</i><div><strong>{{ summary.done }}</strong><small>已完成</small></div></article></section>
        <section v-else-if="metrics" class="stats"><article><i>◇</i><div><strong>{{ metrics.total }}</strong><small>任务总数</small></div></article><article><i>✓</i><div><strong>{{ percent(metrics.completion_rate) }}</strong><small>技术执行完成率</small></div></article><article><i>◷</i><div><strong>{{ percent(metrics.manual_rate) }}</strong><small>转人工率</small></div></article><article><i>◎</i><div><strong>{{ seconds(metrics.average_processing_seconds) }}</strong><small>平均系统耗时</small></div></article></section>
        <section v-if="!isEmployee" class="panel form-section"><div class="section-title"><h2>指标口径</h2><button class="secondary" @click="run(refreshMetrics)" :disabled="busy">刷新指标</button></div><p v-if="metricsError" class="error" role="alert">指标加载失败：{{ metricsError }}</p><p>技术执行完成率：已通过或已拒绝任务 / 全部任务；业务拒绝计为完成。</p><p>转人工率：曾转人工任务 / 全部任务。</p><p>平均系统耗时：已通过或已拒绝任务的平均耗时，扣除人工等待。无样本显示暂无数据。</p><p v-if="metrics">平均端到端耗时（含人工等待）：{{ seconds(metrics.average_end_to_end_seconds) }}</p><small>统计范围为系统内全部任务，包含当前待处理任务。审批提交后会刷新指标。</small></section><div class="section-title"><div><h2>最近任务</h2><p>跟踪报销从提交到最终处理的全过程</p></div><button class="text" @click="navigate('tasks')">查看全部 →</button></div><section class="panel table-panel"><table><thead><tr><th>任务编号</th><th>报销事项</th><th>金额</th><th>提交时间</th><th>当前状态</th><th></th></tr></thead><tbody><tr v-for="task in tasks.slice(0, 5)" :key="task.id"><td class="mono">{{ taskNo(task) }}</td><td><b>{{ task.payload.category }}</b><small>{{ task.payload.description }}</small></td><td>{{ money(task.payload.amount) }}</td><td>{{ dateTime(task.created_at) }}</td><td><span class="status" :class="task.status">{{ labels[task.status] || task.status }}</span></td><td><button class="text" @click="choose(task); navigate('tasks')">查看详情 →</button></td></tr></tbody></table><div v-if="!tasks.length" class="empty"><b>暂无报销任务</b><p>{{ isEmployee ? '发起一笔报销后，任务会显示在这里。' : '员工提交报销后，任务会显示在这里。' }}</p></div></section>
      </div>
      <ReceiptIntake v-else-if="view === 'submit' && isEmployee" :key="editTask?.id || 'new'" :department="user.department" :owner="user.username" :edit-task="editTask" @submitted="receiptSubmitted" @cancel="navigate('tasks')" />
      <div v-else class="page"><div class="page-title"><div><div><h1>{{ isEmployee ? '我的报销' : '待审批工作台' }}</h1><p>选择申请即可核对原件和初审意见；审批操作显示在详情顶部。</p></div></div><div class="title-actions"><button class="secondary" @click="run(refresh)" :disabled="busy || loadingTasks">{{ loadingTasks ? '刷新中…' : '刷新' }}</button></div></div><section class="panel filters"><label>⌕<input v-model="query" aria-label="搜索报销" placeholder="搜索报销编号、项目或说明"></label><select v-model="statusFilter" aria-label="报销状态"><option value="">全部状态</option><option value="succeeded">已通过</option><option value="awaiting_approval">待审批</option><option value="returned">驳回待修改</option></select><label>提交起日<input v-model="fromDate" type="date" :max="toDate || today"></label><label>提交止日<input v-model="toDate" type="date" :min="fromDate" :max="today"></label><select v-model="sortOrder" aria-label="时间排序"><option value="newest">最新提交在前</option><option value="oldest">最早提交在前</option></select><button class="secondary" @click="clearFilters">清除筛选</button></section><div v-if="!isEmployee" class="approval-shortcuts"><button @click="statusFilter='awaiting_approval'">待审批 {{ summary.pending }}</button><button @click="statusFilter='succeeded'">已通过</button><button @click="statusFilter='returned'">驳回待修改</button><button @click="statusFilter=''">全部申请</button></div><div class="task-layout"><section class="task-list"><button :disabled="busy || loadingDetail" v-for="task in filteredTasks" :key="task.id" class="task-card" :class="{ active: selected?.id === task.id }" @click="choose(task)"><div><span class="mono">{{ taskNo(task) }}</span><h3>{{ task.payload.category }} · {{ money(task.payload.amount) }}</h3><p>{{ task.payload.description }}</p></div><div><small>{{ task.payload.project_id }} · {{ dateTime(task.created_at) }}</small><span class="status" :class="task.status">{{ labels[task.status] || task.status }}</span></div></button><div v-if="!filteredTasks.length" class="panel empty"><b>{{ loadingTasks ? '正在加载任务…' : tasks.length ? '没有匹配的任务' : '暂无报销任务' }}</b><p v-if="!loadingTasks">{{ tasks.length ? '尝试更换关键词或状态筛选。' : isEmployee ? '提交报销后即可在这里查看进度。' : '员工提交报销后即可在这里查看。' }}</p></div></section>
          <section v-if="selected" class="panel detail" :aria-busy="loadingDetail"><p v-if="loadingDetail" class="detail-loading">正在加载最新任务详情…</p><div class="detail-title"><div><p class="eyebrow">TASK DETAIL</p><h2>{{ selected.payload.category }} · {{ money(selected.payload.amount) }}</h2><span class="mono">{{ taskNo(selected) }}</span></div><span class="status" :class="selected.status">{{ labels[selected.status] }}</span></div><form v-if="!isEmployee && selected.status === 'awaiting_approval'" class="approval approval-priority" @submit.prevent="approve('approve')"><h3>审批这笔报销</h3><p>请核对下方初审摘要和原件后作出决定。</p><label class="check"><input v-model="reviewedFields" type="checkbox">我已核对当前有效字段及模型异常，确认按这些字段审批</label><label>审批意见 *<textarea v-model="comment" required maxlength="500" placeholder="通过请填写核对意见；驳回请写清需要员工修改的内容"></textarea></label><details><summary>需要补充人工制度依据？</summary><label>人工依据<textarea v-model="basis" maxlength="1000"></textarea></label></details><div class="form-actions"><button class="primary" :disabled="busy || loadingDetail || !comment.trim() || !reviewedFields">通过并继续</button><button class="danger" type="button" @click="askReject" :disabled="busy || loadingDetail || !comment.trim()">驳回申请</button></div><small>驳回后员工可以修改重提或自行撤销，原材料和处理历史保留。</small></form><dl><div><dt>项目编号</dt><dd>{{ selected.payload.project_id }}</dd></div><div><dt>费用日期</dt><dd>{{ selected.payload.expense_date }}</dd></div><div><dt>提交时间</dt><dd>{{ dateTime(selected.created_at) }}</dd></div><div><dt>处理方式</dt><dd>{{ selected.manual ? '已转人工' : '自动初审' }}</dd></div><div class="wide"><dt>报销说明</dt><dd>{{ selected.payload.description }}</dd></div></dl><ReviewSummary :key="selected.id" :task="selected" /><button v-if="isEmployee && selected.status === 'returned'" class="primary" @click="beginResubmit">修改材料并重新提交</button><button v-if="isEmployee && (['returned','awaiting_approval'].includes(selected.status) || selected.status==='failed' && !selected.result.receipt)" class="secondary" @click="askWithdraw" :disabled="busy">撤销申请</button><p v-if="isEmployee && selected.status==='returned'" class="return-actions-note">请根据驳回原因补充材料后重提；决定放弃本次报销时，可选择撤销。</p><FileViewer v-if="selected.payload.file_id" :file-id="selected.payload.file_id" /><details class="policy-details"><summary>查看本次引用的 {{ selected.citations.length }} 条制度依据</summary><h3>判定依据</h3><p v-if="!selected.citations.length" class="muted">暂无适用制度依据，需要人工确认。</p><article v-for="rule in selected.citations" :key="rule.id" class="citation"><b>{{ rule.id }}</b><p>{{ rule.text }}</p><small>{{ rule.source }} · {{ rule.version }}</small></article></details>
            <h3>执行时间线</h3><p v-if="!selected.events.length" class="muted">暂无执行事件。</p><ol v-else class="timeline" aria-label="按执行顺序排列的任务事件"><li v-for="(item, i) in selected.events" :key="i"><b>{{ labels[item.status] || item.status }}</b><p>{{ item.message }}</p><small>{{ dateTime(item.at) }}</small></li></ol><details class="audit-details"><summary>查看审批历史和完整处理记录</summary><AuditTrail :key="selected.id" :task="selected" /></details></section><section v-else class="panel detail-placeholder"><span>◇</span><b>请选择一项任务</b><p>这里将显示初审意见、制度依据和执行时间线。</p></section></div></div>
    <dialog ref="rejectDialog" class="decision-dialog" aria-labelledby="reject-title"><h2 id="reject-title">确认驳回这笔申请？</h2><p>驳回原因会通知员工。员工可以修改材料重新提交，也可以撤销本次申请。</p><div class="form-actions"><button type="button" class="secondary" autofocus @click="rejectDialog?.close()">返回继续核对</button><button type="button" class="danger" @click="confirmReject">确认驳回</button></div></dialog>
    <dialog ref="withdrawDialog" class="decision-dialog" aria-labelledby="withdraw-title"><h2 id="withdraw-title">确认撤销这笔申请？</h2><p>撤销后本次申请停止处理，原件和历史记录继续保留。需要报销时须重新发起申请。</p><div class="form-actions"><button class="secondary" autofocus @click="withdrawDialog?.close()">继续保留申请</button><button class="danger" @click="confirmWithdraw">确认撤销</button></div></dialog>
    </main>
  </div>
</template>
