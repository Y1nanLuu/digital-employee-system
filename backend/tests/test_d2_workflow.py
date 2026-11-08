import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import ai, main, services
from app.database import SessionLocal
from app.models import ParseSnapshot, Task
from conftest import approve, claim, submit
from finance.main import Receipt, Session as FinanceSession


def parsed_claim(client, body, monkeypatch=None, abnormal=False):
    if abnormal:
        snapshot = {**ai.baseline_fields(body['description']), 'mode': 'rule_fallback',
                    'requires_manual_review': True, 'fallback_event': {'trigger': 'test_timeout'}}
        monkeypatch.setattr(main, 'parse_fields', lambda text: snapshot)
    parsed = client.post('/api/parse', json={'description': body['description']},
                         auth=('employee', 'demo123'))
    assert parsed.status_code == 200
    return {**body, 'parse_id': parsed.json()['parse_id']}, parsed.json()


@pytest.mark.parametrize('amount', ['-50', '+50', '1.001', '1e3', 'NaN', 'Infinity', True, 300, 300.0])
def test_strict_money_never_creates_task(clients, amount):
    client, _ = clients
    assert submit(client, claim(amount=amount)).status_code == 422
    assert client.get('/api/tasks', auth=('employee', 'demo123')).json() == []


@pytest.mark.parametrize('changes', [{'confirmed': 1}, {'expense_date': 1793923200},
                                    {'expense_date': '2026-11-06T00:00:00'}])
def test_confirmation_and_date_use_explicit_types(clients, changes):
    client, _ = clients
    assert submit(client, claim(**changes)).status_code == 422


@pytest.mark.parametrize('text,expected', [
    ('交通费-50元', None), ('交通费−50元', None), ('交通费1.001元', None), ('交通费1e3元', None),
    ('交通费0.50元', '0.50'), ('交通费1500元', '1500.00'),
    ('交通费3275元', '3275.00'), ('交通费1,500元', '1500.00'),
    ('金额：320', '320.00'), ('交通费300元另有交通费300元', None),
])
def test_extraction_keeps_complete_money_boundaries(text, expected):
    assert ai.baseline_fields(text)['suggested_amount'] == expected


@pytest.mark.parametrize('text,expected', [
    ('差旅住宿费用300元', '其他'), ('差旅高铁票300元', '交通费'),
    ('高铁票和酒店共300元', '其他'), ('差旅费用300元', '其他'),
])
def test_travel_mapping_depends_on_expense_content(text, expected):
    result = ai._adapt(ai.baseline_fields(text),
                       {'fields': {'expense_type': '差旅', 'amount': 300}, 'source': 'rule'}, None, text)
    assert result['suggested_category'] == expected


def test_parse_snapshot_is_saved_and_bound_to_owner_and_description(clients):
    client, _ = clients
    body, parsed = parsed_claim(client, claim())
    with SessionLocal() as db:
        snapshot = db.get(ParseSnapshot, parsed['parse_id'])
        assert snapshot.owner == 'employee' and snapshot.snapshot == parsed
    assert parsed['latency_ms'] >= 0
    changed = {**body, 'description': '已变更的交通费300元说明'}
    assert submit(client, changed).status_code == 422
    with SessionLocal() as db:
        snapshot = db.get(ParseSnapshot, parsed['parse_id'])
        snapshot.owner = 'approver'
        db.commit()
    assert submit(client, body).status_code == 422
    assert submit(client, {**body, 'parse_id': str(uuid4())}).status_code == 422
    assert client.get('/api/tasks', auth=('employee', 'demo123')).json() == []


def test_conflicting_amount_requires_specific_confirmation(clients):
    client, _ = clients
    body = claim(amount='300.00', description='客户现场交通费500元')
    assert submit(client, body).status_code == 422
    assert submit(client, {**body, 'conflict_acknowledged': True}).status_code == 422
    task = submit(client, {**body, 'conflict_acknowledged': True,
                           'confirmation_note': '其中200元由其他项目承担，本次只申报300元'}).json()
    assert task['status'] == 'succeeded'
    assert task['confirmations'][0]['conflicts']
    assert task['payload']['amount'] == '300.00'
    assert task['submission_payload']['description'] == body['description']


