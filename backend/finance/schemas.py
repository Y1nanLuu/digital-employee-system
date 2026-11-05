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
