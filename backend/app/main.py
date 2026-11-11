from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from time import perf_counter
from threading import RLock
from uuid import uuid4
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import current_user
from .database import SessionLocal, engine, get_db
from .models import ParseSnapshot, Task,InvoiceFile,UploadBatch,TaskRevision
from .migrations import initialize_database
from .schemas import ApprovalInput, ClaimInput, ParseInput, TaskResponse,ResubmitInput,BatchInput,DraftInput
from . import config, invoices, services,files,policies
from .ai import ai_enabled, baseline_fields, parse_fields
from .validation import input_conflicts

# SQLite本地版仅支持一个进程；PostgreSQL审批同时使用行锁。
mutation_lock = RLock()

@asynccontextmanager
async def lifespan(app):
    initialize_database(engine)
    with SessionLocal() as db:
        if config.ACTIVE_POLICY_VERSION!='demo-v1':policies.register(db)
        services.mark_interrupted(db)
    yield

app = FastAPI(title='数字员工业务服务', version=config.APP_VERSION, lifespan=lifespan)

@app.get('/health')
def health(db: Session = Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'mode': 'hybrid' if ai_enabled() else 'rule_baseline', 'version': config.APP_VERSION,
            'stage': config.APP_STAGE, 'ai_enabled': ai_enabled(),
            'database': engine.dialect.name,'active_policy':policies.current()['version'] if config.ACTIVE_POLICY_VERSION!='demo-v1' else 'demo-v1'}

@app.get('/api/me')
def me(user=Depends(current_user)):
    return user

@app.post('/api/parse')
def parse(body: ParseInput, db: Session = Depends(get_db), user=Depends(current_user)):
    start = perf_counter()
    snapshot = parse_fields(body.description)
    parse_id = str(uuid4())
    snapshot = {**snapshot, 'parse_id': parse_id, 'latency_ms': round((perf_counter() - start) * 1000, 2)}
    db.add(ParseSnapshot(id=parse_id, owner=user['username'],
                         description_hash=sha256(body.description.encode()).hexdigest(),
                         snapshot=snapshot, created_at=services.now()))
    db.commit()
    return snapshot

@app.post('/api/invoices')
async def upload_invoice(file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(current_user)):
    """Validate invoice type/readability in memory before creating an archive."""
    if user['role'] != 'employee':
        raise HTTPException(403, '请使用员工账号上传发票')
    data = await file.read(invoices.MAX_PDF_BYTES + 1)
    if config.ACTIVE_POLICY_VERSION!='demo-v1':
        start=perf_counter();recognition_error=None
        try:
            extracted=await run_in_threadpool(invoices.read_receipt,data)
            fields=invoices.receipt_fields(extracted)
            invoices.validate_receipt(extracted,fields)
        except invoices.InvoiceError as exc:
            raise HTTPException(exc.status,exc.message.replace('原件已保存','本次未保存')) from exc
        reject_duplicate_upload(db,user,fields,sha256(data).hexdigest())
        item=files.save(db,data,file.filename or 'invoice.pdf',user['username'])
        item.invoice_fields=fields
        missing=[k for k,v in fields.items() if k!='invoice_code' and not v]
        amount=fields['total']
        from decimal import Decimal
        amount=amount if amount and 0<Decimal(amount)<=5000 else None
        snapshot={'mode':'receipt_fields','source':extracted['read_method'],'suggested_amount':amount,
                  'suggested_category':invoices.receipt_category(fields['item_name']),
                  'requires_confirmation':True,'requires_manual_review':bool(missing or extracted['low_confidence']),
                  'message':'已保存原件，请对照票据核对并补全字段','invoice_id':str(uuid4()),
                  'file_id':item.id,'latency_ms':round((perf_counter()-start)*1000,2),
                  'invoice':{**files.info(item),'pages':extracted['pages'],'read_method':extracted['read_method'],
                             'text_chars':extracted['full_text_chars'],'text_preview':invoices.text_preview(extracted['text']),
                             'fields':fields,'missing_fields':missing,'recognition_error':recognition_error,
                             'hints':{'invoice_no':fields['invoice_no'],'invoice_title':fields['buyer'],'expense_date':fields['invoice_date']}}}
        snapshot['parse_id']=snapshot['invoice_id']
        db.add(ParseSnapshot(id=snapshot['invoice_id'],owner=user['username'],description_hash=sha256(extracted['text'].encode()).hexdigest(),snapshot=snapshot,created_at=services.now()))
        item.snapshot_id=snapshot['invoice_id'];db.commit();return snapshot
    try:
        extracted = invoices.extract_pdf_text(data)
    except invoices.InvoiceError as exc:
        raise HTTPException(exc.status, exc.message) from exc

    start = perf_counter()
    snapshot = parse_fields(extracted['text'])
    hints = invoices.invoice_hints(extracted['text'])
    invoice_id = str(uuid4())
    snapshot = {
        **snapshot,
        'parse_id': invoice_id,
        'invoice_id': invoice_id,
        'latency_ms': round((perf_counter() - start) * 1000, 2),
        'invoice': {
            'filename': (file.filename or 'invoice.pdf')[-120:],
            'pages': extracted['pages'],
            'text_chars': extracted['full_text_chars'],
            'text_preview': invoices.text_preview(extracted['text']),
            'hints': hints,
        },
    }
    db.add(ParseSnapshot(id=invoice_id, owner=user['username'],
                         description_hash=sha256(extracted['text'].encode()).hexdigest(),
                         snapshot=snapshot, created_at=services.now()))
    db.commit()
    return snapshot


