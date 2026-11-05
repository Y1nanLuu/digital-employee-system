# 接口约定

业务API统一前缀`/api`，JSON响应；HTTP Basic预置角色认证。时间戳为UTC ISO格式，前端按Asia/Shanghai显示。金额以十进制字符串传输，不能用二进制浮点计算金额。

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

创建请求示例：

```json
{"project_id":"P001","category":"交通费","amount":"300.00","expense_date":"2026-11-05","description":"客户现场往返交通费300元","confirmed":true}
```

`Idempotency-Key`为8至100字符，必须至少包含一个非空白字符。同一员工用相同标识和相同内容重试返回原任务；同一标识更换内容返回409。新申请必须用新标识。缺失、不满足长度或全为空白字符时返回422，不创建任务。

提交、列表、详情和审批接口统一使用`TaskResponse`响应模型。每笔新申请的`id`为UUID v4，数据库以此作为主键；`payload`保存确认字段，`status`为下文状态枚举，`result`保存处理结果，`citations`、`logs`、`events`和`approvals`分别保存依据、工具日志、状态事件与审批记录。另含`owner`、`created_at`、可为空的`finished_at`及`manual`字段。内部提交标识`request_key`不对外返回。

任务先生成编号并写入数据库，再同步执行规则及财务工具；工具失败仍保留同一编号和失败日志。`GET /api/tasks/{id}`可查询已完成、待审批和失败任务。员工仅可查询自己的记录，查询不存在或不可见的任务均返回404。进程重启后可继续查询已提交的记录；相同标识和内容的重试仍返回原任务，不重复执行财务调用。

审批请求示例：

```json
{"decision":"approve","comment":"用途明确，同意初审","manual_basis":""}
```

`decision`为approve或reject。comment不可为空；无制度依据且通过时，manual_basis不可为空。拒绝后只写回拒绝记录，不执行通过分支。

错误：401未登录，403无权限，404任务或项目不存在，409状态或幂等冲突，422输入不合法。工具故障产生failed任务，创建请求仍返回201，客户端必须查看任务状态。

状态：queued → running → awaiting_approval（可选） → running → writing_back → succeeded/rejected；技术失败进入failed。

## 模拟财务服务

`GET /budgets/{project_id}`、`POST /results`使用`Authorization: Bearer 工具密钥`，由后端调用。默认密钥为`local-finance-key`，可通过`FINANCE_KEY`环境变量覆盖；前端不直接持有密钥。缺失或错误的密钥返回401。

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

交互式完整字段契约见运行后的两个服务/docs。变更字段时同步更新schemas.py、frontend/src/api.ts及测试。
