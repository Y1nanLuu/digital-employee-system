"""Invoice-only limit and supporting materials for new submissions."""
from uuid import uuid4
import pytest
from sqlalchemy import select
from app import config,policies,reviewer
from app.database import SessionLocal
from app.models import Task
from app.enterprise_rules import evaluate,missing_materials
from test_enterprise_review import rule_body,upload

@pytest.mark.parametrize('category',['交通费','住宿费','业务招待费','办公用品','通讯费','培训会议费','软件服务费','出差餐补','其他'])
def test_500_needs_no_category_materials(category,monkeypatch):
    monkeypatch.delenv('ENTERPRISE_POLICY_FILE',raising=False)
    body=rule_body(category,'500.00');body['details']={}
    result=evaluate(body,policies.current())
    assert result['facts']['invoice_only'] is True
    assert missing_materials(body,policies.current())==[]
    assert not any('请补充' in item['message'] or '缺少' in item['message'] for item in result['manual'])

def test_500_01_requires_materials_and_legacy_policy_keeps_its_rules(monkeypatch):
    monkeypatch.delenv('ENTERPRISE_POLICY_FILE',raising=False)
    policy=policies.current();body=rule_body('软件服务费','500.01');body['details']={}
    assert set(missing_materials(body,policy))=={'月份或服务期间','审批或合同编号','明细、收货或业务材料说明'}
    body['details']={'service_period':'2026-11','approval_no':'A1','proof_reference':'已附服务明细'}
    assert missing_materials(body,policy)==[]
    import json
    legacy=json.loads(policies.DEFAULT_PATH.with_name('enterprise-policy-v2.json').read_text(encoding='utf-8'))
    small=rule_body('办公用品','500.00');small['details']={}
    assert any(x['code']=='OFF-01' for x in evaluate(small,legacy)['manual'])
    assert legacy['version']=='enterprise-demo-v2'

def test_api_blocks_missing_materials_before_creating_task(clients,monkeypatch):
    monkeypatch.setattr(config,'ACTIVE_POLICY_VERSION','enterprise-demo-v3')
    monkeypatch.delenv('ENTERPRISE_POLICY_FILE',raising=False)
    monkeypatch.setattr(reviewer,'review',lambda *a:{'conclusion':'通过','reasons':['核对发票'],
                        'citation_ids':['GEN-08'],'source':'llm','latency_ms':1})
    client,_=clients
    _,_,body=upload(client,total='500.01');body['details']={}
    def submit(payload):
        return client.post('/api/tasks',json=payload,auth=('employee','demo123'),headers={'Idempotency-Key':str(uuid4())})
    response=submit(body)
    assert response.status_code==422 and '请补齐' in response.json()['detail']
    with SessionLocal() as db:assert list(db.scalars(select(Task)))==[]
    body['details']={'seat':'市内交通','route':'公司至客户','proof_reference':'业务行程记录'}
    response=submit(body)
    assert response.status_code==201,response.text
    assert response.json()['policy_version']=='enterprise-demo-v3'

def test_api_accepts_500_with_invoice_only(clients,monkeypatch):
    monkeypatch.setattr(config,'ACTIVE_POLICY_VERSION','enterprise-demo-v3')
    monkeypatch.delenv('ENTERPRISE_POLICY_FILE',raising=False)
    client,_=clients
    _,_,body=upload(client,total='500.00');body['details']={}
    response=client.post('/api/tasks',json=body,auth=('employee','demo123'),headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    assert response.json()['result']['rule_findings']['facts']['invoice_only'] is True
