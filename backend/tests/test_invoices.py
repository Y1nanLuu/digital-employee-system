"""发票 PDF 上传与字段提取的契约测试。

测试用的 PDF 由本文件手工构造（无外部依赖），只验证链路与错误分类；
真实电子发票（含 CJK 内嵌字体）的抽取效果需用实际票据人工验证一次。
"""
import base64
import os
import tempfile
from pathlib import Path

import pytest

scratch = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + (Path(scratch.name) / 'tasks.db').as_posix())
os.environ.setdefault('FINANCE_DATABASE_URL', 'sqlite:///' + (Path(scratch.name) / 'finance.db').as_posix())
os.environ['DEMO_PASSWORD'] = 'demo123'
os.environ['FINANCE_KEY'] = 'local-finance-key'
os.environ.pop('AI_MODULE_ENABLED', None)  # 默认走规则基线，测试不依赖外网

from app import invoices  # noqa: E402


def build_pdf(content: str, extra_pages: int = 0) -> bytes:
    """构造带文字层的极简 PDF（ASCII 内容，Helvetica 字体），验证抽取链路。"""
    page_count = extra_pages + 1
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b'<< /Type /Catalog /Pages 2 0 R >>')
    pages_slot = add(b'<< >>')
    font = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')

    page_refs = []
    for index in range(page_count):
        text = content if index == 0 else ''
        lines = ['BT', '/F1 12 Tf', '72 760 Td', '14 TL']
        for line in text.splitlines():
            escaped = line.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')
            lines.append(f'({escaped}) Tj T*')
        lines.append('ET')
        stream = '\n'.join(lines).encode('latin-1')
        content_number = add(f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'\nendstream')
        page_number = add(
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
            f'/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content_number} 0 R >>'.encode())
        page_refs.append(page_number)

    kids = ' '.join(f'{number} 0 R' for number in page_refs)
    objects[pages_slot - 1] = f'<< /Type /Pages /Kids [{kids}] /Count {page_count} >>'.encode()

    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f'{index} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref_at = len(out)
    out += f'xref\n0 {len(objects) + 1}\n'.encode()
    out += b'0000000000 65535 f \n'
    for offset in offsets:
        out += f'{offset:010d} 00000 n \n'.encode()
    out += (f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n').encode()
    return bytes(out)


INVOICE_TEXT = (
    'Beijing Zhongxing XieLi Technology Co Ltd\n'
    'Invoice No: 12345678\n'
    'Date: 2026-11-10\n'
    'Item: Taxi fare\n'
    'Amount CNY 320.00\n'
)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.drop_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def auth():
    return {'Authorization': 'Basic ' + base64.b64encode(b'employee:demo123').decode()}


def upload(client, data: bytes, filename='invoice.pdf', role='employee'):
    credential = 'Basic ' + base64.b64encode(f'{role}:demo123'.encode()).decode()
    return client.post('/api/invoices', files={'file': (filename, data, 'application/pdf')},
                       headers={'Authorization': credential})


def test_extract_pdf_text_reads_text_layer():
    result = invoices.extract_pdf_text(build_pdf(INVOICE_TEXT))
    assert 'Invoice No: 12345678' in result['text']
    assert 'Amount CNY 320.00' in result['text']
    assert result['pages'] == 1
    assert result['full_text_chars'] >= invoices.MIN_TEXT_CHARS


def test_invoice_hints_pull_structured_values():
    hints = invoices.invoice_hints(INVOICE_TEXT)
    assert hints['invoice_no'] == '12345678'
    assert hints['expense_date'] == '2026-11-10'


@pytest.mark.parametrize('payload,code,status', [
    (b'', 'empty_file', 422),
    (b'not a pdf at all', 'not_pdf', 422),
    (b'%PDF-1.4 broken', 'unreadable_pdf', 422),
])
def test_rejects_invalid_files(payload, code, status):
    with pytest.raises(invoices.InvoiceError) as excinfo:
        invoices.extract_pdf_text(payload)
    assert excinfo.value.code == code
    assert excinfo.value.status == status


def test_rejects_oversized_file():
    with pytest.raises(invoices.InvoiceError) as excinfo:
        invoices.extract_pdf_text(b'%PDF-' + b'0' * (invoices.MAX_PDF_BYTES + 1))
    assert excinfo.value.code == 'file_too_large'
    assert excinfo.value.status == 413


def test_rejects_pdf_without_text_layer():
    empty = build_pdf('', extra_pages=1)
    with pytest.raises(invoices.InvoiceError) as excinfo:
        invoices.extract_pdf_text(empty)
    assert excinfo.value.code == 'no_text_layer'
    assert '扫描件' in excinfo.value.message


def test_upload_endpoint_returns_parse_compatible_snapshot(client):
    response = upload(client, build_pdf(INVOICE_TEXT))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['invoice_id'] == body['parse_id']
    assert body['invoice']['pages'] == 1
    assert body['invoice']['hints']['invoice_no'] == '12345678'
    assert 'Invoice No' in body['invoice']['text_preview']
    for key in ('mode', 'suggested_amount', 'suggested_category', 'requires_confirmation',
                'requires_manual_review', 'message'):
        assert key in body


def test_upload_requires_employee_role(client):
    response = upload(client, build_pdf(INVOICE_TEXT), role='approver')
    assert response.status_code == 403


def test_upload_rejects_non_pdf(client):
    response = upload(client, b'%PDF-1.4 but actually broken')
    assert response.status_code == 422
    assert '无法解析' in response.json()['detail']


def test_task_can_reference_invoice_snapshot(client):
    uploaded = upload(client, build_pdf(INVOICE_TEXT)).json()
    claim = {'project_id': 'P001', 'category': '交通费', 'amount': '320.00',
             'expense_date': '2026-11-10', 'description': '客户现场打车费320元',
             'confirmed': True, 'invoice_id': uploaded['invoice_id']}
    created = client.post('/api/tasks', json=claim, headers={**auth(), 'Idempotency-Key': 'invoice-check-0001'})
    assert created.status_code == 201, created.text
    task = created.json()
    assert task['parse_snapshot']['invoice']['hints']['invoice_no'] == '12345678'
    assert task['submission_payload']['invoice_id'] == uploaded['invoice_id']


def test_task_rejects_unknown_or_foreign_invoice(client):
    claim = {'project_id': 'P001', 'category': '交通费', 'amount': '320.00',
             'expense_date': '2026-11-10', 'description': '客户现场打车费320元',
             'confirmed': True, 'invoice_id': '00000000-0000-4000-8000-000000000000'}
    response = client.post('/api/tasks', json=claim,
                           headers={**auth(), 'Idempotency-Key': 'invoice-check-0002'})
    assert response.status_code == 422
    assert '发票记录不存在' in response.json()['detail']


def test_task_rejects_parse_id_with_invoice_id(client):
    claim = {'project_id': 'P001', 'category': '交通费', 'amount': '320.00',
             'expense_date': '2026-11-10', 'description': '客户现场打车费320元', 'confirmed': True,
             'parse_id': '00000000-0000-4000-8000-000000000001',
             'invoice_id': '00000000-0000-4000-8000-000000000002'}
    response = client.post('/api/tasks', json=claim,
                           headers={**auth(), 'Idempotency-Key': 'invoice-check-0003'})
    assert response.status_code == 422
    assert '二选一' in response.json()['detail']
