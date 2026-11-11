"""Generate reproducible, synthetic policy/LLM/tool/workflow test inputs."""
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
import json
ROOT=Path(__file__).resolve().parent.parent

def generate():
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date();date=today.isoformat();prior=(today-timedelta(days=1)).isoformat()
    categories=['交通费','住宿费','业务招待费','办公用品','通讯费','培训会议费','软件服务费','出差餐补','其他']
    amounts=['0.01','0.50','99.99','100.00','199.99','200.00','299.99','300.00','399.99','400.00','499.99','500.00','999.00','1000.00','1000.01','1999.99','2000.00','2000.01','4999.99','5000.00']
    cases=[]
    def append(layer,body,expected,**extra):
        cases.append({'id':f'EV2-{len(cases)+1:03}','layer':layer,'policy_version':'enterprise-demo-v2','reference_date':date,
                      'input':body,'expected':expected,**extra})
    for category in categories:
        for amount in amounts:
            manual=[]
            if Decimal(amount)>1000:manual.append('GEN-03')
            if category=='住宿费' and Decimal(amount)>500:manual.append('TRV-05')
            if category=='业务招待费':
                manual.append('ENT-01')
                if Decimal(amount)>1000:manual.append('ENT-02')  # 5 attendees, 200/person
            if category=='通讯费':manual.append('TEL-01')
            if category=='培训会议费':manual.append('EDU-01')
            if category=='软件服务费':manual.append('IT-01')
            if category=='出差餐补':
                manual.append('MEAL-01')
                if Decimal(amount)>100:manual.append('MEAL-02')
            if category=='其他':manual.append('OTH-01')
            payload={'project_id':'P001','department':'研发部','category':category,'amount':amount,'expense_date':date,
                     'description':f'测试项目因公发生{category}{amount}元','confirmed':True,
                     'invoice':{'invoice_no':'26'+f'{len(cases)+10000:018d}','invoice_code':'','invoice_date':prior,
                                'buyer':'示例科技有限公司','seller':'合成测试供应商','total':amount,'item_name':category},
                     'details':{'city':'北京','check_in':prior,'check_out':date,'trip_start':date,'trip_end':date,
                                'approval_no':'TEST-APPROVAL','proof_reference':'合成明细及业务证明','route':'公司至测试客户',
                                'seat':'二等座','attendees':5,'companions':1,'business_partner':'测试客户',
                                'service_period':date[:7],'supplied_breakfasts':0,'supplied_lunches':0,'supplied_dinners':0}}
            append('policy_and_llm',payload,{'hard_codes':[],'manual_codes':sorted(set(manual)),
                                           'route_with_valid_pass_opinion':'awaiting_approval' if manual else 'succeeded'},
                   tool_fixture={'budget':'10000.00','invoice_status':'valid','duplicate':False,'simulated':True},
                   identity_fixture={'grade':'staff'})
    for grade,limits in [('staff',[500,400,300]),('manager',[650,500,400])]:
        for city,limit in zip(['北京','杭州','佛山'],limits):
            for offset in [Decimal('-.01'),Decimal('0'),Decimal('.01')]:
                body=json.loads(json.dumps(next(c['input'] for c in cases if c['input']['category']=='住宿费')))
                body['details']['city']=city;body['amount']=body['invoice']['total']=format(Decimal(limit)+offset,'.2f')
                body['description']=f"测试项目因公在{city}住宿一晚，费用{body['amount']}元"
                body['invoice']['invoice_no']='26'+f'{len(cases)+10000:018d}'
                append('hotel_boundary',body,{'hard_codes':[],'manual_codes':['TRV-05'] if offset>0 else []},identity_fixture={'grade':grade})
    for status,route in [('valid','succeeded'),('invalid','rejected'),('unknown','awaiting_approval')]:
        append('tool',{'invoice_status':status}, {'route':route,'simulated':True})
    for kind in ['same_invoice','same_original_changed_number','same_owner_date_amount_different_invoice']:
        append('duplicate',{'kind':kind},{'route':'awaiting_approval' if kind.startswith('same_owner') else 'rejected'})
    for action in ['return_reason_required','approver_cannot_edit','owner_resubmit','other_owner_forbidden','non_returned_forbidden','same_key_same_body','same_key_changed_body','old_revision_immutable','requery_all_tools','restart_returned']:
        append('workflow',{'action':action},{'criterion':'见测试方案对应步骤及AC44/45/48/50'})
    for amount in ['0','-1','5000.01','1.001','1e3','NaN','Infinity',True,None,'','+1','1,000.00']:
        append('validation',{'amount':amount},{'http_status':422,'task_created':False})
    for action in ['missing_original','hash_tampered','unauthorized_preview','sqlite_migration','backup_restore','container_volume']:
        append('storage',{'action':action},{'criterion':'见AC31—35及AC48，按实际环境执行'})
    for kind in ['text_pdf','png','jpg','scanned_pdf','rotated_image','multi_amount','missing_text','oversized_file']:
        append('recognition',{'kind':kind},{'criterion':'读取真实脱敏样本；缺失显式提示，金额不得混淆'})
    assert len(cases)==240
    return cases

if __name__=='__main__':
    out=ROOT/'docs/test-evidence/enterprise-v2/test-cases.jsonl';out.parent.mkdir(parents=True,exist_ok=True)
    cases=generate();out.write_text('\n'.join(json.dumps(c,ensure_ascii=False) for c in cases)+'\n',encoding='utf-8')
    print(json.dumps({'cases':len(cases),'policy_and_hotel_inputs':198,'path':str(out)},ensure_ascii=False))
