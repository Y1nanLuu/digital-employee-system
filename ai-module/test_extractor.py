"""
extractor.py 单元测试（纯标准库，可直接 python test_extractor.py 运行，也兼容 pytest）

覆盖三类风险：
  1. 规则提取的金额/日期/类型归一化（提取错误的根源）
  2. LLM 禁用/不可达时的降级行为与降级事件记录
  3. LLM 输出解析的容错（围栏 JSON、null 字段、未知字段、金额为字符串）
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extractor import (LLMExtractor, RuleExtractor, ExtractorError, extract,
                       load_config, merge_with_rule)

ROOT = Path(__file__).parent
SLIP1 = """报销单
提交人：张伟
部门：研发部
费用类型：差旅
金额：¥3,275.00
费用日期：2026-10-12 至 2026-10-15
发票号：26310100001234567890
发票抬头：北京中兴协力科技有限公司
事由：赴上海客户现场部署与验收
"""


def _tmp_config(cfg: dict) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(cfg, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def _load_samples() -> dict[str, str]:
    """从 samples/报销单样例.md 解析出 样例名 → 文本。"""
    text = (ROOT / "samples" / "报销单样例.md").read_text(encoding="utf-8")
    import re
    pairs = re.findall(r"## (样例\d+)[^\n]*\n+```\n(.*?)\n```", text, re.S)
    return {name: body for name, body in pairs}


# ---- 规则提取 -------------------------------------------------------------

def test_rule_extract_complete_slip():
    r = RuleExtractor().extract(SLIP1)
    assert r["missing_fields"] == [], f"应全部提取，缺失: {r['missing_fields']}"
    f = r["fields"]
    assert f["submitter"] == "张伟"
    assert f["department"] == "研发部"
    assert f["expense_type"] == "差旅"
    assert f["amount"] == 3275.0
    assert f["expense_date"] == "2026-10-12"
    assert f["invoice_no"] == "26310100001234567890"
    assert f["invoice_title"] == "北京中兴协力科技有限公司"


def test_rule_amount_formats():
    for raw, want in [("¥3,275.00", 3275.0), ("6,820.50元", 6820.5), ("70元", 70.0)]:
        got = RuleExtractor().extract(f"金额：{raw}")["fields"].get("amount")
        assert got == want, f"{raw} → {got}, 期望 {want}"


def test_rule_cn_date_format():
    got = RuleExtractor().extract("费用日期：2026年10月20日至2026年10月25日")["fields"]["expense_date"]
    assert got == "2026-10-20", got


def test_rule_missing_fields_detected():
    r = RuleExtractor().extract("报销单\n提交人：赵敏\n金额：70元\n事由：加班餐补")
    assert "invoice_no" in r["missing_fields"]
    assert "department" in r["missing_fields"]


def test_rule_expense_type_mapping():
    for text, want in [("费用类型：业务招待", "业务招待"),
                       ("费用类型：加班餐补", "加班餐补"),
                       ("费用类型：办公用品", "办公用品")]:
        got = RuleExtractor().extract(text)["fields"]["expense_type"]
        assert got == want, f"{text} → {got}"


# ---- 降级路径 -------------------------------------------------------------

def test_llm_disabled_uses_rule_without_fallback_event():
    cfg = _tmp_config({"enabled": False})
    r = extract(SLIP1, cfg)
    assert r["source"] == "rule"
    assert r["fallback_event"] is None  # 计划内走规则，不算降级事件


def test_llm_unreachable_falls_back_and_logs_event():
    cfg = _tmp_config({
        "enabled": True,
        "base_url": "http://127.0.0.1:9/v1",  # 保留端口，必然连接失败
        "api_key": "test",
        "model": "test-model",
        "timeout_seconds": 2,
    })
    r = extract(SLIP1, cfg)
    assert r["source"] == "rule"
    assert r["fallback_event"] is not None, "降级必须留痕"
    assert r["fallback_event"]["trigger"].startswith("llm_")
    assert r["fallback_event"]["fallback_to"] == "rule"
    assert r["fallback_event"]["llm_model"] == "test-model"
    assert r["missing_fields"] == [], "降级后提取能力不应受损"


# ---- LLM 输出解析 ---------------------------------------------------------

def test_llm_output_parses_fenced_json():
    content = '```json\n{"submitter": "张伟", "amount": "3,275.00", "invoice_no": null}\n```'
    f, leaked = LLMExtractor._parse_fields(content)
    assert f["submitter"] == "张伟"
    assert f["amount"] == 3275.0
    assert f["invoice_no"] is None
    assert leaked == []


def test_llm_output_rejects_unknown_fields():
    try:
        LLMExtractor._parse_fields('{"submitter": "x", "hacked": 1}')
    except ExtractorError as e:
        assert "unknown_fields" in str(e)
    else:
        raise AssertionError("未知字段应被拒绝（Schema 校验）")


def test_llm_output_rejects_non_json():
    for bad in ["好的，提取结果如下：...", "[]", '"just a string"',
                '[{"a":1},{"b":2}]']:
        try:
            LLMExtractor._parse_fields(bad)
        except ExtractorError:
            continue
        raise AssertionError(f"应拒绝非法输出: {bad[:20]}")


def test_llm_output_unwraps_single_object_array():
    """小模型常见：输出 [{...}] 而非 {...}，应取首个对象。"""
    content = '```json\n[{"submitter": "张伟", "amount": 3275.0}]\n```'
    f, _ = LLMExtractor._parse_fields(content)
    assert f["submitter"] == "张伟"


def test_llm_output_strips_currency_from_amount():
    f, _ = LLMExtractor._parse_fields('{"amount": "¥3,275.00"}')
    assert f["amount"] == 3275.0


def test_llm_output_normalizes_date_range():
    f, _ = LLMExtractor._parse_fields('{"expense_date": "2026-10-12 至 2026-10-15"}')
    assert f["expense_date"] == "2026-10-12"


def test_llm_output_flags_example_leak():
    """模型照抄 few-shot 示例值 → 标记泄漏字段（不再整输出废弃，交字段级仲裁）。"""
    f, leaked = LLMExtractor._parse_fields(
        '{"submitter": "张伟", "invoice_title": "示例公司"}')
    assert leaked == ["invoice_title"]
    assert f["submitter"] == "张伟"


def test_merge_with_rule_patches_leaked_fields():
    """字段级仲裁：泄漏字段被规则结果替换，正常字段保留 LLM 值。"""
    llm_fields = {"submitter": "李娜", "amount": 6820.5,
                  "invoice_title": "示例公司", "description": "示例事由"}
    merged, patched = merge_with_rule(llm_fields, SLIP1)
    assert merged["submitter"] == "李娜"          # LLM 正确值保留
    assert merged["amount"] == 6820.5
    assert merged["invoice_title"] == "北京中兴协力科技有限公司"  # 泄漏→规则补齐
    assert set(patched) >= {"invoice_title"}


def test_merge_with_rule_nulls_unpatchable_leak():
    """泄漏且规则亦无法补齐（如餐补单没有发票）→ 字段必须清空，哨兵值不得残留。"""
    slip4 = """报销单
