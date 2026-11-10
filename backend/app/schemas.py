from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, WithJsonSchema, field_serializer, field_validator, model_validator
from .validation import date_input, date_value, description_value, money_value

Money = Annotated[Decimal, WithJsonSchema({'type': 'string', 'pattern': r'^\d+(?:\.\d{1,2})?$'})]


class ClaimPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: Literal['P001', 'P002']
    category: Literal['交通费', '办公用品', '其他']
    amount: Money
    expense_date: date
    description: str
    confirmed: StrictBool

    _money = field_validator('amount', mode='before')(money_value)
    _description = field_validator('description', mode='before')(description_value)
    _date = field_validator('expense_date')(date_value)
    _date_input = field_validator('expense_date', mode='before')(date_input)

    @field_validator('confirmed')
    @classmethod
    def confirmed_true(cls, value):
        if value is not True:
            raise ValueError('请先确认报销字段')
        return value

    @field_serializer('amount')
    def amount_string(self, value):
        return format(value, '.2f')


class ClaimInput(ClaimPayload):
    parse_id: UUID | None = None
    invoice_id: UUID | None = None
    conflict_acknowledged: StrictBool = False
    confirmation_note: str = Field(default='', max_length=500)

    @field_validator('confirmation_note')
    @classmethod
    def trim_note(cls, value):
        return value.strip()

    def payload(self):
        return self.model_dump(mode='json', include=set(ClaimPayload.model_fields))


class CorrectedFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: Literal['P001', 'P002'] | None = None
    category: Literal['交通费', '办公用品', '其他'] | None = None
    amount: Money | None = None
    expense_date: date | None = None
    description: str | None = None

    _money = field_validator('amount', mode='before')(money_value)
    _description = field_validator('description', mode='before')(description_value)
    _date = field_validator('expense_date')(date_value)
    _date_input = field_validator('expense_date', mode='before')(date_input)

    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('补正必须包含至少一个非空的业务字段')
        return self

    @field_serializer('amount')
    def amount_string(self, value):
        return None if value is None else format(value, '.2f')


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['approve', 'reject']
    comment: str = Field(min_length=1, max_length=500)
    manual_basis: str = Field(default='', max_length=1000)
    corrected_fields: CorrectedFields | None = None
    correction_reason: str = Field(default='', max_length=500)
    reviewed_fields: StrictBool = False

    @field_validator('comment', 'manual_basis', 'correction_reason')
    @classmethod
    def trim_text(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def valid_review(self):
        if not self.comment:
            raise ValueError('审批意见不能为空')
        if self.corrected_fields is not None:
            if self.decision == 'reject':
                raise ValueError('拒绝请求不能同时补正字段')
            if not self.correction_reason or not self.reviewed_fields:
                raise ValueError('补正须填写原因并显式确认已核对字段')
        return self


class ParseInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str
    _description = field_validator('description', mode='before')(description_value)


class TaskResponse(BaseModel):
    id: UUID
    owner: str
    status: Literal['queued', 'running', 'awaiting_approval', 'writing_back', 'succeeded', 'rejected', 'failed']
    payload: ClaimPayload
    result: dict[str, Any]
    citations: list[dict[str, Any]]
    logs: list[dict[str, Any]]
    events: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    created_at: str
    finished_at: str | None
    manual: bool
    submission_payload: dict[str, Any] = Field(default_factory=dict)
    parse_snapshot: dict[str, Any] = Field(default_factory=dict)
    confirmations: list[dict[str, Any]] = Field(default_factory=list)
    amendments: list[dict[str, Any]] = Field(default_factory=list)
    failure_stage: str | None = None
