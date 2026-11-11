"""独立模拟财务服务。预算不扣减、不支付；审核记录持久化且按任务编号幂等。"""
import os
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .schemas import (BudgetResponse, ResultInput, ResultResponse,InvoiceCheckInput,
                      InvoiceCheckResponse,DuplicateResponse,EnterpriseResultInput,EnterpriseResultResponse)

Path('data').mkdir(exist_ok=True)
url = os.getenv('FINANCE_DATABASE_URL', 'sqlite:///./data/finance.db')
engine = create_engine(url, connect_args={'check_same_thread': False} if url.startswith('sqlite') else {})
Session = sessionmaker(engine)

class Base(DeclarativeBase): pass

class Receipt(Base):
    __tablename__ = 'receipts'
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class InvoiceRegistry(Base):
    __tablename__='invoice_registry'
    invoice_key: Mapped[str]=mapped_column(String(100),primary_key=True)
    record: Mapped[dict]=mapped_column(JSON)


class ReimbursedInvoice(Base):
    __tablename__='reimbursed_invoices'
    invoice_key: Mapped[str]=mapped_column(String(100),primary_key=True)
    task_id: Mapped[str]=mapped_column(String(36),unique=True)


class ReimbursedFile(Base):
    __tablename__='reimbursed_files'
    digest: Mapped[str]=mapped_column(String(64),primary_key=True)
    task_id: Mapped[str]=mapped_column(String(36),unique=True)


def invoice_key(number,code=''):
    return number if len(number)==20 else code+':'+number

def authorize(authorization: str = Header(default='')):
    expected = 'Bearer ' + os.getenv('FINANCE_KEY', 'local-finance-key')
    if not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, '工具鉴权失败')

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with Session() as db:
        for no,amount,status in [('26000000000000000001','300.00','valid'),('26000000000000000002','50.00','invalid'),('26000000000000000003','1000.00','valid')]:
            if not db.get(InvoiceRegistry,no):
                db.add(InvoiceRegistry(invoice_key=no,record={'invoice_no':no,'invoice_date':'2026-11-01','total':amount,'status':status,'reason':'课程合成测试台账'}))
        db.commit()
    yield

app = FastAPI(title='模拟财务API', lifespan=lifespan)

@app.get('/health')
def health(): return {'status': 'ok'}

@app.get('/budgets/{project_id}', dependencies=[Depends(authorize)], response_model=BudgetResponse)
def budget(project_id: str):
    amounts = {'P001': '10000.00', 'P002': '500.00'}
    if project_id not in amounts:
        raise HTTPException(404, '项目不存在')
    return {'project_id': project_id, 'available': amounts[project_id], 'currency': 'CNY', 'simulated': True}

@app.post('/results', dependencies=[Depends(authorize)], response_model=ResultResponse)
def write_result(body: ResultInput):
    payload = body.model_dump(mode='json')
    task_id = payload['task_id']
    with Session() as db:
        receipt = db.get(Receipt, task_id)
        if not receipt:
            db.add(Receipt(task_id=task_id, data=payload))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            receipt = db.get(Receipt, task_id)
        if receipt.data != payload:
            raise HTTPException(409, '该任务已经写入不同的审核结果')
    return {**payload, 'receipt_id': 'demo-' + task_id, 'simulated': True}


@app.post('/checks/invoice',dependencies=[Depends(authorize)],response_model=InvoiceCheckResponse)
def check_invoice(body:InvoiceCheckInput):
    from decimal import Decimal,InvalidOperation
    with Session() as db:
        item=db.get(InvoiceRegistry,invoice_key(body.invoice_no,body.invoice_code))
        status='unknown';reason='模拟台账无此票据，须人工核对；格式合格不等于验真成功'
        if item:
            record=item.record;status=record['status'];reason=record.get('reason','模拟台账匹配')
            try:matches=body.invoice_date==record['invoice_date'] and Decimal(body.total)==Decimal(record['total'])
            except (InvalidOperation,KeyError):matches=False
            if not matches:status='invalid';reason='与模拟台账的开票日期或含税总额不一致'
    return {'task_id':body.task_id,'invoice_no':body.invoice_no,'status':status,'reason':reason,'simulated':True}


@app.post('/checks/duplicate',dependencies=[Depends(authorize)],response_model=DuplicateResponse)
def check_duplicate(body:InvoiceCheckInput):
    from sqlalchemy import select
    with Session() as db:
        prior=db.get(ReimbursedInvoice,invoice_key(body.invoice_no,body.invoice_code)) if body.invoice_no else None
        by_file=db.get(ReimbursedFile,body.file_sha256) if body.file_sha256 else None
        if by_file and by_file.task_id!=str(body.task_id):prior=by_file
        duplicate=bool(prior and prior.task_id!=str(body.task_id));suspected=False
        if not duplicate:
            for receipt in db.scalars(select(Receipt)):
                data=receipt.data
                if (receipt.task_id!=str(body.task_id) and data.get('decision')=='approved'
                    and data.get('owner')==body.owner and data.get('amount')==body.amount
                    and data.get('expense_date')==body.expense_date):suspected=True;break
    return {'task_id':body.task_id,'invoice_no':body.invoice_no,'duplicate':duplicate,'suspected':suspected,
            'reference_task_id':prior.task_id if duplicate else None,'simulated':True}


@app.post('/reviews/results',dependencies=[Depends(authorize)],response_model=EnterpriseResultResponse)
def enterprise_result(body:EnterpriseResultInput):
    payload=body.model_dump(mode='json');task_id=payload['task_id']
    with Session() as db:
        prior=db.get(Receipt,task_id)
        if prior:
            if prior.data!=payload:raise HTTPException(409,'该任务已登记不同结果')
        else:
            if body.decision=='approved' and body.invoice_no:
                key=invoice_key(body.invoice_no,body.invoice_code)
                used=db.get(ReimbursedInvoice,key)
                if used and used.task_id!=task_id:raise HTTPException(409,'票据已被其他任务报销')
                if not used:db.add(ReimbursedInvoice(invoice_key=key,task_id=task_id))
            if body.decision=='approved' and body.file_sha256:
                used_file=db.get(ReimbursedFile,body.file_sha256)
                if used_file and used_file.task_id!=task_id:raise HTTPException(409,'该原件已由其他任务登记')
                if not used_file:db.add(ReimbursedFile(digest=body.file_sha256,task_id=task_id))
            db.add(Receipt(task_id=task_id,data=payload))
            try:db.commit()
            except IntegrityError:
                db.rollback();prior=db.get(Receipt,task_id)
                if not prior or prior.data!=payload:raise HTTPException(409,'并发登记冲突，须按原任务对账')
    return {**payload,'receipt_id':'demo-'+task_id,'simulated':True}
