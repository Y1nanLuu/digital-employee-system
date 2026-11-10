# 启动约定与部署

本文件是团队的统一启动约定：进程与端口、环境变量、数据位置、验证命令与常见问题。命令以 Windows PowerShell 与项目根目录为基准，其他平台把路径分隔符换成对应形式。

## 一、技术栈基线

| 层 | 选型 | 实测版本 | 说明 |
|---|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite | Node 26.8.1、npm 11.19.0 | 要求 Node ≥ 22.12；`vue-tsc` 参与构建，类型错误直接构建失败 |
| 业务后端 | FastAPI + SQLAlchemy 2 + Pydantic 2 | Python 3.14.7 | 推荐 3.11 及以上；`Decimal` 处理金额，不用浮点 |
| 模拟财务服务 | FastAPI + SQLAlchemy | 同上 | 独立进程、独立库、独立工具密钥 |
| 数据库 | SQLite（本地）/ PostgreSQL 17（Compose） | psycopg 3.3.8 | 本地不要求安装数据库或 Docker |
| 测试 | pytest + 真实 HTTP 冒烟脚本 | pytest 9.2.0 | 使用临时库，不影响演示数据 |

## 二、进程与端口

| 进程 | 本地端口 | Compose 端口 | 启动命令 |
|---|---|---|---|
| 模拟财务 API | 8001 | 8001 | `scripts/dev.ps1 finance` |
| 业务后端 | 8000 | 8000 | `scripts/dev.ps1 backend` |
| 前端开发服务器 | 5173（代理 `/api` 到 8000） | — | `scripts/dev.ps1 frontend` |
| 前端静态站点（nginx） | — | 8080 | `docker compose up --build -d` |
| PostgreSQL | — | db:5432（仅容器网络） | 随 Compose 启动 |

所有服务只绑定 `127.0.0.1`。浏览器入口：本地 `http://localhost:5173`，Compose `http://localhost:8080`；接口文档 `http://127.0.0.1:8000/docs`、`http://127.0.0.1:8001/docs`。

## 三、环境变量

| 变量 | 默认值 | 作用域 | 说明 |
|---|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/tasks.db` | 业务后端 | 业务库连接串，PostgreSQL 用 `postgresql+psycopg://` |
| `FINANCE_DATABASE_URL` | `sqlite:///./data/finance.db` | 模拟财务 | 仅本地或测试覆盖使用 |
| `FINANCE_URL` | `http://127.0.0.1:8001` | 业务后端 | 后端调用财务服务的地址 |
| `FINANCE_KEY` | `local-finance-key` | 后端与财务 | 工具密钥，只允许后端持有，前端不接触 |
| `DEMO_PASSWORD` | `demo123` | 业务后端 | 三个演示账号的统一密码 |
| `POSTGRES_PASSWORD` | `local-postgres-password` | Compose | 仅 Compose 使用 |
| `AI_MODULE_ENABLED` | `false` | 业务后端 | 字段提取是否走 ai-module 模型通道；关闭时使用内置规则基线 |
| `AI_MODULE_CONFIG` | `ai-module/model_config.json` | 业务后端 | 模型通道配置路径，切换 `active` 即可换通道（deepseek/local_qwen/rule） |
| `AI_PYTHON` | 系统 `python` | `scripts/dev.ps1 ai` | 启动本地模型服务所用解释器，需含 `transformers`/`torch` |
| `APP_VERSION` | `0.4.0` | 业务后端 | 集成版本号，经 `GET /health` 暴露，便于验收确认运行版本 |
| `APP_STAGE` | `D4-baseline` | 业务后端 | 阶段标识，与版本号一并用于现场核对 |

约定：

- `.env.example` 是模板，复制为 `.env` 后生效；`.env` 不入库。
- `scripts/dev.ps1` 只读取 `DEMO_PASSWORD`、`FINANCE_KEY`、`POSTGRES_PASSWORD`、`AI_MODULE_ENABLED`、`AI_MODULE_CONFIG` 五项；`DATABASE_URL`、`FINANCE_DATABASE_URL`、`FINANCE_URL` 通过当前终端环境变量覆盖。
- 直接运行 uvicorn 不会自动读取 `.env`，需要先在终端设置环境变量。
- 任何密钥、真实账号凭据不得写入代码、文档和日志；远程模型密钥只通过环境变量注入。

演示账号：`employee`、`approver`、`admin`，密码均为 `DEMO_PASSWORD`。账号为课程演示用，禁止用于真实业务。

## 四、启动步骤

首次准备依赖（需要网络）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

