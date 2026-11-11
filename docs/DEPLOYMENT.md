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

## Compose可选部署

Docker正常运行后执行docker compose config --quiet、docker compose up -d --build，访问http://127.0.0.1:8080。业务使用PostgreSQL，票据、财务SQLite及PostgreSQL各有持久卷；ai-module只读挂载。启用COMPOSE_AI_MODULE_ENABLED=true和密钥可调用配置模型。

当前电脑WSL2虚拟化环境未就绪，Docker后台无法运行；本轮仅完成Compose静态配置检查，未完成容器及PostgreSQL实机验收。此项须在支持环境补测重启、数据卷和模型可达性，不将其标记为已通过。避免docker compose down -v删除数据卷。
