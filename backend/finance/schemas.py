"""模拟财务接口的数据契约，供财务服务及业务调用端共同校验。"""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class BudgetResponse(BaseModel):
    project_id: str = Field(min_length=1)
    available: Decimal = Field(ge=0, decimal_places=2, allow_inf_nan=False)
    currency: Literal['CNY']
    simulated: Literal[True]


class ResultInput(BaseModel):
    task_id: UUID
    decision: Literal['approved', 'rejected']
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def nonblank_reason(cls, value):
        if not value.strip():
            raise ValueError('审核原因不能为空')
        return value.strip()


class ResultResponse(ResultInput):
    receipt_id: str = Field(min_length=1)
    simulated: Literal[True]


class InvoiceCheckInput(BaseModel):
    task_id: UUID
    invoice_no: str = Field(default='', max_length=40)
    invoice_code: str = Field(default='', max_length=20)
    invoice_date: str = Field(default='', max_length=30)
    total: str = Field(default='', max_length=30)
    owner: str = Field(default='', max_length=40)
    amount: str = Field(default='', max_length=30)
    expense_date: str = Field(default='', max_length=30)
    file_sha256: str = Field(default='',max_length=64)


class InvoiceCheckResponse(BaseModel):
    task_id: UUID
    invoice_no: str
    status: Literal['valid','invalid','unknown']
    reason: str
    simulated: Literal[True]


class DuplicateResponse(BaseModel):
    task_id: UUID
    invoice_no: str
    duplicate: bool
    suspected: bool
    reference_task_id: str | None = None
    simulated: Literal[True]


class EnterpriseResultInput(ResultInput):
    invoice_no: str = Field(default='',max_length=40)
    invoice_code: str = Field(default='',max_length=20)
    owner: str = Field(max_length=40)
    amount: str = Field(max_length=30)
    expense_date: str = Field(max_length=30)
    file_sha256: str = Field(default='',max_length=64)


class EnterpriseResultResponse(EnterpriseResultInput):
    receipt_id: str
    simulated: Literal[True]
