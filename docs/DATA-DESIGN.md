# 数据设计

面向中小企业数字员工系统 D1 版数据设计。覆盖业务库与模拟财务库两张表的字段契约、JSON 列语义、状态机、约束与演进路径。字段变更须同步 `backend/app/models.py`、`backend/app/schemas.py`、`docs/API.md`、`frontend/src/api.ts` 与测试。

## 一、两个数据库

| 库 | 用途 | 默认地址（本地） | Compose 地址 |
|---|---|---|---|
| 业务库 | 报销任务、审批、日志、指标来源 | `sqlite:///./data/tasks.db`（`DATABASE_URL`） | `postgresql+psycopg://employee:***@db:5432/employee` |
| 模拟财务库 | 按任务编号唯一的审核凭证 | `sqlite:///./data/finance.db`（`FINANCE_DATABASE_URL`） | 命名卷 `finance-data:/app/data` |

两库物理分离：业务服务只通过 HTTP 调用财务服务，不直连财务库。业务库不保存资金流水，模拟财务服务也不回写业务库，核对靠 `task_id`。

## 二、业务库表 `tasks`

### 字段

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | String(36) | 主键 | 任务编号，UUID4 字符串，贯穿页面、日志、审批与财务凭证 |
| `owner` | String(40) | 非空 | 提交人用户名，员工端只能看到本人任务 |
| `request_key` | String(100) | 非空，与 `owner` 组成唯一约束 | 客户端传入的 `Idempotency-Key`，8–100 字符 |
| `status` | String(30) | 非空，建索引 | 七状态之一，见状态机 |
| `payload` | JSON | 非空 | 用户确认后的报销字段，内容等于 `ClaimInput` 的 JSON 序列化 |
| `result` | JSON | 默认 `{}` | 初审结论、预算、写回凭证或错误信息 |
| `citations` | JSON | 默认 `[]` | 引用的制度条款（含人工补充依据） |
| `logs` | JSON | 默认 `[]` | 工具调用日志（请求、响应、耗时、成败） |
| `events` | JSON | 默认 `[]` | 状态事件时间线 |
| `approvals` | JSON | 默认 `[]` | 人工审批记录 |
| `created_at` | String(40) | 非空 | 任务创建时间 |
| `finished_at` | String(40) | 可空 | 终态时间；`awaiting_approval`、`failed` 之外未结束时为空 |
| `approval_started_at` | String(40) | 可空 | 进入待审批的时间，用于计算人工等待时长 |
| `wait_seconds` | Float | 默认 0.0 | 累计人工等待秒数，只统计审批暂停时长 |
| `manual` | Boolean | 默认 false | 是否发生过转人工，指标“转人工率”按此去重统计 |

### 约束与索引

- `UniqueConstraint('owner', 'request_key')`：同一员工同一幂等标识只能存在一个任务。重复提交且内容相同返回原任务，内容不同返回 409。该约束是幂等性的最终保证，进程级锁只是减少冲突。
- `status` 建普通索引：列表与指标按状态过滤。
- 未建外键：D1 版本没有独立用户表、项目表，`owner` 与 `project_id` 均为受约束字符串（项目枚举 `P001`、`P002`）。

### 六个 JSON 列的契约

`payload`（只写一次，创建时确定）：

```json
{"project_id":"P001","category":"交通费","amount":"300.00","expense_date":"2026-11-05","description":"客户现场往返交通费300元","confirmed":true}
```

金额为十进制字符串，禁止二进制浮点；`confirmed` 固定为 `true`。

`result`（键按执行阶段增量合并）：`decision`（`approved`/`rejected`）、`reason`、`mode`（当前固定 `rule_baseline`）、`budget`（财务服务返回的预算快照）、`receipt`（写回凭证）、`error`（技术失败信息）。失败任务保留 `error` 且不覆盖已有结论。

`citations`：`id`、`text`、`source`、`version`。人工补充依据记为 `id=MANUAL`、`source=审批人用户名`、`version=审批时间`，与制度条款同结构，保证表述一致。

`logs`：`at`、`tool`（路径）、`method`、`request`、`success`、`response` 或 `error`、`duration_ms`。每次外部调用必须成对记录请求与结果；工具密钥不入库。

`events`：`at`、`status`、`message`。状态变化必须同时落事件，前端时间线直接消费该列。

`approvals`：`at`、`actor`、`decision`、`comment`、`manual_basis`。只允许对 `awaiting_approval` 任务追加，一次终态后不再追加。

