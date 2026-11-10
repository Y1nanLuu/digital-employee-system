from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from time import perf_counter
from threading import RLock
from uuid import uuid4
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import current_user
from .database import SessionLocal, engine, get_db
from .models import ParseSnapshot, Task
from .migrations import initialize_database
from .schemas import ApprovalInput, ClaimInput, ParseInput, TaskResponse
from . import config, invoices, services
from .ai import ai_enabled, baseline_fields, parse_fields
from .validation import input_conflicts

# SQLite本地版仅支持一个进程；PostgreSQL审批同时使用行锁。
mutation_lock = RLock()

@asynccontextmanager
async def lifespan(app):
    initialize_database(engine)
    with SessionLocal() as db:
        services.mark_interrupted(db)
    yield

app = FastAPI(title='数字员工业务服务', version=config.APP_VERSION, lifespan=lifespan)

@app.get('/health')
def health(db: Session = Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'mode': 'rule_baseline', 'version': config.APP_VERSION,
            'stage': config.APP_STAGE, 'ai_enabled': ai_enabled(),
            'database': engine.dialect.name}

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
    """上传电子发票 PDF：抽取文字层文本，再走与 /api/parse 相同的字段提取链路。

    文件只在内存中解析、不落盘；返回结果与 /api/parse 同构，另加 invoice 元信息与 invoice_id，
    员工确认后创建任务时携带 invoice_id，任务快照因此保留发票来源。
    """
    if user['role'] != 'employee':
        raise HTTPException(403, '请使用员工账号上传发票')
    data = await file.read(invoices.MAX_PDF_BYTES + 1)
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


def visible_query(user):
    stmt = select(Task).order_by(Task.created_at.desc())
    return stmt.where(Task.owner == user['username']) if user['role'] == 'employee' else stmt

@app.get('/api/tasks', response_model=list[TaskResponse])
def tasks(db: Session = Depends(get_db), user=Depends(current_user)):
    return [services.serialize(t) for t in db.scalars(visible_query(user))]

@app.get('/api/tasks/{task_id}', response_model=TaskResponse)
def task_detail(task_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    task = db.scalar(visible_query(user).where(Task.id == task_id))
    if not task:
        raise HTTPException(404, '任务不存在')
    return services.serialize(task)

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
            return services.serialize(prior)
        confirmed_payload = body.payload()
        if body.parse_id and body.invoice_id:
            raise HTTPException(422, '解析快照与发票来源不能同时提供，请二选一')
        if body.parse_id:
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
        conflicts = input_conflicts(body.description, confirmed_payload, snapshot)
        if conflicts and (not body.conflict_acknowledged or not body.confirmation_note):
            raise HTTPException(422, '存在字段冲突，请显式确认并填写confirmation_note：' +
                                '；'.join(item['reason'] for item in conflicts))
        timestamp = services.now()
        task = Task(id=str(uuid4()), owner=user['username'], request_key=idempotency_key,
                    status='queued', payload=confirmed_payload, result={}, citations=[], logs=[], events=[], approvals=[],
                    created_at=timestamp, wait_seconds=0.0, manual=False,
                    submission_payload=payload, parse_snapshot=snapshot, amendments=[], pending_writeback={},
                    confirmations=[{'at': timestamp, 'actor': user['username'], 'confirmed': True,
                                    'conflicts': conflicts, 'conflict_acknowledged': body.conflict_acknowledged,
                                    'note': body.confirmation_note, 'final_fields': confirmed_payload}])
        db.add(task)
        db.commit()
        services.execute(task, db)
        db.commit()
        return services.serialize(task)

@app.post('/api/tasks/{task_id}/approval', response_model=TaskResponse)
def approval(task_id: str, body: ApprovalInput, db: Session = Depends(get_db), user=Depends(current_user)):
    if user['role'] != 'approver':
        raise HTTPException(403, '该账号没有审批权限')
    with mutation_lock:
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task:
            raise HTTPException(404, '任务不存在')
        if task.owner == user['username']:
            raise HTTPException(403, '不能审批本人申请')
        if task.status != 'awaiting_approval':
            raise HTTPException(409, '任务不在待审批状态，请勿重复处理')
        services.approve(task, body, user, db)
        db.commit()
        return services.serialize(task)

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
