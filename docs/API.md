# 接口约定

业务API统一前缀`/api`，JSON响应；HTTP Basic预置角色认证。时间戳为UTC ISO格式，前端按Asia/Shanghai显示。金额以十进制字符串传输，不能用二进制浮点计算金额。字段、状态与错误码变更时同步`backend/app/schemas.py`、`frontend/src/api.ts`、本文件与测试。

## 端点清单

| 方法 | 地址 | 权限 | 用途 |
|---|---|---|---|
| GET | /health | 无 | 数据库连接与服务健康；返回`status`、`mode`、`version`、`stage`、`ai_enabled`、`database` |
| GET | /api/me | 登录用户 | 当前身份和角色 |
| POST | /api/parse | 登录用户 | 字段提取建议，返回规则、模型或降级建议及持久化解析编号 |
| POST | /api/invoices | employee | 上传电子发票 PDF，抽取文字层后返回与 `/api/parse` 同构的字段建议 |
| POST | /api/tasks | employee | 确认字段后创建并执行任务；必须有Idempotency-Key头 |
| GET | /api/tasks | 登录用户 | 员工仅看本人，审批人和管理员查看全部 |
| GET | /api/tasks/{id} | 可见任务的用户 | 任务、条款、日志和审批记录 |
| POST | /api/tasks/{id}/approval | approver | 待审批任务通过或拒绝；禁止自审与重复处理 |
| GET | /api/metrics | approver/admin | 全量演示数据统计 |

## 字段约束

创建请求（`ClaimInput`）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `project_id` | 枚举 | 仅 `P001`、`P002` |
| `category` | 枚举 | 仅 `交通费`、`办公用品`、`其他` |
| `amount` | 十进制字符串 | 大于 0 且不超过 5000，最多两位小数；仅普通十进制字符串，拒绝数值/布尔值、符号、指数和非有限数 |
| `expense_date` | 日期 | 不得晚于当天（按 Asia/Shanghai 判定） |
| `description` | 字符串 | 1–500 字符，去空白后不能为空 |
| `confirmed` | 布尔 | 必须为 `true` |

创建请求示例：

```json
{"project_id":"P001","category":"交通费","amount":"300.00","expense_date":"2026-11-05","description":"客户现场往返交通费300元","confirmed":true}
```

`Idempotency-Key`为8至100字符，必须至少包含一个非空白字符。同一员工用相同标识和相同内容重试返回原任务；同一标识更换内容返回409。新申请必须用新标识。缺失、不满足长度或全为空白字符时返回422，不创建任务。该标识由客户端生成，服务端不回传，客户端须自行保存并在重试时复用。

提交、列表、详情和审批接口统一使用`TaskResponse`响应模型。每笔新申请的`id`为UUID v4，数据库以此作为主键；`payload`保存确认字段，`status`为下文状态枚举，`result`保存处理结果，`citations`、`logs`、`events`和`approvals`分别保存依据、工具日志、状态事件与审批记录。另含`owner`、`created_at`、可为空的`finished_at`及`manual`字段。内部提交标识`request_key`不对外返回。

任务先生成编号并写入数据库，再同步执行规则及财务工具；工具失败仍保留同一编号和失败日志。`GET /api/tasks/{id}`可查询已完成、待审批和失败任务。员工仅可查询自己的记录，查询不存在或不可见的任务均返回404。进程重启后可继续查询已提交的记录；相同标识和内容的重试仍返回原任务，不重复执行财务调用。

审批请求（`ApprovalInput`）：

```json
{"decision":"approve","comment":"用途明确，同意初审","manual_basis":""}
```

`decision`为approve或reject。`comment`1–500字符且不可为空；无制度依据且通过时，`manual_basis`不可为空（最长1000字符）。拒绝后只写回拒绝记录，不执行通过分支。

字段提取请求（`ParseInput`）为 `{"description":"..."}`，1–500字符。

**建议只来源于 `description` 这段说明文字**：表单里已填的金额、类别不参与解析，也不作为提示传给模型。因此说明中未写金额时 `suggested_amount` 为 `null`（按“宁缺勿错”不编造），说明中无类别线索时 `suggested_category` 为 `null`；这不是模型故障，员工直接以表单值为准即可，需要建议时把金额写进说明（如「客户现场打车费3200元」）。

