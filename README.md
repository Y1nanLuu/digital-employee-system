# 面向中小企业的数字员工系统

V0.5实现费用报销初审：员工上传PDF或图片，核对预填字段后逐张申报；数字员工检索版本化制度、查询预算、模拟查验和查重、调用大模型生成初审意见，再自动完成或交人工。审批人可通过、拒绝或退回员工补充。原件、修订、日志及结果持久保存。

## 快速使用

Windows启动见[部署说明](docs/DEPLOYMENT.md)。本机模式使用SQLite，无需Docker。安装依赖、填写.env后，三个终端分别运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 finance
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 backend
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 frontend
```

打开http://127.0.0.1:5173；employee、approver、admin三种身份，演示密码demo123。真实模型在.env填写DEEPSEEK_API_KEY并启用AI_MODULE_ENABLED，重启后端；密钥不进入前端或仓库。

## 本期能力

- PDF文字读取、图片/扫描PDF本地OCR，原件保存和授权预览下载；一票一单，每批最多10个文件逐张核对。
- 九类费用制度，金额(0,5000]，自动阈值1000元；住宿、招待、餐补、通讯等细则及材料要求。
- 提交幂等、重复票据检查、模拟发票查验分别记录；真实发票默认为未知并转人工，税务真实验真留待后续。
- 实际模型字段解析和结构化初审；规则控制最终路由，模型故障或输出异常转人工。
- 退回后员工修改原任务重提，保留历史及制度版本；通过前重查工具，最终写回幂等。
- SQLite/可选PostgreSQL数据库、票据档案、审计和真实指标、备份恢复。

预算仅查询，不支付或扣减。类别未知归“其他”由人工确认；新类别和制度由负责人发布版本。上传质量检查通过后，缺少的辅助字段可补填；拒收文件要求重传。多张独立发票合在一个PDF需先拆分。

## 文档和验证

- [需求V0.5](docs/product/产品需求文档V0.5.docx)
- [详细测试制度](docs/policies/中小企业费用报销测试制度_v2.0.md)与[公开参考依据](docs/policies/公开制度研究与设计依据_20261111.md)：数值为本组测试标准。
- [测试方案及交接](docs/test-evidence/enterprise-v2/测试方案与执行说明.md)：240项输入；198项纯规则批测和真实模型联调分别计数。
- [API](docs/API.md)、[数据设计](docs/DATA-DESIGN.md)、[架构](docs/ARCHITECTURE.md)、[部署及备份](docs/DEPLOYMENT.md)。

```powershell
& .venv/Scripts/python.exe -m pytest -c backend/pytest.ini backend/tests ai-module/test_extractor.py -q
& .venv/Scripts/python.exe scripts/generate_enterprise_cases.py
& .venv/Scripts/python.exe scripts/verify_enterprise_rules.py
cd frontend
npm.cmd run build
```

历史scripts/smoke.py显式在隔离demo-v1模式检查旧协议，不作为V0.5新能力验收。Docker Compose静态检查已过，当前电脑WSL2不可用，PostgreSQL容器运行验证须另补；本机SQLite已运行。


本轮交互调整：员工侧栏为“发起报销”和“我的报销”，历史页提供日期、状态、编号搜索及排序；提交一张后可继续登记下一张。业务编号为BX-E001-日期-序号，内部UUID继续关联。管理员直接进入待审批，只能通过或驳回；员工可以修改重提或撤销。明显模糊、非发票或关键字段无法识别时要求重传，拒收文件不落盘、不产生业务数据库记录。初审摘要按结论、问题、模型建议、工具及依据分层展示。

按用户最新指示，最后一批交互变更先完成构建与启动，完整系统测试集中留待最终执行。此前测试证据保留其原验证范围，不能视为最新界面全部通过。

最新补充：发起报销不展示历史申请或历史批次选择，仅恢复当前会话未完成草稿。历史统一在我的报销。状态筛选仅已通过、待审批、驳回待修改三类（可查看全部）。上传归档前及队列创建前查重，重复时提示原业务编号；已通过禁止重报，可撤销原申请先撤销再上传。同批重复项排除。invoice_files.invoice_fields完整保存原票号码、日期、购销方、总额和明细，原始识别与任务确认值分别留存。新增AC61及对应待测用例，最终组合测试按用户要求留待集中执行。

2026-11-12后端修复复测：审批工具失败后沿原决定恢复、当前修订审计同步、旧原件审批权限及撤销状态已补强。后端与AI回归220项、真实HTTP与运维CLI验收11项、前端构建通过；[修复证据](docs/test-evidence/backend-fix-20261112/verification.json)和[每日开发日志](docs/周勃-开发日志汇总.md)保留修复前后记录。真实模型、PostgreSQL及最新浏览器组合流程仍需同版终验。
