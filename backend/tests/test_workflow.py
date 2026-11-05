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
    return {**body,**changes}

def submit(client, body=None, key=None):
    return client.post('/api/tasks',json=body or claim(),auth=('employee','demo123'),headers={'Idempotency-Key':key or str(uuid4())})

def approve(client, task, decision='approve', **changes):
    return client.post(f"/api/tasks/{task['id']}/approval",auth=('approver','demo123'),
                        json={'decision':decision,'comment':'测试审批意见',**changes})

def test_auto_complete_and_logs(clients):
    client,_=clients
    response=submit(client);assert response.status_code==201
    task=response.json();assert task['status']=='succeeded'
    assert task['citations'][0]['version']=='demo-v1'
    assert len(task['logs'])==2 and all(log['success'] for log in task['logs'])
    assert task['result']['receipt']['simulated'] is True

def test_approval_resume_and_no_duplicates(clients):
    client,_=clients
    task=submit(client,claim(amount='1500')).json();assert task['status']=='awaiting_approval'
    result=approve(client,task);assert result.status_code==200
    assert result.json()['status']=='succeeded'
    assert len(result.json()['logs'])==3
    assert approve(client,task).status_code==409

def test_rejection_and_budget(clients):
    client,_=clients
    task=submit(client,claim(amount='1500')).json()
    assert approve(client,task,'reject').json()['status']=='rejected'
    low=submit(client,claim(project_id='P002',amount='600')).json()
    assert low['status']=='rejected' and low['result']['reason']=='项目预算不足'

def test_missing_basis_requires_human_evidence(clients):
    client,_=clients
    task=submit(client,claim(category='其他')).json()
    assert task['status']=='awaiting_approval'
    assert approve(client,task).status_code==422
    done=approve(client,task,manual_basis='演示人工依据：专项业务用途核对完成').json()
    assert done['status']=='succeeded' and done['citations'][0]['id']=='MANUAL'

@pytest.mark.parametrize('amount,status',[('1000','succeeded'),('1000.01','awaiting_approval'),('5000','awaiting_approval')])
def test_thresholds(clients,amount,status):
    assert submit(clients[0],claim(amount=amount)).json()['status']==status

@pytest.mark.parametrize('changes',[{'amount':'5000.01'},{'amount':'0'},{'amount':'1.001'},{'description':' '},{'confirmed':False},{'expense_date':'2999-01-01'}])
def test_invalid_inputs(clients,changes):
    client,_=clients
    assert submit(client,claim(**changes)).status_code==422
    assert client.get('/api/tasks',auth=('employee','demo123')).json()==[]

def test_identity_and_idempotency(clients):
    client,_=clients
    assert client.get('/api/tasks').status_code==401
    assert client.get('/api/me',auth=('employee','wrong')).status_code==401
    key=str(uuid4());task=submit(client,key=key).json()
    assert submit(client,key=key).json()['id']==task['id']
    assert submit(client,claim(amount='301'),key).status_code==409
    assert client.post(f"/api/tasks/{task['id']}/approval",auth=('employee','demo123'),json={'decision':'approve','comment':'无权'}).status_code==403
    assert client.get('/api/metrics',auth=('employee','demo123')).status_code==403
    assert client.post(f"/api/tasks/{task['id']}/approval",auth=('admin','demo123'),json={'decision':'approve','comment':'无权'}).status_code==403

def test_external_timeout_is_visible(clients,monkeypatch):
    client,_=clients
    # fixture patches Client; replace transport instead of replacing workflow behavior.
    def timeout(request): raise httpx.ReadTimeout('simulated',request=request)
    # Use TestClient's unchanged httpx.Client base class for an independent transport.
    base_client=TestClient.__bases__[0]
    monkeypatch.setattr(services.httpx,'Client',lambda **kwargs: base_client(transport=httpx.MockTransport(timeout),**kwargs))
    task=submit(client).json()
    assert task['status']=='failed' and not task['logs'][0]['success']
    assert 'receipt' not in task['result']

