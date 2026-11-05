"""对真实运行的标准端口服务执行 README 演示步骤与接口约定校验。

与 smoke.py 的分工：smoke.py 自起自停临时服务并使用临时库，适合作为提交前门禁；
本脚本校验标准端口上真实运行的整套服务，用于联调与验收现场。

用法（先启动 8000 与 8001）：
    .\\.venv\\Scripts\\python.exe scripts/verify_system.py

每项断言打印 PASS 或 FAIL，全通过返回 0，否则返回 1。数据写入 backend/data 下的演示库。
"""
import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

BACKEND = 'http://127.0.0.1:8000'
FINANCE = 'http://127.0.0.1:8001'
PASSWORD = 'demo123'
FINANCE_KEY = 'local-finance-key'
results = []


def call(base, path, body=None, role='employee', key=None, headers=None, method=None, password=PASSWORD):
    hdrs = dict(headers or {})
    if role:
        hdrs['Authorization'] = 'Basic ' + base64.b64encode(f'{role}:{password}'.encode()).decode()
    data = None
    if body is not None:
        hdrs['Content-Type'] = 'application/json'
        data = json.dumps(body).encode()
    if key:
        hdrs['Idempotency-Key'] = key
    request = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, json.load(response)


def expect(label, condition, detail=''):
    results.append((label, bool(condition), detail))
    print(('PASS  ' if condition else 'FAIL  ') + label + ('' if condition else f'  -> {detail}'))
    return condition


def status_of(base, path, body=None, role='employee', key=None):
    try:
        return call(base, path, body, role=role, key=key)[0]
    except urllib.error.HTTPError as exc:
        return exc.code


today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
base_body = {'project_id': 'P001', 'category': '交通费', 'amount': '300.00',
             'expense_date': today, 'description': '客户现场往返交通费300元', 'confirmed': True}

# 0. 健康检查与身份
code, health = call(BACKEND, '/health', role=None)
expect('GET /health 返回 ok', code == 200 and health['status'] == 'ok', health)
code, health = call(FINANCE, '/health', role=None)
expect('GET finance /health 返回 ok', code == 200 and health['status'] == 'ok', health)
code, me = call(BACKEND, '/api/me', role='employee')
expect('/api/me 返回 employee 与角色', me['username'] == 'employee' and me['role'] == 'employee', me)
code, me = call(BACKEND, '/api/me', role='approver')
expect('/api/me 返回 approver 与角色', me['role'] == 'approver', me)

# 1. 普通金额自动初审通过
key1 = str(uuid4())
code, t1 = call(BACKEND, '/api/tasks', base_body, key=key1)
expect('场景1 P001 交通费300 自动初审通过', code == 201 and t1['status'] == 'succeeded', t1.get('status'))
expect('场景1 日志含预算查询与写回', len(t1['logs']) == 2, len(t1['logs']))
expect('场景1 引用制度条款', len(t1['citations']) >= 1, t1['citations'])
expect('场景1 幂等重放返回同一任务', call(BACKEND, '/api/tasks', base_body, key=key1)[1]['id'] == t1['id'])
expect('场景1 响应未暴露 request_key(契约缺口)', 'request_key' not in t1, sorted(t1.keys()))

# 2. 超过1000元进入人工审批，审批通过后续跑
key2 = str(uuid4())
code, t2 = call(BACKEND, '/api/tasks', {**base_body, 'category': '办公用品', 'amount': '1500'},
                key=key2)
expect('场景2 办公用品1500 进入待审批', t2['status'] == 'awaiting_approval', t2['status'])
expect('场景2 幂等重放无重复任务',
       call(BACKEND, '/api/tasks', {**base_body, 'category': '办公用品', 'amount': '1500'}, key=key2)[1]['id'] == t2['id'])
expect('场景2 员工账号不能审批(403)', status_of(BACKEND, f"/api/tasks/{t2['id']}/approval",
       {'decision': 'approve', 'comment': '越权尝试'}, role='employee') == 403)
code, t2a = call(BACKEND, f"/api/tasks/{t2['id']}/approval", {'decision': 'approve', 'comment': '用途明确，同意初审'},
                 role='approver')
expect('场景2 审批通过后任务 succeeded', t2a['status'] == 'succeeded', t2a['status'])
expect('场景2 通过后产生第3次工具日志', len(t2a['logs']) == 3, len(t2a['logs']))
expect('场景2 重复审批返回409', status_of(BACKEND, f"/api/tasks/{t2['id']}/approval",
       {'decision': 'approve', 'comment': '重复'}, role='approver') == 409)

