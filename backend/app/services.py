import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from finance.schemas import BudgetResponse, ResultInput, ResultResponse,EnterpriseResultResponse
from . import config
from .knowledge import retrieve
from .models import Task
from .schemas import ClaimPayload


def now():
    return datetime.now(timezone.utc).isoformat()


def event(task, status, message, **details):
    previous=task.status
    task.status = status
    task.events = [*task.events, {'at': now(), 'task_id': task.id,
                                 'from':previous,'status': status, 'message': message,
                                 'actor':'system','revision':task.revision,**details}]


def citations_for(category):
    return [{**item, 'retrieved_at': now()} for item in retrieve(category)]


class ToolFailure(RuntimeError):
    def __init__(self, message, stage):
        super().__init__(message)
        self.stage = stage


def tool(task, method, path, body=None, *, response_model, expected, stage):
    start = time.perf_counter()
    address = urlsplit(config.FINANCE_URL + path)
    endpoint = urlunsplit((address.scheme, address.netloc.rsplit('@', 1)[-1], address.path, '', ''))
    record = {'at': now(), 'task_id': task.id, 'stage': stage,
              'revision':task.revision,
              'tool': path.split('?', 1)[0].split('#', 1)[0], 'endpoint': endpoint,
              'method': method, 'request': body}
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.request(method, config.FINANCE_URL + path, json=body,
                                      headers={'Authorization': f'Bearer {config.FINANCE_KEY}'})
        response.raise_for_status()
        data = response_model.model_validate(response.json()).model_dump(mode='json')
        if any(data.get(key) != value for key, value in expected.items()):
            raise ValueError('响应中的项目编号、任务编号或审核内容与请求不一致')
        record.update(success=True, response=data)
        return data
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, httpx.TimeoutException):
            message = '模拟财务服务调用超时，结果尚未确认'
        elif isinstance(exc, httpx.HTTPStatusError):
            message = f'模拟财务服务返回HTTP {exc.response.status_code}'
        elif isinstance(exc, httpx.HTTPError):
            message = '无法连接模拟财务服务'
        elif isinstance(exc, ValidationError):
            fields = ', '.join('.'.join(map(str, item['loc'])) for item in exc.errors(include_input=False))
            message = '模拟财务响应字段不符合约定：' + fields
        else:
            message = '模拟财务响应格式或关联信息不符合约定'
        record.update(success=False, error=message, error_code=type(exc).__name__)
        raise ToolFailure(message, stage) from exc
    finally:
        record['duration_ms'] = round((time.perf_counter() - start) * 1000, 2)
        task.logs = [*task.logs, record]


def write_result(task, decision, reason, db):
    payload = {'task_id': task.id, 'decision': decision, 'reason': reason}
    enterprise = task.policy_version != 'demo-v1'
    if enterprise:
        invoice=task.payload.get('invoice') or {}
        taxable=task.payload['category']!='出差餐补'
        payload.update(invoice_no=invoice.get('invoice_no','') if taxable else '',invoice_code=invoice.get('invoice_code','') if taxable else '',
                       owner=task.owner,amount=task.payload['amount'],expense_date=task.payload['expense_date'])
        from .models import InvoiceFile
        source=db.get(InvoiceFile,str(task.payload.get('file_id')))
        payload['file_sha256']=source.digest if source and taxable else ''
    if task.pending_writeback and task.pending_writeback != payload:
        raise HTTPException(409, '已有待确认的写回内容，禁止更换审核决定')
    task.result = {**task.result, 'decision': decision, 'reason': reason, 'execution_stage': 'writeback'}
    task.failure_stage = None
    task.result.pop('error', None)
    task.result.pop('error_code', None)
    task.pending_writeback = payload
    event(task, 'writing_back', '审核决定已保存，等待模拟财务凭证确认')
    db.commit()  # 副作用发生前保存原编号、决定和规范化载荷。
    receipt = tool(task, 'POST', '/reviews/results' if enterprise else '/results', payload,
                   response_model=EnterpriseResultResponse if enterprise else ResultResponse,
                   expected=payload, stage='writeback')
    task.result = {**task.result, 'receipt': receipt}
    task.result.pop('error', None)
    task.result.pop('error_code', None)
    task.pending_writeback = {}
    task.failure_stage = None
    event(task, 'succeeded' if decision == 'approved' else 'rejected', reason)
    task.finished_at = now()
    if enterprise and decision=='rejected':
        from .models import InvoiceClaim
        for row in db.scalars(select(InvoiceClaim).where(InvoiceClaim.task_id==task.id)):db.delete(row)
    db.commit()


