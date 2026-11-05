# 面向中小企业的数字员工系统

费用报销初审场景的可运行开发框架。包含Vue 3 + TypeScript前端、FastAPI业务后端、独立模拟财务API和Docker Compose配置。

当前版本是规则基线，不包含真实LLM调用、发票识别或资金支付。模型接口预留在`backend/app/ai.py`，制度检索预留在`backend/app/knowledge.py`。

## 已实现的基础链路

员工提交确认后的字段 → 制度条款检索 → 外部预算查询 → 自动初审或人工审批暂停 → 审批后续跑 → 模拟结果写回 → 查看日志和指标。

- 金额范围为(0,5000]，最多两位小数；超过1000元进入审批。
- P001预算10000元，P002预算500元；只查询，不扣减。
- 缺少制度依据转人工，通过时必须补充人工依据。
- 审批人不可审批本人任务；管理员不自动拥有审批权限。
- 待审批任务、审批记录及模拟财务写回记录持久化；重复提交和重复审批受控。
- 规则仍以产品组确认后的业务规则为准，当前参数使用D1草案。

## 环境

- Python 3.11（推荐），Node.js 22.12及以上。
- 本地模式使用SQLite，不要求安装PostgreSQL或启动Docker。
- Compose模式需要Docker Engine / Docker Desktop正在运行，业务库使用PostgreSQL。
- 首次安装依赖及拉取镜像需要网络。

## 本地启动 Windows PowerShell

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

然后在三个终端中分别执行，工作目录都保持在项目根目录：

```powershell
# 终端一：模拟财务服务
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 finance
```

```powershell
# 终端二：业务服务
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 backend
```

```powershell
# 终端三：前端
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 frontend
```

浏览器打开 http://localhost:5173 。后端接口文档为 http://127.0.0.1:8000/docs ，模拟财务接口文档为 http://127.0.0.1:8001/docs 。接口文档中的业务接口须先点击Authorize填写演示账号。

三个角色账号分别为`employee`、`approver`、`admin`，默认密码均为`demo123`。这些是课程演示账号，禁止用于真实业务。退出后切换角色即可完成审批演示。

可复制`.env.example`为`.env`自定义演示密码和工具密钥。`dev.ps1`只读取这三个允许的配置项；本地数据库与服务地址可通过当前终端的环境变量`DATABASE_URL`、`FINANCE_DATABASE_URL`和`FINANCE_URL`覆盖。直接运行uvicorn时不会自动读取.env。

数据存储在`backend/data/tasks.db`和`backend/data/finance.db`，不会因停止服务而丢失。按Ctrl+C停止对应服务。前端未启用自动轮询，操作后自动刷新，其他账号的操作可点击“刷新”获取。

## Docker Compose

```powershell
Copy-Item .env.example .env
# 如已有.env，请保留现有文件，不要覆盖。
docker compose up --build -d
```

打开 http://localhost:8080 。数据库和模拟审核记录使用命名卷持久化。停止服务使用`docker compose down`，不要附加`-v`，以免删除数据。

## 验证

```powershell
# 项目根目录
.\.venv\Scripts\python.exe -m pytest -c backend/pytest.ini backend/tests -q
# 真实HTTP链路与进程重启测试，使用临时数据，不影响演示任务
.\.venv\Scripts\python.exe scripts/smoke.py
cd frontend
npm.cmd run build
```

集成测试使用独立临时SQLite数据库，以及连接两个FastAPI应用的HTTP模拟传输，验证接口契约和状态流转，不覆盖个人开发数据。Docker镜像构建与PostgreSQL联调需在Docker可运行环境另行验证。

## 演示步骤

1. 用employee登录，确认交通费300元后提交：初审通过，出现制度、预算和写回日志。
2. 提交办公用品1500元：进入待审批；退出后用approver登录，填写意见并通过：同一任务续跑并写回。
3. 再提交1500元任务，审批人选择拒绝：记录理由并进入初审拒绝状态。
4. 用P002提交600元：预算不足，拒绝初审。
5. 选择“其他”类别提交300元：进入待审批，通过时须补充人工依据。
6. 用admin查看指标：技术执行完成率包含拒绝任务；转人工率按任务去重；系统耗时不含人工等待。

## 目录和成员接口

```text
backend/app/main.py          任务、审批、指标API入口
backend/app/models.py        任务及审计数据模型
backend/app/services.py      执行链路、审批恢复、财务工具适配
backend/app/ai.py            字段理解接口（规则基线，LLM待接入）
backend/app/knowledge.py     制度检索接口
backend/app/data/rules.json  模拟制度demo-v1
backend/finance/main.py      独立模拟财务API
backend/tests/              流程、权限、边界、异常及幂等测试
frontend/src/App.vue        员工端概览、报销提交、任务追踪及审批/指标兼容页面
frontend/src/api.ts         前端接口类型和请求适配
scripts/                   依赖安装及前台启动脚本
docs/                      接口约定、架构、分工和后续任务
compose.yaml               PostgreSQL及三服务编排
```

卢亦楠维护需求和验收；张程量负责架构、数据库及集成；王可扩展员工端；王丹蕊扩展审批台与指标；周勃负责业务状态机和财务工具；夏一洲接入AI并完善检索；王欣茹扩展测试与质量材料。

员工端支持表单草稿恢复、金额/日期/说明校验、字段提取建议、提交幂等保护、任务搜索与状态筛选，以及制度依据和执行时间线展示。王可的实现说明、自测清单与个人总结见 docs 目录。

## 现有限制和下一步

- 这是D1框架，不能当作最终课程交付。未接LLM，未做复杂语义冲突识别、人工字段补正、审批截止时间或完整业务复核。
- 目前表单由用户勾选确认后提交；规则解析只给建议，不自动改写金额。模型接入后须补全冲突检测和结构校验。
- 执行暂为同步处理，使用单个服务进程；本地SQLite靠进程锁串行处理写操作，PostgreSQL审批增加行锁。暂不支持多副本或多worker。
- 审批等待可以跨重启恢复；若进程在实际工具调用中途崩溃，任务恢复、重试和写回对账仍待实现，不能直接重复执行已发生副作用。
- 模拟财务写回使用任务编号幂等；数据库初始化目前使用create_all，正式修改表结构前补迁移脚本。
- 使用HTTP Basic演示认证；仅绑定本机。真实部署前替换认证、落实HTTPS和更细粒度的数据权限。
- 任务列表无分页，日志未做脱敏扩展，测试集尚须对照课程检查表补齐。

技术参考：[FastAPI HTTP Basic](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/)、[SQLAlchemy引擎配置](https://docs.sqlalchemy.org/en/20/core/engines.html)、[Vite环境要求](https://vite.dev/guide/)。
