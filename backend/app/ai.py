import re

def parse_fields(description: str):
    """可运行的规则基线，不冒充LLM。夏一洲可在此接入模型并保留相同响应契约。"""
    amounts = sorted(set(re.findall(r'(\d+(?:\.\d{1,2})?)\s*元', description)))
    categories = [c for c in ['交通费', '办公用品'] if c in description]
    return {
        'mode': 'rule_baseline',
        'suggested_amount': amounts[0] if len(amounts) == 1 else None,
        'suggested_category': categories[0] if len(categories) == 1 else None,
        'requires_confirmation': True,
        'message': '规则提取建议，请核对字段后再提交。复杂语义解析和LLM接入待开发。',
    }
