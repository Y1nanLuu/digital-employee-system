# V0.5 本机启动与数据保护

本机使用SQLite和受控文件目录，可以直接演示。Python3.11、Node22.12及以上；首次安装需要网络。Docker用于另一种统一部署方式。

## Windows启动

项目根目录先安装依赖：

```powershell
py -3.11 -m venv .venv
& .venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd frontend
npm.cmd ci
```

复制根目录.env.example为.env并修改本机配置。三个终端均从项目根目录分别运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 finance
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 backend
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 frontend
```

浏览器http://127.0.0.1:5173；业务服务8000，模拟财务8001。账号employee、approver、admin，默认密码demo123。三种身份分别提交、审批和查看管理信息。启动器读取.env白名单，当前终端已有变量优先；修改模型配置或密钥后重启业务服务。

Linux/macOS创建.venv并安装同一requirements；导出下述环境变量，在backend工作目录分别运行uvicorn app.main:app --port 8000和uvicorn finance.main:app --port 8001；frontend中npm ci及npm run dev。

## 模型和制度

- AI_MODULE_ENABLED=true启用实际模型；AI_MODULE_CONFIG=ai-module/model_config.deepseek.json；DEEPSEEK_API_KEY填写在本机.env或已忽略的dsapi.txt。密钥不写进JSON、前端或Git。
- 实际模型用于文本字段解析和结构化初审；PDF文字读取、本地RapidOCR和确定性制度校验无需额外OCR API。实际模型不可用则保留原因、转人工。
- COMPANY_TITLE=示例科技有限公司控制企业抬头；样本发票若为其他购买方会产生人工核对提示。勿为通过样本而静默修改真实票面内容。
- ACTIVE_POLICY_VERSION默认enterprise-demo-v2。ENTERPRISE_POLICY_FILE可指定新版本JSON，版本号必须新建且不可原地改已归档制度。历史demo-v1任务仍可查看，退回后升级到新流程。
- GET /health应返回version=0.5.0、active_policy=enterprise-demo-v2；ai_enabled仅说明开关，实际调用成功以任务initial_review.source=llm为证。

## 数据与升级

默认业务库backend/data/tasks.db，财务库backend/data/finance.db，原票backend/data/invoices。FILE_STORAGE_DIR可调整目录。启动时创建新表并增量升级旧表，SQLite升级前自动备份；已有任务和旧制度保留。本轮正式切换前另存了work/pre-v05-upgrade-*备份。

联合备份前停止业务和财务服务的写入，保留前端可访问但不操作。根目录执行：

```powershell
& .venv/Scripts/python.exe scripts/archive_backup.py --output work/backup.zip --maintenance-confirmed
& .venv/Scripts/python.exe scripts/archive_backup.py --restore work/backup.zip --destination work/restored-new
```

恢复只允许空的新目录，逐项核对SHA256，不覆盖现有数据。先在隔离服务用恢复库和files目录核对任务、修订、附件和财务凭证，再安排正式切换。不要只备份数据库而遗漏原票。work、数据库、票据、.env与密钥均不提交Git。

## 受限任务故障恢复

先核对任务编号、当前revision、failure_stage、既有人工决定和财务凭证，再停止业务服务的写入，保持财务服务可用。使用与业务服务一致的DATABASE_URL、FILE_STORAGE_DIR、FINANCE_URL及制度配置，以有权访问数据库和原件目录的运维身份，从项目根目录执行（把任务UUID替换为实际编号）：

```bash
# 无待写回快照、失败发生在初审或查询阶段时，恢复同一任务。
.venv/bin/python scripts/recover_task.py "任务UUID" --action retry-budget --actor "运维人员" --reason "已核对工具恢复及原申请"
# 财务可能已保存、业务尚未确认时，只重放原编号和持久化的原载荷。
.venv/bin/python scripts/recover_task.py "任务UUID" --action replay-writeback --actor "运维人员" --reason "已核对财务原记录并按原载荷对账"
```

Windows将Python路径替换为`.venv/Scripts/python.exe`。恢复CLI不自动读取根目录.env，运行前须按服务启动配置导出环境变量。

retry-budget可处置初审、预算、票据查验、查重或原件校验阶段的失败；原件损坏须先按档案备份恢复。当前修订已有人工通过决定时，重查硬规则和工具后沿该决定续跑，不要求重复审批；预算变低等硬约束仍会导致拒绝。新修订不会复用旧修订的审批决定。

恢复成功或再次失败都会更新当前修订的审计快照；原申请、此前修订和失败调用继续保留。replay-writeback不得改审核内容，遇内容冲突需停止并人工对账。已完成或已撤销任务不能重开，普通用户没有恢复HTTP接口；failed申请也不能由员工撤销。自动重试和自动崩溃续跑仍为P1。

## Compose可选部署

Docker正常运行后执行docker compose config --quiet、docker compose up -d --build，访问http://127.0.0.1:8080。业务使用PostgreSQL，票据、财务SQLite及PostgreSQL各有持久卷；ai-module只读挂载。启用COMPOSE_AI_MODULE_ENABLED=true和密钥可调用配置模型。

2026-11-11 已在有 Docker 的环境完成容器与 PostgreSQL 实机验收（此前“未就绪、仅静态检查”的结论作废，保留原文以记录过程）：

- 本机 Docker Desktop 需先启动；`github.com` 与 Docker Hub 在国内网络常不可达，镜像加速器写入 `~/.docker/daemon.json` 的 `registry-mirrors`（本次用 `docker.m.daocloud.io`、`dockerproxy.net`），该文件必须是 **UTF-8 无 BOM**，否则 Docker 启动报 `parsing daemon.json: invalid character 'ï'`。
- 四容器 `db/finance/backend/frontend` 全部 healthy；`GET /health` 返回 `database=postgresql`、`version=0.5.0`、`stage=enterprise-review`；`http://127.0.0.1:8080` 可访问。
- 在容器化 PostgreSQL 上：`pytest`（除本机 Python 版本限制项）、`verify_system.py` 46 项、`verify_integration.py` 51 项、增量升级（删列后重启自动补齐）、发票上传（本地 OCR 依赖已随镜像安装）均通过。
- 干净目录独立部署：用 `git worktree` 从候选版提交导出（无 `.venv`、无 `.env`、无密钥），在该目录内 `docker compose up -d --build` 成功并跑通门禁。

### 本地依赖与版本约束

- 后端依赖含本地 OCR：`pypdfium2`、`Pillow`、`rapidocr-onnxruntime`。其中 **`rapidocr-onnxruntime` 要求 Python < 3.13**，因此本地开发机请用 Python 3.11/3.12；Python 3.14 只能跑除 OCR 之外的用例（本轮本机为 3.14，OCR 用例改在容器内验证）。
- 后端镜像基础镜像已固定为 `python:3.11-slim-bookworm`；`python:3.11-slim` 现已指向 Debian 13，`libglib2.0-0` 改名会导致系统库安装失败。
- 若构建时容器内无法访问 `deb.debian.org`，可传 `--build-arg APT_MIRROR=mirrors.aliyun.com` 切换 apt 源，默认仍为官方源。避免docker compose down -v删除数据卷。
