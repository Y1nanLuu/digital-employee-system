"""Enterprise v2: tools, archived originals, return/resubmit and policy boundaries."""
from copy import deepcopy
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
import hashlib
import pytest
from conftest import clients
from test_invoices import build_pdf
from app import config,reviewer,policies
from app.database import SessionLocal
from app.models import Task,InvoiceFile,PolicyVersion
from app.enterprise_rules import evaluate


@pytest.fixture
def v2(clients,monkeypatch):
    monkeypatch.setattr(config,'ACTIVE_POLICY_VERSION','enterprise-demo-v2')
    monkeypatch.setenv('ENTERPRISE_POLICY_FILE',str(policies.DEFAULT_PATH.with_name('enterprise-policy-v2.json')))
    monkeypatch.setattr(reviewer,'review',lambda *args:{'conclusion':'通过','reasons':['按已提供材料核对'],
                        'citation_ids':['GEN-03'],'source':'llm','model':'test-double','latency_ms':1})
    return clients


def upload(client,number='26000000000000000001',total='300.00'):
    data=build_pdf(f'Invoice No: {number}\nDate: 2026-11-01\nAmount CNY {total}\nItem: taxi transport\n')
    r=client.post('/api/invoices',files={'file':('receipt.pdf',data,'application/pdf')},auth=('employee','demo123'))
    assert r.status_code==200,r.text
    s=r.json()
    payload={'project_id':'P001','department':'研发部','category':'交通费','amount':total,
             'expense_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
             'description':'项目客户现场往返交通费用','confirmed':True,'file_id':s['file_id'],'invoice_id':s['invoice_id'],
             'invoice':{'invoice_no':number,'invoice_code':'','invoice_date':'2026-11-01',
                        'buyer':config.COMPANY_TITLE,'seller':'测试供应商','total':total,'item_name':'taxi transport'},
             'details':{'route':'公司至客户现场','seat':'市内交通'},
             'conflict_acknowledged':True,'confirmation_note':'对照测试原件补齐购销方'}
    return data,s,payload


def submit(client,payload,key=None):
    r=client.post('/api/tasks',json=payload,auth=('employee','demo123'),headers={'Idempotency-Key':key or str(uuid4())})
    assert r.status_code==201,r.text
    return r.json()


def approve(client,task,decision='approve',**extra):
    return client.post('/api/tasks/'+task['id']+'/approval',json={'decision':decision,'comment':'核对测试材料','reviewed_fields':True,**extra},auth=('approver','demo123'))


def test_valid_fixture_runs_three_tools_and_llm(v2):
    c,_=v2;_,_,body=upload(c);task=submit(c,body)
    assert task['status']=='succeeded'
    assert task['result']['initial_review']['source']=='llm'
    assert {x['tool'] for x in task['logs']} >= {'/budgets/P001','/checks/invoice','/checks/duplicate','/reviews/results'}
    assert task['policy_version']=='enterprise-demo-v2'
    assert task['revisions'][0]['number']==1


