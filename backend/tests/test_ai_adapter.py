"""字段提取适配层测试：不依赖网络、不启动服务，直接校验 ai-module 接入边界。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ai  # noqa: E402

SLIP = (
    '报销单\n提交人：张三\n部门：市场部\n费用类型：差旅\n金额：320\n'
    '费用日期：2026-11-05\n发票号：12345678\n发票抬头：北京中兴协力科技有限公司\n'
    '事由：客户现场往返高铁票'
)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """写入独立模型配置，避免依赖仓库内 ai-module/model_config.json 的当前通道。"""

    def write(content):
        path = tmp_path / 'model_config.json'
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding='utf-8')
        monkeypatch.setenv('AI_MODULE_CONFIG', str(path))
        return path

    monkeypatch.setenv('AI_MODULE_ENABLED', 'true')
    return write


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv('AI_MODULE_ENABLED', raising=False)
    result = ai.parse_fields('交通费320元')
    assert result['ai_enabled'] is False
    assert result['mode'] == 'rule_baseline'
    assert result['requires_confirmation'] is True
    assert set(result) >= {'mode', 'suggested_amount', 'suggested_category', 'requires_confirmation', 'message'}


def test_enabled_rule_channel_returns_structured_fields(config):
    config({'active': 'rule'})
    result = ai.parse_fields(SLIP)
    assert result['ai_enabled'] is True
    assert result['source'] == 'rule'
    assert result['mode'] == 'rule_baseline'
    # AI侧的“差旅”映射到业务侧的“交通费”，金额统一为两位小数字符串
    assert result['suggested_category'] == '交通费'
    assert result['suggested_amount'] == '320.00'
    assert result['fields']['submitter'] == '张三'
    assert result['fields']['expense_type'] == '差旅'
    assert 'missing_fields' in result and 'violations' in result


def test_category_mapping_covers_all_ai_types(config):
    config({'active': 'rule'})
    assert ai.CATEGORY_MAP == {'差旅': '交通费', '办公用品': '办公用品', '加班餐补': '其他', '业务招待': '其他'}
    result = ai.parse_fields('费用类型：业务招待\n金额：800\n事由：宴请客户')
    assert result['fields']['expense_type'] == '业务招待'
    assert result['suggested_category'] == '其他'


def test_free_text_still_uses_business_baseline(config):
    # ai-module 规则路径以键值对为主；自由文本必须由业务基线兜底，降级后不能更差
    config({'active': 'rule'})
    result = ai.parse_fields('客户现场往返交通费320元')
    assert result['suggested_amount'] == '320.00'
    assert result['suggested_category'] == '交通费'


def test_degraded_free_text_keeps_business_fields(config):
    config({'active': 'local_qwen',
            'providers': {'local_qwen': {'base_url': 'http://127.0.0.1:1/v1',
                                         'api_key': 'local', 'model': 'stub'}}})
    result = ai.parse_fields('客户现场往返交通费320元')
    assert result['fallback_event']['trigger'] == 'llm_preflight_unreachable'
    assert result['suggested_amount'] == '320.00'
    assert result['suggested_category'] == '交通费'


def test_unreachable_provider_degrades_without_raising(config):
    # 端口1不可达，预检应立即失败并降级，不等待模型通道的120秒超时
    config({'active': 'local_qwen',
            'providers': {'local_qwen': {'base_url': 'http://127.0.0.1:1/v1',
                                         'api_key': 'local', 'model': 'stub'}},
            'timeout_seconds': 120})
    result = ai.parse_fields(SLIP)
    assert result['ai_enabled'] is True
    assert result['mode'] == 'rule_baseline'
    assert result['fallback_event']['trigger'] == 'llm_preflight_unreachable'
    assert result['suggested_amount'] == '320.00'  # 仍能给出字段，只是来源为规则


def test_unreadable_config_degrades(config):
    config('{不是合法JSON')
    result = ai.parse_fields(SLIP)
    assert result['ai_enabled'] is True
    assert result['fallback_event']['trigger'].startswith('ai_config_unreadable')
    assert result['mode'] == 'rule_baseline'


def test_missing_config_file_degrades(monkeypatch, tmp_path):
    monkeypatch.setenv('AI_MODULE_ENABLED', 'true')
    monkeypatch.setenv('AI_MODULE_CONFIG', str(tmp_path / 'not-exists.json'))
    result = ai.parse_fields(SLIP)
    assert result['fallback_event']['trigger'].startswith('ai_config_unreadable')
    assert result['message'] == ai.MESSAGE_RULE


def test_baseline_never_guesses():
    # 多条金额或未识别类别时保持 None，不猜测
    result = ai.baseline_fields('交通费300元，另有餐饮100元')
    assert result['suggested_amount'] is None
    assert result['suggested_category'] == '交通费'


def test_rule_only_config_file_is_valid():
    data = json.loads(ai.RULE_ONLY_CONFIG.read_text(encoding='utf-8'))
    assert data == {'active': 'rule'}
