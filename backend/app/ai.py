"""字段理解接口：默认规则基线，可通过环境变量接入 ai-module 的模型提取。

集成边界（D2）：ai-module 为独立模块，`extractor.extract()` 只依赖标准库，输出
fields/missing_fields/source/model/patched_fields/fallback_event/violations。
本适配层只做四件事：启用开关、模型可达性预检、业务枚举映射、响应契约保持与降级留痕。

业务判定不在此处：是否超过审批阈值、是否有制度依据、是否转人工，仍由 services.py 决定。
"""
import json
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_MODULE_DIR = REPO_ROOT / 'ai-module'
DEFAULT_CONFIG = AI_MODULE_DIR / 'model_config.json'
# 预检失败时使用的纯规则配置，避免按模型通道默认的120秒超时阻塞 /api/parse
RULE_ONLY_CONFIG = Path(__file__).parent / 'data' / 'ai_rule_only.json'

# AI 侧费用类型 → 业务侧类别（业务枚举仅 交通费/办公用品/其他，映射关系待产品组确认）
CATEGORY_MAP = {
    '差旅': '交通费',
    '办公用品': '办公用品',
    '加班餐补': '其他',
    '业务招待': '其他',
}
_TRUE = {'1', 'true', 'yes', 'on'}

MESSAGE_RULE = '规则提取建议，请核对字段后再提交。复杂语义解析和LLM接入待开发。'
MESSAGE_LLM = '模型提取建议，请核对金额、类别与日期后再提交。'
MESSAGE_DEGRADED = '模型不可用，已降级为规则提取，请核对字段后再提交。'


def baseline_fields(description):
    """可运行的规则基线，不冒充LLM。扩展模型能力放在 ai-module，响应契约保持不变。"""
    amounts = sorted(set(re.findall(r'(\d+(?:\.\d{1,2})?)\s*元', description)))
    categories = [c for c in ['交通费', '办公用品'] if c in description]
    return {
        'mode': 'rule_baseline',
        'suggested_amount': amounts[0] if len(amounts) == 1 else None,
        'suggested_category': categories[0] if len(categories) == 1 else None,
        'requires_confirmation': True,
        'message': MESSAGE_RULE,
    }


def _flag(name):
    return os.getenv(name, '').strip().lower() in _TRUE


def _provider_reachable(config, timeout=0.4):
    """本地模型通道做快速端口预检；远程通道不预检，由模型通道自身超时控制。"""
    providers = config.get('providers')
    if not isinstance(providers, dict):
        return True
    provider = providers.get(config.get('active')) or {}
    parts = urlsplit(str(provider.get('base_url', '')))
    if parts.scheme == 'https':
        return True
    host, port = parts.hostname or '127.0.0.1', parts.port or 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _degraded(baseline, trigger):
    return {**baseline, 'ai_enabled': True,
            'fallback_event': {'trigger': trigger, 'fallback_to': 'rule_baseline'}}


def _adapt(baseline, result, preflight_trigger):
    fields = result.get('fields') or {}
    amount = fields.get('amount')
    # 金额一律以两位小数字符串对外，避免前端与业务端出现浮点比较
    suggested_amount = f'{amount:.2f}' if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None
    expense_type = fields.get('expense_type')
    source = result.get('source') or 'rule'
    fallback_event = result.get('fallback_event')
    if preflight_trigger and not fallback_event:
        fallback_event = {'trigger': preflight_trigger, 'fallback_to': 'rule_baseline'}
    if source == 'llm':
        message, mode = MESSAGE_LLM, 'llm'
    elif fallback_event:
        message, mode = MESSAGE_DEGRADED, 'rule_baseline'
    else:
        message, mode = MESSAGE_RULE, 'rule_baseline'

    # 规则路径以键值对为主，自由文本（如“交通费320元”）提取不到业务字段。
    # 此时用业务侧规则兜底，保证降级后不比未接入更差。
    suggested_category = CATEGORY_MAP.get(expense_type) if expense_type else None
    if suggested_amount is None:
        fallback_amount = baseline.get('suggested_amount')
        if fallback_amount:
            suggested_amount = f'{float(fallback_amount):.2f}'
    if suggested_category is None:
        suggested_category = baseline.get('suggested_category')

    return {
        **baseline,
        'mode': mode,
        'suggested_amount': suggested_amount,
        'suggested_category': suggested_category,
        'ai_enabled': True,
        'source': source,
        'model': result.get('model'),
        'fields': fields,
        'missing_fields': result.get('missing_fields') or [],
        'patched_fields': result.get('patched_fields'),
        'violations': result.get('violations') or [],
        'fallback_event': fallback_event,
        'message': message,
    }


def parse_fields(description: str):
    """字段提取建议。任何异常都不抛出：最差退化为规则基线，保证提交链路不受影响。"""
    baseline = baseline_fields(description)
    if not _flag('AI_MODULE_ENABLED'):
        return {**baseline, 'ai_enabled': False}

    config_path = Path(os.getenv('AI_MODULE_CONFIG') or DEFAULT_CONFIG)
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return _degraded(baseline, f'ai_config_unreadable:{type(exc).__name__}')

    preflight_trigger = None
    effective = config_path
    if not _provider_reachable(config):
        preflight_trigger = 'llm_preflight_unreachable'
        effective = RULE_ONLY_CONFIG

    try:
        if str(AI_MODULE_DIR) not in sys.path:
            sys.path.insert(0, str(AI_MODULE_DIR))
        from extractor import extract as ai_extract
        result = ai_extract(description, effective)
    except Exception as exc:  # ai-module 缺失或接口变动时不得影响业务链路
        return _degraded(baseline, f'ai_module_unavailable:{type(exc).__name__}')
    return _adapt(baseline, result, preflight_trigger)
