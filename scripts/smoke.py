"""在独立临时数据库上启动真实HTTP服务，验证链路及审批跨进程重启恢复。"""
import base64
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
(ROOT / 'work').mkdir(exist_ok=True)
# 测试只访问本机随机端口，避免 HTTP_PROXY 将健康检查和请求发往代理。
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def call(base, path, body=None, role='employee', key=None, bearer=None):
    authorization = ('Bearer ' + bearer if bearer is not None
                     else 'Basic ' + base64.b64encode((role+':demo123').encode()).decode())
    headers={'Authorization': authorization}
    if body is not None: headers['Content-Type']='application/json'
    if key: headers['Idempotency-Key']=key
    request=urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),headers=headers)
    with LOCAL_HTTP.open(request,timeout=15) as response:
        return json.load(response)

def wait_ready(base, process):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if process.poll() is not None: raise RuntimeError('服务提前退出，请检查日志')
        try:
            with LOCAL_HTTP.open(base+'/health',timeout=1): return
        except (OSError, TimeoutError): time.sleep(.25)
    raise RuntimeError('服务启动超时')

def stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=5)

# ignore_cleanup_errors：Windows上被终止的uvicorn进程释放SQLite文件句柄可能略晚于exit，
# 断言已全部通过时不应因临时目录清理失败而返回非零退出码。
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='smoke-',ignore_cleanup_errors=True) as temp:
    temp=Path(temp)
    backend_port,finance_port=port(),port()
    backend_url=f'http://127.0.0.1:{backend_port}'
    finance_url=f'http://127.0.0.1:{finance_port}'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+(temp/'tasks.db').as_posix(),
         'FINANCE_DATABASE_URL':'sqlite:///'+(temp/'finance.db').as_posix(),
         'FINANCE_URL':finance_url,
         'FINANCE_KEY':'local-finance-key','DEMO_PASSWORD':'demo123','AI_MODULE_ENABLED':'false',
         'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'}
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
            finance=launch('finance.main:app',finance_port)
            backend=launch('app.main:app',backend_port)
            for project_id, available in [('P001', '10000.00'), ('P002', '500.00')]:
                sample=call(finance_url,'/budgets/'+project_id,bearer=env['FINANCE_KEY'])
                assert sample=={'project_id':project_id,'available':available,'currency':'CNY','simulated':True}
            body={'project_id':'P001','category':'交通费','amount':'300.00',
                  'expense_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                  'description':'客户现场往返交通费300元','confirmed':True}
            parsed=call(backend_url,'/api/parse',{'description':body['description']})
            body={**body,'parse_id':parsed['parse_id']}
            key=str(uuid4())
            automatic=call(backend_url,'/api/tasks',body,key=key)
            assert automatic['status']=='succeeded'
            assert UUID(automatic['id']).version==4
            assert call(backend_url,'/api/tasks/'+automatic['id'])==automatic
            assert len(automatic['logs'])==2
            assert call(backend_url,'/api/tasks',body,key=key)==automatic
            pending_body={**body,'amount':'1500','description':'客户现场办公用品1500元','category':'办公用品'}
            pending_body.pop('parse_id')
            pending_key=str(uuid4())
            pending=call(backend_url,'/api/tasks',pending_body,key=pending_key)
            assert pending['id']!=automatic['id']
            assert pending['status']=='awaiting_approval'
            stop(backend)
            stop(finance)
            finance=launch('finance.main:app',finance_port)
            backend=launch('app.main:app',backend_port)
            assert call(backend_url,'/api/tasks/'+automatic['id'])==automatic
            assert call(backend_url,'/api/tasks',body,key=key)==automatic
            receipt=automatic['result']['receipt']
            result_body={name:receipt[name] for name in ['task_id','decision','reason']}
            assert call(finance_url,'/results',result_body,bearer=env['FINANCE_KEY'])==receipt
            restored=call(backend_url,'/api/tasks/'+pending['id'],role='approver')
            assert restored['status']=='awaiting_approval'
            done=call(backend_url,'/api/tasks/'+pending['id']+'/approval',{'decision':'approve','comment':'重启后人工核验通过','corrected_fields':{'amount':'900.00'},'correction_reason':'核对后扣除个人承担部分','reviewed_fields':True},role='approver')
            assert done['status']=='succeeded' and len(done['logs'])==3
            assert done['payload']['amount']=='900.00'
            assert done['submission_payload']['amount']=='1500.00'
            assert call(backend_url,'/api/tasks',pending_body,key=pending_key)==done
            rejected_body={**body,'project_id':'P002','amount':'600','description':'客户现场往返交通费600元'}
            rejected_body.pop('parse_id')
            rejected=call(backend_url,'/api/tasks',rejected_body,key=str(uuid4()))
            assert rejected['status']=='rejected'
            assert call(finance_url,'/budgets/P001',bearer=env['FINANCE_KEY'])['available']=='10000.00'
            # 财务已存在原凭证，模拟业务进程未能确认写回时中断。
            stop(backend)
            interrupted_result={key:value for key,value in automatic['result'].items() if key!='receipt'}
            interrupted_result['execution_stage']='writeback'
            with sqlite3.connect(temp/'tasks.db') as connection:
                connection.execute('UPDATE tasks SET status=?, result=?, pending_writeback=?, finished_at=NULL WHERE id=?',
                                   ('writing_back',json.dumps(interrupted_result),json.dumps(result_body),automatic['id']))
            backend=launch('app.main:app',backend_port)
            interrupted=call(backend_url,'/api/tasks/'+automatic['id'])
            assert interrupted['status']=='failed' and interrupted['result']['error_code']=='process_interrupted'
            stop(backend)
            recovered=subprocess.run([sys.executable,str(ROOT/'scripts/recover_task.py'),automatic['id'],
                                      '--action','replay-writeback','--actor','smoke-admin','--reason','按原编号对账'],
                                     cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',check=True)
            recovery_task=json.loads(recovered.stdout)
            assert recovery_task['status']=='succeeded' and recovery_task['result']['receipt']==receipt
            with sqlite3.connect(temp/'finance.db') as connection:
                assert connection.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]==3
            backend=launch('app.main:app',backend_port)
            assert call(backend_url,'/api/tasks/'+automatic['id'])==recovery_task
            metrics=call(backend_url,'/api/metrics',role='admin')
            assert metrics['total']==3 and metrics['completed']==3
            # 保留真实的历史待审批与技术失败，验证脚本不能要求所有任务均已完成。
            historical_pending=call(backend_url,'/api/tasks',pending_body,key=str(uuid4()))
            assert historical_pending['status']=='awaiting_approval'
            stop(finance)
            historical_failed=call(backend_url,'/api/tasks',body,key=str(uuid4()))
            assert historical_failed['status']=='failed' and 'receipt' not in historical_failed['result']
            finance=launch('finance.main:app',finance_port)
            verification_runs=[]
            for script in ['verify_system.py','verify_system.py','verify_integration.py']:
                verified=subprocess.run([sys.executable,str(ROOT/'scripts'/script)],
                                        cwd=ROOT,env={**env,'BACKEND_URL':backend_url},
                                        capture_output=True,text=True,encoding='utf-8',timeout=90)
                if verified.returncode:
                    raise RuntimeError(verified.stdout[-4000:] + verified.stderr[-1000:])
                verification_runs.append({'script':script,'exit_code':verified.returncode,
                                          'summary':verified.stdout.strip().splitlines()[-1]})
                print(f'PASS: {script} — ' + verified.stdout.strip().splitlines()[-1])
            for historical in [historical_pending,historical_failed]:
                assert call(backend_url,'/api/tasks/'+historical['id'])==historical
            tasks=call(backend_url,'/api/tasks',role='admin')
            approval_rejected=next(task for task in tasks if task['status']=='rejected'
                                   and task['approvals'] and task['approvals'][-1]['decision']=='reject')
            missing_basis=next(task for task in tasks if task['status']=='awaiting_approval'
                               and '缺少适用制度依据' in task['result'].get('manual_reasons',[]))
            report={'recorded_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                    'verification_runs':verification_runs,'historical_tasks_preserved':True,
                    'metrics':call(backend_url,'/api/metrics',role='admin'),
                    'scenarios':{'compliant':recovery_task,'approval_approved':done,
                                 'approval_rejected':approval_rejected,'missing_basis_to_human':missing_basis}}
            (ROOT/'work/smoke-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print('PASS: repeated verification with historical pending/failed tasks, without changing their records')
            print('PASS: four-scenario evidence saved to work/smoke-summary.json')
            print('PASS: real HTTP, unique task IDs, task lookup, persisted tasks and receipts, submit idempotency after process restart')
            print('PASS: parse snapshots, atomic amendments, initial-request replay, interrupted writeback detection and CLI reconciliation')
            print('PASS: budget samples, no budget deduction, automatic review, approval resume, budget rejection, metrics')
        finally:
            for process in reversed(processes): stop(process)
