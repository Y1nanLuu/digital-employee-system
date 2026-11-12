"""Enterprise SOP: parse, retrieve, three tools, LLM opinion, controlled routing."""
from decimal import Decimal
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from finance.schemas import InvoiceCheckResponse,DuplicateResponse
from . import policies,enterprise_rules,reviewer
from .models import InvoiceClaim,InvoiceFile,Task

def invoice_key(number,code=''):
    return number if len(number)==20 else code+':'+number

def record_revision(task,db):
    from .models import TaskRevision
    row=db.scalar(select(TaskRevision).where(TaskRevision.task_id==task.id,TaskRevision.number==task.revision))
    if row:
        row.snapshot={**row.snapshot,'review_record':{'result':task.result,'citations':task.citations,
                      'status':task.status,'logs':[x for x in task.logs if x.get('revision')==task.revision]}}
        db.commit()

def reserve(db,task):
    inv=task.payload.get('invoice') or {};number=inv.get('invoice_no','') if enterprise_rules.requires_invoice(task.payload,policies.for_task(db,task)) else ''
    for row in db.scalars(select(InvoiceClaim).where(InvoiceClaim.task_id==task.id)):
        if row.invoice_key!=invoice_key(number,inv.get('invoice_code','')):db.delete(row)
    db.flush()
    if not number:db.commit();return
    key=invoice_key(number,inv.get('invoice_code',''))
    if not db.get(InvoiceClaim,key):
        try:
            with db.begin_nested():
                db.add(InvoiceClaim(invoice_key=key,task_id=task.id));db.flush()
        except IntegrityError:pass
        db.commit()

def checks(task,db):
    from . import services
    from .auth import USERS
    policy=policies.for_task(db,task)
    item=db.get(InvoiceFile,str(task.payload.get('file_id')))
    if not item:raise services.ToolFailure('申请关联的原件记录缺失','file_validation')
    from . import files
    try:files.read(db,item,{'username':'system'},'validation')
    except HTTPException as exc:raise services.ToolFailure('原件缺失或摘要异常，请恢复档案后处理','file_validation') from exc
    findings=enterprise_rules.evaluate(task.payload,policy,USERS.get(task.owner,{}).get('grade','staff'))
    services.event(task,'running','调用预算、模拟发票查验与查重工具',phase='tool_calling');db.commit()
    enough=services.budget(task,db)
    inv=task.payload.get('invoice') or {}
    body={'task_id':task.id,'invoice_no':inv.get('invoice_no',''),'invoice_code':inv.get('invoice_code',''),
          'invoice_date':inv.get('invoice_date') or '', 'total':inv.get('total',''),
          'owner':task.owner,'amount':task.payload['amount'],'expense_date':task.payload['expense_date']}
    item=db.get(InvoiceFile,str(task.payload.get('file_id')))
    body['file_sha256']=item.digest if item and enterprise_rules.requires_invoice(task.payload,policies.for_task(db,task)) else ''
    if not enterprise_rules.requires_invoice(task.payload,policy):body.update(invoice_no='',invoice_code='')
    expected={'task_id':task.id,'invoice_no':body['invoice_no'],'simulated':True}
    verified=services.tool(task,'POST','/checks/invoice',body,response_model=InvoiceCheckResponse,expected=expected,stage='invoice_check')
    duplicate=services.tool(task,'POST','/checks/duplicate',body,response_model=DuplicateResponse,expected=expected,stage='duplicate_check')
    if not enough:findings['hard'].append({'code':'GEN-06','message':'项目预算不足'})
    if enterprise_rules.requires_invoice(task.payload,policies.for_task(db,task)):
        if verified['status']=='invalid':findings['hard'].append({'code':'INV-04','message':verified['reason']})
        elif verified['status']=='unknown':findings['manual'].append({'code':'INV-04','message':'模拟台账未知，请人工核对票据'})
    local=db.get(InvoiceClaim,invoice_key(body['invoice_no'],body['invoice_code'])) if body['invoice_no'] else None
    same_file=False
    if item and enterprise_rules.requires_invoice(task.payload,policies.for_task(db,task)):
        ids={x.id for x in db.scalars(select(InvoiceFile).where(InvoiceFile.digest==item.digest))}
        same_file=any(t.payload.get('file_id') in ids and enterprise_rules.requires_invoice(t.payload,policies.for_task(db,t))
                      for t in db.scalars(select(Task).where(Task.id!=task.id,Task.status.notin_(['rejected','cancelled']))))
    if duplicate['duplicate'] or same_file or (local and local.task_id!=task.id):findings['hard'].append({'code':'INV-05','message':'同票或相同原件已报销或被其他申请占用'})
    elif duplicate['suspected']:findings['manual'].append({'code':'INV-06','message':'同员工同日同额存在疑似重复，请核对'})
    task.result={**task.result,'invoice_check':verified,'duplicate_check':duplicate,'rule_findings':findings}
    db.commit();return findings