本地模式三个终端，工作目录均为项目根目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 finance
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 backend
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 frontend
```

### 可选：启用 ai-module 字段提取

默认关闭，不需要模型也能完整运行。启用时增加一个终端并打开开关：

```powershell
# 终端四：本地模型服务（端口8010，需要含 transformers/torch 的解释器）
$env:AI_PYTHON = 'C:\Python311\python.exe'   # 指向具备模型依赖的解释器
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 ai

# 业务后端：在 .env 中设 AI_MODULE_ENABLED=true，或在启动前设置同名环境变量
```

三条使用路径：

| 目的 | 做法 | `/api/parse` 表现 |
|---|---|---|
| 不接模型（默认） | `AI_MODULE_ENABLED=false` | `ai_enabled=false`，`mode=rule_baseline` |
| 计划内纯规则 | `model_config.json` 的 `active` 设为 `rule` | `ai_enabled=true`、`source=rule`，可拿到 8 个结构化字段 |
| 模型不可用时 | 保持 `AI_MODULE_ENABLED=true` 但不启动 8010 | 预检失败，`fallback_event.trigger=llm_preflight_unreachable`，业务字段由规则兜底 |

没有 `transformers`/`torch` 环境时只影响模型通道：预检失败即降级，业务链路不中断，接口不会因此变慢（预检 0.4 秒上限）。

Compose 模式（需要 Docker 运行）：

```powershell
Copy-Item .env.example .env    # 若已有 .env，保留现有文件不要覆盖
docker compose up --build -d
```

停机：本地按 `Ctrl+C`；Compose 用 `docker compose down`，不要加 `-v`，否则删除数据卷。

## 五、验证命令

```powershell
# 单元与流程测试（临时库，覆盖流程、权限、边界、异常、幂等、迁移与 AI 适配层）
.\.venv\Scripts\python.exe -m pytest -c backend/pytest.ini backend/tests -q

# 真实 HTTP 链路：启动两个服务、校验链路、杀死业务进程后验证审批恢复
.\.venv\Scripts\python.exe scripts/smoke.py

# 前端类型检查与生产构建
cd frontend; npm.cmd run build

# 以下两项需先启动 8000 与 8001
# 演示场景与接口约定校验
.\.venv\Scripts\python.exe scripts/verify_system.py
# D3 集成面：状态事件序列、权限矩阵、解析快照与冲突、审批补正、指标交叉核对
.\.venv\Scripts\python.exe scripts/verify_integration.py

# 重启与受限恢复（自起自停临时服务，含进程中断与 CLI 恢复）
.\.venv\Scripts\python.exe scripts/verify_restart.py
```

四个脚本的分工：`smoke.py` 与 `verify_restart.py` 自起自停临时服务，可作为提交前门禁；`verify_system.py` 校验标准端口上真实运行的整套服务（六个演示步骤）；`verify_integration.py` 校验跨模块集成面。集成版本与实测汇总见 `docs/INTEGRATION.md`。

## 六、实测结果（D4 基准 0.4.0）

在 Windows 11 + Python 3.14.7 + Node 26.8.1 上执行，代码基线为 D4 冻结日的 `master`；运行版本可从 `GET /health` 的 `version`/`stage` 字段确认。

| 项目 | 结果 |
|---|---|
| `scripts/setup.ps1` 等价步骤（venv + 后端依赖、`npm ci`） | 通过，无漏洞报告 |
| `pytest backend/tests` | 131 项全部通过（含发票上传 13 项、D3 后端用例） |
| `scripts/smoke.py` | 通过：解析快照、原子补正、初始请求重放、写回中断检测与 CLI 对账、预算样例与不扣减、审批续跑、指标 |
| `npm run build` | 通过，产物 `dist/`（js 103.58 kB、css 13.54 kB） |
| 本地三服务启动 | 通过：8000/8001 健康检查 200，5173 首页 200 且 `/api` 代理到 8000 |
| `scripts/verify_system.py` | 46 项断言全部通过（六个演示步骤 + 校验、权限、幂等、指标、工具鉴权、任务编号贯穿、AI 开关状态） |
| `scripts/verify_integration.py` | 51 项断言全部通过（健康与版本、四类场景事件序列、权限矩阵、解析快照与冲突、审批补正、指标交叉核对、凭证回溯） |
| `scripts/verify_restart.py` | 19 项断言全部通过（重启保留与续跑、幂等重放、执行中任务标记中断、受限恢复、终态拒绝） |
| 发票 PDF 上传（端到端） | 14 项断言通过：真实模型抽取、冲突拦截、显式确认后自动通过、任务快照保留发票来源 |
| Docker Compose | **未验证**：当前环境无 Docker 引擎；`compose.yaml` 已按 D4 要求补齐版本与模型开关变量，需在有 Docker 的机器上按下面清单执行一次 |
| 本地模型通道（8010） | **未验证**：开发机无 `transformers`/`torch`；远程 DeepSeek 通道已实测 |

### Docker / PostgreSQL 核查清单（需 Docker 环境执行）

```powershell
Copy-Item .env.example .env          # 已有 .env 则保留，不要覆盖
docker compose config                # 1) 校验编排文件与变量展开
docker compose build                 # 2) 构建 backend/frontend 镜像（含 pypdf 依赖）
docker compose up -d                 # 3) 启动四服务
docker compose ps                    # 4) 四个容器应为 healthy
curl.exe http://127.0.0.1:8000/health    # 5) 应返回 status=ok、database=postgresql、version=0.4.0
curl.exe http://127.0.0.1:8080/          # 6) 前端应能打开（nginx 代理 /api）
# 7) 走一遍六步演示（见 README），确认 PostgreSQL 上的迁移、审批续跑与指标
docker compose down                  # 8) 停服务（不要加 -v，以免删数据卷）
```

已知前提：镜像构建上下文是 `./backend`，**不包含仓库根目录的 `ai-module`**，因此 Compose 模式默认按规则基线运行，模型通道请用本地模式（`.env` + `dsapi.txt`）。

### 干净目录启动清单

1. 复制或克隆仓库到新目录，确认没有 `.venv/`、`frontend/node_modules/`、`backend/data/`、`.env`、`dsapi.txt`。
2. `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1`（需要网络）。
3. 三个终端分别执行 `scripts/dev.ps1 finance|backend|frontend`。
4. 核对 `http://127.0.0.1:8000/health` 返回 `status=ok`、`version=0.4.0`；打开 `http://localhost:5173` 用 `employee/demo123` 登录。
5. 提交一笔 300 元交通费，确认状态为「初审通过」并出现制度、预算与写回日志。

