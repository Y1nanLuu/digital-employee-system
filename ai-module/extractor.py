"""
数字员工 · 字段提取模块（成员6 · AI与知识检索线）

双轨设计：
  - LLMExtractor：OpenAI 兼容接口适配层（读 model_config.json），输出结构化 JSON
  - RuleExtractor：规则提取（正则 + 关键词），离线可用，同时充当 LLM 不可用时的降级路径

对外统一入口 extract()：LLM 成功 → source="llm"；LLM 失败 → 自动降级到规则并记录降级事件。
两条路径输出同构字段（见 FIELD_SCHEMA），下游（初审意见生成）无感知切换。

依赖：仅 Python 标准库。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 字段契约（输出 JSON Schema 的简化声明，LLM prompt 与规则提取共用）
# ---------------------------------------------------------------------------

FIELD_SCHEMA: dict[str, str] = {
    "submitter": "提交人，字符串",
    "department": "部门，字符串",
    "expense_type": "费用类型，枚举：差旅|加班餐补|办公用品|业务招待",
    "amount": "申报总额（含税），数值（元），不含千分位与货币符号",
    "expense_date": "费用发生日期，YYYY-MM-DD（区间取起始日）",
    "invoice_no": "发票号，字符串，8~20位数字/字母",
    "invoice_title": "发票抬头，字符串",
    "description": "事由，字符串",
}

REQUIRED_FIELDS = ["submitter", "department", "expense_type", "amount",
                   "expense_date", "invoice_no", "invoice_title", "description"]

COMPANY_TITLE = "北京中兴协力科技有限公司"  # 发票合规校验用（INV-001）


class ExtractionResult(dict):
    """提取结果：fields / missing_fields / source / fallback_event。"""

    __getattr__ = dict.get  # 便于 r.fields 式访问


# ---------------------------------------------------------------------------
# 规则提取（降级路径本体）
# ---------------------------------------------------------------------------

# 费用类型关键词 → 枚举值（顺序即优先级）
_TYPE_RULES = [
    (("差旅", "出差", "交通", "住宿", "机票", "高铁"), "差旅"),
    (("餐补", "加班餐", "餐饮补贴"), "加班餐补"),
    (("招待", "宴请", "接待"), "业务招待"),
    (("办公", "采购", "打印", "文具", "墨盒"), "办公用品"),
]

_AMOUNT_RE = re.compile(
    r"(?:¥|￥|人民币)?\s*([1-9]\d{0,2}(?:,\d{3})*(?:\.\d{1,2})?|[1-9]\d*(?:\.\d{1,2})?)\s*(?:元|块)?"
)
_DATE_RE = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?")
_INVOICE_NO_RE = re.compile(r"(\d{8,20})")
_KV_RE = re.compile(r"^\s*([一-龥A-Za-z]{2,6})\s*[:：]\s*(.+?)\s*$", re.M)


def _norm_amount(raw: str) -> float | None:
    m = _AMOUNT_RE.search(raw.replace("，", ""))
    if not m:
        return None
    try:
        return round(float(m.group(1).replace(",", "")), 2)
    except ValueError:
        return None


def _norm_date(raw: str) -> str | None:
    m = _DATE_RE.search(raw)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return _dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def _norm_type(raw: str) -> str | None:
    for keywords, label in _TYPE_RULES:
        if any(k in raw for k in keywords):
            return label
    return None


# 字段名 → (键值对候选名, 归一化函数)
_FIELD_PARSERS = {
    "submitter":      (("提交人", "报销人", "申请人"), str.strip),
    "department":     (("部门",), str.strip),
    "expense_type":   (("费用类型", "类型"), _norm_type),
    "amount":         (("金额", "总额", "报销金额"), _norm_amount),
    "expense_date":   (("费用日期", "日期", "发生日期"), _norm_date),
    "invoice_no":     (("发票号", "发票号码"), str.strip),
    "invoice_title":  (("发票抬头", "抬头"), str.strip),
    "description":    (("事由", "说明", "用途"), str.strip),
}


class RuleExtractor:
    """基于键值对 + 正则的离线提取器。LLM 不可用时的降级路径。"""

    name = "rule"

    def extract(self, text: str) -> dict:
        kv = {}
        for key, value in _KV_RE.findall(text):
            kv.setdefault(key, value)
        fields = {}
        for field, (aliases, normalizer) in _FIELD_PARSERS.items():
            for alias in aliases:
                if alias in kv:
                    value = normalizer(kv[alias])
                    if value:
                        fields[field] = value
                    break
        # 发票号兜底：无键值对时取文本中最长的纯数字串（≥8位）
        if "invoice_no" not in fields:
            m = _INVOICE_NO_RE.search(text.replace(" ", ""))
            if m:
                fields["invoice_no"] = m.group(1)
        missing = [f for f in REQUIRED_FIELDS if f not in fields]
        return {"fields": fields, "missing_fields": missing}


# ---------------------------------------------------------------------------
# LLM 提取（OpenAI 兼容接口适配层）
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """你是费用报销数字员工的字段提取器。从下面的报销单文本中提取字段，只输出一个 JSON 对象，不要输出任何其他文字。

