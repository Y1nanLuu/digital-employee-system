"""D3：真实状态指标、拒绝终止与可追踪的工具故障。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import services
from app.database import SessionLocal
from app.models import Task
from conftest import approve, claim, submit
from finance.schemas import BudgetResponse


def test_empty_metrics_are_explicitly_unknown(clients):
    client, _ = clients
    expected = {'total': 0, 'completed': 0, 'completion_rate': None, 'manual_rate': None,
                'average_processing_seconds': None, 'average_end_to_end_seconds': None}
    for role in ('admin', 'approver'):
        response = client.get('/api/metrics', auth=(role, 'demo123'))
        assert response.status_code == 200
        assert response.json() == expected


def test_metrics_exclude_failures_and_subtract_only_completed_tasks_wait(clients, monkeypatch):
    client, _ = clients
    succeeded = approve(client, submit(client, claim(amount='1500')).json()).json()
    rejected = submit(client, claim(project_id='P002', amount='600')).json()
    pending = submit(client, claim(amount='1500')).json()

    def timeout(request):
        raise httpx.ReadTimeout('模拟预算超时', request=request)

    real_client = TestClient.__bases__[0]
    monkeypatch.setattr(services.httpx, 'Client',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(timeout), **kwargs))
    failed = submit(client).json()
    assert [succeeded['status'], rejected['status'], pending['status'], failed['status']] == [
        'succeeded', 'rejected', 'awaiting_approval', 'failed']
    with SessionLocal() as db:
        for snapshot in (succeeded, rejected, pending, failed):
            db.get(Task, snapshot['id']).created_at = '2026-11-09T01:00:00+00:00'
        first = db.get(Task, succeeded['id'])
        first.finished_at = '2026-11-09T01:00:20+00:00'
        first.wait_seconds = 12
        db.get(Task, rejected['id']).finished_at = '2026-11-09T01:00:08+00:00'
        # 技术失败即使有结束时间也不得计入完成数和平均耗时。
        db.get(Task, failed['id']).finished_at = '2026-11-09T01:10:00+00:00'
        db.commit()
    metrics = client.get('/api/metrics', auth=('admin', 'demo123')).json()
    assert metrics['total'] == 4 and metrics['completed'] == 2
    assert metrics['completion_rate'] == pytest.approx(.5)
    # 已通过任务的人工历史仍计入转人工率。
    assert metrics['manual_rate'] == pytest.approx(.5)
    assert metrics['average_processing_seconds'] == pytest.approx(8)
    assert metrics['average_end_to_end_seconds'] == pytest.approx(14)


@pytest.mark.parametrize('stage, error_type, message', [
    ('budget_query', httpx.ConnectError, '无法连接模拟财务服务'),
    ('budget_query', httpx.ReadTimeout, '模拟财务服务调用超时'),
    ('writeback', httpx.ConnectError, '无法连接模拟财务服务'),
    ('writeback', httpx.ReadTimeout, '模拟财务服务调用超时'),
])
def test_tool_failure_exposes_endpoint_stage_and_readable_reason(clients, monkeypatch,
                                                               stage, error_type, message):
    client, finance = clients
    real_client = TestClient.__bases__[0]

    def dispatch(request):
        if (stage == 'budget_query' and request.method == 'GET'
                or stage == 'writeback' and request.method == 'POST'):
            raise error_type('不应公开的底层异常详情', request=request)
        response = finance.request(request.method, request.url.path,
                                   content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, json=response.json(), request=request)

    monkeypatch.setattr(services.config, 'FINANCE_URL', 'http://finance.invalid:8001')
    monkeypatch.setattr(services.httpx, 'Client',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(dispatch), **kwargs))
    task = submit(client).json()
    path = '/budgets/P001' if stage == 'budget_query' else '/results'
    assert task['status'] == 'failed' and task['failure_stage'] == stage
    assert 'receipt' not in task['result']
    log = task['logs'][-1]
    assert log['task_id'] == task['id'] and log['stage'] == stage
    assert log['tool'] == path and log['endpoint'] == 'http://finance.invalid:8001' + path
    assert message in log['error'] and log['error_code'] == error_type.__name__
    assert log['success'] is False and log['duration_ms'] >= 0
    if stage == 'writeback':
        assert task['result']['decision'] == 'approved'
        assert log['request']['task_id'] == task['id']
    serialized = json.dumps(task, ensure_ascii=False)
    assert '不应公开的底层异常详情' not in serialized
    assert 'local-finance-key' not in serialized


def test_tool_log_does_not_expose_url_credentials_or_query(clients, monkeypatch):
    client, _ = clients
    task = submit(client).json()
    monkeypatch.setattr(services.config, 'FINANCE_URL', 'http://audit-user:audit-password@finance.invalid:8001')
    real_client = TestClient.__bases__[0]

    def timeout(request):
        raise httpx.ReadTimeout('测试异常', request=request)

    monkeypatch.setattr(services.httpx, 'Client',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(timeout), **kwargs))
    with SessionLocal() as db:
        row = db.get(Task, task['id'])
        with pytest.raises(services.ToolFailure):
            services.tool(row, 'GET', '/budgets/P001?token=hidden-query#hidden-fragment',
                          response_model=BudgetResponse, expected={'project_id': 'P001'}, stage='budget_query')
        log = row.logs[-1]
        assert log['endpoint'] == 'http://finance.invalid:8001/budgets/P001'
        assert log['tool'] == '/budgets/P001'
        serialized = json.dumps(log)
        for secret in ('audit-user', 'audit-password', 'hidden-query', 'hidden-fragment'):
            assert secret not in serialized


def test_rejected_task_cannot_resume_or_write_again(clients):
    client, _ = clients
    pending = submit(client, claim(amount='1500')).json()
    path = f"/api/tasks/{pending['id']}/approval"
    for role in ('employee', 'admin'):
        response = client.post(path, json={'decision': 'reject', 'comment': '越权尝试'},
                               auth=(role, 'demo123'))
        assert response.status_code == 403
    assert client.post(path, json={'decision': 'reject', 'comment': '未认证'}).status_code == 401
    assert client.get(f"/api/tasks/{pending['id']}", auth=('employee', 'demo123')).json() == pending

    rejected = approve(client, pending, 'reject').json()
    assert rejected['status'] == 'rejected' and len(rejected['approvals']) == 1
    assert [log['stage'] for log in rejected['logs']] == ['budget_query', 'writeback']
    assert rejected['result']['receipt']['decision'] == 'rejected'
    for decision in ('approve', 'reject'):
        assert approve(client, rejected, decision).status_code == 409
    assert client.get(f"/api/tasks/{pending['id']}", auth=('employee', 'demo123')).json() == rejected
