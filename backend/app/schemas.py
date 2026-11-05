from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator

class ClaimInput(BaseModel):
    project_id: Literal['P001', 'P002']
    category: Literal['交通费', '办公用品', '其他']
    amount: Decimal = Field(gt=0, le=5000, max_digits=6, decimal_places=2)
    expense_date: date
    description: str = Field(min_length=1, max_length=500)
    confirmed: Literal[True]

    @field_validator('description')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('报销说明不能为空')
        return value.strip()

    @field_validator('expense_date')
    @classmethod
    def not_future(cls, value):
        if value > datetime.now(ZoneInfo('Asia/Shanghai')).date():
            raise ValueError('费用日期不能晚于今天')
        return value

class ApprovalInput(BaseModel):
    decision: Literal['approve', 'reject']
    comment: str = Field(min_length=1, max_length=500)
    manual_basis: str = Field(default='', max_length=1000)

    @field_validator('comment')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('审批意见不能为空')
        return value.strip()

class ParseInput(BaseModel):
    description: str = Field(min_length=1, max_length=500)


class TaskResponse(BaseModel):
    """提交、列表、详情和审批接口共用的任务响应契约。"""

    id: UUID = Field(description='新申请生成的唯一任务编号，重试提交返回原编号')
    owner: str
    status: Literal['queued', 'running', 'awaiting_approval', 'writing_back',
                    'succeeded', 'rejected', 'failed']
    payload: ClaimInput
    result: dict[str, Any]
    citations: list[dict[str, Any]]
    logs: list[dict[str, Any]]
    events: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    created_at: str
    finished_at: str | None
    manual: bool
