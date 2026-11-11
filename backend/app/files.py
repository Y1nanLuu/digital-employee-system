"""Original documents are immutable and are read only through authorization."""
from pathlib import Path
from uuid import uuid4
from hashlib import sha256
from datetime import datetime,timezone
import os
from fastapi import HTTPException
from sqlalchemy import select
from . import config
from .models import InvoiceFile,Task,FileAccess

def root():
    p=Path(config.FILE_STORAGE_DIR).resolve();p.mkdir(parents=True,exist_ok=True);return p

def save(db,data,filename,owner):
    from .invoices import MAX_PDF_BYTES
    if not data:raise HTTPException(422,'文件为空')
    if len(data)>MAX_PDF_BYTES:raise HTTPException(413,'单文件不能超过5MB')
    if data.startswith(b'%PDF-'):suffix,media='.pdf','application/pdf'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):suffix,media='.png','image/png'
    elif data.startswith(b'\xff\xd8\xff'):suffix,media='.jpg','image/jpeg'
    else:raise HTTPException(422,'仅支持PDF、PNG和JPG/JPEG原件')
    identity=str(uuid4());key=identity+suffix;dest=root()/key;temp=dest.with_suffix('.uploading')
    try:
        with temp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        temp.replace(dest)
        item=InvoiceFile(id=identity,owner=owner,filename=Path(filename.replace('\\','/')).name[-200:] or 'invoice'+suffix,
                         media_type=media,size=len(data),digest=sha256(data).hexdigest(),storage_key=key,created_at=datetime.now(timezone.utc).isoformat())
        db.add(item);db.commit();return item
    except Exception:
        db.rollback();temp.unlink(missing_ok=True);dest.unlink(missing_ok=True);raise

def owned(db,file_id,user):
    item=db.get(InvoiceFile,str(file_id))
    if not item:raise HTTPException(404,'票据不存在或不可见')
    allowed=item.owner==user['username'] or user['role']=='admin'
    if user['role']=='approver':
        allowed=any(str(t.payload.get('file_id'))==item.id or any(str(x.get('payload',{}).get('file_id'))==item.id for x in t.amendments)
                    for t in db.scalars(select(Task)))
    if not allowed:raise HTTPException(404,'票据不存在或不可见')
    return item

def info(item):
    return {'file_id':item.id,'filename':item.filename,'media_type':item.media_type,'size':item.size,
            'sha256':item.digest,'uploaded_at':item.created_at,'invoice_id':item.snapshot_id,'invoice_fields':item.invoice_fields}

def read(db,item,user,action):
    path=root()/item.storage_key
    if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=item.digest:
        raise HTTPException(409,'档案缺失或摘要异常，请联系管理员核对')
    db.add(FileAccess(id=str(uuid4()),file_id=item.id,actor=user['username'],action=action,at=datetime.now(timezone.utc).isoformat()));db.commit()
    return path
