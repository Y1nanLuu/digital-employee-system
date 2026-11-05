<script setup lang="ts">
import { ref, reactive } from 'vue'
import { request, setCredentials, type User, type Task } from './api'
const username = ref('employee'), password = ref('demo123'), user = ref<User | null>(null)
const tasks = ref<Task[]>([]), selected = ref<Task | null>(null), metrics = ref<Record<string, number | null> | null>(null)
const busy = ref(false), error = ref(''), note = ref(''), confirmed = ref(false), comment = ref(''), basis = ref('')
const date = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date())
const form = reactive({project_id:'P001', category:'交通费', amount:'300.00', expense_date:date, description:'客户现场往返交通费300元'})
const labels: Record<string,string> = {queued:'待执行',running:'处理中',awaiting_approval:'待人工审批',writing_back:'写回中',succeeded:'初审通过',rejected:'初审拒绝',failed:'技术失败'}
let submitKey = crypto.randomUUID()
let submittedPayload: string | null = null
async function run(action: () => Promise<void>) { busy.value=true; error.value=''; try { await action() } catch(e) { error.value=e instanceof Error?e.message:String(e) } finally { busy.value=false } }
async function refresh() {
 tasks.value=await request<Task[]>('/tasks')
 if(selected.value) selected.value=tasks.value.find(t=>t.id===selected.value?.id) || null
 if(user.value?.role !== 'employee') metrics.value=await request('/metrics')
}
function login() { run(async()=>{setCredentials(username.value,password.value); user.value=await request<User>('/me'); await refresh()}) }
function logout() {user.value=null;tasks.value=[];selected.value=null;metrics.value=null;setCredentials('','');error.value='';note.value='';comment.value='';basis.value=''}
function parse() {run(async()=>{ const data=await request<{suggested_amount:string|null;suggested_category:string|null;message:string}>('/parse','POST',{description:form.description}); note.value=JSON.stringify(data);confirmed.value=false })}
function submit() {run(async()=>{
 if(!confirmed.value) throw new Error('请核对原始说明与字段并勾选确认')
 const payload={...form,confirmed:true};const signature=JSON.stringify(payload)
 if(submittedPayload !== signature) {submitKey=crypto.randomUUID();submittedPayload=signature}
 selected.value=await request<Task>('/tasks','POST',payload,submitKey)
 submitKey=crypto.randomUUID();submittedPayload=null;confirmed.value=false;await refresh()
})}
function approve(decision:string) {run(async()=>{
 if(!selected.value)return
 selected.value=await request<Task>(`/tasks/${selected.value.id}/approval`,'POST',{decision,comment:comment.value,manual_basis:basis.value})
 comment.value='';basis.value='';await refresh()
})}
const percent=(v:number|null|undefined)=>v==null?'暂无数据':`${(v*100).toFixed(1)}%`
const seconds=(v:number|null|undefined)=>v==null?'暂无数据':`${v.toFixed(2)}秒`
function choose(task:Task){selected.value=task;comment.value='';basis.value=''}
</script>

