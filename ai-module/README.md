# ai-module · 数字员工 AI 与知识检索模块（成员6 夏一洲）

费用报销初审场景的 AI 线：制度知识库、字段提取（真实 LLM 调用 + 规则双轨）、检索方案决策。检索与初审意见生成为后续工作。

## 快速开始（本目录内执行）

```bash
# 终端1：启动本地 LLM 服务（Qwen1.5-0.5B，本机 HF 缓存离线加载，端口 8010，避开业务后端 8000）
python llm_server.py

# 终端2：测试与演示（仅需 Python 标准库）
python test_extractor.py   # 单元测试（17 项）
python demo.py             # 真实模型调用 + 字段仲裁 + 降级演示
```

> `llm_server.py` 需要含 transformers/torch 的环境（本机系统 Python 已具备；依赖见 `requirements.txt`）。
> `test_extractor.py` 和 `demo.py` 仅用标准库，任何 Python 3.10+ 可跑。
> 模型三通道：`model_config.json` 的 `active` 切换 `local_qwen`（本地）/ `deepseek`（远程 API，密钥走环境变量 `DEEPSEEK_API_KEY`）/ `rule`（纯规则）。详见 docs/模型配置与降级方案.md。

## 与团队框架的集成点（后续工作，本模块不改动现有后端代码）

| 预留接口 | 位置 | 说明 |
|---|---|---|
| `parse_fields(description)` | `backend/app/ai.py` | 字段理解接口，现为规则基线；接入时由本模块 `extractor.extract()` 提供 LLM 路径，响应契约保持 |
| `retrieve(category)` | `backend/app/knowledge.py` | 制度检索接口，现为按类别返回 `rules.json`；后续按 `docs/检索方案选型.md` 的条款级 BM25 方案替换，保留条款溯源 |
| `rules.json` | `backend/app/data/` | 运行时制度基线（demo-v1）；本模块 `knowledge_base/` 的 16 条版本化条款（POL-TRV/DAY/INV）为检索数据源，正式替换前二者并存，以产品组确认为准 |

## 备用模式（三层防线，随时可用）

| 层级 | 触发条件 | 行为 | 留痕 |
|---|---|---|---|
| L1 解析容错 | 模型输出格式不规范（代码围栏/数组包裹/金额带货币符号） | 解析时自动纠正，字段保留 | 无（视为正常） |
| L2 字段级仲裁 | 个别字段泄漏/缺失 | 规则结果补齐；无法补齐则清空（宁缺勿错） | `patched_fields` |
| L3 整体降级（**备用方案**） | LLM 服务不可达/超时/输出整体非法 | 全部字段走规则提取，系统能力不中断 | `fallback_event` |

**两种使用方式**：
- **计划内走规则**（如无环境演示）：`model_config.json` 置 `"enabled": false`；
- **计划走 LLM 但服务不可用**：保持 `enabled: true`，自动触发 L3 降级（已实测：停止 llm_server 后 demo 仍 5/5 完成提取，每单留 `llm_timeout_or_unreachable` 事件）。

换团队 API 沙盒：改 `base_url + api_key + model` 三处，`extractor.py` 零改动；L2/L3 防线对任何模型始终保留。

## 目录

```
knowledge_base/   实验性制度文档 3 篇 16 条（已按 PRD FR04 隔离，不参与正式判断；首版生效制度为 backend/app/data/rules.json demo-v1，见 knowledge_base/ISOLATED.md）
samples/          模拟报销单样例 5 张
llm_server.py     本地 LLM 服务：HF 缓存模型 → OpenAI 兼容接口（CPU 推理，端口 8010）
extractor.py      字段提取：LLM 适配层 + 规则提取 + 字段级仲裁/整体降级
model_config.json 模型配置（当前指向本地 8010，换团队 API 改三处）
test_extractor.py 单元测试（17 项）
demo.py           演示入口
docs/             模型配置与降级方案 / 检索方案选型（决策记录，检索未实现）
```

## 接口约定（给后端/前端）

提取入口 `extractor.extract(text, config_path)`，返回：

```json
{"fields": {...8字段...}, "missing_fields": [...], "source": "llm|rule",
 "model": "...|null", "patched_fields": [...] | null,
 "fallback_event": null | {timestamp, trigger, llm_model, fallback_to}}
```

- `patched_fields`：字段级仲裁中被规则结果补齐的字段（LLM 整体成功但个别字段不可信）；
- `fallback_event`：整体降级事件（LLM 完全不可用）。

审批卡片建议展示：`fields` 全量 + `patched_fields`/`fallback_event`（可信度提示）；
工具调用日志建议记录：`source`、`patched_fields`、`trigger`、`timestamp`。
