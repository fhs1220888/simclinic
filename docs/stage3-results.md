# 第三阶段：可应用改进与实际回归

日期：2026-10-04。完成两项候选补丁及使用说明，实际应用/撤销验证、67 项针对性测试、固定容器三类回归以及真实结果文件的身份门禁测试。

本文件保留第三阶段当时的验证范围。随后补丁 0003 已接通原生图、PyVista 节点和已有工具函数，见 [原生后处理接入](native-postprocessing.md)；完整模型驱动流程仍未验证。

## 退出状态修复

[补丁 0001](../patches/foam-agent-v2.1.0/0001-execution-status.patch) 让原始执行函数返回真实退出码和超时标记；错误收集同时纳入执行状态。原始文件未修改，测试使用独立的 `integration/foam_agent_v210/patched` 快照，每个实际运行保存 patch manifest 与源文件 SHA-256。

基线取第二阶段归档，修改版取 `real-runs/stage3-patched-20261004`。三对记录的任务、输入版本、环境摘要和证据覆盖均一致，原验收条件没有放宽。

| 实际场景 | 基线执行结论 | 修改版执行结论 | 任务验收 |
| --- | --- | --- | --- |
| 正常通道 | 成功 | 成功 | 两版均通过三层验收 |
| 提前结束的通道 | 执行成功 | 执行成功 | 两版均未通过数值和参考验收 |
| Allrun exit 7、无求解日志 | 错误地报告执行成功 | 正确地报告执行失败 | 两版均未完成任务 |

正常通道保持相同数值结果：速度剖面误差 0.699%，压力梯度误差 0.394%。失败任务没有被包装为任务成功；修改后的 no-log-failure 没有成功 conclusion，所以对应 error_continuation 检查从 finding 变为 not_applicable。这一变化需结合真实结论与退出码理解，不以 N/A 数量直接作为优化指标。

证据：

- [修改版上游进程状态与错误列表](../real-runs/stage3-patched-20261004/no-log-failure/case/.simclinic/upstream.json)
- [修改版体检](../real-runs/stage3-patched-20261004/no-log-failure/audit/report.md)
- [正常案例配对比较](../demo-output/stage3/healthy/comparison.json)
- [故障案例配对比较](../demo-output/stage3/no-log-failure/comparison.json)

每对比较报告同时保存实际墙钟/Allrun 时间及其差值。这里是单次回归，不据此声称加速；模型 token/成本仍为 null，测试不调用模型。

## 结果身份门禁

[补丁 0002](../patches/foam-agent-v2.1.0/0002-verified-result-interface.patch) 提供后处理 JSON 读取接口，依赖 SimClinic 的 `read_verified_json`。它先比较 caller expected run/input 与生成身份，再读取并校验 SHA-256，返回同一次已核验的内容。

对修改版真实正常通道的 `.simclinic/acceptance.json` 调用候选接口：

| caller 上下文 | 允许消费 | 结果 |
| --- | --- | --- |
| 当前 run、当前 input | 是 | 读到实际 profile_error=0.006986786967466754 |
| 另一 run | 否 | stale_run |
| 另一 input | 否 | stale_input |
| 身份缺失 | 否 | unknown_expected_identity |

这些负例是刻意改变调用者期望身份的契约故障注入，没有声称运行了新的入口条件 CFD 算例。拒绝路径 consumed=false；单元测试还验证身份不符时未打开结果文件。结果见 [门禁记录](../demo-output/stage3/result-guard.json)。

接口模块已用实际文件验证，但尚未接到完整 Foam-Agent 原生后处理工具。第一阶段旧结果场景仍明确属于合成数据；没有把它描述为真实自主 Agent 的事故。

## 已执行的软件验证

Python 3.9.6 与 3.12.14 上均有 67 项测试通过，覆盖原始 exit 7 错误、修改版非零/超时/未知状态、正常执行、End 与退出码冲突、零退出错误日志、patch 应用和逆向恢复、正常 JSON 读取和身份/hash/文件拒绝、既有诊断及 CFD 验收。0.3.0 wheel 已在临时目录离线构建、安装，从仓库外验证新增 CLI 的允许/拒绝行为，没有全局安装。

基线与候选版本都在同一个固定 OpenFOAM image digest 下实际执行。原始源与候选源的 hash 分开记录。可复现流程：

```bash
python3 scripts/build_candidate_patches.py
python3 -m unittest discover -s tests
python3 scripts/run_real_integration.py --variant patched --out real-runs/my-patch-retest
python3 scripts/report_stage3.py --after real-runs/my-patch-retest
```

## 结论边界与下一阶段

在所测执行路径上，补丁已纠正 exit 7 的错误执行结论，正常 CFD 验收保持通过。结果接口能拒绝不匹配身份。它们满足第三阶段“开发者能据此完成具体修改”的交付要求。

完整模型驱动的 Agent、原生包全量兼容性、真实入口改变后的整条后处理链、多次保留算例和优化效果评估仍待验证。第四阶段需要预先定义保留任务和重复次数，比较明确的错误结论/失败/重试指标，并在质量门禁满足时解释差异；当前单对服务回归不作为总体优化证明。
