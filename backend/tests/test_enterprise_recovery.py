"""Enterprise recovery, revision audit, and historical-original permissions."""
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import services
from app.database import SessionLocal
from app.models import Task, TaskRevision
from finance.main import Receipt, Session as FinanceSession
from test_enterprise_review import approve, submit, upload, v2


def finance_transport(patch, finance, *, fail_path=None, save_first=False, available=None):
    real_client = TestClient.__bases__[0]

    def dispatch(request):
        if request.url.path == fail_path and not save_first:
            raise httpx.ReadTimeout('test: finance temporarily unavailable', request=request)
        if request.url.path.startswith('/budgets/') and available is not None:
            return httpx.Response(200, json={'project_id': 'P001', 'available': available,
                                           'currency': 'CNY', 'simulated': True}, request=request)
        response = finance.request(request.method, request.url.path,
                                   content=request.content, headers=dict(request.headers))
        if request.url.path == fail_path:
            assert response.status_code == 200
            raise httpx.ReadTimeout('test: finance committed but response lost', request=request)
        return httpx.Response(response.status_code, json=response.json(), request=request)

    patch.setattr(services.httpx, 'Client',
                  lambda **kwargs: real_client(transport=httpx.MockTransport(dispatch), **kwargs))


def detail(client, task):
    return client.get('/api/tasks/' + task['id'], auth=('employee', 'demo123')).json()


def test_failed_withdraw_is_rejected_without_changing_task(v2, monkeypatch):
    client, finance = v2
    _, _, body = upload(client, '26000000000000000999')
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, fail_path='/budgets/P001')
        failed = submit(client, body)
    assert failed['status'] == 'failed' and failed['failure_stage'] == 'budget_query'
    response = client.post('/api/tasks/' + failed['id'] + '/withdraw', auth=('employee', 'demo123'))
    assert response.status_code == 409
    assert detail(client, failed) == failed
    # Duplicate intake must direct the employee to recovery rather than withdrawal.
    retry = client.post('/api/invoices', files={'file': ('again.pdf', upload_bytes(body), 'application/pdf')},
                        auth=('employee', 'demo123'))
    assert retry.status_code == 409 and '恢复原申请' in retry.json()['detail']


def upload_bytes(body):
    from test_invoices import build_pdf
    invoice = body['invoice']
    return build_pdf(f"Invoice No: {invoice['invoice_no']}\nDate: 2026-11-01\n"
                     f"Amount CNY {invoice['total']}\nItem: taxi transport\n")


def test_approver_keeps_access_to_replaced_original(v2):
    client, _ = v2
    original, source, body = upload(client, '26000000000000000999')
    task = submit(client, body)
    route = '/api/files/' + source['file_id']
    assert client.get(route + '/content', auth=('approver', 'demo123')).status_code == 200
    assert approve(client, task, 'return').json()['status'] == 'returned'
    _, _, replacement = upload(client, '26000000000000000888')
    replacement['revision_reason'] = '按意见更换正确原票'
    response = client.post('/api/tasks/' + task['id'] + '/resubmit', json=replacement,
                           headers={'Idempotency-Key': str(uuid4())}, auth=('employee', 'demo123'))
    assert response.status_code == 200 and response.json()['revision'] == 2
    for role in ('employee', 'approver', 'admin'):
        assert client.get(route, auth=(role, 'demo123')).status_code == 200
        data = client.get(route + '/content', auth=(role, 'demo123'))
        assert data.status_code == 200 and sha256(data.content).digest() == sha256(original).digest()
    assert client.get(route + '/content').status_code == 401
    _, unsubmitted, _ = upload(client, '26000000000000000777')
    assert client.get('/api/files/' + unsubmitted['file_id'], auth=('approver', 'demo123')).status_code == 404


@pytest.mark.parametrize('path,stage', [('/budgets/P001', 'budget_query'),
                                      ('/checks/invoice', 'invoice_check'),
                                      ('/checks/duplicate', 'duplicate_check')])
def test_approval_recovery_continues_one_accepted_decision(v2, monkeypatch, path, stage):
    client, finance = v2
    _, _, body = upload(client, '26000000000000000999')
    task = submit(client, body)
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, fail_path=path)
        failed = approve(client, task).json()
    assert failed['status'] == 'failed' and failed['failure_stage'] == stage
    assert len(failed['approvals']) == 1
    with SessionLocal() as db:
        row = db.get(Task, task['id'])
        waited = row.wait_seconds
        services.recover(row, 'retry-budget', 'test-ops', '工具恢复后继续原审批', db)
        assert row.wait_seconds == waited
    recovered = detail(client, task)
    assert recovered['status'] == 'succeeded' and recovered['id'] == task['id']
    assert recovered['approvals'] == failed['approvals']
    assert sum(log['stage'] == 'writeback' for log in recovered['logs']) == 1
    assert sum(log['stage'] == 'initial_review' for log in recovered['logs']) == 1
    assert recovered['revisions'][0]['snapshot']['review_record']['status'] == 'succeeded'
    with FinanceSession() as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1


