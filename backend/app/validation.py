"""业务金额、文本候选与费用映射的确定性校验。"""
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

MONEY = re.compile(r'[0-9]+(?:\.[0-9]{1,2})?\Z')
NUMBER = re.compile(r'(?<![0-9A-Za-z_.,+\-])([+\-]?\d[\d,]*(?:\.\d+)?(?:[eE][+\-]?\d+)?)(?![0-9A-Za-z_.,])')


def money_value(value):
    if isinstance(value, Decimal):
        text = format(value, 'f')
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise ValueError('金额须为普通十进制字符串')
    if not MONEY.fullmatch(text):
        raise ValueError('金额须为普通十进制字符串，最多两位小数，不接受符号或指数记法')
    amount = Decimal(text)
    if not amount.is_finite() or not 0 < amount <= 5000:
        raise ValueError('金额须大于0且不超过5000元')
    return amount


def description_value(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
        raise ValueError('说明去除首尾空白后须为1至500字符')
    return value.strip()


def date_value(value):
    if value is None or value > datetime.now(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('费用日期不能晚于今天或为空')
    return value


def date_input(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期须使用YYYY-MM-DD格式')
    return value


CATEGORIES = ('交通费', '住宿费', '业务招待费', '办公用品', '通讯费',
              '培训会议费', '软件服务费', '出差餐补', '其他')
# demo-v1 时代 AI 枚举旧名 → 企业版九类语义就近归入
_LEGACY_CATEGORY = {'差旅': '交通费', '加班餐补': '出差餐补', '业务招待': '业务招待费'}
# 类别关键词信号（与 ai-module/extractor._TYPE_RULES 口径一致；
# 泛差旅词不作为信号，仅在无具体信号时归"其他"）
_CATEGORY_SIGNALS = (
    (re.compile(r'住宿|酒店|宾馆|房费|民宿'), '住宿费'),
    (re.compile(r'餐补|出差餐|餐饮补贴'), '出差餐补'),
    (re.compile(r'招待|宴请|接待|餐饮|餐费'), '业务招待费'),
    (re.compile(r'话费|通讯|流量|宽带'), '通讯费'),
    (re.compile(r'培训|会议|报名|峰会|研讨'), '培训会议费'),
    (re.compile(r'软件|云服务|API|订阅|技术服务'), '软件服务费'),
    (re.compile(r'办公|采购|打印|文具|墨盒|复印|硒鼓|白板'), '办公用品'),
    (re.compile(r'交通|车票|机票|高铁|火车|地铁|公交|出租|打车|网约|客运|运输|联程|轮渡'), '交通费'),
)


def category_for(description, expense_type=None):
    """费用类别判定：提取值（九类）优先，旧名就近归入，否则按说明关键词推断。

    多类别信号混合或仅有泛差旅词时返回"其他"（转人工核对，不猜测）。
    """
    if expense_type in CATEGORIES:
        return expense_type
    if expense_type in ('加班餐补', '业务招待'):
        # 旧名但语义明确，直接归入新类别
        return _LEGACY_CATEGORY[expense_type]
    # expense_type 为 None 或含糊旧名（'差旅'）时，交由说明信号细分
    specific = {name for pattern, name in _CATEGORY_SIGNALS if pattern.search(description)}
    if len(specific) > 1:
        return '其他'
    if len(specific) == 1:
        return next(iter(specific))
    if re.search(r'差旅|出差', description) or expense_type == '差旅':
        # 仅泛差旅词（或含糊旧名'差旅'）且无具体信号：无法细分交通/住宿，转人工
        return '其他'
    return None


def amount_candidates(description):
    candidates = []
    normalized = description.translate(str.maketrans({'−': '-', '－': '-', '﹣': '-'}))
    for match in NUMBER.finditer(normalized):
        prefix, suffix = normalized[:match.start()], normalized[match.end():]
        if not (re.match(r'\s*(?:元|人民币|CNY)', suffix, re.I)
                or re.search(r'[¥￥]\s*$', prefix)
                or re.search(r'(?:金额|合计|总计|总额)\s*[:：]?\s*$', prefix)):
            continue
        raw = match.group(1)
        item = {'raw': description[match.start():match.end()], 'amount': None, 'valid': False}
        try:
            if ',' in raw and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', raw):
                raise ValueError('千分位格式不合法')
            item.update(amount=format(money_value(raw.replace(',', '')), '.2f'), valid=True)
        except ValueError as exc:
            item['reason'] = str(exc)
        candidates.append(item)
    return candidates


def input_conflicts(description, payload, snapshot):
    candidates = amount_candidates(description)
    conflicts = []
    if len(candidates) > 1:
        conflicts.append({'field': 'amount', 'reason': '说明含多个金额，请明确确认申报总额'})
    elif candidates:
        candidate = candidates[0]
        if not candidate['valid']:
            conflicts.append({'field': 'amount', 'reason': '说明中的金额格式或取值需要核对'})
        elif candidate['amount'] != payload['amount']:
            conflicts.append({'field': 'amount', 'reason': '说明金额与申报金额不一致'})
    model_amount = snapshot.get('suggested_amount')
    if model_amount and model_amount != payload['amount']:
        conflicts.append({'field': 'amount', 'reason': '提取建议金额与申报金额不一致'})
    categories = {category_for(description), snapshot.get('suggested_category')}
    if any(category and category != payload['category'] for category in categories):
        conflicts.append({'field': 'category', 'reason': '说明或提取建议类别与申报类别不一致'})
    return conflicts
