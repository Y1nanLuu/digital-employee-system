"""Validate enterprise recovery with real HTTP services, files and the recovery CLI."""
import ast
import base64
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
from threading import Thread
import time
import urllib.error
import urllib.request
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'backend/tests/conftest.py').is_file())
(ROOT / 'work').mkdir(exist_ok=True)
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# Reuse the deterministic PDF fixture without importing its environment setup.
tree = ast.parse((ROOT / 'backend/tests/test_invoices.py').read_text())
builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build_pdf')
namespace = {}
exec(compile(ast.Module(body=[builder], type_ignores=[]), '<pdf-fixture>', 'exec'), namespace)
build_pdf = namespace['build_pdf']


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


checks = []


def expect(label, condition):
    checks.append({'label': label, 'passed': bool(condition)})
    assert condition, label
    print('PASS: ' + label, flush=True)


with tempfile.TemporaryDirectory(prefix='enterprise-http-', dir=ROOT / 'work') as folder:
    scratch = Path(folder)
    finance_port, backend_port = port(), port()
    finance_url = f'http://127.0.0.1:{finance_port}'
    backend_url = f'http://127.0.0.1:{backend_port}'
    fault = {'budget': False, 'writeback': False}

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def forward(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0))) or None
            if self.path.startswith('/budgets/') and fault['budget']:
                status, data = 503, b'{"detail":"test budget outage"}'
            else:
                request = urllib.request.Request(finance_url + self.path, data=body, method=self.command,
                                                headers={k: v for k, v in self.headers.items()
                                                         if k.lower() in ('authorization', 'content-type')})
                try:
                    with HTTP.open(request, timeout=10) as response:
                        status, data = response.status, response.read()
                except urllib.error.HTTPError as exc:
                    status, data = exc.code, exc.read()
                if self.path == '/reviews/results' and status == 200 and fault['writeback']:
                    status, data = 503, b'{"detail":"test response lost after commit"}'
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = forward

    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, 'DATABASE_URL': 'sqlite:///' + str(scratch / 'tasks.db'),
           'FINANCE_DATABASE_URL': 'sqlite:///' + str(scratch / 'finance.db'),
           'FILE_STORAGE_DIR': str(scratch / 'files'),
           'FINANCE_URL': f'http://127.0.0.1:{proxy.server_port}',
           'FINANCE_KEY': 'local-finance-key', 'DEMO_PASSWORD': 'demo123',
           'ACTIVE_POLICY_VERSION': 'enterprise-demo-v2', 'AI_MODULE_ENABLED': 'false',
           'COMPANY_TITLE': '示例科技有限公司', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    env.pop('ENTERPRISE_POLICY_FILE', None)
    children = []

    def launch(module, selected_port):
        child = subprocess.Popen([sys.executable, '-m', 'uvicorn', module, '--host', '127.0.0.1',
                                  '--port', str(selected_port)], cwd=ROOT / 'backend', env=env,
                                 stdout=log, stderr=log)
        children.append(child)
        deadline = time.monotonic() + 30
        while True:
            if child.poll() is not None:
                raise RuntimeError('Test service exited: ' + (scratch / 'services.log').read_text()[-1500:])
            try:
                with HTTP.open(f'http://127.0.0.1:{selected_port}/health', timeout=1):
                    return child
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Test service startup timeout')
                time.sleep(.2)

    def call(path, payload=None, *, role='employee', key=None, raw=None, content_type='application/json'):
        headers = {'Authorization': 'Basic ' + base64.b64encode((role + ':demo123').encode()).decode()}
        if key:
            headers['Idempotency-Key'] = key
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        if data is not None:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(backend_url + path, data=data, headers=headers)
        try:
            with HTTP.open(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def upload(number):
        pdf = build_pdf(f'Invoice No: {number}\nDate: 2026-11-01\nAmount CNY 300.00\nItem: taxi transport\n')
        boundary = 'audit-' + uuid4().hex
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="receipt.pdf"\r\n'
                'Content-Type: application/pdf\r\n\r\n').encode() + pdf + f'\r\n--{boundary}--\r\n'.encode()
        code, source = call('/api/invoices', raw=data, content_type='multipart/form-data; boundary=' + boundary)
        assert code == 200, source
        return {'project_id': 'P001', 'department': '研发部', 'category': '交通费', 'amount': '300.00',
                'expense_date': datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                'description': '项目客户现场往返交通费', 'confirmed': True,
                'file_id': source['file_id'], 'invoice_id': source['invoice_id'],
                'invoice': {'invoice_no': number, 'invoice_code': '', 'invoice_date': '2026-11-01',
                            'buyer': '示例科技有限公司', 'seller': '测试供应商', 'total': '300.00', 'item_name': 'taxi transport'},
                'details': {'route': '公司至客户现场', 'seat': '市内交通'},
                'conflict_acknowledged': True, 'confirmation_note': '对照合成原件补充购销方'}

    def recover(identity, action):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/recover_task.py'), identity,
                                 '--action', action, '--actor', 'http-cli-ops', '--reason', '隔离HTTP及原载荷恢复验收'],
                                cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with (scratch / 'services.log').open('w') as log:
        try:
            launch('finance.main:app', finance_port)
            backend = launch('app.main:app', backend_port)
            body = upload('26000000000000000999')
            code, task = call('/api/tasks', body, key=str(uuid4()))
            expect('enterprise task awaits human review', code == 201 and task['status'] == 'awaiting_approval')
            old_file = body['file_id']
            call(f"/api/tasks/{task['id']}/approval", {'decision': 'return', 'comment': '请更换原件'}, role='approver')
            replacement = upload('26000000000000000888')
            replacement['revision_reason'] = '按意见替换原票'
            code, task = call(f"/api/tasks/{task['id']}/resubmit", replacement, key=str(uuid4()))
            expect('same task resubmitted with revision 2', code == 200 and task['revision'] == 2)
            code, metadata = call('/api/files/' + old_file, role='approver')
            expect('approver can read old original metadata', code == 200 and metadata['file_id'] == old_file)
            fault['budget'] = True
            code, failed = call(f"/api/tasks/{task['id']}/approval",
                                {'decision': 'approve', 'comment': '原票和用途核对通过', 'reviewed_fields': True}, role='approver')
            expect('persisted approval survives real tool failure', code == 200 and failed['status'] == 'failed'
                   and failed['failure_stage'] == 'budget_query' and len(failed['approvals']) == 2)
            code, _ = call(f"/api/tasks/{task['id']}/withdraw", raw=b'')
            expect('failed task withdrawal rejected', code == 409)
            stop(backend)
            fault['budget'] = False
            restored = recover(task['id'], 'retry-budget')
            expect('CLI continues original human approval once', restored['status'] == 'succeeded'
                   and restored['approvals'] == failed['approvals'])
            expect('CLI synchronizes current revision audit', restored['revisions'][-1]['snapshot']['review_record']['status'] == 'succeeded')
            backend = launch('app.main:app', backend_port)
            code, persisted = call('/api/tasks/' + task['id'])
            expect('recovered result persists across process restart', code == 200 and persisted['status'] == 'succeeded')
            body = upload('26000000000000000777')
            _, second = call('/api/tasks', body, key=str(uuid4()))
            fault['writeback'] = True
            _, lost = call(f"/api/tasks/{second['id']}/approval",
                           {'decision': 'approve', 'comment': '原票和用途核对通过', 'reviewed_fields': True}, role='approver')
            expect('lost response retains original writeback decision', lost['status'] == 'failed' and lost['failure_stage'] == 'writeback')
            stop(backend)
            fault['writeback'] = False
            reconciled = recover(second['id'], 'replay-writeback')
            expect('CLI retrieves original receipt and updates audit', reconciled['status'] == 'succeeded'
                   and reconciled['revisions'][-1]['snapshot']['review_record']['result']['receipt'] == reconciled['result']['receipt'])
            with sqlite3.connect(scratch / 'finance.db') as db:
                count = db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
            expect('two applications produce exactly two financial receipts', count == 2)
            report = {'checks': checks, 'passed': len(checks), 'failed': 0, 'receipt_count': count,
                      'model_mode': 'disabled; human approval with real mock-finance HTTP service',
                      'tasks': [{k: snapshot[k] for k in ('id', 'status', 'revision', 'approvals', 'result')}
                                for snapshot in (restored, reconciled)]}
            (ROOT / 'work/backend-recovery-http-cli-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        finally:
            for child in reversed(children):
                stop(child)
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=2)
