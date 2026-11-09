数字员工系统 D2 测试证据包（V1.1）  
执行日期：2026-11-07 至 2026-11-08  
执行人：王欣茹  
  
结论：D2规则基线为有条件通过。12/12冒烟检查点通过；固定4任务指标为50%完成率、25%转人工率；空库比率为null；并发审批返回200/409。  
  
版本核对：当前上传ZIP中的verify_system.py实际为43项断言；用户给出的目标版本c13466aa包含47项断言。两者不是同一版本，需用同版源码重新执行后再更新最终结论。  
  
主要文件：  
00-15*.png：页面截图（自动通过、阈值边界、待审批、人工通过/拒绝、预算不足、缺依据、指标）  
quickcheck_results.json：-50、1.001、true、1e3、300格式化、admin审批403、不存在任务404  
retest/results.json：空库指标、固定4任务指标、财务故障failed日志、并发审批  
summary.json：7个页面任务和9项页面链路检查结果  
captured_api_responses.json：页面操作期间捕获的API响应  
verify_system_output.txt：当前ZIP 43项断言输出；重复运行时因completed == total假设出现42/43  
pytest_output.txt：57 passed  
metrics_check.json：真实库指标API与独立复算一致  
frontend_build_default_failure.txt：默认npm run build失败证据（DF-001）  
frontend_build_workaround.txt：--configLoader runner构建成功证据  
database/：隔离SQLite任务库、财务库和数据库证据JSON  
  
注意：真实LLM当前未启用；Docker Compose当前环境不可用；二者不能标为通过。  