2026-11-09周勃后续Linux复核：后端118项、现场脚本46项（含历史数据连续运行）、集成51项、重启恢复19项通过，前端构建和浏览器四组验证通过。上述Windows表格属于原环境记录，Windows编码修复仍需实机复验；本轮已在Linux模拟CP936默认文本解码并通过。详见[集成复核补充](INTEGRATION.md)和[验收证据](test-evidence/D3/zhoubo-20261109-summary.json)。

## 七、已知问题与常见故障

- 端口被占用（`[Errno 10048]`）：先 `Get-NetTCPConnection -LocalPort 8000 -State Listen`，再按 PID 停掉残留进程后重启；本地固定使用 8000、8001、5173、8010。
- `scripts/dev.ps1` 若报“意外的标记 `}`”：说明脚本被存成了无 BOM 的 UTF-8 且含中文注释，Windows PowerShell 5.1 会按 GBK 解码。启动脚本保持 **纯 ASCII 注释**（当前已是），中文说明写在 `.env.example` 与 docs 里。
- SQLite 单进程限制：不要同时起两个业务后端指向同一个 `tasks.db`，写操作会互相干扰。
- `backend/data/` 与 `frontend/node_modules/`、`frontend/dist/`、`work/` 均不入库；换机器后必须重新执行依赖准备。
- 数据重置：删除 `backend/data/tasks.db`、`backend/data/finance.db` 后重启；Compose 见 `docs/DATA-DESIGN.md` 第七节。
- 任务跨账号、跨角色变化不会自动刷新，需要点击“刷新”；轮询或推送不在本期范围。
- Compose 首次启动较慢，`db`、`finance` 均带健康检查，后端会等待其就绪；若失败先看 `docker compose logs finance`。
- 启用模型通道后若 `/api/parse` 明显变慢，先确认 8010 是否真的在监听；远程通道的超时由 `ai-module/model_config.json` 的 `timeout_seconds` 控制，演示环境建议保持 `AI_MODULE_ENABLED=false`。
- `/api/parse` 的 `suggested_amount` 为 null 常被误判为模型故障：建议只来源于说明文字，表单里填的金额不参与解析，把金额写进说明即可。
- 发票上传返回“未检测到文字层”：该 PDF 是扫描件或图片型，当前版本不支持 OCR，请改用文字版电子发票或手工填写。
- 发票原文摘要出现乱码：该 PDF 字体缺少 ToUnicode 映射，抽取质量无法保证，请改用可正常复制的电子发票导出件。
- `verify_system.py` 的“指标已完成数与任务总数一致”隐含干净库假设，在带历史任务的库上会失败；现场执行前先重置演示库，或改用 `verify_integration.py` 的指标交叉核对。
- 服务重启后形如 `failed` 且 `result.error_code=process_interrupted` 的任务属预期：中断标记而非业务结论，按第七节后续说明用受限 CLI 处置。


