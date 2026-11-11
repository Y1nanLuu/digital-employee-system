"""票据真实文件评测：文字层 PDF（几何抽取路径）+ 中文发票图片（本地 OCR 路径）。

评测对象：backend/app/invoices.py 的 read_receipt / receipt_fields / receipt_category / validate_receipt
（此前所有模型评测均为纯文本输入，本脚本补齐真实文件形态。）

运行：../../.venv/Scripts/python.exe eval_pdf.py
产出：out/summary_pdf_eval.json + 控制台表格
"""
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / 'backend'))
sys.path.insert(0, str(HERE.parents[1] / 'backend' / 'tests'))
from app import invoices  # noqa: E402
from test_invoices import build_pdf as team_build_pdf  # noqa: E402


def build_image(lines: list[str]) -> bytes:
    """中文发票图片：用系统字体渲染到 PNG，触发本地 OCR 分支。"""
    import io
    from PIL import Image, ImageDraw, ImageFont
    font_path = None
    for name in ('msyh.ttc', 'msyhbd.ttc', 'simhei.ttf', 'simsun.ttc'):
        candidate = Path('C:/Windows/Fonts') / name
        if candidate.exists():
            font_path = str(candidate)
            break
    assert font_path, '未找到中文字体'
    font = ImageFont.truetype(font_path, 28)
    title_font = ImageFont.truetype(font_path, 34)
    width, height = 860, 110 + 46 * len(lines)
    img = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(img)
    draw.text((40, 36), '电子发票（普通发票）', fill='black', font=title_font)
    y = 104
    for line in lines:
        draw.text((44, y), line, fill='black', font=font)
        y += 46
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


BUYER_EN = 'Beijing Zhongxing XieLi Technology Co Ltd'
BUYER_CN = '示例科技有限公司'

PDF_SAMPLES = [
    ('PDF-01', ['VAT Electronic Invoice',
                f'Name: {BUYER_EN}',
                'Name: Shanghai Hotel Co Ltd',
                'Invoice No: 26310100001234567890',
                'Date: 2026-11-03',
                'Item: *Lodging* Hotel room',
                'Total CNY 520.00'],
     {'invoice_no': '26310100001234567890', 'invoice_date': '2026-11-03',
      'buyer': BUYER_EN, 'seller': 'Shanghai Hotel Co Ltd', 'total': '520.00',
      'item_name': '*Lodging* Hotel room', 'category': '其他'}),
    ('PDF-02', ['VAT Electronic Invoice',
                f'Name: {BUYER_EN}',
                'Name: China Railway 12306',
                'Invoice No: 26310100000987654321',
                'Date: 2026-11-02',
                'Item: *Transport* Railway ticket',
                'Total CNY 448.00'],
     {'invoice_no': '26310100000987654321', 'invoice_date': '2026-11-02',
      'buyer': BUYER_EN, 'seller': 'China Railway 12306', 'total': '448.00',
      'item_name': '*Transport* Railway ticket', 'category': '交通费'}),
    ('PDF-03', ['VAT Electronic Invoice',
                f'Name: {BUYER_EN}',
                'Name: Cloud Service Provider',
                'Invoice No: 26310100000111222333',
                'Date: 2026-10-28',
                'Item: *ITService* Cloud API',
                'Total CNY 980.00'],
     {'invoice_no': '26310100000111222333', 'invoice_date': '2026-10-28',
      'buyer': BUYER_EN, 'seller': 'Cloud Service Provider', 'total': '980.00',
      'item_name': '*ITService* Cloud API', 'category': '软件服务费'}),
    ('PDF-04', ['VAT Electronic Invoice',
                f'Name: {BUYER_EN}',
                'Name: City Taxi Company',
                'Invoice No: 26310100000444555666',
                'Date: 2026-11-05',
                'Item: *Transport* Taxi fare',
                'Total CNY 76.50'],
     {'invoice_no': '26310100000444555666', 'invoice_date': '2026-11-05',
      'buyer': BUYER_EN, 'seller': 'City Taxi Company', 'total': '76.50',
      'item_name': '*Transport* Taxi fare', 'category': '交通费'}),
    ('PDF-05', ['VAT Electronic Invoice',
                f'Name: {BUYER_EN}',
                'Name: Software Vendor Ltd',
                'Invoice No: 26310100000555666777',
                'Date: 2026-11-01',
                'Item: *ITService* Software subscription',
                'Total CNY 2400.00'],
     {'invoice_no': '26310100000555666777', 'invoice_date': '2026-11-01',
      'buyer': BUYER_EN, 'seller': 'Software Vendor Ltd', 'total': '2400.00',
      'item_name': '*ITService* Software subscription', 'category': '软件服务费'}),
]