def test_model_failure_keeps_all_manual_reasons_and_requires_review(clients, monkeypatch):
    client, _ = clients
    body, parsed = parsed_claim(client, claim(category='其他', amount='1500'), monkeypatch, abnormal=True)
    task = submit(client, body).json()
    assert task['status'] == 'awaiting_approval'
    assert len(task['result']['manual_reasons']) == 3
    assert task['parse_snapshot'] == parsed
    assert 'receipt' not in task['result']
    assert approve(client, task, manual_basis='专项费用，经人工核对').status_code == 422
    done = approve(client, task, manual_basis='专项费用，经人工核对', reviewed_fields=True)
    assert done.status_code == 200 and done.json()['status'] == 'succeeded'
    assert done.json()['approvals'][0]['reviewed_fields'] is True


def test_budget_rejection_precedes_model_manual_flag(clients, monkeypatch):
    client, _ = clients
    body, _ = parsed_claim(client, claim(project_id='P002', amount='600'), monkeypatch, abnormal=True)
    task = submit(client, body).json()
    assert task['status'] == 'rejected' and task['manual'] is False


def test_amendment_preserves_initial_request_and_idempotency(clients):
    client, _ = clients
    key = str(uuid4())
    original = claim(amount='1500')
    task = submit(client, original, key).json()
    response = approve(client, task, corrected_fields={'amount': '900.00'},
                       correction_reason='原金额含员工自行承担部分', reviewed_fields=True)
    assert response.status_code == 200
    done = response.json()
    assert done['status'] == 'succeeded' and done['payload']['amount'] == '900.00'
    assert done['submission_payload']['amount'] == '1500.00'
    assert done['amendments'][0]['before'] == {'amount': '1500.00'}
    assert done['amendments'][0]['after'] == {'amount': '900.00'}
    assert submit(client, original, key).json() == done
    assert submit(client, claim(amount='900'), key).status_code == 409
    assert len(done['logs']) == 3


@pytest.mark.parametrize('changes', [
    {'corrected_fields': {'amount': '900'}},
    {'corrected_fields': {'amount': '1e3'}, 'correction_reason': '核对', 'reviewed_fields': True},
    {'corrected_fields': {'amount': None}, 'correction_reason': '核对', 'reviewed_fields': True},
    {'corrected_fields': {}, 'correction_reason': '核对', 'reviewed_fields': True},
    {'corrected_fields': {'owner': 'admin'}, 'correction_reason': '核对', 'reviewed_fields': True},
    {'corrected_fields': {'category': '其他'}, 'correction_reason': '核对', 'reviewed_fields': True},
    {'decision': 'reject', 'corrected_fields': {'amount': '900'}, 'correction_reason': '核对', 'reviewed_fields': True},
])
def test_invalid_amendment_is_atomic(clients, changes):
    client, _ = clients
    task = submit(client, claim(amount='1500')).json()
    assert approve(client, task, **changes).status_code == 422
    assert client.get('/api/tasks/' + task['id'], auth=('employee', 'demo123')).json() == task


def test_correction_retrieves_new_basis_and_rechecks_changed_project(clients):
    client, _ = clients
    task = submit(client, claim(category='其他', amount='600')).json()
    result = approve(client, task, corrected_fields={'category': '交通费', 'project_id': 'P002'},
                     correction_reason='核对为市场项目交通费用', reviewed_fields=True).json()
    assert result['status'] == 'rejected'
    assert result['citations'][0]['id'].startswith('DEMO-')
    assert result['result']['budget']['project_id'] == 'P002'


def test_concurrent_approval_has_one_effect(clients):
    client, _ = clients
    task = submit(client, claim(amount='1500')).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: approve(client, task).status_code, range(2)))
    assert sorted(codes) == [200, 409]
    saved = client.get('/api/tasks/' + task['id'], auth=('employee', 'demo123')).json()
    assert len(saved['approvals']) == 1 and len(saved['logs']) == 3