## D2数据库升级与故障处置

日常升级保留原数据，停止旧业务进程后启动新业务服务即可执行幂等追加升级。SQLite旧表升级前会在原目录生成备份。不要用删除数据库代替升级；测试数据重置仅限隔离演示环境。

写回超时或进程中断时先查任务的`failure_stage`、`result.decision`及日志，核对模拟财务原记录。停止业务进程，保留财务服务运行，以具备该数据库访问权限的受控运维身份，在项目根目录执行：

```bash
# 财务已写入但业务尚未确认：只读取并重放已有pending_writeback，不接受新决定或新载荷。
.venv/bin/python scripts/recover_task.py TASK_UUID --action replay-writeback --actor admin --reason "已核对原财务记录，按原载荷重放"
# 仅初审或预算查询阶段失败、无任何待写回载荷时，恢复同一任务。
.venv/bin/python scripts/recover_task.py TASK_UUID --action retry-budget --actor admin --reason "预算服务恢复，确认尚无写回"
```

Windows将解释器替换为`.\.venv\Scripts\python.exe`。自定义数据库或财务地址时，给脚本设置与业务服务相同的`DATABASE_URL`、`FINANCE_URL`、`FINANCE_KEY`；默认数据库相对于backend目录。命令成功后重新启动业务服务。

`--actor`用于审计留痕，实际访问控制依赖受控服务器和数据库权限；该脚本没有对员工或审批人开放HTTP入口。恢复操作记录操作者、原因、状态及工具调用；终态拒绝恢复。内容冲突返回失败并保留原检查点，须人工对账，禁止另建编号规避冲突。

`smoke.py`使用临时数据库验证解析关联、字段补正、补正后原请求重放、业务中断标记和恢复CLI，运行后自动停止进程。生产数据库及密钥不应作为测试输入。

可选浏览器检查见`scripts/browser_smoke.mjs`，需要单独安装测试驱动并设置浏览器路径；使用随机端口、临时数据库，自起自停服务。截图与日志存放在被Git忽略的work目录。

### 可选：启用远程模型通道（DeepSeek）

最省事的方式是一次性写进 `.env`（已被 `.gitignore` 忽略），之后每次只需一条 `dev.ps1 backend`：

```ini
AI_MODULE_ENABLED=true
AI_MODULE_CONFIG=ai-module/model_config.deepseek.json
```

密钥不用写进 `.env`：项目根目录放 `dsapi.txt`（同样被忽略）时，`dev.ps1` 会自动读取为 `DEEPSEEK_API_KEY`。启动后核对 `GET /health` 的 `ai_enabled` 是否为 `true`。

也可以临时用环境变量启动（只在当前终端有效，换终端或重启进程即失效）：


仓库默认通道为 `local_qwen`（需本地模型服务），也可切换到远程 API。密钥只经环境变量注入，不写入配置文件与文档：

```powershell
$env:DEEPSEEK_API_KEY = '<你的密钥>'                 # 或写入被 .gitignore 忽略的本地文件后读取
$env:AI_MODULE_ENABLED = 'true'
$env:AI_MODULE_CONFIG = '<本地覆盖配置路径>'          # 内容同 ai-module/model_config.json，active=deepseek
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 backend
# 核对：GET /health 的 ai_enabled 应为 true；/api/parse 应返回 mode=llm、source=llm、model=deepseek-flash
```

实测：结构化报销单 1.55 秒完成 8 字段提取、缺失 0；自由文本 1.08 秒提取金额与类别；密钥缺失或服务不可达时降级为 `rule_fallback` 并标记 `requires_manual_review`，任务转人工且保留制度与预算证据。完整记录见 `docs/INTEGRATION.md` 第十节。

### 发票 PDF 上传（D4 新增）

员工端「发票与字段核对」支持直接上传电子发票 PDF：

- 依赖 `pypdf`（纯 Python，无外部二进制）与 `python-multipart`（FastAPI 表单解析），已加入 `backend/requirements.txt`；换机后按 `scripts/setup.ps1` 重装即可。
- 只支持**带文字层的电子发票**；扫描件/图片型 PDF 会被明确拒绝并提示手工填写（OCR 不在本期范围）。
- 文件在内存中解析、不落盘，票据内容不写日志；单文件上限 5 MB。
- 抽取结果仅作建议，仍需员工核对并勾选确认；类别与申报值不一致时须显式确认冲突。
- 服务端接口为 `POST /api/invoices`，契约见 `docs/API.md`。
