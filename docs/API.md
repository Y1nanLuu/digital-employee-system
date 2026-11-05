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

`Idempotency-Key`为8至100字符。同一员工用相同标识和相同内容重试返回原任务；同一标识更换内容返回409。新申请必须用新标识。

审批请求示例：

```json
{"decision":"approve","comment":"用途明确，同意初审","manual_basis":""}
```

`decision`为approve或reject。comment不可为空；无制度依据且通过时，manual_basis不可为空。拒绝后只写回拒绝记录，不执行通过分支。

错误：401未登录，403无权限，404任务或项目不存在，409状态或幂等冲突，422输入不合法。工具故障产生failed任务，创建请求仍返回201，客户端必须查看任务状态。

状态：queued → running → awaiting_approval（可选） → running → writing_back → succeeded/rejected；技术失败进入failed。

财务服务：GET /budgets/{project_id}、POST /results。使用Authorization: Bearer工具密钥，由后端调用，前端不直接持有密钥。写回内容为task_id、decision（approved/rejected）及reason。task_id重复且内容相同返回原凭证，不同内容返回409。

交互式完整字段契约见运行后的两个服务/docs。变更字段时同步更新schemas.py、frontend/src/api.ts及测试。
