# 候选版本发布记录（D5）

本文件固定候选版的代码、依赖、配置与数据版本，供独立部署与最终验收核对。每次发布在此追加一节，历史节不覆盖。

## 候选版 v0.5.0（2026-11-11，D5）

### 版本标识

| 项 | 值 | 核对方式 |
|---|---|---|
| 应用版本 | `0.5.0` | `GET /health` 的 `version` |
| 阶段标识 | `D5-candidate` | `GET /health` 的 `stage` |
| 数据库方言 | `postgresql`（Compose）/ `sqlite`（本地） | `GET /health` 的 `database` |

### 固定的四类版本

| 类别 | 固定内容 | 位置 |
|---|---|---|
| 代码 | 候选版提交（`git log` 中本节的发布提交）；主分支线性历史 | 本仓库 |
| 依赖 | 后端 `backend/requirements.txt` 全量锁定（含 `pypdf==7.0.0`、`python-multipart==0.0.33`）；前端 `frontend/package-lock.json` | 仓库内锁定文件 |
| 配置 | `.env.example` 模板；`compose.yaml`（`COMPOSE_AI_MODULE_ENABLED=false`）；`ai-module/model_config.json`（`active=local_qwen`，deepseek `deepseek-flash`、glm `glm-5.3-flash`，`timeout_seconds=120`） | 仓库内配置文件 |
| 数据 | 制度基线 `backend/app/data/rules.json` demo-v1（DEMO-01～05）；预算样例 P001=10000.00、P002=500.00；演示账号 employee/approver/admin | `backend/app/data/`、`backend/finance/main.py` |

辅助配置（不入库，各人本地）：`.env`（演示密码与模型开关）、`dsapi.txt`（DeepSeek 密钥）、`ai-module/model_config.local*.json`（私有通道覆盖）。

### 发布门禁（发布前必须全绿）

```powershell
.\venv\Scripts\python.exe -m pytest -c backend/pytest.ini backend/tests -q   # 131 项
.\venv\Scripts\python.exe scripts/smoke.py                                    # 自起自停
.\venv\Scripts\python.exe scripts/verify_system.py                            # 46 项（需 8000/8001）
.\venv\Scripts\python.exe scripts/verify_integration.py                       # 51 项（需 8000/8001）
.\venv\Scripts\python.exe scripts/verify_restart.py                           # 19 项（自起自停）
cd frontend; npm.cmd run build                                                  # 类型检查+构建
```

### 部署方式（两种，任选其一）

| 方式 | 命令 | 适用 |
|---|---|---|
| 本地三服务 | `scripts/setup.ps1` 后分别 `scripts/dev.ps1 finance|backend|frontend` | 开发与答辩演示 |
| Docker Compose | `docker compose up -d --build`（访问 8080） | 独立部署与可复现验证 |

### 已知限制（候选版如实声明）

- 模型通道：本地千问需 `transformers`/`torch`（未在本机验证）；远程 DeepSeek/GLM 需自备密钥；Compose 容器内不含 `ai-module`，模型通道默认关闭。
- 发票 PDF 只处理带文字层的电子发票，扫描件与验真不在范围内。
- 同步单进程执行；SQLite 模式不支持多 worker；恢复仅提供本机 CLI。
- 认证为 HTTP Basic 演示账号，仅绑定本机；真实部署须替换认证并启用 HTTPS。

### 回滚方式

`git checkout <上一候选版提交>` 后重建镜像或重启服务；数据库结构只做追加式升级，回滚代码不会破坏已有表结构（升级前的 SQLite 备份见 `migrations.py` 的 `.bak` 机制）。

## 候选版 v0.5.0 enterprise-review（2026-11-11 合并远端企业版工作流后）

合并远端 `a4b07d8` 后，候选版在原 D5 基础上纳入企业报销工作流，阶段标识改为 `enterprise-review`。

| 项 | 值 |
|---|---|
| 应用版本 / 阶段 | `0.5.0` / `enterprise-review`（`GET /health` 可核对） |
| 新增接口 | `GET /api/policy`、`GET /api/files/{file_id}`、`GET /api/files/{file_id}/content`、`POST /api/batches`、`GET /api/batches`、`PUT /api/batches/{batch_id}/items/{index}`、`POST /api/tasks/{task_id}/resubmit`、`POST /api/tasks/{task_id}/withdraw` |
| 发票识别 | 由文本层抽取升级为**本地 OCR**（`pypdfium2` + `Pillow` + `rapidocr-onnxruntime`），原件落盘于 `FILE_STORAGE_DIR`（容器内 `/app/data/invoices`，独立数据卷） |
| 制度版本 | `ACTIVE_POLICY_VERSION=enterprise-demo-v2`，可用 `ENTERPRISE_POLICY_FILE` 指定自定义制度 JSON |
| 基础镜像 | `python:3.11-slim-bookworm`（固定 Debian 12，避免 trixie 的 `libglib2.0-0` 改名导致构建失败） |
| 运行约束 | 本地开发需 Python 3.11/3.12（`rapidocr-onnxruntime` 不支持 3.13+）；容器内为 Python 3.11 |
| 部署验证 | 四容器 healthy（`database=postgresql`、`version=0.5.0`、`stage=enterprise-review`、`active_policy=enterprise-demo-v2`）；增量升级与发票上传通过；干净目录独立部署通过 |
| 容器内门禁 | Python 3.11 镜像内 `pytest` **180 项通过**；`verify_enterprise_rules.py` **198/198**；企业版端到端（policy→上传→建单→审批）**5/5** |
| 规则基线门禁 | `smoke.py` 通过（内含 `verify_integration.py` 51 项）；`verify_restart.py` 19 项；`verify_system.py` / `verify_integration.py` 需 `ACTIVE_POLICY_VERSION=demo-v1`，企业版下需先上传票据 |
| 前端构建 | 通过（新增收件、复核、文件查看组件，js 128.65 kB） |

回滚提示：本次合并同时带来前后端与文档的大幅变更，如需回到 D4 基准，回退到 `22d4fc6` 即可；数据库为追加式升级，回退代码不会破坏已有表结构。

## 候选版 v0.4.0（2026-11-10，D4 基准，历史记录）

D4 基准为 `0.4.0` / `D4-baseline`，验证项与门禁数字见 `docs/INTEGRATION.md` 第十三节；本节仅作历史保留，不再作为发布依据。