def test_metrics_and_persistence(clients):
    client,_=clients
    submit(client);submit(client,claim(amount='1500'));submit(client,claim(project_id='P002',amount='600'))
    metrics=client.get('/api/metrics',auth=('admin','demo123')).json()
    assert metrics['total']==3 and metrics['completed']==2
    assert metrics['completion_rate']==pytest.approx(2/3)
    assert metrics['manual_rate']==pytest.approx(1/3)
    with TestClient(app) as restarted:
        pending=restarted.get('/api/tasks',auth=('approver','demo123')).json()
        task=next(t for t in pending if t['status']=='awaiting_approval')
        assert approve(restarted,task).json()['status']=='succeeded'

def test_finance_auth_and_idempotent_receipt(clients):
    _,finance=clients
    assert finance.get('/budgets/P001').status_code==401
    body={'task_id':str(uuid4()),'decision':'approved','reason':'演示'}
    headers={'Authorization':'Bearer local-finance-key'}
    first=finance.post('/results',json=body,headers=headers)
    assert first.status_code==200
    assert finance.post('/results',json=body,headers=headers).json()==first.json()
    assert finance.post('/results',json={**body,'decision':'rejected'},headers=headers).status_code==409


def test_writeback_failure_preserves_decision(clients,monkeypatch):
    client,_=clients
    base_client=TestClient.__bases__[0]
    def dispatch(request):
        if request.url.path.startswith('/budgets/'):
            return httpx.Response(200,json=budget_sample(),request=request)
        return httpx.Response(503,json={'detail':'simulated outage'},request=request)
    monkeypatch.setattr(services.httpx,'Client',lambda **kwargs: base_client(transport=httpx.MockTransport(dispatch),**kwargs))
    task=submit(client).json()
    assert task['status']=='failed'
    assert task['result']['decision']=='approved' and 'receipt' not in task['result']
    assert task['logs'][-1]['success'] is False


def test_recheck_budget_after_approval(clients,monkeypatch):
    client,finance=clients
    task=submit(client,claim(amount='1500')).json()
    base_client=TestClient.__bases__[0]
    def dispatch(request):
        if request.url.path.startswith('/budgets/'):
            return httpx.Response(200,json=budget_sample(available='10.00'),request=request)
        response=finance.request(request.method,request.url.path,content=request.content,headers=dict(request.headers))
        return httpx.Response(response.status_code,json=response.json(),request=request)
    monkeypatch.setattr(services.httpx,'Client',lambda **kwargs: base_client(transport=httpx.MockTransport(dispatch),**kwargs))
    done=approve(client,task).json()
    assert done['status']=='rejected' and '预算不足' in done['result']['reason']


def test_ownership_and_self_approval(clients):
    from app.database import SessionLocal
    from app.models import Task
    client,_=clients
    task=submit(client,claim(amount='1500')).json()
    with SessionLocal() as db:
        row=db.get(Task,task['id']);row.owner='approver';db.commit()
    assert client.get('/api/tasks/'+task['id'],auth=('employee','demo123')).status_code==404
    assert client.get('/api/tasks',auth=('employee','demo123')).json()==[]
    assert approve(client,task).status_code==403


def budget_sample(**changes):
    return {'project_id': 'P001', 'available': '10000.00',
            'currency': 'CNY', 'simulated': True, **changes}


def test_submission_has_unique_id_and_can_be_queried(clients):
    from app.database import SessionLocal
    from app.models import Task

    client, _ = clients
    body = claim()
    first = submit(client, body).json()
    second = submit(client, body).json()
    assert UUID(first['id']).version == 4
    assert first['id'] != second['id']
    detail = client.get('/api/tasks/' + first['id'], auth=('employee', 'demo123'))
    assert detail.status_code == 200
    assert detail.json() == first
    assert 'request_key' not in first
    with SessionLocal() as db:
        saved = db.get(Task, first['id'])
        assert saved.payload == body
        assert saved.owner == 'employee' and saved.status == 'succeeded'


def test_query_and_submission_replay_survive_database_reconnect(clients):
    client, _ = clients
    key = str(uuid4())
    original = submit(client, key=key).json()
    engine.dispose()
    finance_engine.dispose()
    with TestClient(app) as restarted:
        detail = restarted.get('/api/tasks/' + original['id'], auth=('employee', 'demo123'))
        assert detail.json() == original
        assert submit(restarted, key=key).json() == original
        assert len(restarted.get('/api/tasks', auth=('employee', 'demo123')).json()) == 1


