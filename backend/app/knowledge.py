import json
from pathlib import Path

RULES = json.loads((Path(__file__).parent / 'data/rules.json').read_text(encoding='utf-8'))

def retrieve(category: str):
    """首版按费用类别检索。向量检索可替换此接口，但须保留条款溯源。"""
    if category not in ['交通费', '办公用品']:
        return []
    return RULES
