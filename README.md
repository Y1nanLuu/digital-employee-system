# 面向中小企业的数字员工系统

费用报销初审场景的可运行开发框架。包含Vue 3 + TypeScript前端、FastAPI业务后端、独立模拟财务API和Docker Compose配置。

业务默认使用规则基线，可通过`AI_MODULE_ENABLED`接入AI提取适配层；真实模型调用证据需单独验收。当前不包含发票识别或资金支付，制度以demo-v1为准。

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
- 字段提取默认使用规则基线；需要接入 ai-module 的模型通道时，按 `docs/DEPLOYMENT.md` 第四节启动 8010 端口并设置 `AI_MODULE_ENABLED=true`，模型不可用会自动降级。

## 本地启动 Linux / macOS

在项目根目录创建独立环境并安装后端依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
```

然后在两个终端中分别启动服务，每个终端都从项目根目录开始：

```bash
# 终端一：模拟财务服务
cd backend
../.venv/bin/python -m uvicorn finance.main:app --host 127.0.0.1 --port 8001
```

```bash
# 终端二：业务服务
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

业务接口文档为 http://127.0.0.1:8000/docs ，财务接口文档为 http://127.0.0.1:8001/docs 。业务接口使用下文的演示角色账号；财务接口展开对应操作，在 `authorization` 请求头中填写 `Bearer local-finance-key`（如已设置 `FINANCE_KEY`，则替换其中的密钥）。

如需员工端页面，在第三个终端从项目根目录执行：

```bash
cd frontend
npm ci
npm run dev
```

浏览器打开 http://localhost:5173 。

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

Linux / macOS，在项目根目录执行：

```bash
.venv/bin/python -m pytest -c backend/pytest.ini backend/tests -q
.venv/bin/python scripts/smoke.py
```

Windows PowerShell：

```powershell
# 项目根目录
.\.venv\Scripts\python.exe -m pytest -c backend/pytest.ini backend/tests -q
# 真实HTTP链路与进程重启测试，使用临时数据，不影响演示任务
.\.venv\Scripts\python.exe scripts/smoke.py
# 对已启动的标准端口服务执行演示步骤与接口约定校验（需先启动8000与8001）
.\.venv\Scripts\python.exe scripts/verify_system.py
cd frontend
npm.cmd run build
```

`scripts/smoke.py`自行启动并停止临时服务与临时库，适合提交前门禁；`scripts/verify_system.py`校验标准端口上真实运行的整套服务，含六个演示步骤、校验、权限、幂等、指标与工具鉴权共36项断言。

集成测试使用独立临时SQLite数据库，以及连接两个FastAPI应用的HTTP模拟传输，验证接口契约和状态流转，不覆盖个人开发数据。Docker镜像构建与PostgreSQL联调需在Docker可运行环境另行验证。

`smoke.py` 在随机本机端口启动两个真实服务，验证唯一任务编号、按编号查询、两个服务重启后的任务及凭证持久化、重复提交、审批恢复和预算样例。该脚本访问本机服务时绕过代理，支持已设置 `HTTP_PROXY` / `HTTPS_PROXY` 的开发终端。

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
scripts/                   依赖安装、前台启动脚本及集成验证脚本
docs/                      接口约定(API.md)、架构与责任(ARCHITECTURE.md)、数据设计(DATA-DESIGN.md)、启动与部署约定(DEPLOYMENT.md)及成员材料
ai-module/                 AI与知识检索模块：字段提取（LLM+规则双轨）、制度库16条、检索方案（见ai-module/README.md）
compose.yaml               PostgreSQL及三服务编排
```

卢亦楠维护需求和验收；张程量负责架构、数据库及集成；王可扩展员工端；王丹蕊扩展审批台与指标；周勃负责业务状态机和财务工具；夏一洲接入AI并完善检索；王欣茹扩展测试与质量材料。

员工端支持表单草稿恢复、金额/日期/说明校验、字段提取建议、提交幂等保护、任务搜索与状态筛选，以及制度依据和执行时间线展示。实现说明与自测清单见 docs 目录。

报销提交、任务查询及模拟财务接口的本次实现记录和验收结果见 [周勃-后端开发日志](docs/周勃-后端开发日志.md)。

## 现有限制和下一步

- 当前已补齐解析与确认快照、确定性冲突复核、人工字段补正和受限故障处置；复杂语义理解、真实模型评测和完整课程交付仍须单独验收。
- 目前表单由用户勾选确认后提交；规则解析只给建议，不自动改写金额。服务端复核金额与类别冲突，须具体确认；模型降级仍转人工核对。
- 执行暂为同步处理，使用单个服务进程；本地SQLite靠进程锁串行处理写操作，PostgreSQL审批增加行锁。暂不支持多副本或多worker。
- 审批等待可以跨重启恢复；运行阶段中断会标记process_interrupted；按持久化检查点使用受限CLI恢复或对账，详见部署说明。自动重试调度仍未实现。
- 模拟财务写回使用任务编号幂等；数据库启动时执行幂等D2增量升级，旧SQLite表升级前自动备份，详见数据设计。
- 使用HTTP Basic演示认证；仅绑定本机。真实部署前替换认证、落实HTTPS和更细粒度的数据权限。
- 任务列表无分页，日志未做脱敏扩展，测试集尚须对照课程检查表补齐。

技术参考：[FastAPI HTTP Basic](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/)、[SQLAlchemy引擎配置](https://docs.sqlalchemy.org/en/20/core/engines.html)、[Vite环境要求](https://vite.dev/guide/)。


## D2业务链路与审批补正

- 提取结果持久化为解析编号，绑定当前用户和原说明；任务保存初始请求、确认及补正记录。
- 金额仅接收普通十进制字符串；负值、指数、布尔值和多余小数不会被截断或静默修正。
- 字段冲突需具体确认；模型异常标记随任务进入人工审批；全部转人工原因保留。
- 审批人可补正业务字段并填写原因，明确核对后重检制度和预算。原请求防重不受补正影响。
- 页面支持冲突确认、模型异常核对、补正与审计；提交结果不明时保留原请求和标识供重试。
- 写回前保存检查点，启动识别执行中断；恢复命令只按原编号和载荷处理。

本次实际开发与验收记录见[周勃-D2开发日志](docs/周勃-D2开发日志.md)。数据库升级与恢复方法见[部署说明](docs/DEPLOYMENT.md)。