基础响应契约（无论是否启用模型都保证存在）：

| 字段 | 说明 |
|---|---|
| `mode` | `rule_baseline`、`llm`、`llm_with_rule_patch`、`rule_fallback`；直接手工提交记为`structured_input` |
| `suggested_amount` | 唯一可识别的金额，两位小数字符串，识别不到为 `null` |
| `suggested_category` | 唯一可识别的业务类别，识别不到为 `null` |
| `requires_confirmation` | 固定 `true`：建议只是建议，必须由用户确认后提交 |
| `message` | 面向用户的提示，说明当前来源或降级情况 |

启用模型通道（`AI_MODULE_ENABLED=true`）时追加字段：

| 字段 | 说明 |
|---|---|
| `ai_enabled` | 模型通道是否启用；`false` 表示走内置规则基线 |
| `source` | `llm` 或 `rule`：本次字段来自模型还是 ai-module 的规则提取 |
| `model` | 实际使用的模型名，规则路径为 `null` |
| `fields` | 结构化字段：`submitter`、`department`、`expense_type`、`amount`、`expense_date`、`invoice_no`、`invoice_title`、`description` |
| `missing_fields` | 未提取到的实验字段列表，供用户核对，不得编造；当前业务仍以确认后的六个基本字段为准 |
| `patched_fields` | 被规则补齐的字段（字段级仲裁发生） |
| `violations` | 被值校验剔除的字段及原因，供审计留痕 |
| `fallback_event` | 整体降级事件：`trigger` 取 `llm_preflight_unreachable`、`ai_config_unreadable`、`ai_module_unavailable` 或 ai-module 自身的降级原因，`fallback_to` 为 `rule_baseline` |

模型侧 `expense_type` 枚举（差旅/办公用品/加班餐补/业务招待）由适配层映射为业务类别（交通费/办公用品/其他），按PRD映射：办公用品保持；差旅仅明确交通支出时为交通费，住宿、混合或不明差旅为其他；餐补和业务招待为其他。降级时业务字段仍由业务侧规则兜底，`suggested_amount` 与 `suggested_category` 不会因为模型不可用而丢失。

## 发票上传与字段抽取（D4）

`POST /api/invoices` 接收 `multipart/form-data`，字段名 `file`，仅限 employee。服务端只读取带**文字层**的电子发票 PDF，把文本交给与 `/api/parse` 相同的提取链路（模型或规则），响应与 `/api/parse` 同构，另加 `invoice_id` 与 `invoice` 元信息：

| 字段 | 说明 |
|---|---|
| `invoice_id` | 本次发票抽取编号，创建任务时作为 `invoice_id` 回传，任务快照因此保留发票来源 |
| `invoice.filename` | 原始文件名（仅保留末段，最长 120 字符） |
| `invoice.pages` | 页数 |
| `invoice.text_chars` | 抽取到的文本字符数 |
| `invoice.text_preview` | 原文摘要前 200 字，供员工核对是否乱码或抽取失败 |
| `invoice.hints` | 确定性提示：`invoice_no`、`expense_date`、`invoice_title`（从票面文字正则提取，作为模型结果的补充） |

约束与边界：

- 单文件上限 5 MB，只接受以 `%PDF-` 开头的文件；超出或格式不符返回 413/422。
- **不落盘**：文件在内存中解析，票据内容不写入服务器磁盘，也不写入日志。
- **不支持扫描件/图片型 PDF**：检测不到文字层时返回 422 并提示改用手工填写；OCR 与发票验真按 PRD 明确不在本期范围。
- 抽取结果与申报值不一致时，沿用既有冲突闸门：须 `conflict_acknowledged=true` 且填写 `confirmation_note`，否则 422。
- `parse_id` 与 `invoice_id` 不能同时提供（422）；`invoice_id` 必须属于当前用户，否则 422。

创建请求相对 `/api/parse` 的差异：`ClaimInput` 增加可选 `invoice_id`（UUID）。带 `invoice_id` 时服务端加载该发票快照作为 `parse_snapshot`，并把它写入 `submission_payload`，供审批与审计追溯。

