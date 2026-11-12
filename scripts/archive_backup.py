"""Offline SQLite + original-file backup, and verified restore to a new folder."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from hashlib import sha256
import argparse,json,sqlite3,tempfile
from contextlib import closing

ROOT=Path(__file__).resolve().parent.parent

def backup(database,finance,files,output):
    if output.exists():raise ValueError('备份目标已存在，请使用新文件名')
    (ROOT/'work').mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='archive-backup-',ignore_cleanup_errors=True) as scratch:
        temp=Path(scratch)
        for src,name in [(database,'tasks.db'),(finance,'finance.db')]:
            if not src.is_file():raise ValueError('数据库文件不存在')
            with closing(sqlite3.connect(src)) as source,closing(sqlite3.connect(temp/name)) as dest:source.backup(dest)
        manifest={'format':'enterprise-sqlite-backup-v1','files':[],'maintenance_required':True}
        with closing(sqlite3.connect(temp/'tasks.db')) as db:
            present=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='invoice_files'").fetchone()
            rows=db.execute('SELECT storage_key,digest FROM invoice_files').fetchall() if present else []
        output.parent.mkdir(parents=True,exist_ok=True)
        with ZipFile(output,'x',ZIP_DEFLATED) as archive:
            for name in ['tasks.db','finance.db']:
                data=(temp/name).read_bytes();archive.writestr(name,data)
                manifest['files'].append({'name':name,'sha256':sha256(data).hexdigest()})
            for key,digest in rows:
                if Path(key).name!=key:raise ValueError('档案存储键不合法')
                data=(files/key).read_bytes()
                if sha256(data).hexdigest()!=digest:raise ValueError('档案摘要不一致，停止备份')
                name='files/'+key;archive.writestr(name,data);manifest['files'].append({'name':name,'sha256':digest})
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest

def restore(archive_path,destination):
    if destination.exists() and any(destination.iterdir()):raise ValueError('恢复目录必须为空，避免覆盖现有资料')
    with ZipFile(archive_path) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!='enterprise-sqlite-backup-v1':raise ValueError('备份格式不符')
        entries=[]
        for item in manifest['files']:
            name=item['name'];parts=Path(name).parts
            if '\\' in name or Path(name).is_absolute() or '..' in parts or not (name in ['tasks.db','finance.db'] or (len(parts)==2 and parts[0]=='files')):
                raise ValueError('备份包含非法路径')
            data=archive.read(name)
            if sha256(data).hexdigest()!=item['sha256']:raise ValueError('备份摘要校验失败')
            entries.append((name,data))
        destination.mkdir(parents=True,exist_ok=True)
        for name,data in entries:
            path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as f:f.write(data)
        (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'backend/data/tasks.db')
    parser.add_argument('--finance',type=Path,default=ROOT/'backend/data/finance.db')
    parser.add_argument('--files',type=Path,default=ROOT/'backend/data/invoices')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--restore',type=Path)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--maintenance-confirmed',action='store_true',help='确认已停止业务及财务写入')
    args=parser.parse_args();(ROOT/'work').mkdir(exist_ok=True)
    if args.restore:
        if not args.destination:parser.error('恢复须指定新的--destination目录')
        result=restore(args.restore,args.destination)
    else:
        if not args.output or not args.maintenance_confirmed:parser.error('备份须指定--output并确认停写--maintenance-confirmed')
        result=backup(args.database,args.finance,args.files,args.output)
    print(json.dumps({'verified_entries':len(result['files']),'format':result['format']}))
