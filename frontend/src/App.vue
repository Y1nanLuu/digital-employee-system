<script setup lang="ts">
import AuditTrail from './AuditTrail.vue'
import { computed, reactive, ref, watch } from 'vue'
import { ApiError, request, setCredentials, type ApprovalRequest, type Claim, type CorrectedFields, type ParseSuggestion, type Task, type User } from './api'

type View = 'overview' | 'submit' | 'tasks'
type Metrics = Record<string, number | null>
type Form = Pick<Claim, 'project_id' | 'category' | 'amount' | 'expense_date' | 'description'>
const labels: Record<string, string> = { queued: '待执行', running: '智能初审中', awaiting_approval: '待人工审批', writing_back: '结果写回中', succeeded: '初审通过', rejected: '初审拒绝', failed: '处理异常' }
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const defaultForm: Form = { project_id: 'P001', category: '交通费', amount: '', expense_date: today, description: '' }
const username = ref('employee'), password = ref('demo123'), user = ref<User | null>(null)
const tasks = ref<Task[]>([]), selected = ref<Task | null>(null), metrics = ref<Metrics | null>(null)
const view = ref<View>('overview'), busy = ref(false), loadingTasks = ref(false), loadingDetail = ref(false), error = ref(''), success = ref('')
const note = ref<ParseSuggestion | null>(null)
const parsing = ref(false)
let parseController: AbortController | null = null
const confirmed = ref(false), comment = ref(''), basis = ref(''), query = ref(''), statusFilter = ref('')
const conflictAcknowledged = ref(false), confirmationNote = ref(''), reviewedFields = ref(false), correcting = ref(false), correctionReason = ref('')
const form = reactive<Form>({ ...defaultForm }), correction = reactive<Form>({ ...defaultForm }), touched = reactive<Record<string, boolean>>({})
const pending = ref<{ key: string; payload: Claim } | null>(null)
let parseVersion = 0, detailVersion = 0, sessionVersion = 0
const isEmployee = computed(() => user.value?.role === 'employee')
const storageKey = (kind: string) => 'employee-' + kind + ':' + user.value?.username
const filteredTasks = computed(() => tasks.value.filter(task => {
  const needle = query.value.trim().toLowerCase()
  return (!statusFilter.value || task.status === statusFilter.value) && (!needle || (task.id + ' ' + task.payload.category + ' ' + task.payload.description + ' ' + task.payload.project_id).toLowerCase().includes(needle))
}))
const displayName = computed(() => user.value?.name || user.value?.username || '')
const summary = computed(() => ({ total: tasks.value.length, active: tasks.value.filter(t => ['queued','running','writing_back'].includes(t.status)).length, pending: tasks.value.filter(t => t.status === 'awaiting_approval').length, done: tasks.value.filter(t => ['succeeded','rejected','failed'].includes(t.status)).length }))
const errors = computed(() => ({
  amount: !/^\d+(?:\.\d{1,2})?$/.test(form.amount) || Number(form.amount) <= 0 || Number(form.amount) > 5000 ? '请输入大于0且不超过5000的普通十进制金额，最多两位小数' : '',
  expense_date: !/^\d{4}-\d{2}-\d{2}$/.test(form.expense_date) || form.expense_date > today ? '请选择合法且不晚于今天的日期' : '',
  description: !form.description.trim() || form.description.trim().length > 500 ? '说明须为1至500字符' : ''
}))
const formValid = computed(() => Object.values(errors.value).every(value => !value))
function money(value: string) { return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(Number(value)) }
function dateTime(value: string) { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
function resultText(task: Task) { return (task.status === 'failed' && task.result.decision ? '审核决定已产生，结果写回尚未确认。' : '') + (task.result.error || task.result.reason || labels[task.status]) }
function resultDecision(task: Task) { return task.result.decision || '' }
function markAllTouched() { Object.assign(touched, { amount: true, expense_date: true, description: true }) }
function navigate(next: View) { if (busy.value || loadingDetail.value) return; view.value = next; error.value = ''; success.value = ''; if (next !== 'tasks') query.value = '' }
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
    if (user.value?.role !== 'employee') metrics.value = await request<Metrics>('/metrics')
  } finally { loadingTasks.value = false }
}
function resetApproval() { comment.value = ''; basis.value = ''; correcting.value = false; reviewedFields.value = false; correctionReason.value = '' }
function login() { run(async () => {
  setCredentials(username.value, password.value)
  const identity = await request<User>('/me')
  sessionVersion++; user.value = identity; view.value = 'overview'; selected.value = null; note.value = null
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
}) }
function logout() {
  if (busy.value) return
  sessionVersion++; detailVersion++; cancelParsing(); loadingDetail.value = false
  user.value = null; tasks.value = []; selected.value = null; metrics.value = null; pending.value = null
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
  Object.assign(form, defaultForm); confirmed.value = false; note.value = null
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
    ...(note.value?.parse_id ? { parse_id: note.value.parse_id } : {}),
    ...(conflictAcknowledged.value ? { conflict_acknowledged: true, confirmation_note: confirmationNote.value.trim() } : {}) }
  if (conflictAcknowledged.value && !confirmationNote.value.trim()) throw new Error('请说明最终申报金额或类别的确认依据')
  if (pending.value && JSON.stringify(pending.value.payload) !== JSON.stringify(payload)) throw new Error('上次提交结果尚未确认，请先重试原申请或查询任务列表，再明确发起新申请')
  await sendSubmission(payload, pending.value?.key || crypto.randomUUID())
}) }
function retryPending() { run(async () => { if (pending.value) await sendSubmission(pending.value.payload, pending.value.key) }) }
function startNewApplication() { pending.value = null; localStorage.removeItem(storageKey('pending')); success.value = '已转为新申请，请重新核对字段后提交'; confirmed.value = false }
function approve(decision: 'approve' | 'reject') { run(async () => {
  if (!selected.value || loadingDetail.value) throw new Error('请等待任务详情加载完成')
  const target = selected.value.id, generation = detailVersion
  if (!comment.value.trim()) throw new Error('请填写审批意见')
  let corrected: CorrectedFields | undefined
  if (decision === 'reject' && correcting.value) throw new Error('拒绝时请先取消字段补正')
  if (decision === 'approve' && correcting.value) {
    corrected = {}
    for (const key of ['project_id', 'category', 'amount', 'expense_date', 'description'] as const) {
      if (correction[key] !== selected.value.payload[key]) Object.assign(corrected, { [key]: correction[key] })
    }
    if (!Object.keys(corrected).length) throw new Error('尚未修改任何业务字段')
    if (!correctionReason.value.trim()) throw new Error('请填写补正原因')
  }
  if (decision === 'approve' && (corrected || selected.value.parse_snapshot.requires_manual_review) && !reviewedFields.value) throw new Error('请显式确认已核对字段')
  const payload: ApprovalRequest = { decision, comment: comment.value.trim(), manual_basis: basis.value.trim(), reviewed_fields: reviewedFields.value,
    ...(corrected ? { corrected_fields: corrected, correction_reason: correctionReason.value.trim() } : {}) }
  const result = await request<Task>('/tasks/' + target + '/approval', 'POST', payload)
  if (generation === detailVersion && selected.value?.id === target) { selected.value = result; resetApproval() }
  await refresh()
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
  <div v-else class="app-shell"><aside class="sidebar"><div class="brand"><span>智</span><div><b>智报</b><small>数字员工工作台</small></div></div><nav><button :disabled="busy || loadingDetail" :class="{ active: view === 'overview' }" @click="navigate('overview')"><i>◫</i>工作概览</button><button v-if="isEmployee" :disabled="busy || loadingDetail" :class="{ active: view === 'submit' }" @click="navigate('submit')"><i>＋</i>发起报销</button><button :disabled="busy || loadingDetail" :class="{ active: view === 'tasks' }" @click="navigate('tasks')"><i>☷</i>{{ isEmployee ? '我的任务' : '任务与审批' }}</button></nav><div class="sidebar-note"><i></i><span>费用报销初审</span><small>制度版本以任务依据为准</small></div></aside>
    <main class="workspace"><header class="topbar"><div><small>数字员工工作台</small><b> / {{ view === 'overview' ? '工作概览' : view === 'submit' ? '发起报销' : isEmployee ? '我的任务' : '任务与审批' }}</b></div><div class="account"><span>{{ displayName.slice(0, 1) }}</span><div><b>{{ displayName }}</b><small>{{ user.role === 'employee' ? '员工' : user.role === 'approver' ? '审批人' : '管理员' }}</small></div><button @click="logout" :disabled="busy">退出</button></div></header><p v-if="error" role="alert" class="error page-error">{{ error }}<button @click="error = ''" aria-label="关闭">×</button></p><p v-if="success" role="status" class="success page-error">{{ success }}<button @click="success = ''" aria-label="关闭">×</button></p>
      <div v-if="view === 'overview'" class="page"><section class="hero"><div><p class="eyebrow">EXPENSE ASSISTANT</p><h1>{{ isEmployee ? `下午好，${displayName}` : `${displayName}工作台` }}</h1><p>{{ isEmployee ? '数字员工会核对制度、预算和字段信息，并给出可追溯的初审意见。' : '查看真实任务、审批记录和系统运行指标。' }}</p></div><button v-if="isEmployee" class="primary" @click="navigate('submit')">＋ 发起一笔报销</button></section>
        <section v-if="isEmployee" class="stats"><article><i>◇</i><div><strong>{{ summary.total }}</strong><small>累计提交</small></div></article><article><i>◷</i><div><strong>{{ summary.pending }}</strong><small>待人工审批</small></div></article><article><i>◎</i><div><strong>{{ summary.active }}</strong><small>处理中</small></div></article><article><i>✓</i><div><strong>{{ summary.done }}</strong><small>已完成</small></div></article></section>
        <section v-else-if="metrics" class="stats"><article><i>◇</i><div><strong>{{ metrics.total }}</strong><small>任务总数</small></div></article><article><i>✓</i><div><strong>{{ percent(metrics.completion_rate) }}</strong><small>技术执行完成率</small></div></article><article><i>◷</i><div><strong>{{ percent(metrics.manual_rate) }}</strong><small>转人工率</small></div></article><article><i>◎</i><div><strong>{{ seconds(metrics.average_processing_seconds) }}</strong><small>平均系统耗时</small></div></article></section>
        <section v-if="!isEmployee" class="panel form-section"><h2>指标口径</h2><p>技术执行完成率：已通过或已拒绝任务 / 全部任务；业务拒绝计为完成。</p><p>转人工率：曾转人工任务 / 全部任务。</p><p>平均系统耗时：已通过或已拒绝任务的平均耗时，扣除人工等待。无样本显示暂无数据。</p><p v-if="metrics">平均端到端耗时（含人工等待）：{{ seconds(metrics.average_end_to_end_seconds) }}</p><small>来源：GET /api/metrics，任务页刷新同步更新；未使用假数据。</small></section><div class="section-title"><div><h2>最近任务</h2><p>跟踪报销从提交到最终处理的全过程</p></div><button class="text" @click="navigate('tasks')">查看全部 →</button></div><section class="panel table-panel"><table><thead><tr><th>任务编号</th><th>报销事项</th><th>金额</th><th>提交时间</th><th>当前状态</th><th></th></tr></thead><tbody><tr v-for="task in tasks.slice(0, 5)" :key="task.id"><td class="mono">{{ task.id.slice(0, 8) }}…</td><td><b>{{ task.payload.category }}</b><small>{{ task.payload.description }}</small></td><td>{{ money(task.payload.amount) }}</td><td>{{ dateTime(task.created_at) }}</td><td><span class="status" :class="task.status">{{ labels[task.status] || task.status }}</span></td><td><button class="text" @click="choose(task); navigate('tasks')">查看详情 →</button></td></tr></tbody></table><div v-if="!tasks.length" class="empty"><b>暂无报销任务</b><p>发起一笔报销后，任务会显示在这里。</p></div></section>
      </div>
      <div v-else-if="view === 'submit' && isEmployee" class="page narrow"><div class="page-title"><div><button class="back" @click="navigate('overview')">←</button><div><h1>发起费用报销</h1><p>请如实填写费用信息，提交前可查看字段提取建议。</p></div></div><span>草稿自动保存</span></div><form @submit.prevent="submit"><section class="panel form-section"><div class="form-heading"><i>01</i><div><h2>基本信息</h2><p>用于核对费用归属、制度与项目预算</p></div></div><div class="form-grid"><label>项目编号 *<select v-model="form.project_id"><option value="P001">P001 · 核心业务项目</option><option value="P002">P002 · 市场拓展项目</option></select></label><label>报销类别 *<select v-model="form.category"><option>交通费</option><option>办公用品</option><option>其他</option></select></label><label>报销金额（元）*<div class="money-input"><span>¥</span><input v-model="form.amount" type="text" inputmode="decimal" placeholder="0.00" @blur="touched.amount = true"></div><small v-if="touched.amount && errors.amount" class="field-error">{{ errors.amount }}</small></label><label>费用发生日期 *<input v-model="form.expense_date" type="date" :max="today" @blur="touched.expense_date = true"><small v-if="touched.expense_date && errors.expense_date" class="field-error">{{ errors.expense_date }}</small></label><label class="wide">报销说明 *<textarea v-model="form.description" maxlength="500" placeholder="请说明费用用途、地点或参与事项（1至500字）" @blur="touched.description = true"></textarea><span class="counter">{{ form.description.length }}/500</span><small v-if="touched.description && errors.description" class="field-error">{{ errors.description }}</small></label></div></section>
          <section class="panel form-section"><div class="form-heading"><i>02</i><div><h2>智能字段核对</h2><p>解析说明中的金额与类别建议，不会自动覆盖原字段</p></div></div><button type="button" class="secondary" @click="parse" :disabled="busy || parsing">{{ parsing ? '正在解析…' : '查看字段提取建议' }}</button><button v-if="parsing" type="button" class="secondary" @click="cancelParsing">停止等待，手工填写</button><div v-if="note" class="parse-result"><b>{{ note.message }}</b><p>建议类别：{{ note.suggested_category || '未识别' }}　建议金额：{{ note.suggested_amount ? `¥${note.suggested_amount}` : '未识别' }}</p><small>提取模式：{{ note.mode }} · 结果仅供核对，仍需人工确认</small></div><div v-if="note?.conflicts?.length" class="parse-result"><p v-for="(item, i) in note.conflicts" :key="i">{{ item.reason }}</p></div><label class="check"><input v-model="conflictAcknowledged" type="checkbox">存在金额或类别差异时，我已核对并确认本次申报值</label><label v-if="conflictAcknowledged">具体确认说明<textarea v-model="confirmationNote" maxlength="500" placeholder="说明差异原因及最终申报金额或类别"></textarea></label><div v-if="pending" class="parse-result"><p>上次提交结果尚未确认，已保留原请求和提交标识。</p><button type="button" class="secondary" :disabled="busy" @click="retryPending">重试上次提交</button><button type="button" class="secondary" :disabled="busy" @click="startNewApplication">已核对任务列表，作为新申请</button></div><label class="check"><input v-model="confirmed" type="checkbox">我已核对原始说明与金额、类别及项目编号，确认字段无误</label></section><div class="form-actions"><button type="button" class="secondary" @click="navigate('overview')">取消</button><button class="primary" :disabled="busy || !confirmed">{{ busy ? '正在提交与初审…' : '提交并开始初审' }}</button></div></form></div>
      <div v-else class="page"><div class="page-title"><div><div><h1>{{ isEmployee ? '我的报销任务' : '任务与人工审批' }}</h1><p>查看初审意见、制度依据、执行时间线与最终结果。</p></div></div><div class="title-actions"><button class="secondary" @click="run(refresh)" :disabled="busy || loadingTasks">{{ loadingTasks ? '刷新中…' : '刷新' }}</button><button v-if="isEmployee" class="primary" @click="navigate('submit')">＋ 发起报销</button></div></div><section class="panel filters"><label>⌕<input v-model="query" placeholder="搜索任务编号、项目或报销说明"></label><select v-model="statusFilter"><option value="">全部状态</option><option v-for="(label, status) in labels" :key="status" :value="status">{{ label }}</option></select></section><div class="task-layout"><section class="task-list"><button :disabled="busy || loadingDetail" v-for="task in filteredTasks" :key="task.id" class="task-card" :class="{ active: selected?.id === task.id }" @click="choose(task)"><div><span class="mono">{{ task.id }}</span><h3>{{ task.payload.category }} · {{ money(task.payload.amount) }}</h3><p>{{ task.payload.description }}</p></div><div><small>{{ task.payload.project_id }} · {{ dateTime(task.created_at) }}</small><span class="status" :class="task.status">{{ labels[task.status] || task.status }}</span></div></button><div v-if="!filteredTasks.length" class="panel empty"><b>没有匹配的任务</b><p>尝试更换关键词或状态筛选。</p></div></section>
          <section v-if="selected" class="panel detail" :aria-busy="loadingDetail"><p v-if="loadingDetail" class="detail-loading">正在加载最新任务详情…</p><div class="detail-title"><div><p class="eyebrow">TASK DETAIL</p><h2>{{ selected.payload.category }} · {{ money(selected.payload.amount) }}</h2><span class="mono">{{ selected.id }}</span></div><span class="status" :class="selected.status">{{ labels[selected.status] }}</span></div><dl><div><dt>项目编号</dt><dd>{{ selected.payload.project_id }}</dd></div><div><dt>费用日期</dt><dd>{{ selected.payload.expense_date }}</dd></div><div><dt>提交时间</dt><dd>{{ dateTime(selected.created_at) }}</dd></div><div><dt>处理方式</dt><dd>{{ selected.manual ? '已转人工' : '自动初审' }}</dd></div><div class="wide"><dt>报销说明</dt><dd>{{ selected.payload.description }}</dd></div></dl><h3>数字员工初审意见</h3><div class="review" :class="selected.status"><b>{{ resultDecision(selected) === 'approved' ? '建议通过' : resultDecision(selected) === 'rejected' ? '建议拒绝' : labels[selected.status] }}</b><p>{{ resultText(selected) }}</p></div><div v-if="selected.result.budget || selected.result.receipt" class="result-evidence"><div v-if="selected.result.budget"><small>预算查询</small><b>{{ selected.result.budget.project_id }} · {{ money(selected.result.budget.available) }}</b><span>模拟预算余额</span></div><div v-if="selected.result.receipt"><small>结果写回</small><b>{{ selected.result.receipt.receipt_id }}</b><span>模拟凭证，可按任务追溯</span></div></div><h3>判定依据</h3><p v-if="!selected.citations.length" class="muted">暂无适用制度依据，需要人工确认。</p><article v-for="rule in selected.citations" :key="rule.id" class="citation"><b>{{ rule.id }}</b><p>{{ rule.text }}</p><small>{{ rule.source }} · {{ rule.version }}</small></article>
            <form v-if="user.role === 'approver' && selected.status === 'awaiting_approval'" class="approval" @submit.prevent="approve('approve')"><h3>人工审批</h3><p v-for="reason in selected.result.manual_reasons" :key="reason">{{ reason }}</p><label class="check"><input v-model="correcting" type="checkbox">通过前补正报销字段</label><div v-if="correcting" class="form-grid"><label>项目<select v-model="correction.project_id"><option>P001</option><option>P002</option></select></label><label>类别<select v-model="correction.category"><option>交通费</option><option>办公用品</option><option>其他</option></select></label><label>金额<input v-model="correction.amount" inputmode="decimal"></label><label>日期<input v-model="correction.expense_date" type="date" :max="today"></label><label class="wide">说明<textarea v-model="correction.description" maxlength="500"></textarea></label><label class="wide">补正原因<textarea v-model="correctionReason" maxlength="500"></textarea></label></div><label class="check"><input v-model="reviewedFields" type="checkbox">我已核对当前有效字段及模型异常，确认按这些字段审批</label><label>审批意见 *<textarea v-model="comment" required maxlength="500"></textarea></label><label>人工依据（缺少适用制度依据时，通过须填写）<textarea v-model="basis" maxlength="1000"></textarea></label><div class="form-actions"><button class="danger" type="button" @click="approve('reject')" :disabled="busy || loadingDetail || !comment.trim()">拒绝初审</button><button class="primary" :disabled="busy || loadingDetail || !comment.trim()">通过并继续</button></div></form><h3>执行时间线</h3><ol class="timeline"><li v-for="(item, i) in selected.events" :key="i"><b>{{ labels[item.status] || item.status }}</b><p>{{ item.message }}</p><small>{{ dateTime(item.at) }}</small></li></ol><AuditTrail :key="selected.id" :task="selected" /></section><section v-else class="panel detail-placeholder"><span>◇</span><b>请选择一项任务</b><p>这里将显示初审意见、制度依据和执行时间线。</p></section></div></div>
    </main>
  </div>
</template>
