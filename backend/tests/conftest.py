"""业务API与模拟财务API的集成契约测试，不调用真实外部服务。"""
import os
import json
import tempfile
from pathlib import Path
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
import httpx
import pytest
from fastapi.testclient import TestClient

scratch = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + (Path(scratch.name)/'tasks.db').as_posix()
os.environ['FINANCE_DATABASE_URL'] = 'sqlite:///' + (Path(scratch.name)/'finance.db').as_posix()
os.environ['DEMO_PASSWORD'] = 'demo123'
os.environ['FINANCE_KEY'] = 'local-finance-key'
os.environ['AI_MODULE_ENABLED'] = 'false'
from app.main import app
from app import services
from app.database import Base, engine
from finance.main import app as finance_app, Base as FinanceBase, engine as finance_engine

@pytest.fixture(scope='session', autouse=True)
def cleanup_databases():
    yield
    engine.dispose()
    finance_engine.dispose()
    scratch.cleanup()


@pytest.fixture
def clients(monkeypatch):
    Base.metadata.drop_all(engine)
    FinanceBase.metadata.drop_all(finance_engine)
    real_client = httpx.Client
    with TestClient(finance_app) as finance, TestClient(app) as backend:
        def dispatch(request):
            response = finance.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))
            return httpx.Response(response.status_code, json=response.json(), request=request)
        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(dispatch), **kwargs)
        monkeypatch.setattr(services.httpx, 'Client', client_factory)
        yield backend, finance


def claim(**changes):
    body = dict(project_id='P001',category='交通费',amount='300.00',
                expense_date=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                description='客户现场往返交通费300元',confirmed=True)
    body.update(changes)
    if 'description' not in changes:
        body['description'] = f"客户现场发生{body['category']}{body['amount']}元"
    return body

def submit(client, body=None, key=None):
    return client.post('/api/tasks',json=body or claim(),auth=('employee','demo123'),headers={'Idempotency-Key':key or str(uuid4())})

def approve(client, task, decision='approve', **changes):
    return client.post(f"/api/tasks/{task['id']}/approval",auth=('approver','demo123'),
                        json={'decision':decision,'comment':'测试审批意见',**changes})
