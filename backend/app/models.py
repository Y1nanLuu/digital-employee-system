from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (UniqueConstraint('owner', 'request_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_no: Mapped[str | None] = mapped_column(String(70),unique=True,nullable=True)
    owner: Mapped[str] = mapped_column(String(40))
    request_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    logs: Mapped[list] = mapped_column(JSON, default=list)
    events: Mapped[list] = mapped_column(JSON, default=list)
    approvals: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40))
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approval_started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    wait_seconds: Mapped[float] = mapped_column(default=0.0)
    manual: Mapped[bool] = mapped_column(default=False)
    submission_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    parse_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmations: Mapped[list] = mapped_column(JSON, default=list)
    amendments: Mapped[list] = mapped_column(JSON, default=list)
    pending_writeback: Mapped[dict] = mapped_column(JSON, default=dict)
    failure_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(60), default='demo-v1')
    revision: Mapped[int] = mapped_column(default=1)
    return_draft: Mapped[dict] = mapped_column(JSON,default=dict)


class TaskNumberCounter(Base):
    __tablename__='task_number_counters'
    key: Mapped[str] = mapped_column(String(60),primary_key=True)
    value: Mapped[int] = mapped_column(default=0)


class ParseSnapshot(Base):
    __tablename__ = 'parse_snapshots'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), index=True)
    description_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))


class InvoiceFile(Base):
    __tablename__ = 'invoice_files'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(60))
    size: Mapped[int] = mapped_column()
    digest: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[str] = mapped_column(String(40))
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invoice_fields: Mapped[dict] = mapped_column(JSON,default=dict)


class UploadBatch(Base):
    __tablename__ = 'upload_batches'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), index=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    current_index: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class PolicyVersion(Base):
    __tablename__ = 'policy_versions'
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))


class TaskRevision(Base):
    __tablename__ = 'task_revisions'
    __table_args__ = (UniqueConstraint('task_id', 'request_key'), UniqueConstraint('task_id', 'number'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    number: Mapped[int] = mapped_column()
    actor: Mapped[str] = mapped_column(String(40))
    request_key: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSON)
    before: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))


class InvoiceClaim(Base):
    __tablename__ = 'invoice_claims'
    invoice_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), unique=True)


class FileAccess(Base):
    __tablename__ = 'file_access'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(30))
    at: Mapped[str] = mapped_column(String(40))