def test_concurrent_submission_and_normalized_replay(clients):
    client, _ = clients
    key = str(uuid4())
    body = claim(amount='300', description='客户现场交通费300元')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(client, body, key), range(2)))
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert submit(client, {**body, 'amount': '300.00'}, key).json() == responses[0].json()
    assert len(client.get('/api/tasks', auth=('employee', 'demo123')).json()) == 1


@pytest.mark.parametrize('amount', [True, -50, 1.001, float('inf')])
def test_invalid_model_amount_is_patched_and_requires_human_review(amount):
    text = '客户现场交通费300元'
    result = ai._adapt(ai.baseline_fields(text),
                       {'fields': {'amount': amount}, 'source': 'llm'}, None, text)
    assert result['suggested_amount'] == '300.00'
    assert result['mode'] == 'llm_with_rule_patch' and result['requires_manual_review'] is True
    assert result['fields']['amount'] is None


def test_query_failure_can_resume_same_task_without_duplicate_approval(clients, monkeypatch):
    client, finance = clients
    base_client = TestClient.__bases__[0]
    outage = {'enabled': True}

    def dispatch(request):
        if outage['enabled']:
            return httpx.Response(503, json={'detail': '模拟停机'}, request=request)
        response = finance.request(request.method, request.url.path,
                                   content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, json=response.json(), request=request)

    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs:
                        base_client(transport=httpx.MockTransport(dispatch), **kwargs))
    task = submit(client).json()
    assert task['status'] == 'failed' and task['failure_stage'] == 'budget_query'
    outage['enabled'] = False
    with SessionLocal() as db:
        saved = db.get(Task, task['id'])
        services.recover(saved, 'retry-budget', 'admin', '服务恢复后重新查询', db)
        assert saved.status == 'succeeded' and saved.id == task['id']
        assert saved.approvals == []


def test_unknown_writeback_result_replays_original_receipt(clients, monkeypatch):
    client, finance = clients
    base_client = TestClient.__bases__[0]
    timeout = {'enabled': True}

    def dispatch(request):
        response = finance.request(request.method, request.url.path,
                                   content=request.content, headers=dict(request.headers))
        if request.method == 'POST' and timeout['enabled']:
            raise httpx.ReadTimeout('模拟财务已保存但响应丢失', request=request)
        return httpx.Response(response.status_code, json=response.json(), request=request)

    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs:
                        base_client(transport=httpx.MockTransport(dispatch), **kwargs))
    task = submit(client).json()
    assert task['status'] == 'failed' and task['failure_stage'] == 'writeback'
    assert task['result']['decision'] == 'approved' and 'receipt' not in task['result']
    with SessionLocal() as db:
        saved = db.get(Task, task['id'])
        original = copy.deepcopy(saved.pending_writeback)
        timeout['enabled'] = False
        services.recover(saved, 'replay-writeback', 'admin', '对账后按原载荷重放', db)
        assert saved.status == 'succeeded'
        assert {key: saved.result['receipt'][key] for key in original} == original
        with pytest.raises(HTTPException):
            services.recover(saved, 'replay-writeback', 'admin', '禁止打开终态', db)
    with FinanceSession() as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1


def test_startup_marks_incomplete_tasks_but_keeps_pending_approval(clients):
    client, _ = clients
    pending = submit(client, claim(amount='1500')).json()
    original = submit(client).json()
    with SessionLocal() as db:
        saved = db.get(Task, original['id'])
        saved.status = 'writing_back'
        saved.pending_writeback = {key: original['result']['receipt'][key] for key in ['task_id', 'decision', 'reason']}
        db.commit()
    with TestClient(main.app) as restarted:
        failed = restarted.get('/api/tasks/' + original['id'], auth=('employee', 'demo123')).json()
        assert failed['status'] == 'failed' and failed['result']['error_code'] == 'process_interrupted'
        assert failed['failure_stage'] == 'writeback'
        assert restarted.get('/api/tasks/' + pending['id'], auth=('employee', 'demo123')).json() == pending
