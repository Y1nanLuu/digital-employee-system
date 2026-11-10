"""D3 重启与恢复集成校验：真实进程被杀死后的状态语义与受限恢复。

覆盖四项检查（对应 D3-02「检查状态转换、账号权限和重启恢复」中的恢复部分）：
  1. 待审批任务跨重启保留，且审批续跑后事件序列连续、任务编号不变；
  2. 执行中的任务在进程被杀后，于下次启动被标记 failed（process_interrupted），并保留中断前状态；
  3. 受限运维按既有检查点恢复（scripts/recover_task.py retry-budget），不构造新的审核载荷；
  4. 相同幂等键在重启后重放仍返回原任务，不重复执行。

自起自停临时服务与临时库，不影响演示数据。用法：
    .\\venv\\Scripts\\python.exe scripts/verify_restart.py
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'work').mkdir(exist_ok=True)
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []
today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def expect(label, condition, detail=''):
    results.append((label, bool(condition), detail))
    print(('PASS  ' if condition else 'FAIL  ') + label + ('' if condition else f'  -> {detail}'))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def call(base, path, body=None, role='employee', key=None, method=None, timeout=20):
    hdrs = {}
    if role:
        hdrs['Authorization'] = 'Basic ' + base64.b64encode(f'{role}:demo123'.encode()).decode()
    data = None
    if body is not None:
        hdrs['Content-Type'] = 'application/json'
        data = json.dumps(body).encode()
    if key:
        hdrs['Idempotency-Key'] = key
    request = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with LOCAL_HTTP.open(request, timeout=timeout) as response:
        return json.load(response)


def wait_ready(base, process, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('服务提前退出，请查看 work/restart-services.log')
        try:
            with LOCAL_HTTP.open(base + '/health', timeout=1):
                return
        except (OSError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError('服务启动超时')


def stop(process, *, crash=False):
    if process and process.poll() is None:
        # terminate 在 POSIX 上会触发 Uvicorn 优雅退出，允许工具先超时并保存失败。
        # 故障注入必须立即杀死测试进程，普通重启和清理仍使用优雅退出。
        if crash:
            process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


scratch = Path(tempfile.mkdtemp(dir=ROOT / 'work', prefix='restart-'))
tasks_db = scratch / 'tasks.db'
finance_db = scratch / 'finance.db'
backend_port, finance_port = free_port(), free_port()
backend_url = f'http://127.0.0.1:{backend_port}'
finance_url = f'http://127.0.0.1:{finance_port}'
base_env = {**os.environ,
            'DATABASE_URL': 'sqlite:///' + tasks_db.as_posix(),
            'FINANCE_DATABASE_URL': 'sqlite:///' + finance_db.as_posix(),
            'FINANCE_URL': finance_url,
            'FINANCE_KEY': 'local-finance-key', 'DEMO_PASSWORD': 'demo123',
            'AI_MODULE_ENABLED': 'false',
            'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
log_path = ROOT / 'work' / 'restart-services.log'
processes = []


def launch(module, port, env):
    child = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', str(port)],
        cwd=ROOT / 'backend', env=env, stdout=log_path.open('a', encoding='utf8'),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    processes.append(child)
    wait_ready(f'http://127.0.0.1:{port}', child)
    return child


def blackhole(port=None, attempts=10):
    """只监听不响应：让业务端在工具调用阶段挂起，用于制造「执行中」的中断场景。

    Windows 上刚释放的端口可能仍在 TIME_WAIT 或被 Hyper-V 保留，故按需重试换端口。
    """
    for _ in range(attempts):
        candidate = port or free_port()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(('127.0.0.1', candidate))
            server.listen(8)
            return server, candidate
        except OSError:
            server.close()
    raise RuntimeError('无法建立黑洞监听端口')


claim = {'project_id': 'P001', 'category': '办公用品', 'amount': '1500.00',
         'expense_date': today, 'description': '客户现场采购办公用品1500元', 'confirmed': True}

try:
    finance = launch('finance.main:app', finance_port, base_env)
    backend = launch('app.main:app', backend_port, base_env)

    # -------------------------------------------------- 1. 待审批任务跨重启
    key = str(uuid4())
    pending = call(backend_url, '/api/tasks', claim, key=key)
    expect('重启前任务进入 awaiting_approval', pending['status'] == 'awaiting_approval', pending['status'])
    before_events = [item['status'] for item in pending['events']]
    stop(backend)
    backend = launch('app.main:app', backend_port, base_env)
    restored = call(backend_url, f"/api/tasks/{pending['id']}", role='approver')
    expect('重启后待审批任务仍在且编号不变',
           restored['id'] == pending['id'] and restored['status'] == 'awaiting_approval', restored['status'])
    expect('重启不改变既有事件序列',
           [item['status'] for item in restored['events']] == before_events, [item['status'] for item in restored['events']])
    approved = call(backend_url, f"/api/tasks/{pending['id']}/approval",
                    {'decision': 'approve', 'comment': '重启后人工核验通过', 'reviewed_fields': True}, role='approver')
    expect('重启后可继续审批并续跑至终态', approved['status'] == 'succeeded', approved['status'])
    expect('续跑事件接在原序列之后', [item['status'] for item in approved['events']] ==
           before_events + ['running', 'writing_back', 'succeeded'], [item['status'] for item in approved['events']])

    # -------------------------------------------------- 4. 幂等键跨重启重放
    replay = call(backend_url, '/api/tasks', claim, key=key)
    expect('同一幂等键重启后重放返回原任务', replay['id'] == pending['id'], (replay['id'], pending['id']))

    # -------------------------------------------------- 2. 执行中任务被中断
    stop(backend)
    hole, hole_port = blackhole()  # 只监听不响应，让工具调用挂起在预算查询阶段
    hang_env = {**base_env, 'FINANCE_URL': f'http://127.0.0.1:{hole_port}'}
    list_triggered = threading.Event()
    failure = []

    def create_during_hang():
        try:
            call(backend_url, '/api/tasks',
                 {**claim, 'amount': '800.00', 'description': '客户现场采购办公用品800元'},
                 key=str(uuid4()), timeout=30)
        except Exception as exc:  # 服务被杀时客户端必然异常，属预期
            failure.append(type(exc).__name__)

    hanging = launch('app.main:app', backend_port, hang_env)
    worker = threading.Thread(target=create_during_hang, daemon=True)
    worker.start()
    started = time.monotonic()
    interrupted = None
    for _ in range(60):
        time.sleep(0.2)
        try:
            tasks = call(backend_url, '/api/tasks', role='approver', timeout=3)
            running = [item for item in tasks if item['status'] == 'running' and item['payload']['amount'] == '800.00']
            if running:
                interrupted = running[0]
                break
        except Exception:
            continue
    expect('可在中断前观测到执行中任务', interrupted is not None,
           f'等待 {round(time.monotonic() - started, 1)}s')
    stop(hanging, crash=True)  # 强制中断，禁止等待工具超时后正常落库。
    worker.join(timeout=10)
    stop(backend)
    backend = launch('app.main:app', backend_port, hang_env)  # 仍指向黑洞端口，仅验证中断标记
    tasks = call(backend_url, '/api/tasks', role='approver')
    recovered = [item for item in tasks if interrupted and item['id'] == interrupted['id']]
    expect('中断任务在下次启动后被标记 failed',
           bool(recovered) and recovered[0]['status'] == 'failed',
           recovered[0]['status'] if recovered else '任务不存在')
    if recovered:
        task = recovered[0]
        expect('失败原因标记为进程中断', task['result'].get('error_code') == 'process_interrupted', task['result'])
        expect('保留中断前状态用于对账', task['result'].get('interrupted_status') == 'running', task['result'])
        expect('记录中断发生的阶段', task['failure_stage'] in ('initial_review', 'budget_query'), task['failure_stage'])
        expect('失败任务的事件流以 failed 结束',
               [item['status'] for item in task['events']][-1:] == ['failed'], [item['status'] for item in task['events']])

    # -------------------------------------------------- 3. 受限恢复
    stop(backend)
    hole.close()
    recovered_port = free_port()  # 换端口启动正常财务服务，避免黑洞端口残留占用
    finance = launch('finance.main:app', recovered_port, base_env)
    recover_env = {**base_env, 'FINANCE_URL': f'http://127.0.0.1:{recovered_port}'}
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'recover_task.py'), interrupted['id'],
         '--action', 'retry-budget', '--actor', 'ops-admin', '--reason', 'D3重启恢复演练'],
        cwd=ROOT, env=recover_env, capture_output=True, text=True, encoding='utf-8')
    expect('受限恢复命令执行成功', completed.returncode == 0, completed.stderr[-300:] if completed.returncode else '')
    payload = {}
    if completed.returncode == 0:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    expect('恢复后任务离开 failed 状态', payload.get('status') in ('succeeded', 'awaiting_approval'), payload.get('status'))
    expect('恢复后重新执行阈值校验（800元自动通过）', payload.get('status') == 'succeeded', payload.get('status'))
    expect('恢复事件记录操作者与原因',
           any(item.get('actor') == 'ops-admin' for item in payload.get('events', [])), payload.get('events', [])[-2:])
    expect('恢复不新增审批记录', payload.get('approvals') == [], payload.get('approvals'))

    backend = launch('app.main:app', backend_port, recover_env)
    persisted = call(backend_url, f"/api/tasks/{interrupted['id']}", role='approver')
    expect('恢复结果已持久化并可查询', persisted['status'] == 'succeeded', persisted['status'])

    refused = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'recover_task.py'), pending['id'],
         '--action', 'retry-budget', '--actor', 'ops-admin', '--reason', '重复恢复尝试'],
        cwd=ROOT, env=recover_env, capture_output=True, text=True, encoding='utf-8')
    expect('终态任务拒绝恢复(409)', refused.returncode != 0 and '仅允许处置失败任务' in (refused.stderr or ''),
           (refused.returncode, (refused.stderr or '')[-120:]))
finally:
    for process in reversed(processes):
        stop(process)

failed = [item for item in results if not item[1]]
(scratch / 'summary.json').write_text(json.dumps({
    'recorded_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
    'total': len(results), 'passed': len(results) - len(failed), 'failed': len(failed),
    'checks': [{'label': label, 'passed': passed, 'detail': detail} for label, passed, detail in results],
}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f'\n总计 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项')
print(f'临时数据目录：{scratch}')
sys.exit(1 if failed else 0)
