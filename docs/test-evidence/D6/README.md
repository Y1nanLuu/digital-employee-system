# D6-07 王欣茹最终测试交付包

## 最终结论

**有条件通过，可作为课程最终版提交。**

核心回归、企业规则、企业版四类场景、恢复、重启、最新浏览器冒烟及构建 workaround 均可复现；默认前端构建失败、Docker/PostgreSQL 与真实 DeepSeek 未在本测试机独立复验，必须在答辩与提交说明中如实声明。

## 最终版本

- 候选 ZIP：`digital-employee-system-master (4).zip`
- SHA-256：`F05B561FAC7078340BB43F9F4E572266F727826DCA9B26E1FB0687B9A1D31A43`
- 应用版本 / 阶段：`0.5.0 / enterprise-review`
- 生效制度：`enterprise-demo-v2`
- 环境：Windows 11 / Python 3.11.3 / Node 24.13.0 / Chrome 157 / SQLite

## 关键结果

- 后端 + AI 总回归：**220 passed, 1 warning**（先创建 work 目录）
- 干净 ZIP 首次回归：**219 passed / 1 failed**（archive_backup 的 work 目录前置条件，已保留日志）
- 企业规则批测：**198/198**，LLM 调用 0
- 旧协议 smoke：两轮 46/46 + 51/51
- 重启恢复：**19/19**
- HTTP/CLI 恢复：**11/11**；runner 退出 0
- 企业版四类场景：**4/4**
- 最新浏览器冒烟：**6/6**，无 page error
- 默认前端构建：失败（esbuild config loader Access denied）
- 构建 workaround：vue-tsc 0 错误，vite runner 成功，JS 128.60 kB
- Docker/PostgreSQL：本机未独立执行（docker 命令不存在）
- 真实 DeepSeek：本机未独立执行（密钥缺失）；历史 9 组联调证据已审阅

## 交付文件

- `D6_最终质量结论与验收报告_王欣茹_20261112.docx`：最终质量结论、测试矩阵、缺陷和提交建议。
- `D6_最终验收与提交台账_王欣茹_20261112.xlsx`：D6-02版本核对、测试结果、缺陷、提交检查单和证据索引。
- `D6_个人实习总结_王欣茹_20261112.docx`：定稿，约 2450 字。
- `王欣茹-2026-11-12-D6-测试日志.md`：当日执行时间线与判定。
- `D6_测试证据包_王欣茹_20261112.zip`：日志、JUnit、JSON、截图和未执行原因。
- `evidence/`：证据包可读副本。

## 提交前必须声明

1. 默认 `npm run build` 在当前测试环境失败，使用 `--configLoader runner` 完成构建。
2. 干净解压目录执行备份测试前需创建 `work/`，或等待 `archive_backup.py` 修复。
3. Docker/PostgreSQL 与真实模型证据属于开发组历史验收；D6本机没有独立复验。
4. 旧 `browser_smoke.mjs` 已不适配最新 UI；D6使用自定义浏览器冒烟脚本并通过 6/6。