## 任务对象响应字段

`GET /api/tasks`、`GET /api/tasks/{id}`、创建与审批响应返回同一结构：

| 字段 | 说明 |
|---|---|
| `id` | 任务编号（UUID4），贯穿页面、日志、审批与财务凭证 |
| `owner` | 提交人用户名 |
| `status` | 七状态之一，见下节 |
| `payload` | 当前有效报销字段，审批补正不覆盖初始快照 |
| `result` | `decision`、`reason`、`mode`、`budget`、`receipt`、`error` |
| `citations` | 引用条款：`id`、`text`、`source`、`version`；人工依据记为 `id=MANUAL` |
| `logs` | 工具调用日志：`task_id`、`stage`、`at`、`tool`、`method`、`request`、`success`、`response`或`error`、`duration_ms` |
| `events` | 状态事件：`at`、`task_id`、`status`、`message`；审批与恢复事件另带`actor`、`reason`，失败事件带`failure_stage`/`error_code` |
| `approvals` | 审批记录：`at`、`actor`、`decision`、`comment`、`manual_basis`、`corrected_fields`、`correction_reason`、`reviewed_fields`，以及`citations_before`/`citations_after` |
| `created_at` | 创建时间（UTC ISO） |
| `finished_at` | 终态时间，未结束为 `null` |
| `manual` | 是否发生过转人工 |
| `submission_payload` | 规范化后的初始创建请求，包含解析关联与确认信息，补正后不改变 |
| `parse_snapshot` | 提取来源、候选、模型异常与人工核对标记的历史快照 |
| `confirmations` | 员工确认时间、最终字段、冲突与具体说明 |
| `amendments` | 补正字段前后值、操作者、原因与时间 |
| `failure_stage` | 技术失败阶段；正常状态为null |

`request_key`、`pending_writeback`、`approval_started_at`、`wait_seconds` 为服务端内部字段，不对外暴露；人工等待时长只用于指标口径。

## 状态与错误

状态：queued → running → writing_back → succeeded/rejected；`awaiting_approval` 可插入在 running 之后并恢复执行；技术失败进入failed。

| 码 | 含义 |
|---|---|
| 401 | 未登录、账号或密码错误、工具密钥错误 |
| 403 | 无权限（角色不符、员工查指标、审批本人任务） |
| 404 | 任务或项目不存在 |
| 409 | 状态冲突（重复审批）、幂等标识内容冲突、财务写回内容冲突 |
| 422 | 输入不合法（枚举、金额范围、日期、缺人工依据） |

工具故障产生failed任务，创建请求仍返回201，客户端必须查看任务状态。

## 模拟财务服务

独立进程，前缀无`/api`：`GET /health`、`GET /budgets/{project_id}`、`POST /results`。除健康检查外均需`Authorization: Bearer 工具密钥`，由后端调用。默认密钥为`local-finance-key`，可通过`FINANCE_KEY`环境变量覆盖；前端不直接持有密钥。缺失或错误的密钥返回401。

预算查询示例：

```json
{"project_id":"P001","available":"10000.00","currency":"CNY","simulated":true}
```

`P002`的`available`为`"500.00"`，未知项目返回404。预算金额为非负、有限且最多两位小数的十进制字符串。预算只查询，不因提交或写回而扣减。

审核结果写回请求示例：

```json
{"task_id":"37b4da2a-e739-4af1-9430-b086d998bd40","decision":"approved","reason":"金额、制度和预算校验通过"}
```

`task_id`必须为UUID，`decision`仅允许`approved`或`rejected`，`reason`为1至1000字符且不可全为空白；首尾空白会被去除。非法输入返回422，不写入审核记录。响应包含上述字段、非空的`receipt_id`和`simulated:true`：

```json
{"task_id":"37b4da2a-e739-4af1-9430-b086d998bd40","decision":"approved","reason":"金额、制度和预算校验通过","receipt_id":"demo-37b4da2a-e739-4af1-9430-b086d998bd40","simulated":true}
```

`task_id`重复且内容相同返回原凭证，不同内容返回409。审核记录持久化，财务服务重启后仍按同一任务编号去重。

