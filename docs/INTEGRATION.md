# D3 集成版本说明

本文件是 D3 阶段（2026-11-09）的集成交付记录：集成版本基准、跨模块集成面、状态转换与权限矩阵、执行中断与受限恢复、验证入口与结果、已知限制与 D4 前置。业务规则以产品 V0.3 为准，实现状态以本文件所列验证结果为准。

## 一、集成版本

| 项 | 值 |
|---|---|
| 应用版本 | `0.3.0`（`APP_VERSION`，可用同名环境变量覆盖） |
| 阶段标识 | `D3-integration`（`APP_STAGE`） |
| 代码基线 | 拉取 `origin/master` 至 `296209b`（含周勃「任务快照、审批补正与故障恢复」、王丹蕊审批与指标、测试证据与产品 D3 归档） |
| 数据库结构 | `create_all` + 自建增量升级（`backend/app/migrations.py`），升级前自动备份 `.bak` |
| 健康检查 | `GET /health` 返回 `status`、`mode`、`version`、`stage`、`ai_enabled`、`database`，用于现场确认运行版本与依赖状态 |

版本标识的作用：验收与排障时先看 `/health` 的 `version`/`stage`/`database`，避免把不同提交的结果混在一起统计。

## 二、集成面清单

| 链路 | 集成点 | 契约要点 |
|---|---|---|
| 员工端 → 业务服务 | `POST /api/parse`、`POST /api/tasks`、`GET /api/tasks[/{id}]` | 解析快照 `parse_id` 与说明哈希绑定；提交需 `Idempotency-Key`；字段冲突须显式确认 |
| 业务服务 → 认知层 | `ai.py` 适配层 → `ai-module`（8010） / 内置规则基线 | 保留五个既有字段，追加来源与降级标识；模型不可用时降级并标记 `requires_manual_review` |
| 业务服务 → 制度依据 | `knowledge.py` → `data/rules.json`（demo-v1） | 条款编号、来源、版本、检索时间可追溯；无依据不得自动通过 |
| 业务服务 → 模拟财务 | `GET /budgets/{project_id}`、`POST /results` | Bearer 工具密钥；响应用同一模型校验；写回按 `task_id` 幂等，换内容 409 |
| 审批台 → 业务服务 | `POST /api/tasks/{id}/approval` | 仅 approver；禁止自审与重复处理；补正须原因 + 显式核对 |
| 管理台 → 业务服务 | `GET /api/metrics` | 四项指标按任务编号统计，口径与任务数据一致 |

## 三、状态转换与事件序列

状态机取值固定为 `queued`、`running`、`awaiting_approval`、`writing_back`、`succeeded`、`rejected`、`failed`。每条状态事件都带 `task_id`，审批与恢复事件另带 `actor`、`reason` 等字段，前端时间线直接消费 `events`。

四类场景的实测事件序列（`scripts/verify_integration.py` 断言）：

| 场景 | 事件序列 |
|---|---|
| 合规完成（交通费 300 元） | `running → writing_back → succeeded` |
| 审批通过（办公用品 1500 元） | `running → awaiting_approval`，审批后追加 `running → writing_back → succeeded` |
| 审批拒绝 | `running → awaiting_approval → running → writing_back → rejected` |
| 预算不足（P002 600 元） | `running → writing_back → rejected`（不经人工） |
| 无依据转人工（“其他”类别） | `running → awaiting_approval`，转人工原因写入 `result.manual_reasons` |
| 工具故障或进程中断 | 以 `failed` 结尾，`result.error_code`、`failure_stage` 说明原因与阶段 |

集成要点的实现位置：转人工判定（金额 > 1000、缺依据、模型异常）在 `services.execute`；审批续跑与补正在 `services.approve`；写回与凭证确认在 `services.write_result`。

## 四、账号权限矩阵

| 端点 | employee | approver | admin | 未认证 |
|---|---|---|---|---|
| `GET /health` | 200 | 200 | 200 | 200 |
| `GET /api/me` | 200（本人） | 200 | 200 | 401 |
| `POST /api/parse` | 200 | 200 | 200 | 401 |
| `POST /api/tasks` | 201 | 403 | 403 | 401 |
| `GET /api/tasks` | 200（仅本人） | 200（全部） | 200（全部） | 401 |
| `GET /api/tasks/{id}` | 200（本人任务） | 200 | 200 | 401 |
| `POST /api/tasks/{id}/approval` | 403 | 200（待审批且非自审） | 403 | 401 |
| `GET /api/metrics` | 403 | 200 | 200 | 401 |

补充约束：审批人不能审批本人提交的任务（自审 403），管理员不因角色自动获得审批权。当前演示只有一个员工账号，跨员工数据隔离由 `pytest` 的权属用例（`test_ownership_and_self_approval`）覆盖。

## 五、解析快照、字段冲突与审批补正