<template>
 <header><div><small>中小企业办公助手 · 课程演示</small><h1>数字员工工作台</h1></div><div v-if="user">{{user.name}} <button @click="logout" :disabled="busy">退出</button></div></header>
 <p class="banner">费用报销初审 · 规则基线模式 · 使用模拟制度与预算，不执行资金支付</p>
 <p v-if="error" role="alert" class="error">{{error}}</p>
 <section v-if="!user" class="login"><h2>选择演示身份</h2><form @submit.prevent="login"><label>账号<select v-model="username"><option value="employee">员工</option><option value="approver">审批人</option><option value="admin">管理员</option></select></label><label>密码<input v-model="password" type="password" required autocomplete="current-password"></label><button :disabled="busy">登录工作台</button></form><p>本地默认演示密码：demo123；若已改配置，请使用新密码。</p></section>
 <main v-else>
  <section v-if="metrics" class="metrics"><div><small>任务总数</small><strong>{{metrics.total}}</strong></div><div><small>技术执行完成率</small><strong>{{percent(metrics.completion_rate)}}</strong></div><div><small>转人工率</small><strong>{{percent(metrics.manual_rate)}}</strong></div><div><small>平均系统处理耗时</small><strong>{{seconds(metrics.average_processing_seconds)}}</strong></div><p>完成率包含初审拒绝；系统耗时扣除人工等待。平均端到端耗时：{{seconds(metrics.average_end_to_end_seconds)}}</p></section>
  <div class="columns">
   <section v-if="user.role==='employee'"><h2>提交报销初审</h2><form @submit.prevent="submit">
    <label>项目<select v-model="form.project_id" @change="confirmed=false"><option>P001</option><option>P002</option></select></label>
    <label>费用类别<select v-model="form.category" @change="confirmed=false"><option>交通费</option><option>办公用品</option><option>其他</option></select></label>
    <label>金额（元）<input v-model="form.amount" type="number" min="0.01" max="5000" step="0.01" required @input="confirmed=false"></label>
    <label>发生日期<input v-model="form.expense_date" type="date" :max="date" required @input="confirmed=false"></label>
    <label>报销说明<textarea v-model="form.description" maxlength="500" required @input="confirmed=false"></textarea></label>
    <button type="button" @click="parse" :disabled="busy">查看字段提取建议</button><p v-if="note" class="hint">{{note}}</p>
    <label class="check"><input v-model="confirmed" type="checkbox">我已核对说明与金额、类别及项目编号，确认字段无误</label>
    <button :disabled="busy || !confirmed">{{busy?'处理中…':'确认并提交'}}</button>
   </form></section>
   <section><div class="section-head"><h2>任务列表</h2><button @click="run(refresh)" :disabled="busy">刷新</button></div><p v-if="!tasks.length">暂无任务，请先使用员工账号提交。</p><button v-for="task in tasks" :key="task.id" class="task" :class="{active:selected?.id===task.id}" @click="choose(task)"><b>{{task.payload.category}} · ¥{{task.payload.amount}}</b><span>{{labels[task.status] || task.status}}</span><small>{{task.id}}</small></button></section>
   <section v-if="selected" class="detail"><h2>任务详情</h2><p class="muted">{{selected.id}} · {{labels[selected.status]}}</p><p>{{selected.payload.description}}</p><h3>初审与执行结果</h3><pre>{{JSON.stringify(selected.result,null,2)}}</pre>
    <h3>判定依据</h3><p v-if="!selected.citations.length">暂无适用依据，需要人工确认。</p><article v-for="rule in selected.citations" :key="rule.id"><b>{{rule.id}}</b> {{rule.text}}<small>{{rule.source}} · {{rule.version}}</small></article>
    <form v-if="user.role==='approver' && selected.status==='awaiting_approval'" @submit.prevent="approve('approve')"><h3>人工审批</h3><label>审批意见<textarea v-model="comment" required maxlength="500"></textarea></label><label v-if="!selected.citations.length">人工依据（通过时必填）<textarea v-model="basis" maxlength="1000"></textarea></label><div class="actions"><button :disabled="busy">通过并继续</button><button type="button" class="danger" @click="approve('reject')" :disabled="busy || !comment.trim()">拒绝初审</button></div></form>
    <h3>执行时间线</h3><ol><li v-for="(item,i) in selected.events" :key="i"><b>{{labels[item.status]}}</b> {{item.message}}<small>{{new Date(item.at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}}</small></li></ol>
    <details><summary>工具调用日志</summary><pre>{{JSON.stringify(selected.logs,null,2)}}</pre></details><details><summary>审批记录</summary><pre>{{JSON.stringify(selected.approvals,null,2)}}</pre></details>
   </section>
  </div>
 </main>
</template>
