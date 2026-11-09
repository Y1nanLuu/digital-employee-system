# 报告四：DeepSeek 通道评测（待测占位）

> 状态：**待执行**。用例集（cases.jsonl）、跑批脚本（run_eval.py）、通道配置（model_config.json 的 `deepseek`）均已就绪。

执行方式（拿到 DEEPSEEK_API_KEY 后）：

```
1. 在 eval/api_keys.env 填入 DEEPSEEK_API_KEY=sk-xxx（文件已 gitignore）
2. python run_eval.py --channel deepseek        # 全量 190 条，约 15 分钟
3. 按报告三格式回填本文件 + 综合报告对比表
```

## 结果（待填）

| 指标 | 结果 |
|---|---|
| 总通过（严格/PRD 对齐） | 待测 |
| 核心金额 / 类别 | 待测 |
| 注入组 | 待测 |
| 纯 LLM / 降级 | 待测 |
| 耗时 | 待测 |
