<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch, onBeforeUnmount } from 'vue'
import { request, uploadInvoice, type Claim, type Task, type InvoiceFields, type InvoiceSuggestion } from './api'
import FileViewer from './FileViewer.vue'
const props = defineProps<{ department?: string; owner?: string; editTask?: Task | null }>()
const emit = defineEmits<{ submitted: [task: Task]; cancel: [] }>()
interface Item { file_id: string; invoice_id: string; filename: string; suggestion: InvoiceSuggestion; form: Record<string, unknown>; status: string; request_key: string; task_id: string | null; task_status?: string; submit_error?: string }
interface Batch { batch_id: string; items: Item[]; current_index: number; updated_at: string;warnings?:string[] }
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
const blankInvoice = (): InvoiceFields => ({ invoice_no: '', invoice_code: '', invoice_date: null, buyer: '', seller: '', total: '', item_name: '' })
function preferredProject(): 'P001' | 'P002' { try { return localStorage.getItem('receipt-project:' + props.owner) === 'P002' ? 'P002' : 'P001' } catch { return 'P001' } }
const blank = () => ({ project_id: preferredProject(), department: props.department || '研发部', category: '其他', amount: '', expense_date: today, description: '', file_id: '', invoice_id: '', invoice: blankInvoice(), details: {} as Record<string, string | number | null> })
const form = reactive(blank()), confirmed = ref(false), acknowledged = ref(false), confirmationNote = ref(''), revisionReason = ref('')
const busy = ref(false), uploadBusy = ref(false), error = ref(''), message = ref(''), batch = ref<Batch | null>(null), batches = ref<Batch[]>([]), index = ref(0), loaded = ref(false)
const submittedTask = ref<Task | null>(null), categories = ref(['交通费','住宿费','业务招待费','办公用品','通讯费','培训会议费','软件服务费','出差餐补','其他'])
const editing = computed(() => !!props.editTask), current = computed(() => batch.value?.items[index.value]), total = computed(() => batch.value?.items.length || 0)
const currentFilename = computed(() => current.value?.filename)
const pending = reactive<Record<string,boolean>>({})
const processing = computed(() => !!pending[current.value?.request_key || ''] || !!submittedTask.value && ['queued','running','writing_back'].includes(submittedTask.value.status))
function itemLabel(item: Item) { return pending[item.request_key] || ['queued','running','writing_back'].includes(item.task_status || '') ? '处理中' : item.submit_error ? '提交未完成' : item.status === 'submitted' ? (stateLabels[item.task_status || ''] || '已提交') : item.status === 'skipped' ? '已跳过' : '待核对' }
const snapshot = computed(() => current.value?.suggestion)
const missingInvoice = computed(() => Object.entries({发票号码:form.invoice.invoice_no,开票日期:form.invoice.invoice_date,购买方:form.invoice.buyer,销售方:form.invoice.seller,含税总额:form.invoice.total,票面项目:form.invoice.item_name}).filter(([,v])=>!v).map(([k])=>k))
const invoiceDetailsOpen = ref(false), online = ref(navigator.onLine), saveState = ref('草稿自动保存'), amountTouched = ref(false)
const amountError = computed(() => !form.amount ? '请填写本次申报金额' : !/^\d+(?:\.\d{1,2})?$/.test(form.amount) ? '金额最多保留两位小数，例如 300.00' : Number(form.amount) <= 0 || Number(form.amount) > 5000 ? '本次申报须大于0且不超过5000元，票面总额可单独保留' : '')
const hasDifference = computed(() => {
  const original = snapshot.value?.invoice.fields
  return !!(form.invoice.total && form.amount && Number(form.invoice.total) !== Number(form.amount)) || !!(original && Object.entries(original).some(([k,v]) => v && v !== form.invoice[k as keyof InvoiceFields]))
})
const completedCount = computed(() => batch.value?.items.filter(i => i.status === 'submitted').length || 0)
const submitHint = computed(() => !online.value ? '当前离线，请联网后继续。' : !form.department.trim() ? '请填写所属部门。' : amountError.value ? amountError.value : !form.description.trim() ? '请补充这笔费用的业务用途。' : !form.expense_date || form.expense_date > today ? '请核对费用日期，应不晚于今天。' : !confirmed.value ? '核对原件后，勾选确认即可报销。' : '信息已齐备，可以提交本张。')
function networkChanged() { online.value = navigator.onLine }
async function retrySave() { try { await save() } catch { saveState.value = '草稿暂未保存，请检查网络后重试' } }
function openSubmitted() { if (submittedTask.value) emit('submitted',submittedTask.value); emit('cancel') }
const stateLabels: Record<string,string> = {queued:'待执行',running:'初审中',awaiting_approval:'待人工审批',returned:'退回待补充',writing_back:'登记中',succeeded:'初审通过',rejected:'初审拒绝',failed:'处理异常'}
let loadGeneration = 0, saveTimer: ReturnType<typeof setTimeout> | undefined
let active = true
let progressTimer: ReturnType<typeof setInterval> | undefined, polling = false
let editKey = crypto.randomUUID()
function invoicePrefill(suggestion?: InvoiceSuggestion) {
  if (!suggestion) return
  const invoice = suggestion.invoice.fields
  form.invoice = invoice ? { ...blankInvoice(), ...invoice } : blankInvoice()
  form.amount = invoice?.total || suggestion.suggested_amount || ''
  form.category = suggestion.suggested_category || '其他'
  if (invoice?.invoice_date && invoice.invoice_date <= today) form.expense_date = invoice.invoice_date
}
function draft() { return JSON.parse(JSON.stringify({ ...form, confirmation_note: confirmationNote.value, revision_reason: revisionReason.value })) as Record<string, unknown> }
async function save() {
  if (!loaded.value || !form.file_id) return
  if (!editing.value && (pending[current.value?.request_key || ''] || current.value?.task_id)) return
  if (!online.value) { saveState.value = '当前离线，输入仍保留在页面中，请联网后重试保存'; return }
  saveState.value = '正在保存草稿…'
  if (editing.value) {
    await request('/tasks/' + props.editTask!.id + '/draft', 'PUT', { form: draft(), current_index: 0 })
  } else if (batch.value) {
    const batchId = batch.value.batch_id, itemIndex = index.value
    const updated = await request<Batch>('/batches/' + batchId + '/items/' + itemIndex, 'PUT', { form: draft(), current_index: itemIndex })
    if (active && batch.value?.batch_id === batchId) {
      const item = updated.items[itemIndex]
      if (item && batch.value.items[itemIndex]) Object.assign(batch.value.items[itemIndex]!,item)
      batch.value.updated_at = updated.updated_at
    }
  }
  if (active) saveState.value = '草稿已保存'
}
async function loadItem(next: number) {
  if (!batch.value) return
  const item = batch.value.items[next]; if (!item) return
  loaded.value = false; index.value = next; submittedTask.value = null
  Object.assign(form, blank()); form.file_id = item.file_id; form.invoice_id = item.invoice_id
  invoicePrefill(item.suggestion)
  if (Object.keys(item.form || {}).length) Object.assign(form, item.form)
  form.file_id = item.file_id; form.invoice_id = item.invoice_id
  confirmed.value = false; acknowledged.value = false; confirmationNote.value = String(item.form?.confirmation_note || ''); error.value = ''; message.value = ''
  invoiceDetailsOpen.value = missingInvoice.value.length > 0; amountTouched.value = false
  loaded.value = true
  if (item.task_id) submittedTask.value = await request<Task>('/tasks/' + item.task_id)
  if (item.submit_error) error.value = item.submit_error
}
async function switchItem(next: number) {
  if (busy.value || uploadBusy.value || next < 0 || next >= total.value) return
  busy.value = true; error.value = ''
  try { clearTimeout(saveTimer); await save(); await loadItem(next); await save() }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { busy.value = false }
}
async function restore(id: string) {
  if (busy.value || uploadBusy.value) return
  busy.value = true
  try { clearTimeout(saveTimer); await save(); batch.value = batches.value.find(x => x.batch_id === id) || null; await loadItem(batch.value?.current_index || 0) }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { busy.value = false }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement, selected = Array.from(input.files || []); input.value = ''
  await uploadFiles(selected)
}
function dropFiles(event: DragEvent) { if (!busy.value && !uploadBusy.value && online.value) void uploadFiles(Array.from(event.dataTransfer?.files || [])) }
async function uploadFiles(selected: File[]) {
  if (!selected.length) return
  if (selected.length > 10) { error.value = '每批最多10个文件'; return }
  uploadBusy.value = true; error.value = ''; message.value = ''; const failed: string[] = [], ids: string[] = []
  try {
    clearTimeout(saveTimer); await save()
    for (let i = 0; i < selected.length; i++) {
      const file = selected[i]!; message.value = '正在保存及识别 ' + (i + 1) + '/' + selected.length + '：' + file.name
      try {
        const result = await uploadInvoice(file)
        if (!active) return
        if (!result.file_id) throw new Error('上传未返回归档编号')
        if (editing.value) {
          loaded.value = false; form.file_id = result.file_id; form.invoice_id = result.invoice_id; invoicePrefill(result); loaded.value = true; confirmed.value = false
        } else ids.push(result.file_id)
      } catch (e) { failed.push(file.name + '：' + (e instanceof Error ? e.message : String(e))) }
    }
    if (ids.length) {
      batch.value = await request<Batch>('/batches', 'POST', { file_ids: ids })
      failed.push(...(batch.value.warnings||[]))
      try { sessionStorage.setItem('active-receipt-batch:'+props.owner,batch.value.batch_id) } catch { /* Session restoration is optional. */ }
      await loadItem(0); batches.value = await request<Batch[]>('/batches')
    }
    if (editing.value) await save()
    message.value = ids.length ? '合格原件已归档。核对提交后即可处理下一张，初审会在后台继续。' : editing.value ? '已更新草稿票据，旧原件仍保留在历史中。' : ''
    if (failed.length) error.value = failed.join('；')
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { uploadBusy.value = false }
}
function makePayload(): Claim {
  if (!form.file_id || !confirmed.value) throw new Error('请上传原件并核对确认')
  if (!/^\d+(?:\.\d{1,2})?$/.test(form.amount) || Number(form.amount) <= 0 || Number(form.amount) > 5000) throw new Error('申报金额须大于0且不超过5000，最多两位小数；票面总额可单独保留')
  if (!form.description.trim() || !form.department.trim()) throw new Error('请填写部门和报销用途')
  if (acknowledged.value && !confirmationNote.value.trim()) throw new Error('请填写票面差异的具体确认说明')
  const details = Object.fromEntries(Object.entries(form.details).filter(([,v]) => v !== '' && v != null))
  return { project_id: form.project_id, category: form.category as Claim['category'], amount: Number(form.amount).toFixed(2), expense_date: form.expense_date,
    description: form.description.trim(), department: form.department.trim(), confirmed: true, file_id: form.file_id, ...(form.invoice_id ? { invoice_id: form.invoice_id } : {}),
    invoice: { ...form.invoice, invoice_date: form.invoice.invoice_date || null }, details, conflict_acknowledged: acknowledged.value, confirmation_note: confirmationNote.value.trim() }
}
async function submit() {
  if (busy.value || uploadBusy.value || submittedTask.value || processing.value) return
  busy.value = true; error.value = ''; message.value = ''
  const originBatch = batch.value, originIndex = index.value, originItem = current.value
  const originEditing = editing.value, originTask = props.editTask
  let key = ''
  const stillHere = () => active && (originEditing ? props.editTask?.id === originTask?.id : batch.value?.batch_id === originBatch?.batch_id && index.value === originIndex)
  function accept(task: Task) {
    if (originItem) Object.assign(originItem,{status:'submitted',task_id:task.id,task_status:task.status,submit_error:''})
    if (stillHere()) { submittedTask.value = task; confirmed.value = false; error.value = ''; message.value = '本张处理结果已更新，可继续登记其他发票。' }
    if (active) emit('submitted',task)
  }
  try {
    clearTimeout(saveTimer); await save(); const payload = makePayload()
    key = originEditing ? editKey : originItem?.request_key || ''
    if (!key) throw new Error('缺少提交标识，请恢复批次后重试')
    pending[key] = true
    if (originItem) originItem.submit_error = ''
    const body = originEditing ? { ...payload, revision_reason: revisionReason.value.trim() } : payload
    // Freeze this receipt's payload, then release the interface for the next one.
    if (!originEditing) { busy.value = false; message.value = '本张正在后台初审，你可以点击下一张继续登记。' }
    const task = await request<Task>(originEditing ? '/tasks/' + originTask!.id + '/resubmit' : '/tasks','POST',body,key)
    accept(task)
  } catch (e) {
    if (!active) return
    const failure = e instanceof Error ? e.message : String(e)
    if (originItem) originItem.submit_error = failure
    if (stillHere()) error.value = failure
    try {
      if (!originEditing && originBatch) {
        const id = originBatch.batch_id, items = await request<Batch[]>('/batches')
        const restored = items.find(b => b.batch_id === id)
        if (restored?.items[originIndex]?.task_id) {
          const known = await request<Task>('/tasks/' + restored.items[originIndex]!.task_id)
          accept(known)
        }
      } else if (originEditing && originTask) {
        const known = await request<Task>('/tasks/' + originTask.id)
        if (active && (known.revision || 1) > (originTask.revision || 1)) accept(known)
      }
    } catch { /* Keep the original error and original idempotency key. */ }
  }
  finally { if (key) delete pending[key]; if (originEditing || !key) busy.value = false }
}
async function refreshProgress() {
  if (!active || polling || !online.value || !batch.value) return
  const target = batch.value; polling = true
  try {
    for (const item of target.items) {
      if (!item.task_id || !['queued','running','writing_back'].includes(item.task_status || 'running')) continue
      const task = await request<Task>('/tasks/' + item.task_id)
      item.task_status = task.status
      if (active && batch.value?.batch_id === target.batch_id && current.value?.task_id === task.id) submittedTask.value = task
    }
  } catch { /* Offline notice and the next poll provide recovery without clearing drafts. */ }
  finally { polling = false }
}
async function skip() {
  if (!batch.value || !current.value || submittedTask.value) return
  busy.value = true
  try { clearTimeout(saveTimer); batch.value = await request<Batch>('/batches/' + batch.value.batch_id + '/items/' + index.value,'PUT',{form:draft(),skipped:true,current_index:index.value});message.value='已暂时跳过，可从队列返回继续处理' }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { busy.value = false }
}
watch(form, () => {
  confirmed.value = false; acknowledged.value = false
  if (!loaded.value || busy.value || uploadBusy.value || processing.value) return
  try { localStorage.setItem('receipt-project:' + props.owner,form.project_id) } catch { /* Preference storage is optional. */ }
  clearTimeout(saveTimer); saveTimer = setTimeout(() => { save().catch(() => { saveState.value = '草稿暂未保存，请检查网络后重试' }) }, 700)
}, { deep: true, flush: 'sync' })
watch(() => props.editTask?.id, async () => {
  const task = props.editTask; if (!task) return
  clearTimeout(saveTimer); loaded.value = false; submittedTask.value = null; editKey = crypto.randomUUID()
  Object.assign(form, blank(), task.payload, (task as Task & { return_draft?: Record<string,unknown> }).return_draft || {})
  if (!form.department) form.department = props.department || '研发部'
  invoiceDetailsOpen.value = missingInvoice.value.length > 0
  confirmed.value = false; acknowledged.value = false; revisionReason.value = String(task.return_draft?.revision_reason || ''); confirmationNote.value = String(task.return_draft?.confirmation_note || ''); loaded.value = true
}, { immediate: true })
onMounted(async () => {
  window.addEventListener('online',networkChanged);window.addEventListener('offline',networkChanged)
  progressTimer = setInterval(refreshProgress,3000)
  const generation = ++loadGeneration
  try {
    const policy = await request<{ categories: string[] }>('/policy'); if (generation !== loadGeneration) return
    categories.value = policy.categories
    if (!editing.value) {
      batches.value = await request<Batch[]>('/batches')
      let id='';try { id=sessionStorage.getItem('active-receipt-batch:'+props.owner)||'' } catch { /* No active draft. */ }
      const unfinished=batches.value.find(b=>b.batch_id===id && b.items.some(i=>i.status!=='submitted'))
      if (unfinished) { batch.value=unfinished;await loadItem(unfinished.items.findIndex(i=>i.status!=='submitted')) }
    }
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
})
onBeforeUnmount(() => { active = false; loadGeneration++; clearTimeout(saveTimer); clearInterval(progressTimer); window.removeEventListener('online',networkChanged);window.removeEventListener('offline',networkChanged) })
</script>
<template>
  <div class="intake">
    <ol class="intake-steps" aria-label="报销步骤"><li :aria-current="!form.file_id ? 'step' : undefined">1 上传票据</li><li :aria-current="form.file_id && !submittedTask ? 'step' : undefined">2 核对信息</li><li :aria-current="submittedTask ? 'step' : undefined">3 查看结果</li></ol><div class="intake-heading"><div><h1>{{ editing ? '修改并重新提交' : '上传票据并报销' }}</h1><p>{{ editing ? '按退回意见修改，原申请编号和历史材料继续保留。' : '原件归档、自动预填，逐张核对后提交。' }}</p></div><button type="button" @click="emit('cancel')" :disabled="busy || uploadBusy">返回任务</button></div>
    <p v-if="editing" class="return-note">退回意见：{{ editTask?.result.return_reason }}</p>
    <p v-if="!online" class="offline-note" role="alert">网络已断开，当前输入仍保留在页面中。联网后可重试保存。</p><section class="upload-panel" :aria-busy="uploadBusy" @dragover.prevent @drop.prevent="dropFiles"><label>{{ editing ? '选择新票据或补充证明' : '选择发票，或将文件拖到这里' }}<input type="file" accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg" :multiple="!editing" :disabled="busy || uploadBusy || !online" @change="upload"></label><small>PDF、JPG、PNG，每个≤5MB，每批≤10个；多页PDF限同一张票据。原件保存到本系统；确认字段和用途会交给已配置的大模型初审。</small></section>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="message" role="status" class="success">{{ message }}</p>
    <nav v-if="!editing && batch" class="receipt-queue" aria-label="票据处理队列"><b>第 {{ index + 1 }} 张 / 共 {{ total }} 张</b><span class="queue-progress">已提交 {{ completedCount }} 张</span><button v-for="(item,i) in batch.items" :key="item.file_id" type="button" :class="{ active: index === i }" :disabled="busy || uploadBusy" @click="switchItem(i)">{{ i + 1 }} · {{ itemLabel(item) }}</button></nav>
    <section v-if="!form.file_id" class="intake-empty"><h2>从一张发票开始</h2><p>上传PDF或清晰照片，系统会预填票面信息。核对后补充项目和用途，就可以发起报销。</p></section><div v-if="form.file_id" class="receipt-workspace">
      <FileViewer :file-id="form.file_id" :filename="currentFilename" />
      <form class="receipt-form" :aria-busy="busy || uploadBusy" @submit.prevent="submit">
        <p v-if="snapshot?.invoice.recognition_error" class="error">{{ snapshot.invoice.recognition_error.message }}。原件已存档，可人工补全。</p>
        <p class="hint">票据识别结果可修改；项目、用途和业务明细请按实际情况填写。{{ snapshot?.invoice.read_method ? '读取方式：' + (snapshot.invoice.read_method === 'pdf_text' ? 'PDF文字读取' : '图片识别') : '' }}</p>
        <p v-if="form.category !== '出差餐补' && missingInvoice.length" class="hint">待补充票面信息：{{ missingInvoice.join('、') }}。请对照原件填写；关键字段缺失时审批人将退回补充。</p>
        <details v-if="!submittedTask && !processing" class="invoice-details" :open="invoiceDetailsOpen" @toggle="invoiceDetailsOpen = ($event.target as HTMLDetailsElement).open"><summary>查看并核对发票信息 <small>{{ missingInvoice.length ? '还有' + missingInvoice.length + '项待补充' : '已提取，可展开修改' }}</small></summary><fieldset :disabled="busy || uploadBusy"><legend class="sr-only">发票信息</legend><div class="fields">
          <label>发票号码<input v-model="form.invoice.invoice_no" maxlength="40"></label><label>发票代码（传统票）<input v-model="form.invoice.invoice_code" maxlength="20"></label><label>开票日期<input v-model="form.invoice.invoice_date" type="date" :max="today"></label><label>票面含税总额<input v-model="form.invoice.total" inputmode="decimal"></label><label class="wide">购买方<input v-model="form.invoice.buyer" maxlength="200"></label><label class="wide">销售方<input v-model="form.invoice.seller" maxlength="200"></label><label class="wide">票面项目<input v-model="form.invoice.item_name" maxlength="500"></label>
        </div></fieldset></details>
        <fieldset v-if="!submittedTask && !processing" :disabled="busy || uploadBusy"><legend>这笔费用如何报销？</legend><div class="fields">
          <label>部门 *<input v-model="form.department" required maxlength="80"></label><label>归属项目 *<select v-model="form.project_id" aria-label="归属项目"><option>P001</option><option>P002</option></select></label><label>报销类别 *<select v-model="form.category" aria-label="报销类别"><option v-for="c in categories" :key="c">{{ c }}</option></select></label><label class="amount-field">本次申报金额 *<input v-model="form.amount" inputmode="decimal" required :aria-invalid="!!amountError" aria-describedby="amount-help" @blur="amountTouched = true"><small id="amount-help" :class="{ 'field-error': !!amountError && (amountTouched || !!form.amount) }">{{ (amountTouched || form.amount) && amountError ? amountError : '票面含税总额：¥' + (form.invoice.total || '待核对') }}</small></label><label>费用发生日期 *<input v-model="form.expense_date" type="date" required :max="today"></label><label v-if="['住宿费','业务招待费','培训会议费','软件服务费','出差餐补'].includes(form.category) || Number(form.amount) > 1000">审批/合同编号<input v-model="form.details.approval_no" maxlength="100"></label><label class="wide">用途和事由 *<textarea v-model="form.description" required maxlength="500" placeholder="例如：某项目API开发所用的云服务，请说明业务关联"></textarea></label>
          <label v-if="form.category !== '交通费'" class="wide">明细、收货或业务材料说明<textarea v-model="form.details.proof_reference" maxlength="500"></textarea></label>
          <template v-if="form.category === '住宿费'"><label>住宿城市<input v-model="form.details.city"></label><label>入住日期<input v-model="form.details.check_in" type="date"></label><label>离店日期<input v-model="form.details.check_out" type="date"></label></template>
          <template v-if="form.category === '交通费'"><label>席位<select v-model="form.details.seat" aria-label="席位"><option value="">请选择</option><option>二等座</option><option>硬座</option><option>硬卧</option><option>经济舱</option><option>市内交通</option><option>其他席位</option></select></label><label>因公路线<input v-model="form.details.route"></label></template>
          <template v-if="form.category === '业务招待费'"><label>参加总人数<input v-model.number="form.details.attendees" type="number" min="1"></label><label>陪同人数<input v-model.number="form.details.companions" type="number" min="0"></label><label class="wide">招待对象/单位<input v-model="form.details.business_partner"></label></template>
          <label v-if="['通讯费','软件服务费'].includes(form.category)" class="wide">月份或服务期间<input v-model="form.details.service_period" placeholder="2026-11，或实际服务起止日期"></label>
          <template v-if="form.category === '出差餐补'"><label>出差开始<input v-model="form.details.trip_start" type="date"></label><label>出差结束<input v-model="form.details.trip_end" type="date"></label><label>已供早餐次数<input v-model.number="form.details.supplied_breakfasts" type="number" min="0"></label><label>已供午餐次数<input v-model.number="form.details.supplied_lunches" type="number" min="0"></label><label>已供晚餐次数<input v-model.number="form.details.supplied_dinners" type="number" min="0"></label></template>
          <details class="wide optional-note"><summary>逾期、超标准或其他补充说明（选填）</summary><label><span class="sr-only">逾期或超标准说明</span><textarea v-model="form.details.exception_reason" maxlength="500" placeholder="确有特殊情况时填写"></textarea></label></details>
        </div>
        <label v-if="hasDifference" class="check"><input v-model="acknowledged" type="checkbox">识别值、票面总额与申报有差异时，我已核对</label><label v-if="acknowledged">差异确认说明<textarea v-model="confirmationNote" maxlength="500"></textarea></label><label v-if="editing">本次修改说明 *<textarea v-model="revisionReason" required maxlength="500"></textarea></label><label class="check"><input v-model="confirmed" type="checkbox">我已对照原件核对字段，确认提交</label>
        </fieldset>
        <div v-if="submittedTask" class="submission-result"><b>✓ 本张申请已提交</b><p>任务编号：{{ submittedTask.business_no || submittedTask.id }}</p><p>当前状态：{{ stateLabels[submittedTask.status] }}。</p><button type="button" @click="openSubmitted">查看申请进度</button></div>
        <p v-if="busy || processing" class="processing-note" role="status">本张正在后台核对制度、预算和票据信息。你可以点击下一张继续登记，队列会自动更新处理结果。</p><p v-if="!submittedTask && !processing" class="next-hint">{{ submitHint }}</p><div class="draft-feedback" v-if="!submittedTask && !processing" aria-live="polite">{{ saveState }} <button v-if="saveState.includes('未保存') || saveState.includes('离线')" type="button" @click="retrySave" :disabled="!online || busy">重试保存</button></div><div class="intake-actions"><button v-if="!submittedTask && !processing" type="submit" class="primary" :disabled="busy || uploadBusy || !online || !confirmed || !!amountError || !form.description.trim() || !form.department.trim() || !form.expense_date || form.expense_date > today">{{ busy ? '正在检查这笔报销…' : editing ? '保存修改并重新提交' : '确认并提交本张' }}</button><button v-if="!editing && !submittedTask && !processing" type="button" @click="skip" :disabled="busy || uploadBusy">暂时跳过</button><button v-if="!editing && index + 1 < total && (submittedTask || processing || current?.status === 'skipped')" class="primary" type="button" @click="switchItem(index + 1)" :disabled="busy || uploadBusy">处理下一张 →</button><b v-if="!editing && index + 1 === total && submittedTask">已到本批最后一张，可检查队列中的跳过或失败项。</b></div>
      </form>
    </div>
  </div>
</template>
<style scoped>
.intake{padding:28px;max-width:1600px;margin:auto}.intake-heading{display:flex;justify-content:space-between;align-items:start;gap:20px}.intake-heading h1{margin:0 0 8px}.intake-heading p,.hint{color:#68788c}.upload-panel{margin:22px 0;background:white;border:1px solid #dce4ee;border-radius:14px;padding:20px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}.upload-panel>label{display:grid;gap:8px}.upload-panel small{max-width:350px;color:#68788c}.receipt-queue{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px}.receipt-queue .active{background:#e7f0fc;border-color:#2463a8;color:#164b8e}.receipt-workspace{display:grid;grid-template-columns:minmax(360px,1fr) minmax(400px,1fr);gap:22px;align-items:start}.receipt-form{min-width:0;background:white;border:1px solid #dce4ee;border-radius:14px;padding:22px}.receipt-form fieldset{border:0;padding:0;margin:18px 0}.receipt-form legend{font-weight:700;padding:0 0 12px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:14px}.fields .wide{grid-column:1/-1}.receipt-form label{display:grid;gap:6px;font-size:13px}.receipt-form .check{display:flex;margin:15px 0;align-items:center;gap:8px}.receipt-form input,.receipt-form select,.receipt-form textarea,.upload-panel select{width:100%;box-sizing:border-box;border:1px solid #cbd6e3;border-radius:7px;padding:10px;font:inherit}.receipt-form input[type=checkbox]{width:auto}.receipt-form textarea{min-height:70px;resize:vertical}.intake-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.submission-result,.return-note{background:#edf5ff;padding:15px;border-radius:10px;overflow-wrap:anywhere}.intake button{border:1px solid #c8d5e5;border-radius:7px;padding:10px 15px;background:white;cursor:pointer}.intake .primary{background:#2463a8;color:white;border-color:#2463a8}.intake button:disabled{opacity:.55;cursor:wait}.intake .error{background:#fff0ef;color:#a13931;padding:12px;border-radius:8px}.intake .success{background:#edf7f1;color:#206745;padding:12px;border-radius:8px}@media(max-width:1000px){.receipt-workspace{grid-template-columns:1fr}.intake{padding:16px}.fields{grid-template-columns:1fr}.fields .wide{grid-column:auto}}

.intake-heading p,.hint,.upload-panel small{color:#526171;line-height:1.65}.intake-steps{list-style:none;padding:0;display:flex;gap:20px;color:#526171;font-size:13px;margin:0 0 20px}.intake-steps [aria-current=step]{font-weight:750;color:#0b5e58}.invoice-details{margin:8px 0 18px;border:1px solid #dce4ee;border-radius:10px;padding:0 14px;font-size:14px}.invoice-details summary{min-height:48px;display:flex;align-items:center;gap:12px;cursor:pointer;color:#253a4d;font-weight:650}.invoice-details summary small{font-size:12px;color:#526171;font-weight:400}.invoice-details[open] summary{border-bottom:1px solid #dce4ee}.invoice-details fieldset{margin:14px 0}.amount-field input{font-size:22px;font-weight:700;color:#153c35}.amount-field small{font-size:12px;line-height:1.5}.queue-progress{font-size:13px;color:#526171}.next-hint,.draft-feedback{font-size:13px;line-height:1.6;color:#526171}.draft-feedback{margin-bottom:14px}.draft-feedback button{margin-left:8px}.processing-note,.offline-note{padding:14px;background:#fff5dc;border:1px solid #d9be70;border-radius:8px;color:#654600;line-height:1.7}.intake-empty{padding:50px 24px;text-align:center;background:white;border:1px dashed #b5c9c4;border-radius:14px;color:#526171}.intake-empty h2{font-size:21px;color:#183f38}.intake-empty p{max-width:460px;margin:10px auto;line-height:1.8}.optional-note{padding:0;border:0}.optional-note summary{min-height:44px;display:flex;align-items:center;font-size:13px}.upload-panel{border-style:dashed;background:#fbfefd}.upload-panel input[type=file]::file-selector-button{min-height:44px;border:1px solid #0f766e;background:#0f766e;color:white;border-radius:8px;padding:8px 18px;margin-right:12px;cursor:pointer}.intake .primary{background:#0f766e;border-color:#0f766e}.intake .primary:hover{background:#0b5e58}.intake button{min-height:44px}.intake button:active:not(:disabled){transform:translateY(1px)}.receipt-form .check{min-height:44px}.receipt-form input:not([type=checkbox]),.receipt-form select{min-height:44px}
@media(min-width:1001px){.receipt-workspace>.original-viewer{position:sticky;top:90px}}
@media(max-width:600px){.intake-heading{display:block}.intake-heading>button{margin-top:12px}.intake-steps{gap:12px;font-size:12px}.receipt-form{padding:17px}.intake-actions>*{width:100%;text-align:center}.upload-panel{padding:16px}.invoice-details summary{display:block;padding:12px 0}.invoice-details summary small{display:block;margin-top:5px}.receipt-queue{gap:7px}}
</style>
