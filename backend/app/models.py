from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (UniqueConstraint('owner', 'request_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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


class ParseSnapshot(Base):
    __tablename__ = 'parse_snapshots'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), index=True)
    description_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
