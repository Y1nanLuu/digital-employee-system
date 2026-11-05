"""在独立临时数据库上启动真实HTTP服务，验证链路及审批跨进程重启恢复。"""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
(ROOT / 'work').mkdir(exist_ok=True)

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def call(base, path, body=None, role='employee', key=None):
    headers={'Authorization':'Basic '+base64.b64encode((role+':demo123').encode()).decode()}
    if body is not None: headers['Content-Type']='application/json'
    if key: headers['Idempotency-Key']=key
    request=urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=15) as response:
        return json.load(response)

def wait_ready(base, process):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if process.poll() is not None: raise RuntimeError('服务提前退出，请检查日志')
        try:
            with urllib.request.urlopen(base+'/health',timeout=1): return
        except (OSError, TimeoutError): time.sleep(.25)
    raise RuntimeError('服务启动超时')

def stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=5)

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='smoke-') as temp:
    temp=Path(temp)
    backend_port,finance_port=port(),port()
    backend_url=f'http://127.0.0.1:{backend_port}'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+(temp/'tasks.db').as_posix(),
         'FINANCE_DATABASE_URL':'sqlite:///'+(temp/'finance.db').as_posix(),
         'FINANCE_URL':f'http://127.0.0.1:{finance_port}',
         'FINANCE_KEY':'local-finance-key','DEMO_PASSWORD':'demo123','PYTHONIOENCODING':'utf-8'}
    processes=[]
    with (ROOT/'work/smoke-services.log').open('w',encoding='utf8') as log:
        def launch(module,selected_port):
            child=subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(selected_port)],
                cwd=ROOT/'backend',env=env,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            processes.append(child)
            wait_ready(f'http://127.0.0.1:{selected_port}',child)
            return child
        try:
            launch('finance.main:app',finance_port)
            backend=launch('app.main:app',backend_port)
            body={'project_id':'P001','category':'交通费','amount':'300.00',
                  'expense_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                  'description':'客户现场往返交通费300元','confirmed':True}
            key=str(uuid4())
            automatic=call(backend_url,'/api/tasks',body,key=key)
            assert automatic['status']=='succeeded'
            assert len(automatic['logs'])==2
            assert call(backend_url,'/api/tasks',body,key=key)['id']==automatic['id']
            pending=call(backend_url,'/api/tasks',{**body,'amount':'1500'},key=str(uuid4()))
            assert pending['status']=='awaiting_approval'
            stop(backend)
            launch('app.main:app',backend_port)
            restored=call(backend_url,'/api/tasks/'+pending['id'],role='approver')
            assert restored['status']=='awaiting_approval'
            done=call(backend_url,'/api/tasks/'+pending['id']+'/approval',{'decision':'approve','comment':'重启后人工核验通过'},role='approver')
            assert done['status']=='succeeded' and len(done['logs'])==3
            rejected=call(backend_url,'/api/tasks',{**body,'project_id':'P002','amount':'600'},key=str(uuid4()))
            assert rejected['status']=='rejected'
            metrics=call(backend_url,'/api/metrics',role='admin')
            assert metrics['total']==3 and metrics['completed']==3
            print('PASS: real HTTP, automatic review, submit idempotency, process restart, approval resume, budget rejection, metrics')
        finally:
            for process in reversed(processes): stop(process)
