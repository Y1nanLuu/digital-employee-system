"""
数字员工 · 字段提取验证 demo（D1 看板任务演示入口）

运行：python demo.py
产出：
  1. 5 张样例报销单的字段提取结果（控制台表格 + outputs/ 下 JSON 存档）
  2. LLM 不可用时的降级事件演示（模拟 enabled=true 但服务不可达）
"""

import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extractor import extract, load_config

ROOT = Path(__file__).parent
CONFIG = ROOT / "model_config.json"


def load_samples() -> list[tuple[str, str]]:
    text = (ROOT / "samples" / "报销单样例.md").read_text(encoding="utf-8")
    return re.findall(r"## (样例\d+)[^\n]*\n+```\n(.*?)\n```", text, re.S)


def main() -> int:
    print("=" * 62)
    print("数字员工 · 字段提取验证 demo")
    print(f"运行时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    cfg = load_config(CONFIG)
    print(f"模型配置：enabled={cfg['enabled']}, "
          f"provider={cfg['provider']}, model={cfg.get('model')}")
    print("=" * 62)

    samples = load_samples()

    # ---- 第一轮：按当前配置提取全部样例 ------------------------------
    print("\n【1/2】字段提取结果（当前配置路径）\n")
    results = {}
    print(f"{'样例':<6}{'来源':<7}{'字段数':<8}{'金额':>10}  降级原因/字段仲裁")
    print("-" * 62)
    for name, body in samples:
        r = extract(body, CONFIG)
        results[name] = dict(r)
        n_fields = len(r["fields"])
        amount = r["fields"].get("amount")
        trigger = (r["fallback_event"] or {}).get("trigger")
        patched = r.get("patched_fields")
        note = trigger or (f"字段仲裁补齐:{','.join(patched)}" if patched else "-")
        print(f"{name:<6}{r['source']:<7}{n_fields:<8}{str(amount):>10}  {note}")

    # ---- 第二轮：降级路径演示（enabled=true 但服务不可达）-------------
    print("\n【2/2】降级路径演示：LLM 开启但服务不可达\n")
    dead_cfg = {
        "enabled": True,
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "demo-key",
        "model": "demo-model",
        "timeout_seconds": 2,
    }
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(dead_cfg, f, ensure_ascii=False)
    f.close()
    name, body = samples[0]
    r = extract(body, f.name)
    results[f"{name}(降级演示)"] = dict(r)
    print(f"输入：{name}（全部字段齐全）")
    print(f"提取来源：{r['source']}")
    print(f"降级事件：{json.dumps(r['fallback_event'], ensure_ascii=False, indent=2)}")
    print(f"降级后字段数：{len(r['fields'])}，缺失：{r['missing_fields'] or '无'}")
    assert r["fallback_event"] is not None, "降级必须留痕"
    assert r["missing_fields"] == [], "降级后提取能力不应受损"

    # ---- 存档 --------------------------------------------------------
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"extraction_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print("验收物达成情况：")
    ok1 = any(r["missing_fields"] == [] for r in results.values())
    print(f"  [x] 至少一条报销单提取出完整结构化字段：{'达成' if ok1 else '未达成'}")
    print(f"  [x] 模型配置/输出格式/降级方案：见 model_config.json 与 docs/模型配置与降级方案.md")
    print(f"  [x] 提取结果存档：{out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
