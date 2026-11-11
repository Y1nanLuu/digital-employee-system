"""Read-only production audit: extra regression probes against isolated fixtures."""
import json
from pathlib import Path
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'backend/tests/conftest.py').is_file())
sys.path.insert(0, str(ROOT / 'backend/tests'))
from conftest import clients, cleanup_databases
from test_enterprise_review import v2, upload, submit, approve
from fastapi.testclient import TestClient
from sqlalchemy import select
import httpx
import pytest

from app import services
from app.database import SessionLocal
from app.models import Task, TaskRevision

OBSERVATIONS = {}


def record(name, **observed):
    OBSERVATIONS[name] = observed
    Path(__file__).with_name('observations.json').write_text(
        json.dumps(OBSERVATIONS, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def timeout_budget(monkeypatch):
    real_client = TestClient.__bases__[0]

    def dispatch(request):
        raise httpx.ReadTimeout('audit: simulated budget timeout', request=request)

    monkeypatch.setattr(services.httpx, 'Client',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(dispatch), **kwargs))


def test_failed_withdraw_follows_ac59(v2, monkeypatch):
    client, _ = v2
    _, _, body = upload(client, '26000000000000000999')
    with monkeypatch.context() as patch:
        timeout_budget(patch)
        task = submit(client, body)
    assert task['status'] == 'failed' and task['failure_stage'] == 'budget_query'
    response = client.post(f"/api/tasks/{task['id']}/withdraw", auth=('employee', 'demo123'))
    record('failed_withdraw', expected_http=409, actual_http=response.status_code,
           actual_status=response.json().get('status'), original_stage=task['failure_stage'])
    assert response.status_code == 409, 'AC59 only authorizes returned/awaiting_approval withdrawal'


def test_approver_can_read_old_original_after_employee_replaces_it(v2):
    client, _ = v2
    _, first_source, body = upload(client, '26000000000000000999')
    task = submit(client, body)
    route = '/api/files/' + first_source['file_id'] + '/content'
    assert client.get(route, auth=('approver', 'demo123')).status_code == 200
    assert approve(client, task, 'return').json()['status'] == 'returned'
    _, _, replacement = upload(client, '26000000000000000888')
    replacement['revision_reason'] = '按意见更换正确原票'
    response = client.post(f"/api/tasks/{task['id']}/resubmit", json=replacement,
                           headers={'Idempotency-Key': 'audit-replacement'}, auth=('employee', 'demo123'))
    assert response.status_code == 200, response.text
    assert len(response.json()['revisions']) == 2
    old = client.get(route, auth=('approver', 'demo123'))
    owner = client.get(route, auth=('employee', 'demo123'))
    record('old_original_access', expected_approver_http=200, actual_approver_http=old.status_code,
           actual_owner_http=owner.status_code, revisions=len(response.json()['revisions']))
    assert old.status_code == 200, 'Historical original must remain accessible for authorized review'


def test_enterprise_budget_recovery_resumes_accepted_human_approval(v2, monkeypatch):
    client, _ = v2
    _, _, body = upload(client, '26000000000000000999')
    task = submit(client, body)
    assert task['status'] == 'awaiting_approval'
    with monkeypatch.context() as patch:
        timeout_budget(patch)
        failed = approve(client, task).json()
    assert failed['status'] == 'failed' and failed['failure_stage'] == 'budget_query'
    assert len(failed['approvals']) == 1 and failed['approvals'][0]['decision'] == 'approve'
    with SessionLocal() as db:
        row = db.get(Task, task['id'])
        services.recover(row, 'retry-budget', 'audit-ops', '工具恢复后继续原人工决定', db)
        db.commit()
    recovered = client.get(f"/api/tasks/{task['id']}", auth=('employee', 'demo123')).json()
    record('approval_recovery', expected_status='succeeded', actual_status=recovered['status'],
           approval_count=len(recovered['approvals']),
           writebacks=sum(x['stage'] == 'writeback' for x in recovered['logs']),
           manual_reasons=recovered['result'].get('manual_reasons'))
    assert recovered['status'] == 'succeeded', 'Recovery must continue the previously accepted human approval'


def test_enterprise_replay_updates_current_revision_audit(v2, monkeypatch):
    client, finance = v2
    _, _, body = upload(client)
    real_client = TestClient.__bases__[0]

    def dispatch(request):
        response = finance.request(request.method, request.url.path, content=request.content,
                                   headers=dict(request.headers))
        if request.url.path == '/reviews/results':
            assert response.status_code == 200
            raise httpx.ReadTimeout('audit: finance committed but response lost', request=request)
        return httpx.Response(response.status_code, json=response.json(), request=request)

    with monkeypatch.context() as patch:
        patch.setattr(services.httpx, 'Client',
                      lambda **kwargs: real_client(transport=httpx.MockTransport(dispatch), **kwargs))
        failed = submit(client, body)
    assert failed['status'] == 'failed' and failed['failure_stage'] == 'writeback'
    with SessionLocal() as db:
        row = db.get(Task, failed['id'])
        services.recover(row, 'replay-writeback', 'audit-ops', '按原载荷取回原财务凭证', db)
        db.commit()
        revision = db.scalar(select(TaskRevision).where(TaskRevision.task_id == row.id,
                                                       TaskRevision.number == row.revision))
        review = revision.snapshot['review_record']
        record('recovery_revision_audit', expected_revision_status=row.status,
               actual_revision_status=review['status'], actual_task_status=row.status,
               task_has_receipt=bool(row.result.get('receipt')),
               revision_has_receipt=bool(review['result'].get('receipt')))
        assert row.status == 'succeeded'
        assert review['status'] == row.status, 'Current revision audit must reflect successful reconciliation'
