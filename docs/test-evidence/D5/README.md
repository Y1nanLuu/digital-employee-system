# D5-07 王欣茹最终测试交付包

## 交付结论

**有条件发布（仅课程本地演示）**。独立部署、四类核心演示、规则基线回归、浏览器路径和主要异常路径通过；Docker/PostgreSQL、真实模型业务成功路径、默认前端生产构建和候选版本阶段标识仍未完成独立复验或关闭。

## 文件说明

- `D5_独立部署复现记录与发布建议_王欣茹_20261111.docx`：D5主报告，含版本、部署、四类演示、测试矩阵、缺陷门禁和发布建议。
- `D5_测试与缺陷跟踪台账_王欣茹_20261111.xlsx`：部署、测试、缺陷、发布动作和证据索引。
- `D5_产品缺陷处理案例_王欣茹_20261111.docx`：DF-R01、DF-001、DF-003、DOC-D5-01四个案例。
- `D5_个人实习总结_王欣茹_20261111.docx`：个人总结，约3000字。
- `王欣茹-2026-11-11-D5-测试日志.md`：当日执行时间线、发现与结论。
- `D5_测试证据包_王欣茹_20261111.zip`：原始日志、JUnit、JSON、截图和未执行原因。
- `evidence/`：测试证据包解压后的可读副本。

## 关键结果

- 候选ZIP SHA-256：`6C126683AA771F17167F474D8A3B5F161150E75340A70817252864D377025B0D`
- 源码版本：`0.4.0`，默认阶段 `D4-baseline`；本轮运行环境覆盖为 `D5-candidate`
- 后端 pytest：131 passed，1 warning
- AI pytest：30 passed
- 独立部署 verify_system：46/46
- 独立部署 verify_integration：51/51
- verify_restart：19/19
- 四类演示：自动通过、人工审批通过、人工拒绝、预算不足，4/4
- 发票测试：13 passed（源码部署说明写14，已记录 DOC-D5-01）
- 浏览器：5组断言通过；默认Vite配置加载失败，使用 `--configLoader runner` workaround
- 财务中止：failed、无receipt、错误可读
- AI缺密钥：rule_fallback + llm_api_key_missing，任务转人工

## 未执行/阻塞项

- **GATE-01**：测试机无Docker，Compose/PostgreSQL未独立复验；开发日志中的历史结果不能替代本轮证据。
- **DF-003**：测试机无DEEPSEEK_API_KEY，真实模型业务接口成功/超时/非法输出未独立复验；仅fallback已通过。
- **DF-001**：默认 `npm run build` 在当前Windows/Node环境失败；`--configLoader runner` 可完成构建，但属于workaround。
- **D5-REL-01**：源码默认 `APP_STAGE=D4-baseline`，候选版阶段标识未冻结。

## 证据边界

本包只对上述候选ZIP和独立部署runtime负责。报告中未执行项均按“阻塞/未执行”记录，未将开发组历史声明写成D5独立通过。D6提交前请按主报告表8和台账A1–A6关闭。
