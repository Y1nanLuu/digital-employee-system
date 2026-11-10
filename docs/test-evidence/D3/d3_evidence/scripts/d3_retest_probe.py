# -*- coding: cp936 -*-  
import base64,json,os,socket,subprocess,sys,time,urllib.request,urllib.error,sqlite3  
from pathlib import Path  
from concurrent.futures import ThreadPoolExecutor  
from datetime import datetime  
from uuid import uuid4  
from zoneinfo import ZoneInfo  
ROOT=Path(__file__).resolve().parent.parent  
WORK=ROOT/'work'/'d2_retest'  
WORK.mkdir(parents=True,exist_ok=True)  
LOCAL=urllib.request.build_opener(urllib.request.ProxyHandler({}))  
def port():  
    s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close(); return p  
def wait_ready(base,proc):  
    for i in range(120):  
        if proc.poll() is not None: raise RuntimeError('service exited early')  
        try:  
            LOCAL.open(base+'/health',timeout=1).read(); return  
        except Exception: time.sleep(.25)  
    raise RuntimeError('service timeout')  
def stop(proc):  
    if proc.poll() is None:  
        proc.terminate()  
        try: proc.wait(timeout=5)  
        except Exception: proc.kill(); proc.wait()  
def call(base,path,body=None,role='employee',key=None,status=False):  
    h={'Authorization':'Basic '+base64.b64encode((role+':demo123').encode()).decode()}  
    if body is not None: h['Content-Type']='application/json'  
    if key: h['Idempotency-Key']=key  
    req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=h)  
    try:  
        with LOCAL.open(req,timeout=20) as r:  
            data=json.load(r); return (r.status,data) if status else data  
    except urllib.error.HTTPError as e:  
        return (e.code,None) if status else None 
backend_port=port(); finance_port=port()  
backend_url='http://127.0.0.1:'+str(backend_port); finance_url='http://127.0.0.1:'+str(finance_port)  
env={**os.environ,'DATABASE_URL':'sqlite:///'+(WORK/'tasks.db').as_posix(),'FINANCE_DATABASE_URL':'sqlite:///'+(WORK/'finance.db').as_posix(),'FINANCE_URL':finance_url,'FINANCE_KEY':'local-finance-key','DEMO_PASSWORD':'demo123','PYTHONIOENCODING':'utf-8'}  
procs=[]  
def launch(module,p):  
    f=open(WORK/'service.log','a',encoding='utf8')  
    child=subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(p)],cwd=ROOT/'backend',env=env,stdout=f,stderr=f,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)  
    procs.append(child); wait_ready('http://127.0.0.1:'+str(p),child); return child  
today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()  
body={'project_id':'P001','category':'交通费','amount':'300.00','expense_date':today,'description':'D2 retest 300 traffic','confirmed':True}  
result={}  
try:  
    finance=launch('finance.main:app',finance_port); backend=launch('app.main:app',backend_port)  
    empty=call(backend_url,'/api/metrics',role='admin')  
    result['empty_metrics']=empty  
    a=call(backend_url,'/api/tasks',body,key=str(uuid4()))  
    r=call(backend_url,'/api/tasks',{**body,'project_id':'P002','amount':'600'},key=str(uuid4()))  
    p=call(backend_url,'/api/tasks',{**body,'category':'办公用品','amount':'1500'},key=str(uuid4()))  
    stop(finance)  
    f_task=call(backend_url,'/api/tasks',{**body,'description':'D2 retest finance down'},key=str(uuid4()))  
    result['tasks_before_metrics']={'auto':a['status'],'rejected':r['status'],'pending':p['status'],'failed':f_task['status']}  
    result['failed_logs']=f_task.get('logs')  
    fixed=call(backend_url,'/api/metrics',role='admin')  
    result['fixed_metrics']=fixed  
    finance=launch('finance.main:app',finance_port)  
    def approve_once():  
        return call(backend_url,'/api/tasks/'+p['id']+'/approval',{'decision':'approve','comment':'D2 concurrent approval'},role='approver',status=True)[0]  
    with ThreadPoolExecutor(max_workers=2) as ex:  
        result['concurrent_approval']=sorted(ex.map(lambda n: approve_once(),range(2)))  
    result['final_pending']=call(backend_url,'/api/tasks/'+p['id'],role='approver')['status']  
finally:  
    for x in reversed(procs): stop(x)  
conn=sqlite3.connect(WORK/'tasks.db'); rows=conn.execute('select id,status,manual from tasks order by created_at').fetchall(); result['db_tasks']=rows  
(WORK/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')  
print(json.dumps(result,ensure_ascii=False,indent=2)) 
