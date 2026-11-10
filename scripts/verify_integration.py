"""D3 集成面校验：状态转换、账号权限、解析快照、审批补正与指标口径。

与另两个脚本的分工：
  - smoke.py            自起自停临时服务，覆盖进程重启与幂等（提交前门禁）
  - verify_system.py    README 六个演示步骤的接口契约（验收现场）
  - verify_integration.py（本脚本）跨模块集成面：事件序列、权限矩阵、parse_id 链路、
                        审批补正、指标与任务数据交叉核对，均针对标准端口上真实运行的服务。

用法（先启动 8000 与 8001）：
    .\\venv\\Scripts\\python.exe scripts/verify_integration.py
环境变量与 verify_system.py 一致：BACKEND_URL、FINANCE_URL、DEMO_PASSWORD、FINANCE_KEY。
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

BACKEND = os.getenv('BACKEND_URL', 'http://127.0.0.1:8000')
FINANCE = os.getenv('FINANCE_URL', 'http://127.0.0.1:8001')
PASSWORD = os.getenv('DEMO_PASSWORD', 'demo123')
FINANCE_KEY = os.getenv('FINANCE_KEY', 'local-finance-key')
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []


def call(base, path, body=None, role='employee', key=None, method=None, password=None, headers=None):
    hdrs = dict(headers or {})
    if role:
        credential = base64.b64encode(f'{role}:{password or PASSWORD}'.encode()).decode()
        hdrs['Authorization'] = 'Basic ' + credential
    data = None
    if body is not None:
        hdrs['Content-Type'] = 'application/json'
        data = json.dumps(body).encode()
    if key:
        hdrs['Idempotency-Key'] = key
    request = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with LOCAL_HTTP.open(request, timeout=20) as response:
        return response.status, json.load(response)


def status_of(base, path, body=None, role='employee', key=None):
    try:
        return call(base, path, body, role=role, key=key)[0]
    except urllib.error.HTTPError as exc:
        return exc.code


def expect(label, condition, detail=''):
    results.append((label, bool(condition), detail))
    print(('PASS  ' if condition else 'FAIL  ') + label + ('' if condition else f'  -> {detail}'))


today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def claim(**changes):
    body = {'project_id': 'P001', 'category': '交通费', 'amount': '300.00', 'expense_date': today,
            'description': '客户现场往返交通费300元', 'confirmed': True}
    body.update(changes)
    return body


def sequences(task):
    return [item['status'] for item in task['events']]


# ---------------------------------------------------------------- 1. 健康检查与版本
code, health = call(BACKEND, '/health', role=None)
expect('业务服务健康检查返回 ok', code == 200 and health.get('status') == 'ok', health)
expect('健康检查暴露集成版本与阶段', bool(health.get('version')) and bool(health.get('stage')), health)
expect('健康检查暴露AI开关与数据库方言',
       isinstance(health.get('ai_enabled'), bool) and health.get('database') in ('sqlite', 'postgresql'), health)
code, finance_health = call(FINANCE, '/health', role=None)
expect('模拟财务服务健康检查返回 ok', code == 200 and finance_health.get('status') == 'ok', finance_health)

# ---------------------------------------------------------------- 2. 状态转换与事件序列
code, t1 = call(BACKEND, '/api/tasks', claim(), key=str(uuid4()))
expect('自动初审任务终态为 succeeded', t1['status'] == 'succeeded', t1['status'])
expect('自动初审事件序列为 running→writing_back→succeeded',
       sequences(t1) == ['running', 'writing_back', 'succeeded'], sequences(t1))
expect('事件均携带同一任务编号',
       all(item.get('task_id') == t1['id'] for item in t1['events']), t1['events'])
expect('工具日志按阶段记录预算查询与写回',
       [log.get('stage') for log in t1['logs']] == ['budget_query', 'writeback'], [log.get('stage') for log in t1['logs']])
expect('工具日志均携带同一任务编号',
       all(log.get('task_id') == t1['id'] for log in t1['logs']), t1['logs'])

key2 = str(uuid4())
office = claim(category='办公用品', amount='1500.00', description='客户现场采购办公用品1500元')
code, t2 = call(BACKEND, '/api/tasks', office, key=key2)
expect('超阈值任务进入 awaiting_approval', t2['status'] == 'awaiting_approval', t2['status'])
expect('转人工事件序列为 running→awaiting_approval', sequences(t2) == ['running', 'awaiting_approval'], sequences(t2))
expect('转人工原因写入手工原因列表', t2['result'].get('manual_reasons'), t2['result'].get('manual_reasons'))

code, t2a = call(BACKEND, f"/api/tasks/{t2['id']}/approval",
                 {'decision': 'approve', 'comment': '用途明确，同意初审', 'reviewed_fields': True}, role='approver')
expect('审批通过后任务续跑至 succeeded', t2a['status'] == 'succeeded', t2a['status'])
expect('审批续跑事件接在原序列之后',
       sequences(t2a) == ['running', 'awaiting_approval', 'running', 'writing_back', 'succeeded'], sequences(t2a))
expect('审批记录保留条款变更前后对照',
       t2a['approvals'][-1].get('citations_before') is not None and t2a['approvals'][-1].get('citations_after'), t2a['approvals'][-1])

code, t3 = call(BACKEND, '/api/tasks', office, key=str(uuid4()))
code, t3a = call(BACKEND, f"/api/tasks/{t3['id']}/approval",
                 {'decision': 'reject', 'comment': '超标准，材料不全', 'reviewed_fields': True}, role='approver')
expect('审批拒绝后任务终止为 rejected', t3a['status'] == 'rejected', t3a['status'])
expect('拒绝事件序列为 running→awaiting_approval→running→writing_back→rejected',
       sequences(t3a) == ['running', 'awaiting_approval', 'running', 'writing_back', 'rejected'], sequences(t3a))

code, t4 = call(BACKEND, '/api/tasks',
                claim(project_id='P002', amount='600.00', description='客户现场交通费600元'), key=str(uuid4()))
expect('预算不足直接拒绝且不经人工', t4['status'] == 'rejected' and '预算' in t4['result'].get('reason', ''), t4['result'])
expect('预算拒绝事件序列为 running→writing_back→rejected',
       sequences(t4) == ['running', 'writing_back', 'rejected'], sequences(t4))

code, t5 = call(BACKEND, '/api/tasks', claim(category='其他', description='专项其他费用300元元'), key=str(uuid4()))
expect('无制度依据转人工并说明原因',
       t5['status'] == 'awaiting_approval' and '缺少适用制度依据' in '；'.join(t5['result'].get('manual_reasons') or []),
       t5['result'].get('manual_reasons'))

# ---------------------------------------------------------------- 3. 账号权限矩阵
expect('员工不能审批他人/本人任务(403)', status_of(BACKEND, f"/api/tasks/{t5['id']}/approval",
       {'decision': 'approve', 'comment': '越权'}, role='employee') == 403)
expect('员工不能查看指标(403)', status_of(BACKEND, '/api/metrics') == 403)
expect('审批人不能提交报销(403)', status_of(BACKEND, '/api/tasks', claim(), role='approver', key=str(uuid4())) == 403)
expect('管理员不能提交报销(403)', status_of(BACKEND, '/api/tasks', claim(), role='admin', key=str(uuid4())) == 403)
expect('管理员不能审批(403)', status_of(BACKEND, f"/api/tasks/{t5['id']}/approval",
       {'decision': 'approve', 'comment': '管理员越权'}, role='admin') == 403)
expect('审批人可查看指标(200)', status_of(BACKEND, '/api/metrics', role='approver') == 200)
expect('管理员可查看指标(200)', status_of(BACKEND, '/api/metrics', role='admin') == 200)
expect('审批人可查看全部任务(200)', status_of(BACKEND, '/api/tasks', role='approver') == 200)
expect('员工可查看本人任务(200)', status_of(BACKEND, f"/api/tasks/{t1['id']}", role='employee') == 200)
expect('无认证访问任务列表返回401', status_of(BACKEND, '/api/tasks', role=None) == 401)

# ---------------------------------------------------------------- 4. 解析快照与字段冲突链路
description = '客户现场往返交通费320元'
code, parsed = call(BACKEND, '/api/parse', {'description': description})
expect('/api/parse 返回解析编号与耗时', bool(parsed.get('parse_id')) and 'latency_ms' in parsed, parsed)
expect('/api/parse 返回金额候选与冲突标记',
       isinstance(parsed.get('amount_candidates'), list) and isinstance(parsed.get('conflicts'), list), parsed)
parse_id = parsed['parse_id']
code, bound = call(BACKEND, '/api/tasks',
                   claim(amount='320.00', description=description, parse_id=parse_id), key=str(uuid4()))
expect('携带 parse_id 提交成功并记录解析快照', code == 201 and bound['parse_snapshot'].get('parse_id') == parse_id,
       bound.get('parse_snapshot'))
expect('解析快照与提交载荷一并留存',
       bound['submission_payload'].get('parse_id') == parse_id and bound['confirmations'], bound['submission_payload'])
expect('复用他人或不存在的 parse_id 被拒(422)', status_of(BACKEND, '/api/tasks',
       claim(amount='320.00', description=description, parse_id=str(uuid4())), key=str(uuid4())) == 422)
expect('说明变更后复用旧 parse_id 被拒(422)', status_of(BACKEND, '/api/tasks',
       claim(amount='320.00', description='另一笔交通费320元', parse_id=parse_id), key=str(uuid4())) == 422)
conflict_body = claim(amount='400.00', description=description)
expect('说明与申报金额不一致需显式确认(422)',
       status_of(BACKEND, '/api/tasks', conflict_body, key=str(uuid4())) == 422)
code, confirmed = call(BACKEND, '/api/tasks',
                       {**conflict_body, 'conflict_acknowledged': True, 'confirmation_note': '以发票金额400元为准'},
                       key=str(uuid4()))
expect('显式确认冲突后提交成功并保留确认记录',
       code == 201 and confirmed['confirmations'][-1].get('conflicts'), confirmed.get('confirmations'))

# ---------------------------------------------------------------- 5. 审批补正链路
code, pending = call(BACKEND, '/api/tasks', office, key=str(uuid4()))
expect('补正前置任务处于 awaiting_approval', pending['status'] == 'awaiting_approval', pending['status'])
expect('补正但未显式核对字段被拒(422)', status_of(BACKEND, f"/api/tasks/{pending['id']}/approval",
       {'decision': 'approve', 'comment': '按900核减', 'corrected_fields': {'amount': '900.00'},
        'correction_reason': '发票金额小于申报金额'}, role='approver') == 422)
expect('拒绝请求不允许同时补正(422)', status_of(BACKEND, f"/api/tasks/{pending['id']}/approval",
       {'decision': 'reject', 'comment': '拒绝', 'corrected_fields': {'amount': '900.00'},
        'correction_reason': '误填'}, role='approver') == 422)
code, amended = call(BACKEND, f"/api/tasks/{pending['id']}/approval",
                     {'decision': 'approve', 'comment': '按900核减后通过', 'reviewed_fields': True,
                      'corrected_fields': {'amount': '900.00'}, 'correction_reason': '发票金额小于申报金额'},
                     role='approver')
expect('补正后沿原任务续跑并成功', amended['status'] == 'succeeded', amended['status'])
expect('补正保留原值与新值', amended['payload']['amount'] == '900.00'
       and amended['amendments'][-1]['before']['amount'] == '1500.00'
       and amended['amendments'][-1]['after']['amount'] == '900.00', amended.get('amendments'))
expect('补正后重新执行阈值校验（900元不再转人工）',
       'awaiting_approval' not in sequences(amended)[2:], sequences(amended))

# ---------------------------------------------------------------- 6. 指标口径与任务数据交叉核对
code, metrics = call(BACKEND, '/api/metrics', role='admin')
code, all_tasks = call(BACKEND, '/api/tasks', role='admin')
done = [item for item in all_tasks if item['status'] in ('succeeded', 'rejected')]
manual = [item for item in all_tasks if item['manual']]
expect('指标总数与任务列表一致', metrics['total'] == len(all_tasks), (metrics['total'], len(all_tasks)))
expect('指标完成数与终态任务数一致', metrics['completed'] == len(done), (metrics['completed'], len(done)))
expect('转人工率与任务 manual 标记一致',
       abs((metrics['manual_rate'] or 0) - (len(manual) / len(all_tasks) if all_tasks else 0)) < 1e-9,
       (metrics['manual_rate'], len(manual), len(all_tasks)))
expect('系统耗时不高于端到端耗时',
       metrics['average_processing_seconds'] is None
       or metrics['average_processing_seconds'] <= metrics['average_end_to_end_seconds'],
       (metrics['average_processing_seconds'], metrics['average_end_to_end_seconds']))
expect('指标键齐全', all(key in metrics for key in
       ['total', 'completed', 'completion_rate', 'manual_rate',
        'average_processing_seconds', 'average_end_to_end_seconds']), sorted(metrics))

# ---------------------------------------------------------------- 7. 财务凭证与编号回溯
code, detail = call(BACKEND, f"/api/tasks/{t1['id']}")
expect('财务凭证编号可由任务编号追溯',
       detail['result']['receipt']['receipt_id'] == 'demo-' + t1['id'], detail['result'].get('receipt'))
code, receipt = call(FINANCE, '/results',
                     {'task_id': t1['id'], 'decision': 'approved', 'reason': '金额、制度和预算校验通过'},
                     role=None, method='POST', headers={'Authorization': f'Bearer {FINANCE_KEY}'})
expect('财务服务同内容重放返回原凭证', receipt.get('receipt_id') == 'demo-' + t1['id'], receipt.get('receipt_id'))

failed = [item for item in results if not item[1]]
print(f'\n总计 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项')
sys.exit(1 if failed else 0)