- **解析快照**：`/api/parse` 落库 `parse_snapshots` 并返回 `parse_id` 与 `latency_ms`。提交时携带 `parse_id`，服务端校验归属与说明哈希；复用他人、伪造或说明已变更的 `parse_id` 返回 422。
- **字段冲突**：说明中的金额、类别与申报值不一致时返回 422，须带 `conflict_acknowledged=true` 与 `confirmation_note` 才能创建，冲突明细写入 `confirmations`，保留原始请求与最终字段。
- **审批补正**：审批人可提交 `corrected_fields` 修正字段，但必须同时给 `correction_reason` 且显式 `reviewed_fields=true`；拒绝请求不允许同时补正。补正写入 `amendments`（`before`/`after`），并重新检索依据、重新执行阈值与预算校验。

## 六、执行中断与受限恢复

设计原则：**先落库、后副作用**，任何恢复都不得构造新的审核载荷。

| 机制 | 实现 |
|---|---|
| 写回检查点 | 写回前先提交 `pending_writeback`（任务编号、决定、理由），副作用发生后清空；期间禁止更换审核决定（409） |
| 启动时识别中断 | 服务启动调用 `services.mark_interrupted`：把 `queued`/`running`/`writing_back` 任务标记为 `failed`，写入 `error_code=process_interrupted`、`failure_stage` 与 `result.interrupted_status` |
| 受限恢复 | 仅本机 CLI `scripts/recover_task.py <task_id> --action <replay-writeback|retry-budget> --actor <操作者> --reason <原因>`，无普通用户 HTTP 恢复接口 |
| 恢复边界 | `replay-writeback` 只按既有 `pending_writeback` 原载荷重放；`retry-budget` 仅限尚未写回且失败阶段为 `initial_review`/`budget_query`；终态任务一律 409，不得重新打开 |

重启恢复的实测结论（`scripts/verify_restart.py`，19 项断言）：

| 检查 | 结果 |
|---|---|
| 待审批任务跨重启保留，编号不变，事件序列不变 | 通过 |
| 重启后审批续跑，事件接在原序列之后 | 通过 |
| 同一幂等键重启后重放返回原任务，不重复执行 | 通过 |
| 执行中任务在进程被杀后，下次启动标记 `failed`（`process_interrupted`） | 通过 |
| 失败任务保留 `interrupted_status` 与 `failure_stage` 供对账 | 通过 |
| `retry-budget` 按检查点恢复并重新执行阈值校验（800 元自动通过） | 通过 |
| 终态任务拒绝恢复（409） | 通过 |

## 七、验证入口与结果

| 入口 | 覆盖 | 本轮结果 |
|---|---|---|
| `pytest -c backend/pytest.ini backend/tests -q` | 契约、权限、边界、异常、幂等、迁移、AI 适配层 | 109 项通过 |
| `scripts/smoke.py` | 临时服务真实 HTTP、解析快照、原子补正、初始请求重放、写回中断检测与 CLI 对账 | 2 条通过，退出码 0 |
| `scripts/verify_system.py` | README 六个演示步骤与接口契约（标准端口） | 43 项通过 |
| `scripts/verify_integration.py` | 健康与版本、状态事件序列、权限矩阵、解析快照与冲突、审批补正、指标交叉核对、凭证回溯 | 51 项通过 |
| `scripts/verify_restart.py` | 重启保留与续跑、幂等重放、中断标记、受限恢复与终态拒绝 | 19 项通过 |

复现顺序：先跑 `pytest` 与 `smoke.py`（自起自停，不影响演示库），再启动 8000/8001 后跑 `verify_system.py` 与 `verify_integration.py`，最后跑 `verify_restart.py`（自起自停临时服务）。

## 八、已知限制

- 执行仍为同步单进程；SQLite 版本不支持多 worker 或多副本，PostgreSQL 路径依赖 Docker 环境验证。
- 恢复仅提供本机 CLI，没有面向普通用户的恢复接口，也没有自动重试调度；中断发生在写回中途时需要人工对账。
- 模型通道：本地通道需 `transformers`/`torch`（当前开发机未安装），远程通道需 `DEEPSEEK_API_KEY`；两者均未在本轮形成真实调用证据，降级路径已验证。
- Docker Compose 与 PostgreSQL 链路本轮仍未验证（引擎未运行），列为 D4 首选任务。
- 浏览器端业务操作（`scripts/browser_smoke.mjs`）未在本机执行，由测试负责人按看板 AC 执行。
- JSON 列仍为单表存储；分页、独立日志表与推送按 P1 后置。

## 九、D4 前置

1. Compose + PostgreSQL 联调：同一提交上验证迁移脚本、行锁审批与指标口径。
2. 模型通道联调：按产品 P0 完成真实调用与降级证据，固定提示词与模型配置版本。
3. 冻结候选基准：D4 停止新增功能，以本文件第七节全部入口作为回归门禁。
4. 与服务端状态机的接口一致性复核：任何字段变更按 `schemas.py → docs/API.md → frontend/src/api.ts → 测试 → 现场脚本` 顺序同步。
