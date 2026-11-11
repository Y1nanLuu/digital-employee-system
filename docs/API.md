# V0.5 API契约

业务端FastAPI /docs提供完整字段结构。Basic身份employee/approver/admin，演示密码demo123；财务服务使用Bearer FINANCE_KEY。预置身份决定所属员工和职级，客户端不能冒用。

| 接口 | 作用 |
| --- | --- |
| GET /health、/api/me、/api/policy | 运行版本、身份、生效制度及参数 |
| POST /api/invoices | multipart文件上传；保存原件后识别，返回file_id、invoice_id、字段、读取方法和提示 |
| GET /api/files/{file_id} | 文件名、大小、类型及摘要元数据 |
| GET /api/files/{file_id}/content | 通过授权读取原PDF/图片；private/no-store，记录读取日志 |
| POST /api/batches | 以1—10个file_ids创建员工批次 |
| GET /api/batches | 本人批次和各项草稿、提交状态；补偿提交响应丢失 |
| PUT /api/batches/{batch_id}/items/{index} | 保存该张草稿；提交成功由服务端协调状态 |
| POST /api/tasks | request_id、submission_payload及识别快照invoice_id；201，新任务固定当前制度 |
| GET /api/tasks、/api/tasks/{task_id} | 可见任务、确认字段、制度、初审、工具、流转、修订和凭证 |
| POST /api/tasks/{task_id}/approval | approve/reject/return、comment、reviewed_fields等；审批人无权改申报字段 |
| PUT /api/tasks/{task_id}/draft | 仅本人returned任务保存修改草稿 |
| POST /api/tasks/{task_id}/resubmit | 原任务request_id、submission_payload、revision_reason；追加revision并重跑 |
| GET /api/metrics | 完成率、处理耗时、人工率、端到端耗时、原因分布 |

submission_payload含project_id、department、category、amount、expense_date、description、confirmed、file_id、invoice、details。invoice保存invoice_no/code/date、buyer、seller、total、item_name；details按住宿、招待、餐补等类别保存城市日期、人数、事前审批、行程、服务期间等。金额用十进制字符串；票面total与本次amount独立，申报(0,5000]且最多两位小数。九类费用见生效制度JSON。

返回409表示标识正文冲突、重复有效票据或不允许的状态变更；422为字段格式；403为越权。上传先校验发票及关键字段，明显不清晰、非发票、解析失败均拒收且不写业务库。预览只有上传者或关联任务的合法审批/管理身份可读。

## 模拟财务API

| 接口 | 行为 |
| --- | --- |
| GET /budgets/{project_id} | 项目可用预算；只查询 |
| POST /checks/invoice | 号码/代码、日期、总额对照模拟台账，valid/invalid/unknown，simulated=true |
| POST /checks/duplicate | 票号、文件摘要精确重复和同人同日同额疑似重复 |
| POST /reviews/results | 以task_id幂等写回最终结果，通过时唯一登记票号和原件摘要 |

精确字段以财务8001/docs及schemas.py为准；原POST /results仅供历史demo-v1协议兼容。每次预算、查验、查重、写回由业务工具封装记录输入、输出、时长、重试及revision。人工通过前重新查询三个校验工具。

## 流转与指标

queued→running→awaiting_approval或writing_back→succeeded/rejected；return进入returned，员工重提回到running；工具故障failed可按受控恢复命令处理。业务日志记录具体解析、检索、工具、初审步骤，前端展示中文状态。

completed_rate=(succeeded+rejected)/全部任务；人工率=进入过人工环节/全部任务；processing耗时扣除awaiting_approval和returned等待，end_to_end耗时保留。分母为零返回null；业务拒绝单列，不混为系统执行失败。


最新交互契约补充：Task增加business_no，内部id保持UUID。POST /api/tasks/{id}/withdraw仅允许本人returned/awaiting_approval申请，进入cancelled且释放占用、保留档案，不写财务结果。管理员与审批人均可审批；生产v2人工reject归为return，页面仅显示通过和驳回。上传先内存检查，失败422且不保存任何原件、快照或任务；仅合格项进入批次。处理本张的HTTP请求不锁住下一张草稿保存。

最新补充：发起报销不展示历史申请或历史批次选择，仅恢复当前会话未完成草稿。历史统一在我的报销。状态筛选仅已通过、待审批、驳回待修改三类（可查看全部）。上传归档前及队列创建前查重，重复时提示原业务编号；已通过禁止重报，可撤销原申请先撤销再上传。同批重复项排除。invoice_files.invoice_fields完整保存原票号码、日期、购销方、总额和明细，原始识别与任务确认值分别留存。新增AC61及对应待测用例，最终组合测试按用户要求留待集中执行。
