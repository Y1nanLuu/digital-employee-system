# D3 集成版本说明

本文件是 D3 阶段（2026-11-09）的集成交付记录：集成版本基准、跨模块集成面、状态转换与权限矩阵、执行中断与受限恢复、验证入口与结果、已知限制与 D4 前置。业务规则以产品 V0.3 为准，实现状态以本文件所列验证结果为准。

## 一、集成版本

| 项 | 值 |
|---|---|
| 应用版本 | `0.4.0`（`APP_VERSION`，可用同名环境变量覆盖） |
| 阶段标识 | `D4-baseline`（`APP_STAGE`） |
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

## 十、2026-11-09 周勃 D3-05 复核补充

本次以`7897aa1`加本地D3修改为基准，在Linux、Python 3.13.13下执行。上文第七节保留原集成报告的执行结果；本节记录后续复核与修复，不将不同环境的结论混为一轮。

- 历史指标缺陷DF-002已复现：13笔任务仅12笔完成时，原脚本错误要求completed=total，导致42/43。现按真实任务列表核对总数、终态数、完成率和转人工率，扩展为46项；人工历史在续跑后继续统计。
- 原重启脚本本机17/19：terminate触发Uvicorn优雅退出，工具超时先落库，未形成预期进程中断。故障注入改用强制结束测试进程，正常重启仍优雅停止，修复后19/19。
- smoke子进程显式指定UTF-8读写，并在默认文本解码模拟为CP936的父进程中通过。此项为Linux上的编码模拟，不替代Windows实机复验。
- 工具日志增加去除URL认证信息、查询参数与片段的endpoint，结合task_id、stage、error_code和可读原因定位预算或写回故障；旧日志字段继续兼容。
- 后端118项通过；包含空库、四状态固定时长指标、人工等待扣除、连接/超时故障、地址脱敏、拒绝终止、并发通过及拒绝仅一次生效。
- 同一临时库保留待审批和失败历史，连续两轮46/46，再执行51/51；历史任务内容不变。前端构建、浏览器四组业务验证通过。

当前四类场景及重启恢复结果见[周勃D3验收证据](test-evidence/D3/zhoubo-20261109-summary.json)，开发过程见[周勃开发日志汇总](周勃-开发日志汇总.md)。自动生成的原始场景快照在`work/smoke-summary.json`，重启逐项结果在脚本输出目录的`summary.json`。本轮未验证PostgreSQL、Compose、Windows实机或真实LLM调用。

## 十一、模型通道联调记录（2026-11-10，D4）

按产品 D4 安排完成远程模型通道联调，补齐 PRD FR03（P0）要求的「至少一组真实模型调用成功证据 + 一组服务不可用降级证据」。

### 通道与配置方式

| 项 | 值 |
|---|---|
| 通道 | `deepseek`（`base_url=https://api.deepseek.com/v1`，`model=deepseek-flash`） |
| 密钥注入 | 环境变量 `DEEPSEEK_API_KEY`（本地存放于被 `.gitignore` 忽略的 `dsapi.txt`），密钥不入库、不入日志、不写入文档 |
| 配置生效方式 | `AI_MODULE_ENABLED=true` + `AI_MODULE_CONFIG=<本地覆盖配置>`，仓库内 `ai-module/model_config.json` 的 `active` 保持 `local_qwen` 未改 |
| 超时 | 实测单次调用 0.8–1.6 秒，远低于 PRD 的 120 秒上限，无需调整 `timeout_seconds` |

未改仓库默认值的理由：若把 `active` 直接改成 `deepseek`，没有配置密钥的成员每次解析都会触发 `llm_api_key_missing` 降级并被标记 `requires_manual_review`，导致其演示任务全部转人工。是否把默认通道改为 `deepseek` 属于 D4-06「冻结模型配置版本」内容，需产品与 AI 负责人确认后统一提交。

### 真实调用成功证据

| 检查 | 结果 |
|---|---|
| 提取器层：结构化报销单（8 字段） | `source=llm`、`model=deepseek-flash`、缺失 0、违规 0、耗时 1.55 s |
| 提取器层：自由文本「上周出差上海，客户现场往返高铁票320元」 | 提取到 `amount=320`、`expense_type=差旅`，耗时 1.08 s，其余字段按缺失返回、不编造 |
| 接口层：`POST /api/parse` | `mode=llm`、`source=llm`、`model=deepseek-flash`、`latency_ms≈1306`、`requires_manual_review=false`、无 `fallback_event` |
| 接口层：用模型建议创建任务 | 201，`parse_snapshot.mode=llm`，任务 `succeeded`，事件序列 `running→writing_back→succeeded` |

### 服务不可用降级证据

| 检查 | 结果 |
|---|---|
| `POST /api/parse`（密钥缺失，等价服务不可用） | `mode=rule_fallback`、`requires_manual_review=true`、`fallback_event.trigger=llm_api_key_missing`，业务字段仍由规则兜底给出（300.00 / 交通费） |
| 用降级结果创建任务 | `awaiting_approval`，`result.manual_reasons=["模型处理异常，须由审批人核对字段"]`，依据与预算证据保留 |