# 3. 审批拒绝后终止
code, t3 = call(BACKEND, '/api/tasks', {**base_body, 'category': '办公用品', 'amount': '1500'}, key=str(uuid4()))
code, t3a = call(BACKEND, f"/api/tasks/{t3['id']}/approval", {'decision': 'reject', 'comment': '超标准，材料不全'},
                 role='approver')
expect('场景3 审批拒绝后状态 rejected', t3a['status'] == 'rejected', t3a['status'])
expect('场景3 拒绝也写回凭证', t3a['result'].get('receipt', {}).get('decision') == 'rejected', t3a['result'].get('receipt'))

# 4. 预算不足
code, t4 = call(BACKEND, '/api/tasks', {**base_body, 'project_id': 'P002', 'amount': '600'}, key=str(uuid4()))
expect('场景4 P002 600 预算不足被拒', t4['status'] == 'rejected' and '预算' in t4['result'].get('reason', ''), t4['status'])

# 5. 其他类别无制度依据转人工，须补人工依据
code, t5 = call(BACKEND, '/api/tasks', {**base_body, 'category': '其他'}, key=str(uuid4()))
expect('场景5 其他类别转人工', t5['status'] == 'awaiting_approval' and not t5['citations'], t5['status'])
expect('场景5 无依据通过且未填人工依据返回422',
       status_of(BACKEND, f"/api/tasks/{t5['id']}/approval", {'decision': 'approve', 'comment': '同意'}, role='approver') == 422)
code, t5a = call(BACKEND, f"/api/tasks/{t5['id']}/approval",
                 {'decision': 'approve', 'comment': '属临时支出，人工核定', 'manual_basis': '总经理口头批准，邮件留档'},
                 role='approver')
expect('场景5 补充人工依据后通过', t5a['status'] == 'succeeded' and t5a['citations'][0]['id'] == 'MANUAL', t5a['status'])

# 6. 输入校验与边界
expect('金额0被拒(422)', status_of(BACKEND, '/api/tasks', {**base_body, 'amount': '0'}, key=str(uuid4())) == 422)
expect('金额超过5000被拒(422)', status_of(BACKEND, '/api/tasks', {**base_body, 'amount': '5000.01'}, key=str(uuid4())) == 422)
expect('未来日期被拒(422)', status_of(BACKEND, '/api/tasks',
       {**base_body, 'expense_date': '2099-01-01'}, key=str(uuid4())) == 422)
expect('非法项目被拒(422)', status_of(BACKEND, '/api/tasks', {**base_body, 'project_id': 'P999'}, key=str(uuid4())) == 422)
expect('confirmed=false 被拒(422)', status_of(BACKEND, '/api/tasks',
       {**base_body, 'confirmed': False}, key=str(uuid4())) == 422)
expect('同幂等键换内容返回409', status_of(BACKEND, '/api/tasks', base_body, key=key2) == 409)

# 7. 指标与权限
code, metrics = call(BACKEND, '/api/metrics', role='admin')
expect('/api/metrics 返回六项指标', all(k in metrics for k in
       ['total', 'completed', 'completion_rate', 'manual_rate',
        'average_processing_seconds', 'average_end_to_end_seconds']), metrics)
expect('指标已完成数与任务总数一致', metrics['completed'] == metrics['total'], metrics)
expect('员工账号查指标返回403', status_of(BACKEND, '/api/metrics') == 403)
expect('无认证访问任务返回401', status_of(BACKEND, '/api/tasks', role=None) == 401)

# 8. 财务服务工具密钥与幂等凭证
try:
    call(FINANCE, '/budgets/P001', role=None)
    expect('财务服务缺工具密钥被拒', False, '未拒绝')
except urllib.error.HTTPError as exc:
    expect('财务服务缺工具密钥被拒(401/403)', exc.code in (401, 403), exc.code)
code, budget = call(FINANCE, '/budgets/P001', role=None, headers={'Authorization': f'Bearer {FINANCE_KEY}'})
expect('财务服务预算查询返回样例', budget.get('available') == '10000.00' or 'available' in budget, budget)
code, budget2 = call(FINANCE, '/budgets/P002', role=None, headers={'Authorization': f'Bearer {FINANCE_KEY}'})
expect('P002 预算500', str(budget2.get('available')).startswith('500'), budget2)

# 9. 字段提取建议接口
code, parsed = call(BACKEND, '/api/parse', {'description': '上周出差上海往返高铁票320元，报销'}, role='employee')
expect('/api/parse 返回 mode/建议金额/需确认', parsed.get('mode') == 'rule_baseline'
       and parsed.get('suggested_amount') == '320' and parsed.get('requires_confirmation') is True, parsed)
expect('/api/parse 未识别类别时返回 None 而非编造', parsed.get('suggested_category') is None, parsed)

failed = [r for r in results if not r[1]]
print(f'\n总计 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项')
sys.exit(1 if failed else 0)