业务调用端使用与财务服务相同的响应模型校验数据，并核对预算项目、写回任务编号、决定和原因是否与请求一致。缺失字段、非法金额、非CNY币种、错误模拟标记或不匹配的审核凭证会产生`failed`任务和失败日志；不会将HTTP 200直接视为业务成功。写回失败时保留已作出的审核决定，`result`中不写入未经校验的凭证。

## 一致性核对

交互式完整字段契约见运行后的两个服务 `/docs`。改动后按下述顺序核对：`schemas.py` → 本文件 → `frontend/src/api.ts` → `backend/tests` → `scripts/verify_system.py` → `scripts/verify_integration.py`。

联调现场按顺序执行：`scripts/verify_system.py`（标准端口 43 项，六个演示步骤与接口契约）→ `scripts/verify_integration.py`（51 项，状态事件序列、账号权限矩阵、解析快照与冲突、审批补正、指标交叉核对、凭证回溯）→ `scripts/verify_restart.py`（19 项，重启保留与续跑、中断标记与受限恢复）。集成版本与实测结果见 `docs/INTEGRATION.md`。


## D2/D3：解析关联、冲突确认与补正

`POST /api/parse`为每次提取生成`parse_id`，保存当前身份、去首尾空白后的说明摘要及完整结果。响应始终增加`parse_id`、`amount_candidates`、`conflicts`、`requires_manual_review`和`latency_ms`。候选保存完整原文及是否有效；负值、多余小数或指数不会被截成合法金额。解析快照保留至验收结束。

创建请求在原六个字段上支持可选`parse_id`、`conflict_acknowledged`（默认false）、`confirmation_note`（最多500字符）。解析编号必须属于当前用户且匹配原说明，否则422。未提供编号时明确走`structured_input`，仍执行服务端金额及类别冲突复核。说明与申报值不一致、有多个金额或候选非法时，须同时提供`conflict_acknowledged:true`及非空的具体确认说明，否则422且不建任务、不调用财务。仅`confirmed:true`不能代替具体冲突确认。

金额统一规范为两位小数字符串再保存、比较。相同提交标识始终比较`submission_payload`，不比较补正后的`payload`；因此补正后重放原创建请求仍返回同一任务且不再执行工具。

模型不可用、配置错误或输出值校验异常会留下`fallback_event`或`violations`，`requires_manual_review:true`保存在服务端快照。员工确认不消除此标记。预算不足优先拒绝；预算充足时，超1000元、缺依据及模型异常的全部原因保存在`result.manual_reasons`，进入待审批并停止写回。

审批补正示例：

```json
{"decision":"approve","comment":"已核对原始用途与个人承担部分","corrected_fields":{"amount":"900.00"},"correction_reason":"原申报含个人承担部分","reviewed_fields":true}
```

`corrected_fields`仅允许project_id、category、amount、expense_date、description，不接受空对象、null值或其他字段。补正必须有1至500字符的非空`correction_reason`和`reviewed_fields:true`。拒绝请求不能补正；模型异常任务通过也必须显式核对。无补正且无模型异常的旧审批请求保持兼容。

补正与通过均重新按当前类别检索制度、重新查询预算。补正后缺依据须填写`manual_basis`；不能利用补正绕过5000元、日期、预算或权限硬规则。所有字段、依据、核对标记校验完成后，原子提交有效字段、补正记录、审批记录及running状态；非法请求422且任务和日志不变。并发审批只有一次生效，后续请求409。

## 写回检查点与受限恢复

每次调用`POST /results`之前先提交`pending_writeback`和writing_back状态。只有验证有效凭证后才进入succeeded或rejected并清除待写回快照。超时、无效响应或内容冲突进入failed，保留原决定、快照及可读失败阶段；不伪造凭证。

启动时，awaiting_approval保持原状；遗留queued、running、writing_back标记为failed，记录`process_interrupted`、原状态及阶段，不自动重发请求。受限运维脚本见`docs/DEPLOYMENT.md`：写回仅按原编号及原载荷重放，查询阶段可在确认没有写回检查点后恢复；业务终态不可重开。普通用户没有恢复HTTP接口。