### 启用模型通道后的回归

| 入口 | 结果 |
|---|---|
| `scripts/verify_system.py`（AI 启用） | 44 项通过（比默认模式多一条「启用AI时返回来源与结构化字段」） |
| `scripts/verify_integration.py`（AI 启用） | 51 项通过 |

### 观察与遗留

- 联调初期配置写 `deepseek-chat`，服务端实际返回 `deepseek-flash`；夏一洲在 D4 把主配置与评测统一为 `deepseek-flash`，本文件对应的覆盖配置已同步，冻结版本以该模型名为准。
- `verify_system.py` 的「指标已完成数与任务总数一致」隐含干净库假设，在已有历史任务的库上会失败（产品已指派修复）。现场执行前请重置演示库，或改用 `verify_integration.py` 的指标交叉核对。
- 多实例并发启动时曾出现旧进程占用 8000 端口、新进程静默失败的情况；验证前先确认端口无残留监听，`GET /health` 的 `ai_enabled`/`version` 应作为第一道核对。

## 十二、发票 PDF 上传与字段抽取（2026-11-10，D4）

员工端由「手写说明 → 提取建议」扩展为「上传电子发票 PDF → 抽取文本 → 提取字段 → 确认提交」，服务端新增 `POST /api/invoices`。

### 实现方式

| 环节 | 做法 |
|---|---|
| 文本抽取 | `pypdf`（纯 Python，无外部二进制）读取 PDF 文字层；不保存文件本体，只在内存解析 |
| 字段提取 | 复用现有 `/api/parse` 的链路：`ai.py` 适配层 → 模型通道或规则基线，响应契约保持一致 |
| 溯源 | 抽取结果写入 `parse_snapshots`，`invoice_id` 随创建请求进入任务快照与 `submission_payload` |
| 冲突闸门 | 抽取类别/金额与申报值不一致时，沿用既有 `conflict_acknowledged` + `confirmation_note` 才能提交 |
| 审计 | 任务详情、审批台、指标口径均无需改动，发票来源随快照可查 |

### 边界与限制

- 仅支持带文字层的电子发票；扫描件/图片型 PDF 返回 422 并提示改用手工填写。**OCR 与发票验真按 PRD V0.3 明确不在本期范围**，本功能属文本层抽取，不是 OCR。
- 单文件上限 5 MB，只接受 `%PDF-` 开头的文件；文件不落盘、内容不入日志。
- 中文电子发票的抽取质量取决于字体与 ToUnicode 映射，实际效果需用真实票据核对一次（响应里的 `text_preview` 便于判断是否乱码）。

### 验证结果

| 入口 | 结果 |
|---|---|
| `pytest backend/tests/test_invoices.py` | 13 项通过（文件类型/大小/空文件/无文字层/上传契约/发票归属/parse_id 与 invoice_id 互斥） |
| 端到端（上传 → 抽取 → 建单） | 14 项断言通过：真实调用 `mode=llm`、金额 320.00、类别冲突被拦 422、显式确认后按交通费自动通过、任务快照保留发票来源 |
| 全量回归 | `pytest` 122 项、`smoke.py`、`verify_system.py` 43 项、`verify_integration.py` 51 项全部通过 |
| 前端 | `npm run build` 通过（js 103.58 kB） |

### 范围提示

本功能超出了 PRD V0.3 的原始范围（原文把 OCR 列为不纳入项），属于 D4 由用户要求追加的变更。若保留到候选版，需产品在范围内确认并同步需求文档与看板；若不保留，可整体回退本节涉及的四类改动（`invoices.py`、`/api/invoices` 路由、`invoice_id` 字段、前端上传区块）。

## 十三、D4 候选基准（2026-11-10）

D4 停止新增功能，把当日验证通过的集成版本固定为候选基准，D5 在此基础上发布候选版。

| 项 | 值 |
|---|---|
| 版本 | `0.4.0`（`APP_VERSION`），阶段标识 `D4-baseline`（`APP_STAGE`） |
| 基准范围 | 规则业务闭环、人工审批与补正、解析快照与冲突确认、任务溯源、执行中断与受限恢复、发票 PDF 上传与字段抽取、指标口径、D4 模型通道联调 |
| 回归门禁 | `pytest` 131 项；`smoke.py`；`verify_system.py` 46 项；`verify_integration.py` 51 项；`verify_restart.py` 19 项；发票端到端 14 项；`npm run build` |
| 已知未验证 | Docker Compose / PostgreSQL 链路（本机无 Docker 引擎，核查清单见 `docs/DEPLOYMENT.md` 第六节）；本地千问通道（缺 `transformers`/`torch`） |
| 变更纪律 | 基准之后只修缺陷，不再加功能；任何接口或数据变更按 `schemas.py → docs/API.md → frontend/src/api.ts → 测试 → 现场脚本` 顺序同步 |

基准不包含的事项按 PRD 记录在案：OCR、发票验真、真实财务集成、付款与预算扣减、分页与推送、自动重试调度。
