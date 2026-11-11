"""D1 到 D2 的增量升级：只新增列/表，并回填初始请求；不删除历史记录。"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from .database import Base
from .models import Task
from .schemas import ClaimInput

ADDITIONS = {
    'business_no':'VARCHAR(70)',
    'submission_payload': "JSON NOT NULL DEFAULT '{}'",
    'parse_snapshot': "JSON NOT NULL DEFAULT '{}'",
    'confirmations': "JSON NOT NULL DEFAULT '[]'",
    'amendments': "JSON NOT NULL DEFAULT '[]'",
    'pending_writeback': "JSON NOT NULL DEFAULT '{}'",
    'failure_stage': 'VARCHAR(40)',
    'policy_version': "VARCHAR(60) NOT NULL DEFAULT 'demo-v1'",
    'revision': 'INTEGER NOT NULL DEFAULT 1',
    'return_draft': "JSON NOT NULL DEFAULT '{}'",
}


def initialize_database(engine):
    inspector = inspect(engine)
    columns = {item['name'] for item in inspector.get_columns('tasks')} if inspector.has_table('tasks') else set()
    missing = set(ADDITIONS) - columns if columns else set()
    file_upgrade=inspector.has_table('invoice_files') and 'invoice_fields' not in {c['name'] for c in inspector.get_columns('invoice_files')}
    if (missing or file_upgrade) and engine.dialect.name == 'sqlite' and engine.url.database not in [None, '', ':memory:']:
        path = Path(engine.url.database)
        backup = path.with_name(path.name + '.before-d2-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f') + '.bak')
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
    Base.metadata.create_all(engine)
    if missing:
        with engine.begin() as connection:
            for name, definition in ADDITIONS.items():
                if name in missing:
                    connection.execute(text(f'ALTER TABLE tasks ADD COLUMN {name} {definition}'))
    file_columns={item['name'] for item in inspect(engine).get_columns('invoice_files')}
    if 'invoice_fields' not in file_columns:
        with engine.begin() as connection:connection.execute(text("ALTER TABLE invoice_files ADD COLUMN invoice_fields JSON NOT NULL DEFAULT '{}'"))
    with Session(engine) as db:
        from .numbers import allocate
        for task in db.scalars(select(Task).order_by(Task.created_at,Task.id)):
            if not task.business_no:task.business_no=allocate(db,task.owner,task.created_at)
            if task.submission_payload:
                continue
            original = dict(task.payload)
            normalized = {**original, 'amount': Decimal(str(original['amount']))}
            body = ClaimInput.model_validate(normalized)
            task.payload = body.payload()
            task.submission_payload = body.model_dump(mode='json')
            task.parse_snapshot = {'mode': 'rule_baseline', 'requires_manual_review': False, 'source': 'legacy_backfill'}
            task.confirmations = [{
                'at': datetime.now(timezone.utc).isoformat(), 'actor': 'system',
                'source': 'legacy_backfill', 'original_payload': original,
                'message': '升级前记录无解析快照；仅回填已有初始字段，不推断模型来源',
            }]
        db.commit()
        from .models import InvoiceFile,ParseSnapshot
        for item in db.scalars(select(InvoiceFile)):
            if not item.invoice_fields and item.snapshot_id:
                snapshot=db.get(ParseSnapshot,item.snapshot_id)
                if snapshot:item.invoice_fields=(snapshot.snapshot.get('invoice') or {}).get('fields') or {}
        db.commit()
    with engine.begin() as connection:
        connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_tasks_business_no ON tasks (business_no)'))
