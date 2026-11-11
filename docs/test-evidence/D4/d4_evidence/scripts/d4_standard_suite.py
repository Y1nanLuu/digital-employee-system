# -*- coding: cp936 -*-  
import json,os,socket,subprocess,sys,tempfile,time,urllib.request  
from pathlib import Path  
ROOT=Path(__file__).resolve().parent.parent  
WORK=ROOT/'work'/'d3-suite'; WORK.mkdir(parents=True,exist_ok=True)  
LOCAL=urllib.request.build_opener(urllib.request.ProxyHandler({}))  
def port():  
    s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close(); return p  
def ready(url,proc):  
    for i in range(120):  
        if proc.poll() is not None: raise RuntimeError('service exited')  
        try: LOCAL.open(url+'/health',timeout=1).read(); return  
        except Exception: time.sleep(.25)  
    raise RuntimeError('timeout')  
def stop(p):  
    if p.poll() is None:  
        p.terminate()  
        try: p.wait(timeout=5)  
        except Exception: p.kill(); p.wait() 
bp,fp=port(),port()  
bu='http://127.0.0.1:'+str(bp); fu='http://127.0.0.1:'+str(fp)  
env={**os.environ,'DATABASE_URL':'sqlite:///'+(WORK/'tasks.db').as_posix(),'FINANCE_DATABASE_URL':'sqlite:///'+(WORK/'finance.db').as_posix(),'FINANCE_URL':fu,'FINANCE_KEY':'local-finance-key','DEMO_PASSWORD':'demo123','PYTHONUTF8':'1'}  
procs=[]; log=open(WORK/'services.log','w',encoding='utf8')  
def launch(module,p):  
    c=subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(p)],cwd=ROOT/'backend',env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0); procs.append(c); ready('http://127.0.0.1:'+str(p),c); return c  
out={}  
try:  
    launch('finance.main:app',fp); launch('app.main:app',bp)  
    child={**env,'BACKEND_URL':bu,'FINANCE_URL':fu}  
    for name in ['verify_system.py','verify_integration.py']:  
        r=subprocess.run([sys.executable,str(ROOT/'scripts'/name)],cwd=ROOT,env=child,capture_output=True,text=True,encoding='utf-8')  
        (WORK/(name+'.txt')).write_text((r.stdout or '')+(r.stderr or ''),encoding='utf-8'); out[name]=r.returncode  
finally:  
    log.close(); [stop(p) for p in reversed(procs)]  
(WORK/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out))  
