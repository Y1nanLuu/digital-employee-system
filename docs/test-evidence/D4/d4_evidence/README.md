# D4 完整回归与性能测试证据包

- 日期：2026-11-10
- 执行人：王欣茹
- D4回归基线：f4b55a7
- 应用版本：0.3.0 / D3-integration（D4未更新版本标识）
- 环境：Windows 11 / Python 3.11.3 / Node 24.13.0 / Chrome 157.0.8103.38 / SQLite

## 结论

D4测试侧有条件通过。后端118项、AI30项、系统46/46、集成51/51、重启19/19、浏览器5组和100任务基础性能均通过。默认前端构建在当前深路径环境失败，Compose/PostgreSQL未验证，真实模型有模块级DeepSeek评测但业务L2适配层仍需D5抽检。

## 文件

- pytest_backend.txt / pytest_ai.txt：118 + 30项回归
- smoke_default.txt：默认命令通过，两轮46/46并保留历史任务，集成51/51
- verify_system.txt / verify_integration.txt / verify_restart.txt：46/46、51/51、19/19
- browser_smoke.txt / browser_database.json / browser_smoke.png：五组浏览器断言及任务数据库
- performance.json：100任务、30次查询、10并发性能
- summary_deepseek.json / 4-DeepSeek通道评测报告.md：模块级AI评测183/190
- frontend_build_default_failure.txt：DF-001失败证据
- frontend_build_workaround.txt：configLoader runner构建成功
- standard_tasks.db / standard_finance.db：标准端口集成校验隔离库
- restart_tasks.db / restart_finance.db：重启恢复隔离库
- performance_tasks.db：性能测量任务库
- d4_summary.json：机器可读结果、缺陷和D5阻断

## D5必须处理

DF-003、DF-R01、GATE-01、DF-001，以及候选版本标识和最终全量回归。

- scripts/browser_smoke_workaround.mjs：带Vite configLoader runner的浏览器脚本副本
- scripts/d4_performance.py：100任务与10并发性能测量
- scripts/d4_standard_suite.py：启动临时服务并执行verify_system/verify_integration