### 时间表示

所有时间为 UTC ISO 8601 字符串，存 `String(40)` 而非数据库时间类型。原因：SQLite 无时区类型，字符串可保证两库一致，前端统一按 Asia/Shanghai 展示。迁移到 PostgreSQL 时可评估改用 `timestamptz`，但须同步序列化与测试，非 D1 范围。

## 三、状态机

```text
queued → running → writing_back → succeeded
                 ↘ rejected                （预算不足或审批拒绝时同样经写回）
queued → running → awaiting_approval → running → writing_back → succeeded / rejected
queued → running → failed                （外部工具调用失败）
```

- `queued` 与 `running` 是执行过程中的瞬态，创建请求在同步执行结束后才返回，客户端通常直接看到终态或 `awaiting_approval`。
- `awaiting_approval` 可跨进程重启恢复：任务落库后才调用外部服务，审批状态不依赖内存。
- `failed` 为技术失败，创建请求仍返回 201，`result.error` 记录失败原因，客户端必须检查任务状态。
- `wait_seconds` 在离开 `awaiting_approval` 时累加；指标中的“系统耗时”为端到端时长减去人工等待，避免人工时间污染自动处理性能。

## 四、模拟财务库表 `receipts`

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `task_id` | String(36) | 主键 | 与业务库任务编号一一对应 |
| `data` | JSON | 非空 | `task_id`、`decision`（`approved`/`rejected`）、`reason` |

写回按 `task_id` 幂等：同一编号相同内容返回原凭证，不同内容返回 409。这是“不重复执行已发生副作用”的数据基础，也是后续中断恢复对账的依据。

写回请求的 `task_id` 必须为 UUID，`decision` 仅允许 `approved` 或 `rejected`，`reason` 为 1–1000 字符且不可全为空白；非法输入返回 422 且不落库。业务端使用与财务服务相同的响应模型校验预算项目、任务编号、决定与原因，不一致按技术失败处理：产生 `failed` 任务和失败日志，不把 HTTP 200 直接当作业务成功，也不在 `result` 中写入未经校验的凭证。

预算不落库：`/budgets/{project_id}` 返回固定样例值（`P001` 10000.00、`P002` 500.00），只查询不扣减。

## 五、建表与迁移

- 当前使用 `Base.metadata.create_all(engine)`，在服务启动时建表，无版本化迁移。
- D1 决定：字段未冻结前不引入迁移框架，避免每天改动都产生迁移脚本与团队额外成本。
- 触发条件：D4 功能冻结后若还有结构变更，引入 Alembic，并把 `create_all` 换成 `alembic upgrade head`。改动前须先写迁移说明，禁止直接改已有表结构。
- SQLite 到 PostgreSQL 的差异：`JSON` 在 SQLite 存文本，在 PostgreSQL 存 `jsonb`；`String` 长度约束在 SQLite 不强制。开发与联调须至少在 PostgreSQL 上跑一次（Compose 路径），避免只在 SQLite 上验证。

## 六、并发与锁

- 进程级 `RLock` 包住创建与审批的读—改—写，SQLite 版本限制为单进程。
- PostgreSQL 上审批使用 `SELECT ... FOR UPDATE` 行锁；唯一约束作为幂等最终兜底。
- 已知限制：多 worker 或多副本时进程锁失效，只剩数据库行锁与唯一约束，需另行设计（记入待开发事项）。

## 七、数据重置（演示前必读）

删除两个库文件后重启服务即可回到初始状态，模拟财务服务会随启动重建表：

```powershell
Remove-Item backend/data/tasks.db, backend/data/finance.db -ErrorAction SilentlyContinue
```

Compose 模式使用命名卷，重置需 `docker compose down` 后删除 `postgres-data`、`finance-data` 卷；日常停服务只用 `docker compose down`，不要加 `-v`。

## 八、演进方向

| 方向 | 触发时机 | 说明 |
|---|---|---|
| 日志与审批拆独立表 | 查询、分页或审计需求出现时 | 当前 JSON 列只适合小数据量与整表读取；独立表可支撑按工具、状态、时间检索 |
| 任务列表分页 | 任务数超过百条 | 现在全量返回，`created_at` 降序 |
| 迁移脚本 | D4 结构冻结 | 见第五节 |
| 中断恢复与对账 | D3 执行可靠性任务 | 依据 `logs` + `receipts` 判定副作用是否已发生 |