def test_unknown_and_invalid_are_distinct(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');task=submit(c,body)
    assert task['status']=='awaiting_approval' and task['result']['invoice_check']['status']=='unknown'
    _,_,body=upload(c,'26000000000000000002','50.00');invalid=submit(c,body)
    assert invalid['status']=='rejected' and invalid['result']['invoice_check']['simulated'] is True


def test_model_pass_cannot_override_budget(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000003','1000.00');body['project_id']='P002'
    task=submit(c,body)
    assert task['status']=='rejected' and any(x['code']=='GEN-06' for x in task['result']['rule_findings']['hard'])


def test_exact_duplicate_different_file_and_idempotent_submit(v2):
    c,_=v2;_,_,body=upload(c);_,_,other=upload(c);key=str(uuid4());first=submit(c,body,key)
    assert submit(c,body,key)['id']==first['id']
    second=submit(c,other)
    assert second['status']=='rejected' and second['result']['duplicate_check']['duplicate']


def test_return_employee_edits_original_task_and_reruns_tools(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');original=submit(c,body)
    denied=approve(c,original,corrected_fields={'amount':'200.00'},correction_reason='不允许代改')
    assert denied.status_code==422
    returned=approve(c,original,'return',comment='请补充用途和明细').json()
    assert returned['status']=='returned' and not returned['result'].get('receipt')
    draft=c.put('/api/tasks/'+original['id']+'/draft',json={'form':{**body,'description':'修改后的真实业务用途'},'current_index':0},auth=('employee','demo123'))
    assert draft.status_code==200
    assert c.get('/api/tasks/'+original['id'],auth=('employee','demo123')).json()['return_draft']['description']=='修改后的真实业务用途'
    body['description']='修改后的真实业务用途';body['revision_reason']='按意见补充说明'
    key=str(uuid4());headers={'Idempotency-Key':key}
    result=c.post('/api/tasks/'+original['id']+'/resubmit',json=body,headers=headers,auth=('employee','demo123'))
    assert result.status_code==200,result.text
    task=result.json();assert task['id']==original['id'] and task['revision']==2 and task['status']=='awaiting_approval'
    assert len(task['revisions'])==2 and task['submission_payload']['description']==original['payload']['description']
    assert sum(x['tool']=='/checks/invoice' for x in task['logs'])==2
    assert c.post('/api/tasks/'+original['id']+'/resubmit',json=body,headers=headers,auth=('employee','demo123')).json()['revision']==2
    changed={**body,'description':'与原重提不同'}
    assert c.post('/api/tasks/'+original['id']+'/resubmit',json=changed,headers=headers,auth=('employee','demo123')).status_code==409


def test_file_original_integrity_and_access(v2):
    c,_=v2;data,s,body=upload(c)
    route='/api/files/'+s['file_id']+'/content'
    assert c.get(route).status_code==401
    assert c.get(route,auth=('approver','demo123')).status_code==404
    content=c.get(route,auth=('employee','demo123'));assert hashlib.sha256(content.content).digest()==hashlib.sha256(data).digest()
    task=submit(c,body)
    assert c.get(route,auth=('approver','demo123')).status_code==200
    assert c.delete('/api/files/'+s['file_id'],auth=('employee','demo123')).status_code in [404,405]


def test_batch_persists_draft_and_marks_created_task(v2):
    c,_=v2;_,s,body=upload(c)
    batch=c.post('/api/batches',json={'file_ids':[s['file_id']]},auth=('employee','demo123')).json()
    route='/api/batches/'+batch['batch_id']+'/items/0'
    draft=c.put(route,json={'form':{**body,'confirmed':True},'current_index':0},auth=('employee','demo123')).json()
    assert 'confirmed' not in draft['items'][0]['form']
    task=submit(c,body,batch['items'][0]['request_key'])
    result=c.put(route,json={'form':body,'current_index':0},auth=('employee','demo123')).json()
    assert result['items'][0]['task_id']==task['id'] and result['items'][0]['status']=='submitted'
    assert c.get('/api/batches',auth=('employee','demo123')).json()[0]['batch_id']==batch['batch_id']


def test_v2_requires_original_and_department(v2):
    c,_=v2;_,_,body=upload(c);body['file_id']=None
    assert c.post('/api/tasks',json=body,headers={'Idempotency-Key':str(uuid4())},auth=('employee','demo123')).status_code==422


def rule_body(category='住宿费',amount='500.00'):
    return {'amount':amount,'category':category,'department':'研发部','expense_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
            'invoice':{'invoice_no':'26000000000000000999','invoice_date':'2026-11-01','buyer':config.COMPANY_TITLE,'seller':'测试供应商','total':amount,'item_name':'测试费用'},
            'details':{'city':'北京','check_in':'2026-11-01','check_out':'2026-11-02','approval_no':'TRIP-001','proof_reference':'材料已附'}}


@pytest.mark.parametrize('amount,over',[('499.99',False),('500.00',False),('500.01',True)])
def test_hotel_limit_exact_boundary(amount,over):
    result=evaluate(rule_body(amount=amount),policies.current())
    assert any(x['code']=='TRV-05' for x in result['manual']) is over


def test_policy_threshold_changes_without_changing_history(v2):
    c,_=v2;_,_,body=upload(c);task=submit(c,body)
    with SessionLocal() as db:before=deepcopy(db.get(PolicyVersion,task['policy_version']).document)
    altered=deepcopy(before);altered['parameters']['auto_limit']='500.00'
    payload=rule_body('交通费','999.00');payload['details']={'route':'业务路线','seat':'二等座'}
    assert not any(x['code']=='GEN-03' for x in evaluate(payload,before)['manual'])
    assert any(x['code']=='GEN-03' for x in evaluate(payload,altered)['manual'])
    with SessionLocal() as db:assert db.get(PolicyVersion,task['policy_version']).document==before


def test_llm_failure_goes_manual(v2,monkeypatch):
    c,_=v2;monkeypatch.setattr(reviewer,'review',lambda *a:{'source':'rule_fallback','conclusion':'存疑','reasons':['接口超时'],'citation_ids':[]})
    _,_,body=upload(c);assert submit(c,body)['status']=='awaiting_approval'


@pytest.mark.parametrize('amount,expected',[('0.01','succeeded'),('999.00','succeeded'),('1000.00','succeeded'),
                                           ('1000.01','awaiting_approval'),('4999.99','awaiting_approval'),('5000.00','awaiting_approval')])
def test_real_api_amount_boundaries(v2,amount,expected):
    from finance.main import Session,InvoiceRegistry
    c,_=v2;number='26000000000000000888'
    with Session() as db:
        db.add(InvoiceRegistry(invoice_key=number,record={'invoice_date':'2026-11-01','total':amount,'status':'valid'}));db.commit()
    _,_,body=upload(c,number,amount)
    assert submit(c,body)['status']==expected


def test_invoice_over_claim_limit_is_read_but_not_submitted(v2):
    c,_=v2;_,snapshot,body=upload(c,'26000000000000000888','5000.01')
    assert snapshot['invoice']['fields']['total']=='5000.01'
    r=c.post('/api/tasks',json=body,headers={'Idempotency-Key':str(uuid4())},auth=('employee','demo123'))
    assert r.status_code==422


def test_changed_policy_is_actually_used_by_api(v2,tmp_path,monkeypatch):
    from finance.main import Session,InvoiceRegistry
    import json
    c,_=v2
    for number in ['26000000000000000888','26000000000000000889']:
        with Session() as db:
            db.add(InvoiceRegistry(invoice_key=number,record={'invoice_date':'2026-11-01','total':'999.00','status':'valid'}));db.commit()
    _,_,body=upload(c,'26000000000000000888','999.00');before=submit(c,body)
    assert before['status']=='succeeded'
    changed=policies.current();changed['version']='enterprise-test-threshold-500';changed['parameters']['auto_limit']='500.00'
    path=tmp_path/'policy.json';path.write_text(json.dumps(changed),encoding='utf-8');monkeypatch.setenv('ENTERPRISE_POLICY_FILE',str(path))
    _,_,body=upload(c,'26000000000000000889','999.00');after=submit(c,body)
    assert after['status']=='awaiting_approval' and after['policy_version']=='enterprise-test-threshold-500'
    original=c.get('/api/tasks/'+before['id'],auth=('employee','demo123')).json()
    assert original['policy_version']=='enterprise-demo-v2'
    assert any('1000.00' in x['text'] for x in original['citations'] if x['id']=='GEN-03')


def test_changed_ticket_number_cannot_bypass_same_original(v2):
    c,_=v2;_,_,body=upload(c);_,_,other=upload(c);first=submit(c,body);assert first['status']=='succeeded'
    # Same original, deliberately edited number: still detected by archived hash.
    body=other;body['invoice']['invoice_no']='26000000000000000777'
    assert submit(c,body)['status']=='rejected'


def test_cannot_approve_missing_fields_and_cannot_edit_active_task(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');body['invoice']['seller']=''
    task=submit(c,body);assert task['status']=='awaiting_approval'
    assert approve(c,task).status_code==422
    body['revision_reason']='尝试越过退回'
    assert c.post('/api/tasks/'+task['id']+'/resubmit',json=body,headers={'Idempotency-Key':str(uuid4())},auth=('employee','demo123')).status_code==409


def test_batch_key_cannot_reference_another_original(v2):
    c,_=v2;_,s,_=upload(c);_,_,other=upload(c,'26000000000000000003','1000.00')
    batch=c.post('/api/batches',json={'file_ids':[s['file_id']]},auth=('employee','demo123')).json()
    r=c.post('/api/tasks',json=other,headers={'Idempotency-Key':batch['items'][0]['request_key']},auth=('employee','demo123'))
    assert r.status_code==422


def test_corrupt_archive_cannot_be_approved(v2):
    from app import files
    c,_=v2;_,s,body=upload(c,'26000000000000000999');task=submit(c,body)
    with SessionLocal() as db:stored=db.get(InvoiceFile,s['file_id']);path=files.root()/stored.storage_key
    path.write_bytes(b'corrupt archive')
    assert c.get('/api/files/'+s['file_id']+'/content',auth=('employee','demo123')).status_code==409
    result=approve(c,task).json()
    assert result['status']=='failed' and result['failure_stage']=='file_validation'


@pytest.mark.parametrize('category,details,amount,code',[
 ('业务招待费',{'attendees':5,'business_partner':'客户','approval_no':'A1'},'1000.01','ENT-02'),
 ('业务招待费',{'attendees':0},'100.00','ENT-01'),
 ('办公用品',{},'300.00','OFF-01'),
 ('通讯费',{'service_period':'2026-11'},'100.01','TEL-01'),
 ('培训会议费',{},'300.00','EDU-01'),
 ('软件服务费',{},'300.00','IT-01'),
 ('其他',{},'300.00','OTH-01'),
 ('出差餐补',{'approval_no':'T1','trip_start':'2026-11-01','trip_end':'2026-11-01','supplied_lunches':1},'60.01','MEAL-02'),
 ('住宿费',{'city':'未知城市','check_in':'2026-11-01','check_out':'2026-11-02','approval_no':'T1','proof_reference':'清单'},'200.00','TRV-03'),
])
def test_category_specific_rules(category,details,amount,code,monkeypatch):
    monkeypatch.setenv('ENTERPRISE_POLICY_FILE',str(policies.DEFAULT_PATH.with_name('enterprise-policy-v2.json')))
    body=rule_body(category,amount);body['details']=details
    assert any(x['code']==code for x in evaluate(body,policies.current())['manual'])


def test_review_rejects_invented_citations(tmp_path,monkeypatch):
    import json,io
    path=tmp_path/'model.json';path.write_text(json.dumps({'active':'test','providers':{'test':{'base_url':'https://example.invalid/v1','api_key':'test-only','model':'test'}}}),encoding='utf-8')
    monkeypatch.setenv('AI_MODULE_ENABLED','true');monkeypatch.setenv('AI_MODULE_CONFIG',str(path))
    raw={'choices':[{'message':{'content':json.dumps({'conclusion':'通过','reasons':['伪造理由'],'citation_ids':['FAKE-999']})}}]}
    monkeypatch.setattr(reviewer.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(raw).encode()))
    result=reviewer.review({},[{'id':'GEN-03'}],{},{} )
    assert result['source']=='rule_fallback' and result['conclusion']=='存疑'


def test_batch_get_recovers_when_submit_response_was_lost(v2):
    c,_=v2;_,s,body=upload(c)
    batch=c.post('/api/batches',json={'file_ids':[s['file_id']]},auth=('employee','demo123')).json()
    task=submit(c,body,batch['items'][0]['request_key'])
    # Simulate closing the page before the client updates batch status.
    recovered=c.get('/api/batches',auth=('employee','demo123')).json()[0]
    assert recovered['items'][0]['status']=='submitted' and recovered['items'][0]['task_id']==task['id']


def test_employee_draft_is_not_visible_to_approver(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');task=submit(c,body)
    approve(c,task,'return')
    c.put('/api/tasks/'+task['id']+'/draft',json={'form':{'description':'尚未提交的草稿'},'current_index':0},auth=('employee','demo123'))
    assert c.get('/api/tasks/'+task['id'],auth=('employee','demo123')).json()['return_draft']
    assert not c.get('/api/tasks/'+task['id'],auth=('approver','demo123')).json()['return_draft']


def test_archive_backup_and_new_folder_restore(v2,tmp_path,monkeypatch):
    from scripts.archive_backup import backup,restore
    from scripts import archive_backup
    monkeypatch.setattr(archive_backup,'ROOT',tmp_path/'clean-checkout')
    from app import files
    from finance.main import engine as finance_engine
    from app.database import engine
    import sqlite3
    c,_=v2;data,s,body=upload(c);task=submit(c,body)
    archive=tmp_path/'backup.zip';target=tmp_path/'restored'
    backup(__import__('pathlib').Path(engine.url.database),__import__('pathlib').Path(finance_engine.url.database),files.root(),archive)
    assert (tmp_path/'clean-checkout/work').is_dir()
    manifest=restore(archive,target)
    assert len(manifest['files'])>=3
    with sqlite3.connect(target/'tasks.db') as db:
        assert db.execute('SELECT id FROM tasks WHERE id=?',(task['id'],)).fetchone()
        storage_key,digest=db.execute('SELECT storage_key,digest FROM invoice_files WHERE id=?',(s['file_id'],)).fetchone()
    assert hashlib.sha256((target/'files'/storage_key).read_bytes()).hexdigest()==digest
    with pytest.raises(ValueError):restore(archive,target)


@pytest.mark.parametrize('kind',['not_invoice','blank_image','unclear_ocr','broken_pdf','missing_total'])
def test_rejected_upload_does_not_persist_any_business_record(v2,monkeypatch,kind):
    from app import files,invoices
    from app.models import ParseSnapshot,UploadBatch,TaskRevision,FileAccess,InvoiceClaim
    from sqlalchemy import select,func
    import io
    from PIL import Image
    c,_=v2
    tables=[InvoiceFile,ParseSnapshot,Task,UploadBatch,TaskRevision,FileAccess,InvoiceClaim]
    def counts():
        with SessionLocal() as db:return [db.scalar(select(func.count()).select_from(t)) for t in tables]
    before=counts();paths=set(files.root().iterdir())
    name='bad.pdf';data=build_pdf('Meeting minutes 2026-11-01, budget 300.00, project notes')
    if kind=='blank_image':
        b=io.BytesIO();Image.new('RGB',(300,300),'white').save(b,format='PNG');name='blank.png';data=b.getvalue()
    elif kind=='unclear_ocr':
        monkeypatch.setattr(invoices,'read_receipt',lambda _: {'text':'Invoice No: 26000000000000000001\nDate: 2026-11-01\nAmount CNY 300.00',
                          'spans':[{'text':'Invoice','score':.42}], 'low_confidence':True,'read_method':'ocr'})
        monkeypatch.setattr(invoices,'receipt_fields',lambda _: {'invoice_no':'26000000000000000001','invoice_date':'2026-11-01','total':'300.00'})
    elif kind=='broken_pdf':data=b'%PDF-broken'
    elif kind=='missing_total':data=build_pdf('Invoice No: 26000000000000000001\nDate: 2026-11-01\nItem transport')
    r=c.post('/api/invoices',files={'file':(name,data)},auth=('employee','demo123'))
    assert r.status_code==422,r.text
    assert '未保存' in r.text or kind=='broken_pdf'
    assert counts()==before
    assert set(files.root().iterdir())==paths


def test_next_receipt_draft_can_save_during_previous_review(v2,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app import services
    c,_=v2;_,first,body=upload(c);_,second,_=upload(c,'26000000000000000999')
    batch=c.post('/api/batches',json={'file_ids':[first['file_id'],second['file_id']]},auth=('employee','demo123')).json()
    entered,release=Event(),Event();real=services.execute
    def slow(task,db):
        entered.set();assert release.wait(10);return real(task,db)
    monkeypatch.setattr(services,'execute',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(submit,c,body,batch['items'][0]['request_key'])
        try:
            assert entered.wait(5)
            saved=pool.submit(c.put,'/api/batches/'+batch['batch_id']+'/items/1',json={'form':{'description':'下一张独立草稿'},'current_index':1},auth=('employee','demo123')).result(timeout=3)
            assert saved.status_code==200,saved.text
            assert saved.json()['items'][1]['form']['description']=='下一张独立草稿'
            assert not pending.done()
        finally:release.set()
        assert pending.result(timeout=10)['status']=='succeeded'


def test_business_number_mapping_is_stable_and_sequential(v2):
    c,_=v2;_,_,body=upload(c);key=str(uuid4());first=submit(c,body,key)
    _,_,body2=upload(c,'26000000000000000999','50.00');second=submit(c,body2)
    day=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
    assert first['business_no']==f'BX-E001-{day}-0001'
    assert second['business_no']==f'BX-E001-{day}-0002'
    assert submit(c,body,key)['business_no']==first['business_no']
    assert first['id']!=first['business_no']


def test_admin_return_allows_employee_to_withdraw(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');task=submit(c,body)
    rejected=c.post('/api/tasks/'+task['id']+'/approval',auth=('admin','demo123'),json={'decision':'return','comment':'请补充业务证明'})
    assert rejected.status_code==200,rejected.text
    assert rejected.json()['status']=='returned' and not rejected.json()['result'].get('receipt')
    assert c.post('/api/tasks/'+task['id']+'/withdraw',auth=('admin','demo123')).status_code==404
    cancelled=c.post('/api/tasks/'+task['id']+'/withdraw',auth=('employee','demo123'))
    assert cancelled.status_code==200,cancelled.text
    assert cancelled.json()['status']=='cancelled'
    assert not cancelled.json()['result'].get('receipt')
    assert c.post('/api/tasks/'+task['id']+'/withdraw',auth=('employee','demo123')).json()['status']=='cancelled'
    from app.models import InvoiceClaim
    from sqlalchemy import select
    with SessionLocal() as db:assert not db.scalar(select(InvoiceClaim).where(InvoiceClaim.task_id==task['id']))
    retry=submit(c,body)
    assert retry['status']=='awaiting_approval'


def test_admin_reject_terminates_and_writes_final_reason(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');task=submit(c,body)
    reason='费用与公司业务无关，拒绝本次报销'
    response=c.post('/api/tasks/'+task['id']+'/approval',auth=('admin','demo123'),json={'decision':'reject','comment':reason})
    assert response.status_code==200,response.text
    final=response.json()
    assert final['status']=='rejected' and final['finished_at']
    assert final['result']['reason']==reason and final['result']['receipt']['decision']=='rejected'
    assert final['approvals'][-1]['decision']=='reject'
    assert final['revisions'][-1]['snapshot']['review_record']['status']=='rejected'
    assert c.post('/api/tasks/'+task['id']+'/resubmit',json={**body,'revision_reason':'原单不可重提'},auth=('employee','demo123'),headers={'Idempotency-Key':str(uuid4())}).status_code==409
    assert c.post('/api/tasks/'+task['id']+'/withdraw',auth=('employee','demo123')).status_code==409
    assert approve(c,final,'reject').status_code==409


def test_admin_can_approve_but_cannot_modify_or_withdraw_completed(v2):
    c,_=v2;_,_,body=upload(c,'26000000000000000999');task=submit(c,body)
    response=c.post('/api/tasks/'+task['id']+'/approval',auth=('admin','demo123'),json={'decision':'approve','comment':'原票及用途已核对','reviewed_fields':True})
    assert response.status_code==200,response.text
    assert response.json()['status']=='succeeded'
    assert c.post('/api/tasks/'+task['id']+'/withdraw',auth=('employee','demo123')).status_code==409


def test_duplicate_upload_is_rejected_before_archive_and_allowed_after_withdraw(v2):
    from app import files
    from app.models import ParseSnapshot
    from sqlalchemy import select,func
    c,_=v2;data,s,body=upload(c,'26000000000000000999');task=submit(c,body)
    paths=set(files.root().iterdir())
    with SessionLocal() as db:
        before=[db.scalar(select(func.count()).select_from(t)) for t in [InvoiceFile,ParseSnapshot,Task]]
        assert db.get(InvoiceFile,s['file_id']).invoice_fields['invoice_no']=='26000000000000000999'
    response=c.post('/api/invoices',files={'file':('again.pdf',data)},auth=('employee','demo123'))
    assert response.status_code==409 and task['business_no'] in response.text and '撤销' in response.text
    with SessionLocal() as db:assert before==[db.scalar(select(func.count()).select_from(t)) for t in [InvoiceFile,ParseSnapshot,Task]]
    assert paths==set(files.root().iterdir())
    c.post('/api/tasks/'+task['id']+'/withdraw',auth=('employee','demo123'))
    assert c.post('/api/invoices',files={'file':('again.pdf',data)},auth=('employee','demo123')).status_code==200


def test_same_batch_duplicate_excluded_from_submittable_queue(v2):
    c,_=v2;_,first,_=upload(c);_,second,_=upload(c)
    response=c.post('/api/batches',json={'file_ids':[first['file_id'],second['file_id']]},auth=('employee','demo123'))
    assert response.status_code==201
    assert len(response.json()['items'])==1 and response.json()['warnings']