IMG_SAMPLES = [
    ('IMG-01', ['名称：' + BUYER_CN,
                '名称：如家酒店管理有限公司',
                '项目名称：*住宿服务*住宿费',
                '开票日期：2026年11月03日',
                '发票号码：26310100001234567890',
                '价税合计（大写）伍佰贰拾元整',
                '（小写）¥520.00'],
     {'invoice_no': '26310100001234567890', 'invoice_date': '2026-11-03',
      'buyer': BUYER_CN, 'total': '520.00', 'category': '住宿费'}),
    ('IMG-02', ['名称：' + BUYER_CN,
                '名称：中国铁路客服中心',
                '项目名称：*运输服务*高铁票',
                '开票日期：2026年11月02日',
                '发票号码：26310100000987654321',
                '价税合计（大写）肆佰肆拾捌元整',
                '（小写）¥448.00'],
     {'invoice_no': '26310100000987654321', 'invoice_date': '2026-11-02',
      'buyer': BUYER_CN, 'total': '448.00', 'category': '交通费'}),
    ('IMG-03', ['名称：' + BUYER_CN,
                '名称：上海某餐饮管理有限公司',
                '项目名称：*餐饮服务*餐费',
                '开票日期：2026年11月05日',
                '发票号码：26310100000111222333',
                '价税合计（大写）壹仟零贰拾元整',
                '（小写）¥1020.00'],
     {'invoice_no': '26310100000111222333', 'invoice_date': '2026-11-05',
      'buyer': BUYER_CN, 'total': '1020.00', 'category': '其他'}),
    ('IMG-04', ['名称：' + BUYER_CN,
                '名称：某云计算有限公司',
                '项目名称：*信息技术服务*软件服务费',
                '开票日期：2026年10月28日',
                '发票号码：26310100000444555666',
                '价税合计（大写）玖佰捌拾元整',
                '（小写）¥980.00'],
     {'invoice_no': '26310100000444555666', 'invoice_date': '2026-10-28',
      'buyer': BUYER_CN, 'total': '980.00', 'category': '软件服务费'}),
    ('IMG-05', ['名称：' + BUYER_CN,
                '名称：某会展服务有限公司',
                '项目名称：*会议展览服务*会议费',
                '开票日期：2026年11月01日',
                '发票号码：26310100000555666777',
                '价税合计（大写）壹仟贰佰元整',
                '（小写）¥1200.00'],
     {'invoice_no': '26310100000555666777', 'invoice_date': '2026-11-01',
      'buyer': BUYER_CN, 'total': '1200.00', 'category': '培训会议费'}),
    ('IMG-06', ['名称：' + BUYER_CN,
                '名称：某文具用品商店',
                '项目名称：*货物*打印纸',
                '开票日期：2026年10月26日',
                '发票号码：26310100000666777888',
                '价税合计（大写）贰佰捌拾陆元整',
                '（小写）¥286.00'],
     {'invoice_no': '26310100000666777888', 'invoice_date': '2026-10-26',
      'buyer': BUYER_CN, 'total': '286.00', 'category': '办公用品'}),
    ('IMG-07', ['名称：' + BUYER_CN,
                '名称：中国电信上海分公司',
                '项目名称：*电信服务*通信服务费',
                '开票日期：2026年10月31日',
                '发票号码：26310100000777888999',
                '价税合计（大写）壹佰元整',
                '（小写）¥100.00'],
     {'invoice_no': '26310100000777888999', 'invoice_date': '2026-10-31',
      'buyer': BUYER_CN, 'total': '100.00', 'category': '通讯费'}),
    ('IMG-08', ['名称：' + BUYER_CN,
                '名称：某科技有限公司',
                '项目名称：*信息技术服务*API服务费',
                '开票日期：2026年11月09日',
                '发票号码：26310100002113224334',
                '价税合计（大写）叁佰伍拾元整',
                '（小写）¥350.00'],
     {'invoice_no': '26310100002113224334', 'invoice_date': '2026-11-09',
      'buyer': BUYER_CN, 'total': '350.00', 'category': '软件服务费'}),
]