def budget(task, db):
    project_id = task.payload['project_id']
    task.result = {**task.result, 'execution_stage': 'budget_query'}
    db.commit()
    data = tool(task, 'GET', f'/budgets/{project_id}', response_model=BudgetResponse,
                expected={'project_id': project_id}, stage='budget_query')
    task.result = {**task.result, 'budget': data}
    db.commit()
    return ClaimPayload.model_validate(task.payload).amount <= BudgetResponse.model_validate(data).available


def fail(task, message, stage, db, code=None):
    task.failure_stage = stage
    task.result = {**task.result, 'error': message}
    if code:
        task.result = {**task.result, 'error_code': code}
    event(task, 'failed', message, failure_stage=stage, error_code=code)
    task.finished_at = now()
    db.commit()


def execute(task, db):
    if task.policy_version!='demo-v1':
        from .enterprise import execute as execute_enterprise,record_revision
        try:return execute_enterprise(task,db)
        finally:record_revision(task,db)
    try:
        task.failure_stage = None
        task.finished_at = None
        task.result = {**task.result, 'mode': task.parse_snapshot.get('mode', 'structured_input')}
        event(task, 'running', '按确认字段、有效制度与预算执行初审')
        task.citations = citations_for(task.payload['category'])
        if not budget(task, db):
            write_result(task, 'rejected', '项目预算不足', db)
            return
        reasons = []
        if ClaimPayload.model_validate(task.payload).amount > 1000:
            reasons.append('金额超过1000元')
        if not task.citations:
            reasons.append('缺少适用制度依据')
        if task.parse_snapshot.get('requires_manual_review'):
            reasons.append('模型处理异常，须由审批人核对字段')
        task.result = {**task.result, 'manual_reasons': reasons}
        if reasons:
            task.manual = True
            task.approval_started_at = now()
            task.result = {**task.result, 'reason': '；'.join(reasons)}
            event(task, 'awaiting_approval', task.result['reason'])
            db.commit()
        else:
            write_result(task, 'approved', '金额、制度和预算校验通过', db)
    except ToolFailure as exc:
        fail(task, str(exc), exc.stage, db)


def finish_approval(task, request, db):
    try:
        if request['decision'] == 'reject':
            write_result(task, 'rejected', request['comment'], db)
        elif budget(task, db):
            write_result(task, 'approved', request['comment'], db)
        else:
            write_result(task, 'rejected', '审批后重新查询发现预算不足', db)
    except ToolFailure as exc:
        fail(task, str(exc), exc.stage, db)


def approve(task, request, user, db):
    if task.policy_version!='demo-v1':
        from .enterprise import approve as approve_enterprise,record_revision
        try:return approve_enterprise(task,request,user,db)
        finally:record_revision(task,db)
    if request.decision=='return':
        raise HTTPException(422,'历史制度申请仅支持原有审批，请使用新版本申请')
    # 所有422校验先完成；在此之前不改变任务、等待时间、有效字段或审批记录。
    corrected = request.corrected_fields.model_dump(mode='json', exclude_unset=True) if request.corrected_fields else {}
    previous_citations = task.citations
    candidate = ClaimPayload.model_validate({**task.payload, **corrected}).model_dump(mode='json')
    citations = citations_for(candidate['category']) if request.decision == 'approve' else task.citations
    if request.decision == 'approve':
        if (corrected or task.parse_snapshot.get('requires_manual_review')) and not request.reviewed_fields:
            raise HTTPException(422, '通过前须显式核对字段：reviewed_fields=true')
        if not citations and not request.manual_basis:
            raise HTTPException(422, '缺少制度依据，通过前必须补充人工依据')
    timestamp = now()
    if corrected:
        task.amendments = [*task.amendments, {
            'at': timestamp, 'actor': user['username'], 'reason': request.correction_reason,
            'before': {key: task.payload[key] for key in corrected}, 'after': corrected,
        }]
        task.payload = candidate
    if request.decision == 'approve' and not citations:
        citations = [{'id': 'MANUAL', 'text': request.manual_basis,
                      'source': user['username'], 'version': timestamp, 'retrieved_at': timestamp}]
    task.citations = citations
    task.wait_seconds += (datetime.fromisoformat(timestamp) - datetime.fromisoformat(task.approval_started_at)).total_seconds()
    record = {'at': timestamp, 'actor': user['username'], **request.model_dump(mode='json'),
              'citations_before': previous_citations, 'citations_after': citations}
    task.approvals = [*task.approvals, record]
    task.failure_stage = None
    task.finished_at = None
    event(task, 'running', '人工核对已提交，沿原任务继续硬规则校验')
    db.commit()  # 审批、补正、状态原子提交；后续审批看到running返回409。
    finish_approval(task, record, db)


