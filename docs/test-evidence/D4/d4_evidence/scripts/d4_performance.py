import base64,json,os,socket,subprocess,sys,time,urllib.request,urllib.error,statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent.parent
WORK=ROOT/'work'/'d4_perf'
WORK.mkdir(parents=True,exist_ok=True)
LOCAL=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def port():
    s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close(); return p
def call(base,path,body=None,role='employee',key=None,timeout=30):
    h={}
    if role: h['Authorization']='Basic '+base64.b64encode((role+':demo123').encode()).decode()
    if key: h['Idempotency-Key']=key
    data=None
    if body is not None: h['Content-Type']='application/json'; data=json.dumps(body).encode()
    req=urllib.request.Request(base+path,data=data,headers=h)
    with LOCAL.open(req,timeout=timeout) as r: return json.load(r)
def wait_ready(base,proc):
    for _ in range(120):
        if proc.poll() is not None: raise RuntimeError('service exited')
        try: LOCAL.open(base+'/health',timeout=1).read(); return
        except Exception: time.sleep(.25)
    raise RuntimeError('timeout')
def stop(p):
    if p.poll() is None:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill(); p.wait()
def pct(vals,q):
    vals=sorted(vals); return vals[min(len(vals)-1,int(round((len(vals)-1)*q)))]
bp,fp=port(),port(); bu='http://127.0.0.1:'+str(bp); fu='http://127.0.0.1:'+str(fp)
env={**os.environ,'DATABASE_URL':'sqlite:///'+(WORK/'tasks.db').as_posix(),'FINANCE_DATABASE_URL':'sqlite:///'+(WORK/'finance.db').as_posix(),'FINANCE_URL':fu,'FINANCE_KEY':'local-finance-key','DEMO_PASSWORD':'demo123','PYTHONUTF8':'1'}
procs=[]; log=open(WORK/'services.log','w',encoding='utf8')
def launch(module,p):
    c=subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(p)],cwd=ROOT/'backend',env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0); procs.append(c); wait_ready('http://127.0.0.1:'+str(p),c); return c
today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
body={'project_id':'P001','category':'交通费','amount':'100.00','expense_date':today,'description':'D4性能测量交通费100元','confirmed':True}
res={}
try:
    launch('finance.main:app',fp); launch('app.main:app',bp)
    creates=[]; ids=[]
    for i in range(100):
        t=time.perf_counter(); obj=call(bu,'/api/tasks',{**body,'description':'D4性能测量交通费100元'},key='d4-perf-'+str(i)); creates.append(time.perf_counter()-t); ids.append(obj['id'])
    lists=[]; details=[]; metrics=[]
    for i in range(30):
        t=time.perf_counter(); call(bu,'/api/tasks',role='admin'); lists.append(time.perf_counter()-t)
        t=time.perf_counter(); call(bu,'/api/tasks/'+ids[0],role='employee'); details.append(time.perf_counter()-t)
        t=time.perf_counter(); call(bu,'/api/metrics',role='admin'); metrics.append(time.perf_counter()-t)
    def one_list(i): return call(bu,'/api/tasks',role='admin')
    t=time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as ex: list(ex.map(one_list,range(10)))
    concurrent=time.perf_counter()-t
    res={'tasks':100,'create_seconds':{'p50':pct(creates,.5),'p95':pct(creates,.95),'max':max(creates)},'list_seconds':{'p50':pct(lists,.5),'p95':pct(lists,.95),'max':max(lists)},'detail_seconds':{'p50':pct(details,.5),'p95':pct(details,.95),'max':max(details)},'metrics_seconds':{'p50':pct(metrics,.5),'p95':pct(metrics,.95),'max':max(metrics)},'concurrent_10_list_seconds':concurrent,'thresholds':{'query_p95_seconds':2.0,'rule_end_to_end_p95_seconds':10.0},'pass':pct(lists,.95)<=2.0 and pct(details,.95)<=2.0 and pct(creates,.95)<=10.0}
finally:
    log.close()
    for p in reversed(procs): stop(p)
(WORK/'performance.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(res,ensure_ascii=False,indent=2))

