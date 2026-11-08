import json
from uuid import uuid4

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.migrations import initialize_database
from app.models import Task


def test_d1_database_upgrade_preserves_task_and_is_repeatable(tmp_path):
    path = tmp_path / 'legacy.db'
    engine = create_engine('sqlite:///' + path.as_posix())
    task_id = str(uuid4())
    original = {'project_id': 'P001', 'category': '交通费', 'amount': '1E+3',
                'expense_date': '2026-11-05', 'description': '客户现场交通费1000元', 'confirmed': True}
    with engine.begin() as db:
        db.execute(text('''CREATE TABLE tasks (
            id VARCHAR(36) PRIMARY KEY, owner VARCHAR(40) NOT NULL,
            request_key VARCHAR(100) NOT NULL, status VARCHAR(30) NOT NULL,
            payload JSON NOT NULL, result JSON NOT NULL, citations JSON NOT NULL,
            logs JSON NOT NULL, events JSON NOT NULL, approvals JSON NOT NULL,
            created_at VARCHAR(40) NOT NULL, finished_at VARCHAR(40),
            approval_started_at VARCHAR(40), wait_seconds FLOAT NOT NULL,
            manual BOOLEAN NOT NULL, UNIQUE(owner, request_key))'''))
        db.execute(text('''INSERT INTO tasks VALUES
            (:id, 'employee', 'legacy-key', 'awaiting_approval', :payload,
             '{}', '[]', '[]', '[]', '[]', '2026-11-05T00:00:00+00:00', NULL,
             '2026-11-05T00:00:00+00:00', 0, 1)'''), {'id': task_id, 'payload': json.dumps(original)})
    initialize_database(engine)
    assert list(tmp_path.glob('legacy.db.before-d2-*.bak'))
    with Session(engine) as db:
        task = db.get(Task, task_id)
        assert task.status == 'awaiting_approval'
        assert task.payload['amount'] == '1000.00'
        assert task.submission_payload['amount'] == '1000.00'
        assert task.confirmations[0]['original_payload'] == original
        assert task.request_key == 'legacy-key'
        first = list(task.confirmations)
    initialize_database(engine)
    with Session(engine) as db:
        assert db.get(Task, task_id).confirmations == first
        assert len(list(db.scalars(select(Task)))) == 1
    assert 'parse_snapshots' in inspect(engine).get_table_names()
    engine.dispose()