def execute(task,db):
    from . import services
    from .ai import parse_fields
    try:
        task.finished_at=None;task.failure_stage=None
        services.event(task,'running','解析已确认报销字段',phase='parsing');db.commit()
        from .auth import USERS
        canonical='\n'.join([f"提交人：{USERS.get(task.owner,{}).get('name',task.owner)}",f"部门：{task.payload.get('department','')}",
                             f"金额：{task.payload['amount']}元",f"日期：{task.payload['expense_date']}",f"事由：{task.payload['description']}"])
        parsed=parse_fields(canonical)
        task.result={**task.result,'field_analysis':parsed,'mode':parsed.get('mode','rule_baseline')}
        services.event(task,'running','检索任务生效制度',phase='retrieving')
        task.citations=policies.retrieve(policies.for_task(db,task),task.payload['category']);db.commit()
        findings=checks(task,db)
        if parsed.get('requires_manual_review'):findings['manual'].append({'code':'GEN-01','message':'字段解析模型出现异常，须人工核对'})
        services.event(task,'running','生成数字员工结构化初审意见',phase='drafting');db.commit()
        opinion=reviewer.review({**task.payload,'employee_confirmation':task.confirmations[-1].get('note','') if task.confirmations else ''},task.citations,{k:task.result.get(k) for k in ['budget','invoice_check','duplicate_check']},findings)
        task.logs=[*task.logs,{'at':services.now(),'task_id':task.id,'revision':task.revision,'stage':'initial_review',
                    'tool':'llm_initial_review','method':'POST','request':{'policy_version':task.policy_version},
                    'success':opinion['source']=='llm','response':opinion,'duration_ms':opinion.get('latency_ms',0)}]
        task.result={**task.result,'initial_review':opinion}
        if findings['hard']:
            reason='；'.join(x['message'] for x in findings['hard'])
            task.result={**task.result,'initial_decision':'拒绝','reason':reason}
            services.write_result(task,'rejected',reason,db);return
        reasons=[x['message'] for x in findings['manual']]
        if opinion['source']!='llm' or opinion['conclusion']!='通过':reasons.append('模型初审需人工核对：'+'；'.join(opinion['reasons']))
        if reasons:
            task.manual=True;task.approval_started_at=services.now()
            task.result={**task.result,'initial_decision':'存疑','manual_reasons':reasons,'reason':'；'.join(reasons)}
            services.event(task,'awaiting_approval','等待人工处理');db.commit()
        else:
            task.result={**task.result,'initial_decision':'通过'}
            services.write_result(task,'approved','制度、工具及模型初审一致通过',db)
    except services.ToolFailure as exc:services.fail(task,str(exc),exc.stage,db)

def approve(task,request,user,db):
    from . import services
    if request.corrected_fields is not None:raise HTTPException(422,'审批人不能修改字段，请退回员工补充')
    if request.decision=='approve' and not request.reviewed_fields:raise HTTPException(422,'请确认已核对原票、字段、制度和工具结果')
    if request.decision=='approve' and enterprise_rules.requires_invoice(task.payload,policies.for_task(db,task)):
        inv=task.payload.get('invoice') or {}
        if any(not inv.get(k) for k in ['invoice_no','invoice_date','buyer','seller','total','item_name']):
            raise HTTPException(422,'关键票面字段缺失，请退回员工补齐后重新初审')
    if request.decision=='approve':
        from .auth import USERS
        findings=enterprise_rules.evaluate(task.payload,policies.for_task(db,task),USERS.get(task.owner,{}).get('grade','staff'))
        if any(any(word in x['message'] for word in ['请补充','缺少','需要修正']) for x in findings['manual']):
            raise HTTPException(422,'必需业务材料仍未补齐，请退回员工修改后重新初审')
        if task.payload['category']=='其他' and not request.manual_basis.strip():raise HTTPException(422,'其他费用通过前请补充人工适用依据')
    at=services.now()
    record={'at':at,'actor':user['username'],'decision':request.decision,'comment':request.comment,'manual_basis':request.manual_basis,'revision':task.revision}
    if task.approval_started_at:
        task.wait_seconds+=(datetime.fromisoformat(at)-datetime.fromisoformat(task.approval_started_at)).total_seconds()
    task.approvals=[*task.approvals,record]
    if request.decision=='return':
        task.result={**task.result,'return_reason':request.comment,'returned_at':at}
        services.event(task,'returned','审批退回，请员工修改后重新提交',actor=user['username'],reason=request.comment);db.commit();return
    services.event(task,'running','人工决定已记录，继续核对工具',actor=user['username']);db.commit()
    finish_approval(task,record,db)


def finish_approval(task,request,db):
    """Continue a persisted decision without recording another approval or wait."""
    from . import services
    try:
        if request['decision']=='reject':
            services.write_result(task,'rejected',request['comment'],db)
            return
        findings=checks(task,db)
        if findings['hard']:services.write_result(task,'rejected','；'.join(x['message'] for x in findings['hard']),db)
        else:services.write_result(task,'approved',request['comment'],db)
    except services.ToolFailure as exc:services.fail(task,str(exc),exc.stage,db)
