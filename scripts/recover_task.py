"""受限本机运维入口：只按已有任务与检查点恢复，不接受新审核载荷。"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--action', choices=['replay-writeback', 'retry-budget'], required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    os.chdir(ROOT / 'backend')
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.database import SessionLocal, engine
    from app.migrations import initialize_database
    from app.models import Task
    from app.services import mark_interrupted, recover, serialize
    import json

    initialize_database(engine)
    with SessionLocal() as db:
        mark_interrupted(db)
        task = db.scalar(select(Task).where(Task.id == args.task_id).with_for_update())
        if task is None:
            parser.exit(1, '任务不存在\n')
        try:
            recover(task, args.action, args.actor, args.reason, db)
        except (HTTPException, ValueError) as exc:
            parser.exit(1, str(getattr(exc, 'detail', exc)) + '\n')
        print(json.dumps(serialize(task), ensure_ascii=False))
        return 0 if task.status in ['succeeded', 'rejected', 'awaiting_approval'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
