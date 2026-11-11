"""Stable employee-facing numbers; UUIDs remain internal primary keys."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from hashlib import sha256
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .auth import USERS
from .models import TaskNumberCounter

def allocate(db,owner,created_at):
    identity=USERS.get(owner,{}).get('employee_no') or 'U'+sha256(owner.encode()).hexdigest()[:8].upper()
    when=datetime.fromisoformat(created_at)
    if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
    day=when.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d');key=identity+'-'+day
    row=db.scalar(select(TaskNumberCounter).where(TaskNumberCounter.key==key).with_for_update())
    if not row:
        try:
            with db.begin_nested():
                db.add(TaskNumberCounter(key=key,value=0));db.flush()
        except IntegrityError:pass
        row=db.scalar(select(TaskNumberCounter).where(TaskNumberCounter.key==key).with_for_update())
    row.value+=1;db.flush()
    return f'BX-{identity}-{day}-{row.value:04d}'