@pytest.mark.parametrize('key', [None, 'short', ' ' * 8, 'x' * 101])
def test_invalid_submission_key_creates_no_task(clients, key):
    client, _ = clients
    headers = {} if key is None else {'Idempotency-Key': key}
    response = client.post('/api/tasks', json=claim(), auth=('employee', 'demo123'), headers=headers)
    assert response.status_code == 422
    assert client.get('/api/tasks', auth=('employee', 'demo123')).json() == []


def test_task_query_missing_id_and_authentication(clients):
    client, _ = clients
    assert client.get('/api/tasks/' + str(uuid4()), auth=('employee', 'demo123')).status_code == 404
    task = submit(client).json()
    assert client.get('/api/tasks/' + task['id']).status_code == 401
    for role in ['approver', 'admin']:
        assert client.get('/api/tasks/' + task['id'], auth=(role, 'demo123')).json() == task


@pytest.mark.parametrize('project_id,available', [('P001', '10000.00'), ('P002', '500.00')])
def test_budget_query_returns_documented_sample(clients, project_id, available):
    _, finance = clients
    headers = {'Authorization': 'Bearer local-finance-key'}
    response = finance.get('/budgets/' + project_id, headers=headers)
    assert response.status_code == 200
    assert response.json() == {'project_id': project_id, 'available': available,
                               'currency': 'CNY', 'simulated': True}
    assert finance.get('/budgets/P999', headers=headers).status_code == 404
    assert finance.get('/budgets/' + project_id,
                       headers={'Authorization': 'Bearer incorrect-key'}).status_code == 401


@pytest.mark.parametrize('changes', [{'task_id': 'not-a-uuid'}, {'reason': '   '},
                                     {'decision': 'paid'}])
def test_invalid_finance_result_is_not_saved(clients, changes):
    from finance.main import Receipt, Session

    _, finance = clients
    body = {'task_id': str(uuid4()), 'decision': 'approved', 'reason': '测试初审通过', **changes}
    response = finance.post('/results', json=body,
                            headers={'Authorization': 'Bearer local-finance-key'})
    assert response.status_code == 422
    with Session() as db:
        assert db.get(Receipt, body['task_id']) is None


@pytest.mark.parametrize('response_body', [
    [], {}, budget_sample(available='invalid'), budget_sample(available='NaN'),
    budget_sample(available='Infinity'), budget_sample(available='-1.00'),
    budget_sample(available='1.001'), budget_sample(project_id='P002'),
    budget_sample(currency='USD'), budget_sample(simulated=False),
])
def test_invalid_budget_response_produces_queryable_failure(clients, monkeypatch, response_body):
    client, _ = clients
    base_client = TestClient.__bases__[0]

    def dispatch(request):
        return httpx.Response(200, json=response_body, request=request)

    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs:
                        base_client(transport=httpx.MockTransport(dispatch), **kwargs))
    response = submit(client)
    assert response.status_code == 201
    task = response.json()
    assert task['status'] == 'failed'
    assert task['result']['error']
    assert len(task['logs']) == 1 and task['logs'][0]['success'] is False
    assert 'receipt' not in task['result']
    assert client.get('/api/tasks/' + task['id'], auth=('employee', 'demo123')).json() == task


@pytest.mark.parametrize('changes', [
    {'task_id': str(uuid4())}, {'decision': 'rejected'}, {'reason': '不匹配的审核意见'},
    {'receipt_id': ''}, {'simulated': False},
])
def test_invalid_receipt_does_not_complete_task(clients, monkeypatch, changes):
    client, finance = clients
    base_client = TestClient.__bases__[0]

    def dispatch(request):
        if request.url.path.startswith('/budgets/'):
            response = finance.get(request.url.path, headers=dict(request.headers))
            return httpx.Response(response.status_code, json=response.json(), request=request)
        body = json.loads(request.content)
        receipt = {**body, 'receipt_id': 'demo-' + body['task_id'], 'simulated': True, **changes}
        return httpx.Response(200, json=receipt, request=request)

    monkeypatch.setattr(services.httpx, 'Client', lambda **kwargs:
                        base_client(transport=httpx.MockTransport(dispatch), **kwargs))
    task = submit(client).json()
    assert task['status'] == 'failed'
    assert task['result']['decision'] == 'approved' and 'receipt' not in task['result']
    assert task['logs'][0]['success'] is True and task['logs'][1]['success'] is False
    assert client.get('/api/tasks/' + task['id'], auth=('employee', 'demo123')).json() == task
