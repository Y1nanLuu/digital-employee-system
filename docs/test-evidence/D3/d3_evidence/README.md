# D3 端到端测试证据包

- 日期：2026-11-09
- 执行人：王欣茹
- 代码基线：296209b / APP_VERSION 0.3.0 / D3-integration
- 环境：Windows 11 / Python 3.11.3 / Node 24.13.0 / Chrome 157.0.8103.38 / SQLite

## 结论

规则基线端到端链路有条件通过。API 侧 43/43、51/51、19/19 全部通过；后端 109 项、AI 30 项单元测试通过；浏览器四组场景通过。D4 必须关闭真实 LLM、默认前端构建、verify_system 干净库假设、smoke.py 默认编码和 Compose/PostgreSQL 验证。

## 文件

- 01-auto-pass.png：自动通过
- 02-approval-amend.png：人工通过并补正 1500→900
- 03-rejection.png：人工拒绝
- 04-unknown-result-retry.png：提交结果未知后按原幂等键重试
- 05-admin-metrics.png：三项指标与真实任务对应
- browser_smoke.txt：浏览器四组断言输出
- pytest_backend.txt：109 passed
- pytest_ai.txt：30 passed
- smoke.txt：真实HTTP、补正、写回中断与CLI恢复
- verify_system.txt：43/43
- verify_integration.txt：51/51
- verify_restart.txt：19/19
- frontend_build_default_failure.txt：默认 npm run build 失败
- frontend_build_workaround.txt：--configLoader runner 构建成功
- browser_database.json：四类浏览器场景的数据库记录、事件、工具日志和审批记录
- d3_suite_tasks.db / d3_suite_finance.db：标准端口校验隔离数据库
- restart_tasks.db / restart_finance.db：重启与受限恢复隔离数据库
- d3_summary.json：机器可读结果、缺陷和 D4 阻断清单

补充失败证据：smoke_default_failure.txt 记录默认 smoke.py 的 GBK 解码失败；frontend_build_default_failure.txt 记录默认 Vite 配置加载失败。两块均在 D4 必须关闭。


- scripts/browser_smoke_workaround.mjs：带Vite configLoader runner的浏览器脚本副本
- scripts/d3_standard_suite.py：启动临时服务并执行verify_system/verify_integration
- scripts/d3_retest_probe.py：空库指标、固定4任务、财务故障和并发审批探针