def mark_interrupted(db):
    for task in db.scalars(select(Task).where(Task.status.in_(['queued', 'running', 'writing_back']))):
        prior = task.status
        stage = 'writeback' if task.pending_writeback else task.result.get('execution_stage', 'initial_review')
        task.result = {**task.result, 'interrupted_status': prior}
        fail(task, '服务执行中断，须按原阶段和原载荷受限处置', stage, db, 'process_interrupted')


def recover(task, action, actor, reason, db):
    if task.status != 'failed':
        raise HTTPException(409, '仅允许处置失败任务，业务终态不可重新打开')
    if not actor.strip() or not reason.strip():
        raise HTTPException(422, '恢复操作须记录操作者和原因')
    if action == 'replay-writeback':
        payload = task.pending_writeback
        if not payload:
            raise HTTPException(409, '任务没有待写回快照，禁止构造新的写回载荷')
        ResultInput.model_validate(payload)
        if payload['task_id'] != task.id or payload['decision'] != task.result.get('decision'):
            raise HTTPException(409, '待写回快照与原审核决定不一致，须人工对账')
        event(task, 'writing_back', '受限运维按原载荷重放写回', actor=actor, reason=reason)
        try:
            write_result(task, payload['decision'], payload['reason'], db)
        except ToolFailure as exc:
            fail(task, str(exc), exc.stage, db)
    elif action == 'retry-budget':
        if (task.pending_writeback or task.result.get('receipt')
                or task.failure_stage not in ['initial_review', 'budget_query', 'invoice_check', 'duplicate_check','file_validation']):
            raise HTTPException(409, '仅可恢复已确认尚未发生写回的预算查询阶段')
        task.finished_at = None
        task.failure_stage = None
        task.result = {key: value for key, value in task.result.items() if key not in ['error', 'error_code']}
        event(task, 'running', '受限运维恢复预算查询', actor=actor, reason=reason)
        db.commit()
        if task.policy_version!='demo-v1':
            from .enterprise import execute as execute_enterprise, finish_approval as finish_enterprise_approval
            decision=next((record for record in reversed(task.approvals)
                           if record.get('revision')==task.revision),None)
            if decision and decision['decision'] in ['approve','reject']:
                finish_enterprise_approval(task,decision,db)
            else:
                execute_enterprise(task,db)
        elif task.approvals:
            finish_approval(task, task.approvals[-1], db)
        else:
            execute(task, db)
    else:
        raise HTTPException(422, '不支持的恢复操作')
    if task.policy_version!='demo-v1':
        from .enterprise import record_revision
        record_revision(task,db)


def serialize(task,viewer=None):
    keys = ['id', 'business_no', 'owner', 'status', 'payload', 'result', 'citations', 'logs', 'events',
            'approvals', 'created_at', 'finished_at', 'manual', 'submission_payload',
            'parse_snapshot', 'confirmations', 'amendments', 'failure_stage','policy_version','revision','return_draft']
    result={key: getattr(task, key) for key in keys}
    result['payload']=ClaimPayload.model_validate(task.payload).model_dump(mode='json')
    if not viewer or viewer.get('username')!=task.owner:result['return_draft']={}
    from sqlalchemy.orm import object_session
    from .models import TaskRevision
    db=object_session(task)
    result['revisions']=[{**{key:getattr(row,key) for key in ['number','actor','reason','payload','before','created_at']},
                         'snapshot':{k:v for k,v in row.snapshot.items() if k!='_resubmit_request'}}
                         for row in db.scalars(select(TaskRevision).where(TaskRevision.task_id==task.id).order_by(TaskRevision.number))] if db else []
    return result
