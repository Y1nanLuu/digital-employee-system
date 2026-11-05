import time
from datetime import datetime, timezone
from decimal import Decimal
import httpx
from finance.schemas import BudgetResponse, ResultResponse
from . import config
from .knowledge import retrieve

def now():
    return datetime.now(timezone.utc).isoformat()

def event(task, status, message):
    task.status = status
    task.events = [*task.events, {'at': now(), 'status': status, 'message': message}]

def tool(task, method, path, body=None, *, response_model, expected):
    start = time.perf_counter()
    record = {'at': now(), 'tool': path, 'method': method, 'request': body}
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.request(method, config.FINANCE_URL + path, json=body,
                                      headers={'Authorization': f'Bearer {config.FINANCE_KEY}'})
        response.raise_for_status()
        data = response_model.model_validate(response.json()).model_dump(mode='json')
        if any(data.get(key) != value for key, value in expected.items()):
            raise ValueError('财务响应与请求的项目或审核结果不一致')
        record.update(success=True, response=data)
        return data
    except (httpx.HTTPError, ValueError) as exc:
        record.update(success=False, error=type(exc).__name__)
        message = ('模拟财务服务调用失败，请查看调用日志' if isinstance(exc, httpx.HTTPError)
                   else '模拟财务响应不符合接口约定，请查看调用日志')
        raise RuntimeError(message) from exc
    finally:
        record['duration_ms'] = round((time.perf_counter() - start) * 1000, 2)
        task.logs = [*task.logs, record]

def write_result(task, decision, reason):
    task.result = {**task.result, 'decision': decision, 'reason': reason, 'mode': 'rule_baseline'}
    event(task, 'writing_back', '写回模拟审核记录，不执行资金支付')
    payload = {'task_id': task.id, 'decision': decision, 'reason': reason}
    receipt = tool(task, 'POST', '/results', payload,
                   response_model=ResultResponse, expected=payload)
    task.result = {**task.result, 'receipt': receipt}
    event(task, 'succeeded' if decision == 'approved' else 'rejected', reason)
    task.finished_at = now()

def budget(task):
    project_id = task.payload['project_id']
    data = tool(task, 'GET', f'/budgets/{project_id}',
                response_model=BudgetResponse, expected={'project_id': project_id})
    task.result = {**task.result, 'budget': data}
    return Decimal(task.payload['amount']) <= Decimal(data['available'])

def execute(task):
    try:
        event(task, 'running', '按模拟制度执行规则基线')
        task.citations = retrieve(task.payload['category'])
        if not budget(task):
            write_result(task, 'rejected', '项目预算不足')
        elif Decimal(task.payload['amount']) > 1000 or not task.citations:
            task.manual = True
            task.approval_started_at = now()
            reason = '金额超过1000元' if task.citations else '缺少适用制度依据'
            task.result = {**task.result, 'reason': reason, 'mode': 'rule_baseline'}
            event(task, 'awaiting_approval', reason)
        else:
            write_result(task, 'approved', '金额、制度和预算校验通过')
    except RuntimeError as exc:
        fail(task, str(exc))

def fail(task, message):
    task.result = {**task.result, 'error': message}
    event(task, 'failed', message)
    task.finished_at = now()

def approve(task, request, user):
    timestamp = now()
    task.wait_seconds += (datetime.fromisoformat(timestamp) - datetime.fromisoformat(task.approval_started_at)).total_seconds()
    task.approvals = [*task.approvals, {'at': timestamp, 'actor': user['username'], **request.model_dump()}]
    try:
        event(task, 'running', '人工处理结束，恢复执行')
        if request.decision == 'reject':
            write_result(task, 'rejected', request.comment)
        else:
            if not task.citations:
                task.citations = [{'id': 'MANUAL', 'text': request.manual_basis.strip(), 'source': user['username'], 'version': timestamp}]
            if budget(task):
                write_result(task, 'approved', request.comment)
            else:
                write_result(task, 'rejected', '审批后重新查询发现预算不足')
    except RuntimeError as exc:
        fail(task, str(exc))

def serialize(task):
    keys = ['id','owner','status','payload','result','citations','logs','events','approvals','created_at','finished_at','manual']
    return {key: getattr(task, key) for key in keys}
