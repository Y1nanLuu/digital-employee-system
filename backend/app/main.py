from contextlib import asynccontextmanager
from datetime import datetime
from threading import RLock
from uuid import uuid4
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import current_user
from .database import Base, engine, get_db
from .models import Task
from .schemas import ApprovalInput, ClaimInput, ParseInput
from . import services
from .ai import parse_fields

# SQLite本地版仅支持一个进程；PostgreSQL审批同时使用行锁。
mutation_lock = RLock()

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    yield

app = FastAPI(title='数字员工业务服务', version='0.1.0', lifespan=lifespan)

@app.get('/health')
def health(db: Session = Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'mode': 'rule_baseline'}

@app.get('/api/me')
def me(user=Depends(current_user)):
    return user

@app.post('/api/parse')
def parse(body: ParseInput, user=Depends(current_user)):
    return parse_fields(body.description)

def visible_query(user):
    stmt = select(Task).order_by(Task.created_at.desc())
    return stmt.where(Task.owner == user['username']) if user['role'] == 'employee' else stmt

@app.get('/api/tasks')
def tasks(db: Session = Depends(get_db), user=Depends(current_user)):
    return [services.serialize(t) for t in db.scalars(visible_query(user))]

@app.get('/api/tasks/{task_id}')
def task_detail(task_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    task = db.scalar(visible_query(user).where(Task.id == task_id))
    if not task:
        raise HTTPException(404, '任务不存在')
    return services.serialize(task)

@app.post('/api/tasks', status_code=201)
def create_task(body: ClaimInput, idempotency_key: str = Header(min_length=8, max_length=100),
                db: Session = Depends(get_db), user=Depends(current_user)):
    if user['role'] != 'employee':
        raise HTTPException(403, '请使用员工账号提交')
    payload = body.model_dump(mode='json')
    with mutation_lock:
        prior = db.scalar(select(Task).where(Task.owner == user['username'], Task.request_key == idempotency_key))
        if prior:
            if prior.payload != payload:
                raise HTTPException(409, '同一提交标识不能用于不同的报销内容')
            return services.serialize(prior)
        task = Task(id=str(uuid4()), owner=user['username'], request_key=idempotency_key,
                    status='queued', payload=payload, result={}, citations=[], logs=[], events=[], approvals=[],
                    created_at=services.now(), wait_seconds=0.0, manual=False)
        db.add(task)
        db.commit()
        services.execute(task)
        db.commit()
        return services.serialize(task)

@app.post('/api/tasks/{task_id}/approval')
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
        if body.decision == 'approve' and not task.citations and not body.manual_basis.strip():
            raise HTTPException(422, '缺少制度依据，通过前必须补充人工依据')
        services.approve(task, body, user)
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