字段定义：
{schema}

输出规则（必须遵守）：
- 直接以 {{ 开头、以 }} 结尾，禁止用数组 [] 包裹
- amount 只输出数字，去掉货币符号（¥/元）和千分位逗号，如 3275.00
- expense_date 只输出一个日期，格式 YYYY-MM-DD，日期区间只取起始日
- expense_type 必须是枚举值之一：差旅|加班餐补|办公用品|业务招待
- 发票抬头必须原样抄写文本中"发票抬头"字段的值
- 文本中确实没有的字段值设为 null

示例（输入→输出的映射，输出值必须来自实际输入，禁止照抄示例的值）：
输入：
报销单
提交人：示例员工
部门：示例部门
费用类型：办公用品
金额：99.9
费用日期：2000-01-01
发票号：10000000000000000000
发票抬头：示例公司
事由：示例事由

输出：
{{"submitter": "示例员工", "department": "示例部门", "expense_type": "办公用品", "amount": 99.9, "expense_date": "2000-01-01", "invoice_no": "10000000000000000000", "invoice_title": "示例公司", "description": "示例事由"}}

报销单文本：
---
{slip}
---"""

# 示例哨兵值：模型输出若原样照抄示例值，判定为泄漏，拒绝并降级
_EXAMPLE_SENTINELS = {
    "submitter": "示例员工",
    "department": "示例部门",
    "amount": 99.9,
    "expense_date": "2000-01-01",
    "invoice_no": "10000000000000000000",
    "invoice_title": "示例公司",
    "description": "示例事由",
}


class LLMExtractor:
    """OpenAI 兼容 /chat/completions 适配层。失败时抛 ExtractorError（由上层降级）。"""

    name = "llm"

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "")
        self.timeout = config.get("timeout_seconds", 10)
        self.temperature = config.get("temperature", 0)

    def extract(self, text: str) -> dict:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "user",
                 "content": PROMPT_TEMPLATE.format(
                     schema=json.dumps(FIELD_SCHEMA, ensure_ascii=False, indent=2),
                     slip=text)},
            ],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ExtractorError(f"llm_http_{e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ExtractorError("llm_timeout_or_unreachable") from e
        except json.JSONDecodeError as e:
            raise ExtractorError("llm_response_not_json") from e

        content = data["choices"][0]["message"]["content"]
        fields, leaked = self._parse_fields(content)
        missing = [f for f in REQUIRED_FIELDS
                   if not fields.get(f)]  # null 也算缺失
        return {"fields": fields, "missing_fields": missing, "leaked_fields": leaked}

    @staticmethod
    def _parse_fields(content: str) -> tuple[dict, list]:
        """解析模型输出，返回 (fields, leaked)。

        leaked = 照抄 few-shot 示例哨兵值的字段列表（可信度存疑，交由上层仲裁）。
        容忍 ```json 围栏、数组包裹、货币符号等小模型常见格式问题。
        """
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        try:
            fields = json.loads(content)
        except json.JSONDecodeError as e:
            raise ExtractorError("llm_output_not_valid_json") from e
        if isinstance(fields, list):  # 小模型常见：用 [{...}] 包裹单个对象
            if len(fields) == 1 and isinstance(fields[0], dict):
                fields = fields[0]
            else:
                raise ExtractorError("llm_output_not_object")
        if not isinstance(fields, dict):
            raise ExtractorError("llm_output_not_object")
        unknown = set(fields) - set(FIELD_SCHEMA)
        if unknown:
            raise ExtractorError(f"llm_output_unknown_fields:{sorted(unknown)}")
        if isinstance(fields.get("amount"), str):
            cleaned = fields["amount"].replace(",", "").strip()
            cleaned = re.sub(r"^(?:¥|￥|人民币|元)+", "", cleaned)
            cleaned = re.sub(r"元$", "", cleaned)
            try:
                fields["amount"] = round(float(cleaned), 2)
            except ValueError as e:
                raise ExtractorError("llm_amount_not_numeric") from e
        if isinstance(fields.get("expense_date"), str):  # 区间日期只取起始日
            d = _norm_date(fields["expense_date"])
            if d:
                fields["expense_date"] = d
        leaked = [k for k, v in _EXAMPLE_SENTINELS.items() if fields.get(k) == v]
        return fields, leaked


class ExtractorError(Exception):
    """LLM 提取失败，携带降级触发原因码。"""


def merge_with_rule(llm_fields: dict, text: str) -> tuple[dict, list]:
    """字段级仲裁：LLM 缺失/泄漏的字段用规则提取结果补齐。

    返回 (合并后字段, 被规则补齐的字段列表)。整输出不可用时走 extract() 的
    整体降级，不走这里。
    """
    merged = dict(llm_fields)
    rule_fields = RuleExtractor().extract(text)["fields"]
    patched = []
    for f in REQUIRED_FIELDS:
        leaked = merged.get(f) == _EXAMPLE_SENTINELS.get(f)
        if not merged.get(f) or leaked:
            if rule_fields.get(f):
                merged[f] = rule_fields[f]
                patched.append(f)
            elif leaked:
                # 泄漏且规则也无法提供 → 置空。宁可缺失（走补件流程），
                # 不可让示例哨兵值或幻觉值冒充真实数据流到下游。
                merged.pop(f)
    return merged, patched


# ---------------------------------------------------------------------------
# 统一入口：优先 LLM，失败自动降级到规则，并记录降级事件
# ---------------------------------------------------------------------------

def load_config(config_path: str | Path) -> dict:
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def extract(text: str, config_path: str | Path = "model_config.json") -> ExtractionResult:
    config = load_config(config_path)
    fallback_event = None
    source = "rule"

    if config.get("enabled"):
        llm = LLMExtractor(config)
        try:
            result = llm.extract(text)
            # 字段级仲裁：泄漏/缺失的个别字段用规则结果补齐，整输出保留
            merged, patched = merge_with_rule(result["fields"], text)
            missing = [f for f in REQUIRED_FIELDS if not merged.get(f)]
            return ExtractionResult(
                fields=merged,
                missing_fields=missing,
                source="llm",
                model=config.get("model"),
                fallback_event=None,
                patched_fields=patched,
            )
        except ExtractorError as e:
            fallback_event = {
                "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
                "trigger": str(e),
                "llm_model": config.get("model"),
                "fallback_to": "rule",
            }
        except Exception as e:  # 未预期异常同样降级，不让提取链路中断
            fallback_event = {
                "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
                "trigger": f"unexpected:{type(e).__name__}",
                "llm_model": config.get("model"),
                "fallback_to": "rule",
            }

    rule_result = RuleExtractor().extract(text)
    return ExtractionResult(
        fields=rule_result["fields"],
        missing_fields=rule_result["missing_fields"],
        source=source,
        model=None,
        fallback_event=fallback_event,
        patched_fields=None,
    )