@app.get('/api/policy')
def policy(db:Session=Depends(get_db),user=Depends(current_user)):
    return policies.register(db)


@app.get('/api/files/{file_id}')
def file_info(file_id:str,db:Session=Depends(get_db),user=Depends(current_user)):
    return files.info(files.owned(db,file_id,user))


@app.get('/api/files/{file_id}/content')
def file_content(file_id:str,download:bool=False,db:Session=Depends(get_db),user=Depends(current_user)):
    item=files.owned(db,file_id,user);path=files.read(db,item,user,'download' if download else 'preview')
    return FileResponse(path,media_type=item.media_type,filename=item.filename,
                        content_disposition_type='attachment' if download else 'inline',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


def batch_info(batch):
    return {'batch_id':batch.id,'items':batch.items,'current_index':batch.current_index,'updated_at':batch.updated_at}


def reject_duplicate_upload(db,user,fields,digest,exclude_task=None):
    """Reject before a duplicate becomes an archive or a queue item."""
    number=fields.get('invoice_no','');code=fields.get('invoice_code','')
    for task in db.scalars(select(Task).where(Task.status.notin_(['rejected','cancelled']))):
        if task.id==exclude_task or task.payload.get('category')=='出差餐补':continue
        inv=task.payload.get('invoice') or {}
        original=db.get(InvoiceFile,str(task.payload.get('file_id')))
        same_number=bool(number and inv.get('invoice_no')==number and (len(number)==20 or inv.get('invoice_code','')==code))
        if same_number or original and original.digest==digest:
            own=task.owner==user['username'];label=task.business_no or task.id
            if own and (task.status in ['returned','awaiting_approval'] or task.status=='failed' and not task.pending_writeback):
                detail=f'这张发票已关联报销 {label}。请到“我的报销”修改原申请，或先撤销原申请再上传；本次未保存、未加入队列。'
            elif own and task.status=='succeeded':
                detail=f'这张发票已在报销 {label} 中通过，不能重复申报；本次未保存、未加入队列。'
            else:
                detail=('这张发票已有处理中或有效报销'+(f' {label}' if own else '')+'，请先处理原申请；本次未保存、未加入队列。')
            raise HTTPException(409,detail)


@app.post('/api/batches',status_code=201)
def create_batch(body:BatchInput,db:Session=Depends(get_db),user=Depends(current_user)):
    if user['role']!='employee':raise HTTPException(403,'仅员工可管理批次')
    if len(set(body.file_ids))!=len(body.file_ids):raise HTTPException(422,'同一批次不能重复选择同一文件')
    items=[];seen_numbers=set();seen_digests=set();warnings=[]
    for file_id in body.file_ids:
        item=files.owned(db,file_id,user)
        if item.owner!=user['username']:raise HTTPException(404,'文件不可见')
        snap=db.get(ParseSnapshot,item.snapshot_id) if item.snapshot_id else None
        reject_duplicate_upload(db,user,item.invoice_fields,item.digest)
        number=item.invoice_fields.get('invoice_no','');code=item.invoice_fields.get('invoice_code','')
        key=number if len(number)==20 else code+':'+number
        if item.digest in seen_digests or number and key in seen_numbers:
            warnings.append(item.filename+'：同批次发票重复，已从可提交队列排除');continue
        seen_digests.add(item.digest)
        if number:seen_numbers.add(key)
        items.append({'file_id':item.id,'filename':item.filename,'invoice_id':item.snapshot_id,
                      'suggestion':snap.snapshot if snap else None,'form':{},'status':'pending',
                      'request_key':str(uuid4()),'task_id':None})
    now=services.now();batch=UploadBatch(id=str(uuid4()),owner=user['username'],items=items,current_index=0,created_at=now,updated_at=now)
    db.add(batch);db.commit();return {**batch_info(batch),'warnings':warnings}


@app.get('/api/batches')
def list_batches(db:Session=Depends(get_db),user=Depends(current_user)):
    batches=list(db.scalars(select(UploadBatch).where(UploadBatch.owner==user['username']).order_by(UploadBatch.updated_at.desc())))
    for batch in batches:
        items=[dict(x) for x in batch.items];changed=False
        for item in items:
            prior=db.scalar(select(Task).where(Task.owner==user['username'],Task.request_key==item['request_key']))
            if prior and (item['status']!='submitted' or item.get('task_id')!=prior.id):
                item.update(status='submitted',task_id=prior.id);changed=True
        if changed:batch.items=items
    db.commit()
    return [batch_info(b) for b in batches]


@app.put('/api/batches/{batch_id}/items/{index}')
def save_draft(batch_id:str,index:int,body:DraftInput,db:Session=Depends(get_db),user=Depends(current_user)):
    with mutation_lock:
        batch=db.get(UploadBatch,batch_id)
        if not batch or batch.owner!=user['username']:raise HTTPException(404,'批次不存在或不可见')
        if index<0 or index>=len(batch.items):raise HTTPException(404,'条目不存在')
        items=[dict(x) for x in batch.items]
        prior=db.scalar(select(Task).where(Task.owner==user['username'],Task.request_key==items[index]['request_key']))
        if prior:
            items[index].update(status='submitted',task_id=prior.id)
        elif items[index]['status']=='submitted':raise HTTPException(409,'已提交条目只读')
        else:
            if len(str(body.form))>20000:raise HTTPException(422,'草稿过大')
            form={k:v for k,v in body.form.items() if k not in ['confirmed','conflict_acknowledged']}
            items[index].update(form=form,status='skipped' if body.skipped else 'pending')
        batch.items=items;batch.current_index=min(body.current_index,len(items)-1);batch.updated_at=services.now();db.commit()
        return batch_info(batch)


def source_snapshot(body,db,user):
    if body.file_id:
        item=files.owned(db,body.file_id,user)
        if item.owner!=user['username']:raise HTTPException(404,'票据不存在或不可见')
        files.read(db,item,user,'submission')
        parsed=db.get(ParseSnapshot,item.snapshot_id) if item.snapshot_id else None
        if body.invoice_id and (not parsed or str(body.invoice_id)!=parsed.id):raise HTTPException(422,'识别记录与当前原件不符')
        return parsed.snapshot if parsed else {'mode':'manual_fields','requires_manual_review':True}
    raise HTTPException(422,'请先上传并关联本人票据或出差证明原件')


def enterprise_conflicts(body,snapshot):
    fields=(snapshot.get('invoice') or {}).get('fields') or {}
    actual=body.invoice.model_dump(mode='json');conflicts=[]
    for key,value in fields.items():
        if value and value!=actual.get(key):conflicts.append({'field':key,'reason':'票面识别值与确认值不同'})
    if fields.get('total') and body.invoice.total and body.payload()['amount']!=body.invoice.total:
        conflicts.append({'field':'amount','reason':'本次申报与票面总额不同'})
    if conflicts and (not body.conflict_acknowledged or not body.confirmation_note):raise HTTPException(422,'票面或金额存在差异，请确认并填写具体说明')
    return conflicts


def visible_query(user):
    stmt = select(Task).order_by(Task.created_at.desc())
    return stmt.where(Task.owner == user['username']) if user['role'] == 'employee' else stmt

@app.get('/api/tasks', response_model=list[TaskResponse])
def tasks(db: Session = Depends(get_db), user=Depends(current_user)):
    return [services.serialize(t,user) for t in db.scalars(visible_query(user))]

@app.get('/api/tasks/{task_id}', response_model=TaskResponse)
def task_detail(task_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    task = db.scalar(visible_query(user).where(Task.id == task_id))
    if not task:
        raise HTTPException(404, '任务不存在')
    return services.serialize(task,user)

@app.post('/api/tasks', status_code=201, response_model=TaskResponse)
def create_task(body: ClaimInput, idempotency_key: str = Header(min_length=8, max_length=100, pattern=r'\S'),
                db: Session = Depends(get_db), user=Depends(current_user)):
    if user['role'] != 'employee':
        raise HTTPException(403, '请使用员工账号提交')
    payload = body.model_dump(mode='json')
    with mutation_lock:
        prior = db.scalar(select(Task).where(Task.owner == user['username'], Task.request_key == idempotency_key))
        if prior:
            if prior.submission_payload != payload:
                raise HTTPException(409, '同一提交标识不能用于不同的报销内容')
            return services.serialize(prior,user)
        confirmed_payload = body.payload()
        enterprise=config.ACTIVE_POLICY_VERSION!='demo-v1'
        if body.parse_id and body.invoice_id:
            raise HTTPException(422, '解析快照与发票来源不能同时提供，请二选一')
        if enterprise:
            if not body.department.strip():raise HTTPException(422,'请填写归属部门')
            snapshot=source_snapshot(body,db,user)
            for batch in db.scalars(select(UploadBatch).where(UploadBatch.owner==user['username'])):
                for item in batch.items:
                    if item['request_key']==idempotency_key and item['file_id']!=str(body.file_id):
                        raise HTTPException(422,'提交标识与批次中的原件不符')
        elif body.parse_id:
            parsed = db.get(ParseSnapshot, str(body.parse_id))
            if (not parsed or parsed.owner != user['username']
                    or parsed.description_hash != sha256(body.description.encode()).hexdigest()):
                raise HTTPException(422, '解析记录不存在、归属不符或说明已变化，请重新解析')
            snapshot = parsed.snapshot
        elif body.invoice_id:
            parsed = db.get(ParseSnapshot, str(body.invoice_id))
            if not parsed or parsed.owner != user['username'] or not (parsed.snapshot or {}).get('invoice'):
                raise HTTPException(422, '发票记录不存在或归属不符，请重新上传发票')
            snapshot = parsed.snapshot
        else:
            snapshot = {**baseline_fields(body.description), 'mode': 'structured_input',
                        'source': 'structured_input', 'requires_manual_review': False}
        conflicts = enterprise_conflicts(body,snapshot) if enterprise else input_conflicts(body.description, confirmed_payload, snapshot)
        if conflicts and (not body.conflict_acknowledged or not body.confirmation_note):
            raise HTTPException(422, '存在字段冲突，请显式确认并填写confirmation_note：' +
                                '；'.join(item['reason'] for item in conflicts))
        timestamp = services.now()
        from .numbers import allocate
        policy=policies.register(db) if enterprise else None
        task = Task(id=str(uuid4()), business_no=allocate(db,user['username'],timestamp), owner=user['username'], request_key=idempotency_key,
                    status='queued', payload=confirmed_payload, result={}, citations=[], logs=[], events=[], approvals=[],
                    created_at=timestamp, wait_seconds=0.0, manual=False,
                    submission_payload=payload, parse_snapshot=snapshot, amendments=[], pending_writeback={},
                    policy_version=policy['version'] if policy else 'demo-v1',revision=1,
                    confirmations=[{'at': timestamp, 'actor': user['username'], 'confirmed': True,
                                    'conflicts': conflicts, 'conflict_acknowledged': body.conflict_acknowledged,
                                    'note': body.confirmation_note, 'final_fields': confirmed_payload}])
        db.add(task)
        db.commit()
        if enterprise:
            from .enterprise import reserve
            db.add(TaskRevision(id=str(uuid4()),task_id=task.id,number=1,actor=user['username'],request_key=idempotency_key,
                                reason='初次提交',payload=confirmed_payload,before={},snapshot=snapshot,created_at=timestamp))
            reserve(db,task);db.commit()
        else:
            # Preserve the synchronous replay contract of historical demo-v1.
            services.execute(task,db);db.commit();return services.serialize(task,user)
    # Slow model/tool calls must not hold the short database mutation lock:
    # users can save the next receipt while this one is being reviewed.
    services.execute(task, db)
    db.commit()
    return services.serialize(task,user)


@app.post('/api/tasks/{task_id}/resubmit',response_model=TaskResponse)
def resubmit(task_id:str,body:ResubmitInput,idempotency_key:str=Header(min_length=8,max_length=100,pattern=r'\S'),
             db:Session=Depends(get_db),user=Depends(current_user)):
    if user['role']!='employee':raise HTTPException(403,'仅员工可修改重提')
    with mutation_lock:
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update())
        if not task or task.owner!=user['username']:raise HTTPException(404,'任务不存在或不可见')
        data=body.model_dump(mode='json')
        prior=db.scalar(select(TaskRevision).where(TaskRevision.task_id==task.id,TaskRevision.request_key==idempotency_key))
        if prior:
            if prior.snapshot.get('_resubmit_request')!=data:raise HTTPException(409,'重提标识不能用于不同内容')
            return services.serialize(task,user)
        if task.status!='returned':raise HTTPException(409,'只有退回任务可以修改重提')
        if task.policy_version=='demo-v1':raise HTTPException(409,'历史任务不支持新流程')
        if not body.department.strip():raise HTTPException(422,'请填写部门')
        snapshot=source_snapshot(body,db,user);conflicts=enterprise_conflicts(body,snapshot)
        before=task.payload;now=services.now()
        task.revision+=1;task.payload=body.payload();task.parse_snapshot=snapshot
        if task.result.get('returned_at'):
            elapsed=(datetime.fromisoformat(now)-datetime.fromisoformat(task.result['returned_at'])).total_seconds()
            task.wait_seconds+=max(0,elapsed)
        task.amendments=[*task.amendments,{'actor':user['username'],'at':now,'reason':body.revision_reason,
                                         'before':before,'after':task.payload,'revision':task.revision}]
        task.confirmations=[*task.confirmations,{'actor':user['username'],'at':now,'confirmed':True,'conflicts':conflicts,
                                                'note':body.confirmation_note,'final_fields':task.payload}]
        task.result={};task.finished_at=None;task.failure_stage=None;task.pending_writeback={}
        task.return_draft={}
        services.event(task,'running','员工修改重提，重新执行初审',actor=user['username'],revision=task.revision)
        db.add(TaskRevision(id=str(uuid4()),task_id=task.id,number=task.revision,actor=user['username'],request_key=idempotency_key,
                            reason=body.revision_reason,payload=task.payload,before=before,
                            snapshot={**snapshot,'_resubmit_request':data},created_at=now))
        from .enterprise import reserve
        reserve(db,task);db.commit();services.execute(task,db);return services.serialize(task,user)


@app.put('/api/tasks/{task_id}/draft')
def returned_draft(task_id:str,body:DraftInput,db:Session=Depends(get_db),user=Depends(current_user)):
    with mutation_lock:
        task=db.get(Task,task_id)
        if not task or task.owner!=user['username'] or user['role']!='employee':raise HTTPException(404,'任务不存在或不可见')
        if task.status!='returned':raise HTTPException(409,'仅退回任务可保存修改草稿')
        if len(str(body.form))>20000:raise HTTPException(422,'草稿过大')
        task.return_draft={k:v for k,v in body.form.items() if k not in ['confirmed','conflict_acknowledged']}
        db.commit();return {'saved':True}

@app.post('/api/tasks/{task_id}/approval', response_model=TaskResponse)
def approval(task_id: str, body: ApprovalInput, db: Session = Depends(get_db), user=Depends(current_user)):
    if user['role'] not in (['approver'] if config.ACTIVE_POLICY_VERSION=='demo-v1' else ['approver','admin']):
        raise HTTPException(403, '该账号没有审批权限')
    with mutation_lock:
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task:
            raise HTTPException(404, '任务不存在')
        if task.owner == user['username']:
            raise HTTPException(403, '不能审批本人申请')
        if task.status != 'awaiting_approval':
            raise HTTPException(409, '任务不在待审批状态，请勿重复处理')
        if config.ACTIVE_POLICY_VERSION!='demo-v1' and body.corrected_fields is not None:
            raise HTTPException(422,'审批人不能修改员工字段，请退回补充')
        if config.ACTIVE_POLICY_VERSION!='demo-v1' and body.decision=='reject':
            body=body.model_copy(update={'decision':'return'})
        if config.ACTIVE_POLICY_VERSION!='demo-v1' and task.policy_version=='demo-v1' and body.decision=='return':
            policy=policies.register(db)
            if not db.scalar(select(TaskRevision).where(TaskRevision.task_id==task.id,TaskRevision.number==task.revision)):
                db.add(TaskRevision(id=str(uuid4()),task_id=task.id,number=task.revision,actor='system',request_key=task.request_key,
                                    reason='保留历史申请；退回后按新制度补齐再提交',payload=task.payload,before={},
                                    snapshot={**task.parse_snapshot,'policy_version':'demo-v1'},created_at=services.now()))
            task.policy_version=policy['version']
            db.commit()
        services.approve(task, body, user, db)
        db.commit()
        return services.serialize(task,user)


@app.post('/api/tasks/{task_id}/withdraw',response_model=TaskResponse)
def withdraw(task_id:str,db:Session=Depends(get_db),user=Depends(current_user)):
    with mutation_lock:
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update())
        if not task or user['role']!='employee' or task.owner!=user['username']:raise HTTPException(404,'申请不存在或不可见')
        if task.status=='cancelled':return services.serialize(task,user)
        if task.status not in ['returned','awaiting_approval','failed'] or task.pending_writeback or task.result.get('receipt'):
            raise HTTPException(409,'当前状态不能撤销；处理中请待初审结束，写回结果未核实请先恢复，已完成申请保留记录')
        from .models import InvoiceClaim
        for row in db.scalars(select(InvoiceClaim).where(InvoiceClaim.task_id==task.id)):db.delete(row)
        if task.approval_started_at and task.status=='awaiting_approval':
            task.wait_seconds+=max(0,(datetime.fromisoformat(services.now())-datetime.fromisoformat(task.approval_started_at)).total_seconds())
        services.event(task,'cancelled','员工撤销本次申请',actor=user['username'])
        task.finished_at=services.now();task.result={**task.result,'withdrawn_by':user['username'],'reason':'员工已撤销申请，未产生报销写回'}
        db.commit()
        from .enterprise import record_revision
        record_revision(task,db)
        return services.serialize(task,user)

@app.get('/api/metrics')
def metrics(db: Session = Depends(get_db), user=Depends(current_user)):
    if user['role'] not in ['admin','approver']:
        raise HTTPException(403, '仅管理与审批账号可查看指标')
    all_tasks = list(db.scalars(select(Task)))
    done = [t for t in all_tasks if t.status in ['succeeded', 'rejected']]
    elapsed = [(datetime.fromisoformat(t.finished_at) - datetime.fromisoformat(t.created_at)).total_seconds() for t in done]
    return {
        'total': len(all_tasks), 'completed': len(done),
        'completion_rate': len(done) / len(all_tasks) if all_tasks else None,
        'manual_rate': sum(t.manual for t in all_tasks) / len(all_tasks) if all_tasks else None,
        'average_processing_seconds': sum(max(0, s-t.wait_seconds) for s,t in zip(elapsed,done))/len(done) if done else None,
        'average_end_to_end_seconds': sum(elapsed)/len(done) if done else None,
    }