def test_approval_recovery_still_rejects_reduced_budget(v2, monkeypatch):
    client, finance = v2
    _, _, body = upload(client, '26000000000000000999')
    task = submit(client, body)
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, fail_path='/budgets/P001')
        failed = approve(client, task).json()
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, available='100.00')
        with SessionLocal() as db:
            services.recover(db.get(Task, task['id']), 'retry-budget', 'test-ops', '预算重新确认', db)
    recovered = detail(client, task)
    assert recovered['status'] == 'rejected' and '预算' in recovered['result']['reason']
    assert recovered['approvals'] == failed['approvals']
    assert recovered['result']['receipt']['decision'] == 'rejected'


def test_resubmission_recovery_does_not_reuse_previous_revision_return(v2, monkeypatch):
    client, finance = v2
    _, _, body = upload(client, '26000000000000000999')
    task = submit(client, body)
    returned = approve(client, task, 'return').json()
    body['revision_reason'] = '补充真实业务说明'
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, fail_path='/budgets/P001')
        failed = client.post('/api/tasks/' + task['id'] + '/resubmit', json=body,
                             headers={'Idempotency-Key': str(uuid4())}, auth=('employee', 'demo123')).json()
    assert failed['status'] == 'failed' and failed['revision'] == 2
    with SessionLocal() as db:
        services.recover(db.get(Task, task['id']), 'retry-budget', 'test-ops', '恢复新修订初审', db)
    recovered = detail(client, task)
    assert recovered['status'] == 'awaiting_approval' and recovered['revision'] == 2
    assert recovered['approvals'] == returned['approvals']
    assert not recovered['result'].get('receipt')
    assert recovered['revisions'][1]['snapshot']['review_record']['status'] == 'awaiting_approval'


@pytest.mark.parametrize('fail_again', [False, True])
def test_writeback_replay_updates_current_revision_only(v2, monkeypatch, fail_again):
    client, finance = v2
    _, _, first_body = upload(client, '26000000000000000999')
    task = submit(client, first_body)
    approve(client, task, 'return')
    _, _, body = upload(client)
    body['revision_reason'] = '换用已核对票据'
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, fail_path='/reviews/results', save_first=True)
        failed = client.post('/api/tasks/' + task['id'] + '/resubmit', json=body,
                             headers={'Idempotency-Key': str(uuid4())}, auth=('employee', 'demo123')).json()
    assert failed['status'] == 'failed' and failed['failure_stage'] == 'writeback'
    with SessionLocal() as db:
        row = db.get(Task, task['id'])
        first = db.scalar(select(TaskRevision).where(TaskRevision.task_id == row.id, TaskRevision.number == 1))
        original_snapshot = deepcopy(first.snapshot)
        with monkeypatch.context() as patch:
            if fail_again:
                finance_transport(patch, finance, fail_path='/reviews/results', save_first=True)
            services.recover(row, 'replay-writeback', 'test-ops', '按原载荷对账', db)
        current = db.scalar(select(TaskRevision).where(TaskRevision.task_id == row.id, TaskRevision.number == 2))
        review = current.snapshot['review_record']
        assert row.status == ('failed' if fail_again else 'succeeded')
        assert review['status'] == row.status and review['result'] == row.result
        assert review['logs'] == [log for log in row.logs if log['revision'] == 2]
        assert first.snapshot == original_snapshot
        assert bool(row.pending_writeback) == fail_again
    with FinanceSession() as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1


def test_initial_review_recovery_updates_audit_and_does_not_reopen_terminal(v2, monkeypatch):
    client, finance = v2
    _, _, body = upload(client)
    with monkeypatch.context() as patch:
        finance_transport(patch, finance, fail_path='/budgets/P001')
        task = submit(client, body)
    assert task['status'] == 'failed' and not task['approvals']
    with SessionLocal() as db:
        row = db.get(Task, task['id'])
        services.recover(row, 'retry-budget', 'test-ops', '恢复初审查询', db)
        assert row.status == 'succeeded' and row.approvals == []
        before = services.serialize(row)
        with pytest.raises(HTTPException) as exc:
            services.recover(row, 'retry-budget', 'test-ops', '不得重开终态', db)
        assert exc.value.status_code == 409
        assert services.serialize(row) == before
        assert before['revisions'][0]['snapshot']['review_record']['status'] == 'succeeded'
