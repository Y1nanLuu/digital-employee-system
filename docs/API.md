# 接口约定

业务API统一前缀`/api`，JSON响应；HTTP Basic预置角色认证。时间戳为UTC ISO格式，前端按Asia/Shanghai显示。金额以十进制字符串传输，不能用二进制浮点计算金额。字段、状态与错误码变更时同步`backend/app/schemas.py`、`frontend/src/api.ts`、本文件与测试。

## 端点清单

| 方法 | 地址 | 权限 | 用途 |
|---|---|---|---|
| GET | /health | 无 | 数据库连接与服务健康 |
| GET | /api/me | 登录用户 | 当前身份和角色 |
| POST | /api/parse | 登录用户 | 字段提取建议，当前mode=rule_baseline |
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
| `amount` | 十进制字符串 | 大于 0 且不超过 5000，最多两位小数 |
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

字段提取请求（`ParseInput`）为 `{"description":"..."}`，1–500字符。响应契约：

| 字段 | 说明 |
|---|---|
| `mode` | 当前固定 `rule_baseline`，接入模型后用于区分来源 |
| `suggested_amount` | 唯一可识别的金额，识别不到为 `null` |
| `suggested_category` | 唯一可识别的类别，识别不到为 `null` |
| `requires_confirmation` | 固定 `true`：建议只是建议，必须由用户确认后提交 |
| `message` | 面向用户的提示，说明当前为规则基线 |

## 任务对象响应字段

`GET /api/tasks`、`GET /api/tasks/{id}`、创建与审批响应返回同一结构：

| 字段 | 说明 |
|---|---|
| `id` | 任务编号（UUID4），贯穿页面、日志、审批与财务凭证 |
| `owner` | 提交人用户名 |
| `status` | 七状态之一，见下节 |
| `payload` | 确认后的报销字段 |
| `result` | `decision`、`reason`、`mode`、`budget`、`receipt`、`error` |
| `citations` | 引用条款：`id`、`text`、`source`、`version`；人工依据记为 `id=MANUAL` |
| `logs` | 工具调用日志：`at`、`tool`、`method`、`request`、`success`、`response`或`error`、`duration_ms` |
| `events` | 状态事件：`at`、`status`、`message` |
| `approvals` | 审批记录：`at`、`actor`、`decision`、`comment`、`manual_basis` |
| `created_at` | 创建时间（UTC ISO） |
| `finished_at` | 终态时间，未结束为 `null` |
| `manual` | 是否发生过转人工 |

`request_key`、`approval_started_at`、`wait_seconds` 为服务端内部字段，不对外暴露；人工等待时长只用于指标口径。

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

交互式完整字段契约见运行后的两个服务 `/docs`。改动后按下述顺序核对：`schemas.py` → 本文件 → `frontend/src/api.ts` → `backend/tests` → `scripts/verify_system.py`。联调现场用 `scripts/verify_system.py` 对标准端口服务执行 36 项断言，覆盖六个演示步骤、校验、权限、幂等、指标与工具鉴权。