提交人：赵敏
部门：财务部
费用类型：加班餐补
金额：70元
费用日期：2026-10-30
事由：加班餐补两次
"""
    llm_fields = {"submitter": "赵敏", "amount": 70,
                  "invoice_no": "10000000000000000000",   # 哨兵泄漏
                  "invoice_title": "示例公司"}              # 哨兵泄漏
    merged, _ = merge_with_rule(llm_fields, slip4)
    assert "invoice_no" not in merged, "泄漏哨兵值不得残留在输出里"
    assert "invoice_title" not in merged
    assert merged["submitter"] == "赵敏"          # 正常字段不受影响


# ---- 端到端样例 -----------------------------------------------------------

def test_all_sample_slips_meet_expectations():
    samples = _load_samples()
    assert len(samples) == 5, f"样例数应为5，实际 {len(samples)}"
    expectations = {
        "样例1": lambda r: r["missing_fields"] == [] and r["fields"]["amount"] == 3275.0,
        "样例2": lambda r: r["fields"]["amount"] == 6820.5,
        "样例3": lambda r: r["fields"]["invoice_title"] == "王强",
        "样例4": lambda r: "invoice_no" in r["missing_fields"],
        "样例5": lambda r: r["fields"]["expense_type"] == "业务招待",
    }
    for name, body in samples.items():
        r = RuleExtractor().extract(body)
        assert expectations[name](r), f"{name} 未达预期: {r}"


if __name__ == "__main__":
    tests = [(n, fn) for n, fn in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
