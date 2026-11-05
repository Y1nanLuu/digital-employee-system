"""独立模拟财务服务。预算不扣减、不支付；审核记录持久化且按任务编号幂等。"""
import os
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

Path('data').mkdir(exist_ok=True)
url = os.getenv('FINANCE_DATABASE_URL', 'sqlite:///./data/finance.db')
engine = create_engine(url, connect_args={'check_same_thread': False} if url.startswith('sqlite') else {})
Session = sessionmaker(engine)

class Base(DeclarativeBase): pass

class Receipt(Base):
    __tablename__ = 'receipts'
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)

class ResultInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=36)
    decision: Literal['approved','rejected']
    reason: str = Field(min_length=1, max_length=1000)

def authorize(authorization: str = Header(default='')):
    expected = 'Bearer ' + os.getenv('FINANCE_KEY', 'local-finance-key')
    if not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, '工具鉴权失败')

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    yield

app = FastAPI(title='模拟财务API', lifespan=lifespan)

@app.get('/health')
def health(): return {'status': 'ok'}

@app.get('/budgets/{project_id}', dependencies=[Depends(authorize)])
def budget(project_id: str):
    amounts = {'P001': '10000.00', 'P002': '500.00'}
    if project_id not in amounts:
        raise HTTPException(404, '项目不存在')
    return {'project_id': project_id, 'available': amounts[project_id], 'currency': 'CNY', 'simulated': True}

@app.post('/results', dependencies=[Depends(authorize)])
def write_result(body: ResultInput):
    payload = body.model_dump()
    with Session() as db:
        receipt = db.get(Receipt, body.task_id)
        if not receipt:
            db.add(Receipt(task_id=body.task_id, data=payload))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            receipt = db.get(Receipt, body.task_id)
        if receipt.data != payload:
            raise HTTPException(409, '该任务已经写入不同的审核结果')
    return {**payload, 'receipt_id': 'demo-' + body.task_id, 'simulated': True}
