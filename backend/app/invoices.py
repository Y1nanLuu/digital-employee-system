"""Read text PDFs and OCR images in memory, then gate invoice intake before storage.

extract_pdf_text remains the isolated legacy demo-v1 reader.
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


# Enterprise receipts use geometry to keep labels and values together. PDFium
# and OCR sessions are serialized: PDFium itself is not thread safe.
from threading import RLock
from decimal import Decimal, InvalidOperation
from datetime import date
import unicodedata

_receipt_lock = RLock()
_ocr_engine = None
MAX_PAGES = 5
MAX_IMAGE_PIXELS = 20_000_000


def _clean(value):
    return unicodedata.normalize('NFKC', value).replace('⼦','子').strip()


def _ocr(image, page_index):
    global _ocr_engine
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        if _ocr_engine is None:
            _ocr_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        result, _ = _ocr_engine(np.asarray(image.convert('RGB')))
    except Exception as exc:
        raise InvoiceError('ocr_unavailable','图片识别暂不可用，原件已保存，请手工补充或稍后重试',503) from exc
    spans=[]
    for box,text,score in result or []:
        spans.append({'text':_clean(text),'x':min(p[0] for p in box),'y':min(p[1] for p in box),
                      'right':max(p[0] for p in box),'bottom':max(p[1] for p in box),
                      'page':page_index,'score':float(score)})
    return spans


def read_receipt(data: bytes) -> dict:
    """Read PDF/JPEG/PNG locally; no original is sent to the text model."""
    if not data:raise InvoiceError('empty_file','上传的文件为空')
    if len(data)>MAX_PDF_BYTES:raise InvoiceError('file_too_large','文件超过5MB上限',413)
    from PIL import Image,ImageOps,UnidentifiedImageError
    spans=[];methods=[];pages=1
    with _receipt_lock:
        if data.startswith(b'%PDF-'):
            try:
                import pypdfium2 as pdfium
                doc=pdfium.PdfDocument(data)
                try:
                    pages=len(doc)
                    if pages<1 or pages>MAX_PAGES:raise InvoiceError('page_limit','单个PDF须为1至5页，且属于同一张票据')
                    for index in range(pages):
                        page=doc[index]
                        try:
                            tp=page.get_textpage();page_spans=[]
                            try:
                                tp.count_rects()
                                for j in range(tp.count_rects()):
                                    left,bottom,right,top=tp.get_rect(j)
                                    if not 0<=bottom<top<=page.get_height()+2:continue
                                    value=_clean(tp.get_text_bounded(left,bottom,right,top))
                                    if value:page_spans.append({'text':value,'x':left,'y':page.get_height()-top,'right':right,'bottom':page.get_height()-bottom,'page':index,'score':1.0})
                            finally:tp.close()
                            if sum(len(s['text']) for s in page_spans)<MIN_TEXT_CHARS:
                                scale=min(2.5,2200/max(page.get_width(),page.get_height()))
                                bitmap=page.render(scale=scale)
                                try:image=bitmap.to_pil().copy()
                                finally:bitmap.close()
                                page_spans=_ocr(image,index);methods.append('ocr')
                            else:methods.append('pdf_text')
                            spans.extend(page_spans)
                        finally:page.close()
                finally:doc.close()
            except InvoiceError:raise
            except Exception as exc:raise InvoiceError('unreadable_pdf','无法解析PDF，请检查是否损坏或加密') from exc
            media_type='application/pdf'
        elif data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff'):
            try:
                with Image.open(io.BytesIO(data)) as source:
                    if source.width*source.height>MAX_IMAGE_PIXELS:raise InvoiceError('image_limit','图片像素超过2000万，请缩小后上传',413)
                    image=ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((2200,2200))
                spans=_ocr(image,0);methods=['ocr']
            except InvoiceError:raise
            except (UnidentifiedImageError,OSError,ValueError) as exc:raise InvoiceError('unreadable_image','图片损坏或无法读取') from exc
            media_type='image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
        else:raise InvoiceError('unsupported_type','仅支持PDF、JPG/JPEG和PNG')
    # Row grouping permits label/value checks while leaving columns separate.
    lines=[]
    for s in sorted(spans,key=lambda x:(x['page'],round(x['y']/5)*5,x['x'])):
        line=next((l for l in reversed(lines[-15:]) if l['page']==s['page'] and abs(l['y']-s['y'])<=max(4,(s['bottom']-s['y'])*.55)),None)
        if line is None:lines.append({'page':s['page'],'y':s['y'],'spans':[s]})
        else:line['spans'].append(s)
    lines.sort(key=lambda x:(x['page'],x['y']))
    text='\n'.join(' '.join(s['text'] for s in sorted(l['spans'],key=lambda x:x['x'])) for l in lines)
    if len(text.strip())<5:raise InvoiceError('no_text','未读取到有效文字，请重新上传清晰、完整的发票；本次未保存')
    return {'text':text[:MAX_TEXT_CHARS],'full_text_chars':len(text),'pages':pages,'spans':spans,
            'read_method':'+'.join(sorted(set(methods))),'media_type':media_type,
            'low_confidence':any(s['score']<.85 for s in spans)}


def validate_receipt(extracted, fields):
    """Conservative pre-storage intake gate; this is not tax authenticity."""
    text=re.sub(r'\s+','',_clean(extracted['text']))
    if not re.search(r'发票|invoice',text,re.I):
        raise InvoiceError('not_invoice','未能识别为发票，请上传清晰完整的发票PDF或照片；本次未保存',422)
    numbers=set(re.findall(r'(?<!\d)\d{20}(?!\d)',extracted['text']))
    if len(numbers)>1:
        raise InvoiceError('multiple_invoices','检测到多个发票号码，请拆成一票一文件后重新上传；本次未保存',422)
    required=[label for key,label in [('invoice_no','发票号码'),('invoice_date','开票日期'),('total','含税总额')] if not fields.get(key)]
    scores=[(float(s.get('score',1)),max(1,len(s.get('text','')))) for s in extracted.get('spans',[])]
    weak=(sum(score*n for score,n in scores)/sum(n for _,n in scores)<.88 or
          sum(n for score,n in scores if score<.8)/sum(n for _,n in scores)>.2) if scores else extracted.get('low_confidence',False)
    if required or (extracted.get('read_method')!='pdf_text' and weak):
        missing='（未能读清'+ '、'.join(required)+'）' if required else ''
        raise InvoiceError('unclear_invoice','发票关键信息不清晰或不完整'+missing+'，请重新上传清晰完整的原件；本次未保存',422)


def receipt_fields(extracted: dict) -> dict:
    """Separate invoice total from tax/line prices using labels and geometry."""
    text=_clean(extracted['text']);spans=extracted.get('spans',[])
    fields={'invoice_no':'','invoice_code':'','invoice_date':None,'buyer':'','seller':'','total':'','item_name':''}
    compact=lambda t:re.sub(r'\s+','',t)
    def right_value(label,pattern=None):
        found=[]
        for anchor in spans:
            value=compact(anchor['text'])
            if not re.search(label,value,re.I):continue
            inline=re.split(label,value,maxsplit=1,flags=re.I)[-1].lstrip(':：¥￥ ')
            if inline and (pattern is None or re.fullmatch(pattern,inline)):found.append((anchor['x'],inline))
            candidates=[s for s in spans if s['page']==anchor['page'] and s['x']>=anchor['right']-1
                        and abs((s['y']+s['bottom'])/2-(anchor['y']+anchor['bottom'])/2)<max(7,anchor['bottom']-anchor['y'])]
            candidates.sort(key=lambda x:x['x'])
            for s in candidates:
                v=compact(s['text']).lstrip(':：¥￥')
                if v and (pattern is None or re.fullmatch(pattern,v)):
                    found.append((anchor['x'],v));break
        return sorted(found,key=lambda x:x[0])
    numbers=right_value(r'发票号码|InvoiceNo\.?',r'\d{8,20}')
    if numbers:fields['invoice_no']=numbers[0][1]
    if not fields['invoice_no']:
        ns=re.findall(r'(?<!\d)\d{20}(?!\d)',text)
        if len(set(ns))==1:fields['invoice_no']=ns[0]
        else:
            m=re.search(r'(?:发票号码|Invoice\s*No\.?)\s*[:：]?\s*(\d{8,20})',text,re.I)
            if m:fields['invoice_no']=m.group(1)
    dates=right_value(r'开票日期',r'\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?')
    dm=_DATE_RE.search(dates[0][1] if dates else text)
    if dm:
        try:fields['invoice_date']=date(*map(int,dm.groups())).isoformat()
        except ValueError:pass
    names=right_value(r'名称')
    # The first two name labels in domestic VAT invoices are buyer then seller.
    names=[x for x in names if len(x[1])>2 and not re.search(r'项目|规格|单位|数量|单价|金额|税',x[1])]
    if len(names)>=2:fields['buyer'],fields['seller']=names[0][1],names[1][1]
    totals=right_value(r'\(?小写\)?',r'[-]?\d[\d,]*\.\d{2}')
    if totals:fields['total']=totals[0][1].replace(',','')
    if not fields['total']:
        # Uppercase currency and its adjacent small total survive shuffled PDF text.
        matches=re.findall(r'[零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆元角分整正]+\s*[¥￥]\s*(-?\d[\d,]*\.\d{2})',text)
        if len(set(matches))==1:fields['total']=matches[0].replace(',','')
    if not fields['total']:
        m=re.search(r'(?:价税合计.{0,12}|Amount\s*CNY|Total\s*CNY)\s*[:：]?\s*[¥￥]?\s*(-?\d+(?:\.\d{1,2})?)(?!\d)',text,re.I)
        if m:fields['total']=format(Decimal(m.group(1)),'.2f')
    items=[s for s in spans if '*' in s['text']]
    if items:
        first=sorted(items,key=lambda s:(s['page'],s['y']))[0]
        values=[first['text']]
        # Include wrapped item-name continuations in its own column.
        for s in sorted(spans,key=lambda s:s['y']):
            if s['page']==first['page'] and first['bottom']<=s['y']<=first['bottom']+22 and abs(s['x']-first['x'])<5 and re.fullmatch(r'[\u4e00-\u9fff]{1,10}',s['text']):values.append(s['text'])
        fields['item_name']=''.join(values)
    if not fields['item_name']:
        m=re.search(r'(?:项目名称|Item)\s*[:：]\s*([^\n]+)',text,re.I)
        if m:fields['item_name']=m.group(1).strip()
    if fields['total']:
        try:fields['total']=format(Decimal(fields['total']),'.2f')
        except InvalidOperation:fields['total']=''
    return fields


def receipt_category(item):
    if re.search(r'住宿|酒店|宾馆|房费',item):return '住宿费'
    if re.search(r'交通|运输|客运|车票|机票|高铁|出租|taxi|transport',item,re.I):return '交通费'
    if re.search(r'云服务|技术服务|软件|API|订阅',item,re.I):return '软件服务费'
    if re.search(r'培训|会议|报名',item):return '培训会议费'
    if re.search(r'文具|办公|打印|墨盒',item):return '办公用品'
    if re.search(r'通信|通讯|话费',item):return '通讯费'
    return '其他' if item else None