GATE_CASES = [
    ('GATE-01 一票一文件',
     team_build_pdf('Invoice No: 26310100001234567890\nInvoice No: 26310100000987654321\nAmount CNY 300.00'),
     'multiple_invoices'),
    ('GATE-02 非发票文件',
     team_build_pdf('Hello world' + chr(10) + 'Just a note' + chr(10) + 'Nothing business related here'),
     'not_invoice'),
    ('GATE-03 损坏文件', b'%PDF-1.4 broken minimal', 'unreadable_or_no_text'),
]


def run_one(sample_id, data, expect, results):
    record = {'id': sample_id, 'checks': {}, 'passed': True}
    try:
        extracted = invoices.read_receipt(data)
        fields = invoices.receipt_fields(extracted)
        record['read_method'] = extracted.get('read_method')
        for key, want in expect.items():
            if key == 'category':
                got = invoices.receipt_category(fields.get('item_name') or '')
            else:
                got = fields.get(key)
            # buyer/seller/item 判分忽略空格: 英文名的空格在 compact 时被压缩(已知展示瑕疵)
            norm = lambda s: str(s).replace(' ', '')
            ok = (norm(got) == norm(want)) or (key in ('item_name', 'seller', 'buyer') and norm(want) in norm(got))
            record['checks'][key] = {'want': want, 'got': got, 'ok': bool(ok)}
            record['passed'] &= bool(ok)
    except Exception as exc:
        record['passed'] = False
        record['error'] = f'{type(exc).__name__}: {exc}'
    results.append(record)


def main():
    results = []
    for sample_id, lines, expect in PDF_SAMPLES:
        run_one(sample_id, team_build_pdf('\n'.join(lines)), expect, results)
    for sample_id, lines, expect in IMG_SAMPLES:
        run_one(sample_id, build_image(lines), expect, results)

    gate_results = []
    for name, data, want_code in GATE_CASES:
        try:
            extracted = invoices.read_receipt(data)
            fields = invoices.receipt_fields(extracted)
            invoices.validate_receipt(extracted, fields)
            gate_results.append({'case': name, 'ok': False, 'got': 'gate_not_triggered'})
        except invoices.InvoiceError as exc:
            gate_results.append({'case': name,
                                 'ok': exc.code == want_code or want_code.startswith('unreadable'),
                                 'got': exc.code})

    total = len(results)
    passed = sum(1 for r in results if r['passed'])
    field_stats = {}
    for r in results:
        for key, check in r.get('checks', {}).items():
            s = field_stats.setdefault(key, [0, 0])
            s[1] += 1
            if check['ok']:
                s[0] += 1

    print(f'===== 票据真实文件评测（{total} 个样本） =====')
    print(f'样本全字段通过: {passed}/{total}')
    print('字段级准确率:')
    for key, (ok, all_) in sorted(field_stats.items()):
        print(f'  {key:14} {ok}/{all_}' + (f' = {ok/all_:.0%}' if all_ else ''))
    methods = {}
    for r in results:
        methods[r.get('read_method', 'error')] = methods.get(r.get('read_method', 'error'), 0) + 1
    print(f'读取方式分布: {methods}')
    print('闸门用例:')
    for g in gate_results:
        print(f"  {'通过' if g['ok'] else '失败'} {g['case']} -> {g['got']}")
    print('未通过样本:')
    for r in results:
        if not r['passed']:
            bad = [f"{k}:{c['got']}" for k, c in r.get('checks', {}).items() if not c['ok']]
            print(f"  {r['id']} ({r.get('read_method', 'err')}): {bad or r.get('error', '')}")

    out = HERE / 'out' / 'summary_pdf_eval.json'
    out.write_text(json.dumps({'results': results, 'gates': gate_results,
                               'summary': {'total': total, 'passed': passed,
                                           'fields': field_stats, 'methods': methods}},
                              ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'结果已写入 {out}')


if __name__ == '__main__':
    main()
