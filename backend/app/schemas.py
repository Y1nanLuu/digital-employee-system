from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, WithJsonSchema, field_serializer, field_validator, model_validator
from .validation import date_input, date_value, description_value, money_value

Money = Annotated[Decimal, WithJsonSchema({'type': 'string', 'pattern': r'^\d+(?:\.\d{1,2})?$'})]

Category = Literal['交通费', '住宿费', '业务招待费', '办公用品', '通讯费', '培训会议费', '软件服务费', '出差餐补', '其他']


class InvoiceFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    invoice_no: str = Field(default='', max_length=40)
    invoice_code: str = Field(default='', max_length=20)
    invoice_date: date | None = None
    buyer: str = Field(default='', max_length=200)
    seller: str = Field(default='', max_length=200)
    total: str = Field(default='', max_length=30)
    item_name: str = Field(default='', max_length=500)

    @field_validator('invoice_no','invoice_code','buyer','seller','total','item_name')
    @classmethod
    def strip_values(cls,value):return value.strip()

    @field_validator('invoice_date')
    @classmethod
    def invoice_not_future(cls,value):
        if value is not None:date_value(value)
        return value

    @field_validator('total')
    @classmethod
    def invoice_money(cls,value):
        import re
        if value and not re.fullmatch(r'-?\d+(?:\.\d{1,2})?',value):
            raise ValueError('票面总额须为普通十进制字符串，最多两位小数')
        return value


class ExpenseDetails(BaseModel):
    model_config = ConfigDict(extra='forbid')
    city: str = Field(default='', max_length=80)
    check_in: date | None = None
    check_out: date | None = None
    trip_start: date | None = None
    trip_end: date | None = None
    approval_no: str = Field(default='', max_length=100)
    proof_reference: str = Field(default='', max_length=500)
    route: str = Field(default='', max_length=200)
    seat: str = Field(default='', max_length=60)
    attendees: int = Field(default=0, ge=0, le=10000)
    companions: int = Field(default=0, ge=0, le=10000)
    business_partner: str = Field(default='', max_length=200)
    service_period: str = Field(default='', max_length=100)
    supplied_breakfasts: int = Field(default=0, ge=0, le=366)
    supplied_lunches: int = Field(default=0, ge=0, le=366)
    supplied_dinners: int = Field(default=0, ge=0, le=366)
    exception_reason: str = Field(default='', max_length=500)

    @field_validator('city','approval_no','proof_reference','route','seat','business_partner','service_period','exception_reason')
    @classmethod
    def strip_values(cls,value):return value.strip()

    @field_validator('attendees','companions','supplied_breakfasts','supplied_lunches','supplied_dinners',mode='before')
    @classmethod
    def counts(cls,value):
        if isinstance(value,bool) or not isinstance(value,int):raise ValueError('人数和餐次数须为整数')
        return value

    @model_validator(mode='after')
    def date_ranges(self):
        if self.check_in and self.check_out and self.check_out<=self.check_in:
            raise ValueError('离店日期须晚于入住日期')
        if self.trip_start and self.trip_end and self.trip_end<self.trip_start:
            raise ValueError('出差结束日期须不早于开始日期')
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        if any(d and d>today for d in [self.check_in,self.check_out,self.trip_start,self.trip_end]):
            raise ValueError('费用相关行程日期不能晚于今天')
        if self.attendees and self.companions>self.attendees:raise ValueError('陪同人数不能超过参加总人数')
        return self


class ClaimPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: Literal['P001', 'P002']
    category: Category
    amount: Money
    expense_date: date
    description: str
    confirmed: StrictBool
    department: str = Field(default='', max_length=80)
    file_id: UUID | None = None
    invoice: InvoiceFields = Field(default_factory=InvoiceFields)
    details: ExpenseDetails = Field(default_factory=ExpenseDetails)
    _department = field_validator('department')(lambda value:value.strip())

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
        from . import config
        fields=set(ClaimPayload.model_fields)
        if config.ACTIVE_POLICY_VERSION=='demo-v1':fields={'project_id','category','amount','expense_date','description','confirmed'}
        return self.model_dump(mode='json', include=fields)


class CorrectedFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: Literal['P001', 'P002'] | None = None
    category: Category | None = None
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
    decision: Literal['approve', 'reject', 'return']
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
    business_no: str | None = None
    owner: str
    status: Literal['queued', 'running', 'awaiting_approval', 'returned', 'writing_back', 'succeeded', 'rejected', 'failed','cancelled']
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
    policy_version: str = 'demo-v1'
    revision: int = 1
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    return_draft: dict[str,Any] = Field(default_factory=dict)


class ResubmitInput(ClaimInput):
    revision_reason: str = Field(min_length=1,max_length=500)

    @field_validator('revision_reason')
    @classmethod
    def reason(cls,value):
        if not value.strip():raise ValueError('请填写修改说明')
        return value.strip()


class BatchInput(BaseModel):
    file_ids: list[UUID] = Field(min_length=1,max_length=10)


class DraftInput(BaseModel):
    form: dict[str, Any]
    skipped: bool = False
    current_index: int = Field(ge=0,le=9)
