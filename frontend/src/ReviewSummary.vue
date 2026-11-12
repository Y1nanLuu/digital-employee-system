<script setup lang="ts">
import {computed,ref} from 'vue'
import type {Task} from './api'
const props=defineProps<{task:Task}>(), expanded=ref(false)
const labels:Record<string,string>={GEN:'通用要求',INV:'票据信息',TRV:'交通住宿',ENT:'业务招待',OFF:'办公材料',TEL:'通讯费用',EDU:'培训会议',IT:'软件服务',MEAL:'出差餐补',OTH:'费用依据'}
const findings=computed(()=>{
 const rules=(props.task.result as unknown as {rule_findings?:{hard?:{code:string;message:string}[];manual?:{code:string;message:string}[]}}).rule_findings
 const items=[...(rules?.hard||[]).map(x=>({...x,blocking:true})),...(rules?.manual||[]).map(x=>({...x,blocking:false}))]
 if(!items.length) for(const text of props.task.result.manual_reasons||[]) items.push({code:'',message:text,blocking:false})
 const seen=new Set<string>();return items.filter(x=>{const key=x.message.replace(/\s/g,'');if(seen.has(key))return false;seen.add(key);return true})
})
const visible=computed(()=>expanded.value?findings.value:findings.value.slice(0,4))
const conclusion=computed(()=>props.task.status==='cancelled'?'员工已撤销':props.task.status==='returned'?'已驳回，等待员工修改':props.task.status==='succeeded'?'已通过':props.task.status==='rejected'?'已拒绝并终止':props.task.status==='failed'?'处理异常，需恢复':props.task.status==='awaiting_approval'?'需要人工核对':'正在初审')
const explanation=computed(()=>props.task.status==='returned'?props.task.result.return_reason:props.task.status==='rejected'?props.task.result.reason:findings.value.some(x=>x.blocking)?'发现阻止通过的问题，请核对具体原因。':props.task.status==='awaiting_approval'?`有${findings.value.length||'待核对的'}项问题需要审批人确认。` :props.task.status==='succeeded'?'本次申请已完成核对和登记。':props.task.status==='cancelled'?'本次申请已停止，材料和记录继续保留。':'处理结果以本页实际状态为准。')
const modelReasons=computed(()=>[...new Set(props.task.result.initial_review?.reasons||[])])
function money(v?:string){return v?'¥'+Number(v).toFixed(2):'尚未查询'}
</script>
<template>
 <section class="review-summary" aria-labelledby="review-title">
  <h3 id="review-title">数字员工初审摘要</h3><div class="review-conclusion"><strong>{{ conclusion }}</strong><p>{{ explanation }}</p></div>
  <section v-if="task.status==='returned'" class="return-reason"><h4>驳回原因</h4><p>{{ task.result.return_reason }}</p><p>员工补充材料并重提后，系统会重新初审。</p></section>
  <section v-if="findings.length"><h4>需要核对的问题 <small>{{ findings.length }}项</small></h4><ol class="review-issues"><li v-for="(issue,i) in visible" :key="i"><b>{{ issue.blocking?'阻止通过':labels[issue.code.split('-')[0]!]||'待核对' }}</b><p>{{ issue.message }}</p><small v-if="issue.code">依据 {{ issue.code }}</small></li></ol><button v-if="findings.length>4" class="secondary" @click="expanded=!expanded" type="button">{{ expanded?'收起其余问题':'查看其余'+(findings.length-4)+'项' }}</button></section>
  <section v-if="task.result.initial_review"><h4>模型建议：{{ task.result.initial_review.conclusion }}</h4><ul class="model-reasons"><li v-for="(reason,i) in modelReasons" :key="i">{{ reason }}</li></ul><small>{{ task.result.initial_review.source==='llm'?'大模型初审':'规则降级，需人工核对' }}；最终结果结合制度和工具检查决定。</small></section>
  <section><h4>工具检查</h4><dl class="check-grid"><div><dt>项目预算</dt><dd>{{ money(task.result.budget?.available) }}</dd></div><div><dt>发票查验（模拟）</dt><dd>{{ task.result.invoice_check?.status==='valid'?'模拟台账匹配':task.result.invoice_check?.status==='invalid'?'模拟台账无效':task.result.invoice_check?.status==='unknown'?'台账未知，需人工核对':'尚未查询' }}</dd></div><div><dt>重复票据</dt><dd>{{ task.result.duplicate_check?.duplicate?'发现重复':task.result.duplicate_check?.suspected?'疑似重复，需核对':task.result.duplicate_check?'未发现重复':'尚未查询' }}</dd></div></dl><small>查验结果来自课程模拟服务。</small></section>
  <p v-if="task.result.error" class="review-error">处理提示：{{ task.result.error }}</p>
  <details v-if="task.result.initial_review"><summary>查看模型信息和原始意见</summary><p>模型：{{ task.result.initial_review.model||'规则降级' }}；耗时：{{ task.result.initial_review.latency_ms||0 }}毫秒</p><pre>{{ JSON.stringify(task.result.initial_review,null,2) }}</pre></details>
 </section>
</template>
<style scoped>
.review-summary{margin:22px 0;line-height:1.75;color:#203a36}.review-summary h3{font-size:18px}.review-summary h4{font-size:14px;margin:16px 0 8px}.review-conclusion{background:#eef7f4;padding:16px 18px;border-radius:10px}.review-conclusion strong{font-size:21px}.review-conclusion p{margin:5px 0 0}.review-summary small{color:#526171;font-size:12px}.review-issues{padding-left:24px}.review-issues li{margin:10px 0;padding-left:6px}.review-issues b{font-size:13px;color:#754313}.review-issues p{margin:3px 0}.model-reasons{padding-left:24px}.check-grid{display:grid;grid-template-columns:1fr;gap:0;background:#f7faf9;padding:6px 14px;border-radius:8px}.check-grid>div{display:flex;justify-content:space-between;gap:16px;padding:9px 0;border-bottom:1px solid #d8e4e0}.check-grid>div:last-child{border:0}.check-grid dt{color:#526171}.check-grid dd{margin:0;text-align:right}.review-summary details{margin-top:18px}.review-summary summary{min-height:44px;cursor:pointer;display:flex;align-items:center}.review-summary pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f4;padding:12px;font-size:12px}.return-reason,.review-error{background:#fff4e3;padding:14px;border-radius:8px;color:#694400}.review-summary button{min-height:44px}@media(max-width:600px){.check-grid>div{display:block}.check-grid dd{text-align:left}.review-conclusion strong{font-size:19px}}
</style>
