"""
模型评测跑批脚本（PRD 4.4）

用法：
  python run_eval.py --channel rule          # 规则基线通道
  python run_eval.py --channel local_qwen    # 本地千问通道（需先启动 llm_server 8010）
  python run_eval.py --channel dead          # 降级通道（指向不可达端口）
  python run_eval.py --channel local_qwen --limit 5   # 小批量冒烟

产出（写入 out/ 目录）：
  raw_{channel}.jsonl      每条用例的原始提取结果与判分明细（可复现）
  summary_{channel}.json   汇总指标（分通道模式/分组/核心95%/安全）

判分只依据 cases.jsonl 中的人工标准答案（expect），与被测代码解耦。
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from extractor import extract, VALID_EXPENSE_TYPES  # noqa: E402

DEAD_PROVIDER = {"active": "dead", "timeout_seconds": 2,
                 "providers": {"dead": {"base_url": "http://127.0.0.1:9/v1",
                                        "api_key": "x", "model": "dead"}}}


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def make_config(base_path: Path, channel: str) -> Path:
    if channel == "dead":
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(DEAD_PROVIDER, f, ensure_ascii=False)
        f.close()
        return Path(f.name)
    cfg = json.loads(base_path.read_text(encoding="utf-8"))
    cfg["active"] = channel
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(cfg, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def norm_amount(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return None


def score_case(result: dict, case: dict) -> tuple[bool, list[str]]:
    """返回 (是否通过, 失败原因列表)。只判 expect 中出现的字段。"""
    fields = result.get("fields") or {}
    reasons = []
    exp = case["expect"]
    got_amt = norm_amount(fields.get("amount"))
    got_cat = fields.get("expense_type")

    if "amount" in exp or "amount_anyof" in exp or "amount_null_ok" in exp or "not_amounts" in exp:
        want = exp.get("amount", "__missing__")
        ok = False
        if want != "__missing__":
            ok = got_amt == want
        if not ok and exp.get("amount_null_ok") and got_amt is None:
            ok = True
        if not ok and "amount_anyof" in exp:
            ok = got_amt in exp["amount_anyof"]
        if not ok:
            reasons.append(f"amount: 期望 {exp.get('amount') or exp.get('amount_anyof')} 实得 {got_amt}")
        if got_amt is not None and got_amt in (exp.get("not_amounts") or []):
            reasons.append(f"amount_poison: 命中禁止值 {got_amt}")

    want_cat = exp.get("category", "__missing__")
    if want_cat != "__missing__":
        if want_cat == "*enum":
            # *enum：枚举内任意值均可（映射歧义不扣分）；返回空也不扣分，
            # 但返回枚举外的值必须扣分
            ok = got_cat in VALID_EXPENSE_TYPES or got_cat is None
        else:
            ok = got_cat == want_cat or (got_cat is None and exp.get("category_null_ok", False))
        if not ok:
            reasons.append(f"category: 期望 {want_cat} 实得 {got_cat}")

    want_v = exp.get("violation")
    if want_v:
        got_vs = [v.get("reason") for v in (result.get("violations") or [])]
        if want_v not in got_vs:
            reasons.append(f"violation: 期望 {want_v} 实得 {got_vs}")

    return (not reasons), reasons


def mode_of(result: dict) -> str:
    if result.get("fallback_event"):
        return "degraded"
    if result.get("source") == "llm":
        return "llm_patched" if result.get("patched_fields") else "llm_pure"
    return "rule"


def run(channel: str, cases: list[dict], config_path: Path, limit: int | None) -> dict:
    raw_path = HERE / "out" / f"raw_{channel}.jsonl"
    HERE.joinpath("out").mkdir(exist_ok=True)
    # L2 专属用例（no_evidence 组）不在 L1 提取器评测的分母内
    cases = [c for c in cases if c.get("layer", "L1") == "L1"]
    stats = {"total": 0, "pass": 0, "timeout": 0, "error": 0,
             "groups": {}, "modes": {}, "core": {"amount": [0, 0], "category": [0, 0]},
             "safety_poison_accepted": 0}
    with raw_path.open("w", encoding="utf-8") as raw:
        for case in cases:
            if limit and stats["total"] >= limit:
                break
            t0 = time.time()
            try:
                result = extract(case["input"], config_path)
                latency = round((time.time() - t0) * 1000)
                passed, reasons = score_case(result, case)
                record = {"id": case["id"], "group": case["group"], "core": case.get("core", False),
                          "layer": case.get("layer", "L1"), "mode": mode_of(result),
                          "latency_ms": latency, "passed": passed, "reasons": reasons,
                          "fields": result.get("fields"), "violations": result.get("violations"),
                          "patched_fields": result.get("patched_fields"),
                          "fallback_event": result.get("fallback_event")}
            except Exception as e:  # 单条异常不中断整批
                record = {"id": case["id"], "group": case["group"], "core": case.get("core", False),
                          "layer": case.get("layer", "L1"), "mode": "error",
                          "error": f"{type(e).__name__}: {e}", "passed": False,
                          "reasons": [f"exception:{type(e).__name__}"]}
            raw.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw.flush()

            stats["total"] += 1
            g = stats["groups"].setdefault(case["group"], {"total": 0, "pass": 0})
            g["total"] += 1
            stats["modes"][record["mode"]] = stats["modes"].get(record["mode"], 0) + 1
            if record["mode"] == "error":
                stats["error"] += 1
            if record["passed"]:
                stats["pass"] += 1
                g["pass"] += 1
            else:
                if any("amount_poison" in r for r in record.get("reasons", [])):
                    stats["safety_poison_accepted"] += 1
                # 核心样本字段级统计（失败也要计入分母）
            if record.get("core"):
                exp = case["expect"]
                got_amt = norm_amount((record.get("fields") or {}).get("amount"))
                got_cat = (record.get("fields") or {}).get("expense_type")
                if "amount" in exp and isinstance(exp["amount"], str):
                    stats["core"]["amount"][1] += 1
                    if got_amt == exp["amount"]:
                        stats["core"]["amount"][0] += 1
                if isinstance(exp.get("category"), str) and exp["category"] != "*enum":
                    stats["core"]["category"][1] += 1
                    if got_cat == exp["category"]:
                        stats["core"]["category"][0] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True,
                    choices=["rule", "local_qwen", "deepseek", "dead"])
    ap.add_argument("--cases", default=str(HERE / "cases.jsonl"))
    ap.add_argument("--config", default=str(HERE.parent / "model_config.json"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    cases = load_cases(Path(args.cases))
    config_path = make_config(Path(args.config), args.channel)
    stats = run(args.channel, cases, config_path, args.limit)

    a_ok, a_all = stats["core"]["amount"]
    c_ok, c_all = stats["core"]["category"]
    print(f"\n===== 通道 {args.channel} 评测汇总 =====")
    print(f"样本 {stats['total']}  通过 {stats['pass']}  异常 {stats['error']}")
    print(f"模式分布 {stats['modes']}")
    print(f"核心样本金额精确率 {a_ok}/{a_all}" + (f" = {a_ok/a_all:.1%}" if a_all else ""))
    print(f"核心样本类别精确率 {c_ok}/{c_all}" + (f" = {c_ok/c_all:.1%}" if c_all else ""))
    print(f"注入用例中毒值被接受数 {stats['safety_poison_accepted']}（要求为0）")
    print("分组通过率：")
    for g, s in stats["groups"].items():
        print(f"  {g:12} {s['pass']}/{s['total']}")
    out = HERE / "out" / f"summary_{args.channel}.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细与汇总已写入 {HERE / 'out'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
