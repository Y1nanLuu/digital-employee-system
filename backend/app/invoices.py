"""发票 PDF 文本抽取。

只处理带文字层的电子发票：把 PDF 里的文本读出来交给字段提取链路（模型或规则）。
扫描件/图片型 PDF 没有文字层，本模块明确拒绝并提示，不做 OCR。
文件只在内存中解析，不落盘，避免票据内容留在服务器上。
"""
from __future__ import annotations

import io
import re

MAX_PDF_BYTES = 5 * 1024 * 1024      # 单文件上限 5MB
MIN_TEXT_CHARS = 20                   # 低于该长度视为没有文字层
MAX_TEXT_CHARS = 8000                 # 交给模型的文本上限，避免超长拖慢调用

_INVOICE_NO_RE = re.compile(r'(?:发票号码|发票号|Invoice\s*No\.?)\s*[:：]?\s*([0-9A-Za-z]{8,20})', re.I)
_DATE_RE = re.compile(r'(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})')
_TITLE_RE = re.compile(r'(?:发票抬头|购买方名称|购方名称|名称)\s*[:：]\s*([^\n]{2,60})')


class InvoiceError(Exception):
    """发票处理失败，携带可返回给前端的原因码与提示。"""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _normalize(text: str) -> str:
    lines = []
    for raw in text.replace('\u3000', ' ').splitlines():
        line = re.sub(r'[ \t]+', ' ', raw).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def extract_pdf_text(data: bytes) -> dict:
    """从 PDF 字节流抽取文本，返回文本与该文件的结构化元信息。"""
    if not data:
        raise InvoiceError('empty_file', '上传的文件为空')
    if len(data) > MAX_PDF_BYTES:
        raise InvoiceError('file_too_large', f'发票文件超过 {MAX_PDF_BYTES // (1024 * 1024)} MB 上限，请压缩后重试', 413)
    if not data.startswith(b'%PDF-'):
        raise InvoiceError('not_pdf', '只接受 PDF 格式的电子发票')
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # 依赖缺失属于服务端问题，不是用户输入问题
        raise InvoiceError('extractor_unavailable', '服务端缺少 PDF 解析依赖 pypdf，请联系管理员', 500) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt('')
            except Exception as exc:
                raise InvoiceError('encrypted_pdf', 'PDF 已加密，请提供未加密的电子发票') from exc
        pages = [(page.extract_text() or '') for page in reader.pages]
    except InvoiceError:
        raise
    except Exception as exc:
        raise InvoiceError('unreadable_pdf', '无法解析该 PDF，文件可能损坏或格式特殊') from exc

    text = _normalize('\n'.join(pages))
    if len(text) < MIN_TEXT_CHARS:
        raise InvoiceError(
            'no_text_layer',
            '未检测到文字层，疑似扫描件或图片型 PDF。当前版本只能识别带文字层的电子发票，请改用文字版电子发票或手工填写')
    return {
        'text': text[:MAX_TEXT_CHARS],
        'full_text_chars': len(text),
        'pages': len(pages),
    }


def invoice_hints(text: str) -> dict:
    """从发票文本里做确定性抽取，作为模型结果的补充与降级兜底。"""
    hints: dict = {}
    match = _INVOICE_NO_RE.search(text)
    if match:
        hints['invoice_no'] = match.group(1)
    match = _DATE_RE.search(text)
    if match:
        year, month, day = match.groups()
        hints['expense_date'] = f'{int(year):04d}-{int(month):02d}-{int(day):02d}'
    match = _TITLE_RE.search(text)
    if match:
        hints['invoice_title'] = match.group(1).strip()
    return hints


def text_preview(text: str, size: int = 200) -> str:
    """给前端看的原文摘要，便于发现乱码或抽取失败。"""
    return text[:size]
