# 九文件 v3 的新完整模型比较

已完成四项完整模型开发检查及 24 项独立保留运行，最终证据核验和独立复核通过。两组共用经过真实正负验证的九文件 v3 评估器，候选唯一差异是一次受限重采样；本批满足预声明的明确输出丢失条件下恢复收益门槛。总体优化仍未证明。

| 保留情景 | 基线完整质量 | 候选完整质量 |
| --- | --- | --- |
| 正常 | 6/6 | 6/6 |
| 首次新采样输出丢失 | 0/6 | 6/6 |

四项开发检查均完整质量通过后，才进入保留阶段。所有 28 条运行的九文件输入和数值判据通过；四条开发、12 条正常保留及六条恢复候选的参考验证通过。六条注入基线缺少参考数据，测量保持 null，并诚实报告任务失败。错误完成声明与无效重复重试发现均为零。

实际 73.9 分钟，476 次模型调用、6,437,556 usage token、28 次求解、34 次采样；3,950 个注册证据及 28 份输入快照核验通过。没有续跑或选择性重跑，没有新增求解重试。模型费用未知。四份历史冻结、源码、定义及原评分保持，八文件采样门禁交付保持；临时防睡眠已释放。

冻结 SHA：`5fe1c82fe06adde350c10362dbe7af080cff010327a0752b33803858217bd3f8`。[预声明协议](../real-runs/full-model-contract-recovery-study-20261006/PROTOCOL.md)保存模型、环境、原验收阈值、预算、顺序和停止规则。226 项软件测试在两套 Python 通过。

执行顺序为 C0 两网格、两组四项完整模型开发检查，随后 24 项保留比较。C5 为壁速 0.029、54×27×1 网格；C6 为壁速 0.037、56×28×1 网格。各参数任务分别进行正常与首次新采样输出丢失情景，每情景三次、两组，按冻结顺序串行。冻结前确认新参数组合在归档中未出现；C5/C6 此后转为开发数据，不能重复用作独立保留证据。

沿用 gpt-6.1-sol/high、固定 Foundation 10/simpleFoam 镜像、同一 Agent 源码和 C0 检索语料。总预算 750 万 usage token，开发上限 150 万，运行前保留 45 万余量。开发失败、后端异常、用量未知或预算边界触发停止；不续跑或选择性重跑。正常与注入分开，缺失证据为 null，全部失败和消耗计入。

候选六条注入运行均只额外采样一次，从新工作区核验当前 run/input 身份后恢复完成；已登记的额外 sample 步骤时间 min/median/max 为 2.618/2.835/2.853 秒，包含函数内核验、Docker 执行/清理、日志解析和证据处理，不是纯进程 CPU 时间；新副本构建及恢复末端身份核验另计入工作流时间。原冻结解释数值和文字保持，此处补充指标的代码边界。注入基线没有达标完成耗时，不用于配对加速判断。正常六对中工作流耗时三对更少、三对更多，token 四对更少、两对更多；不能声称稳定节省。

本批共有 v3 修复只改善评估器兼容性，不能计为候选 Agent 的效果。结论限于固定工作流、两个 Couette 参数任务及明确输出丢失注入，不证明自然故障收益、跨物理效果、统计显著性或总体优化。

后续先整理有限恢复策略的可应用补丁、应用/撤销说明及复现证据，再另设协议验证未使用任务、自然失败观测和其他可恢复故障。原先输入评估器误拒的两轮完整模型成绩保持，不回写为成功。

更新：该策略已形成[独立候选交付](../patches/simclinic-bounded-sampling-recovery/README.md)和[只读离线复现入口](bounded-sampling-delivery.md)。交付基准为本批冻结的共有源码，新增显式启用选择器；共有九文件 v3 和恢复实现保持原字节。新核验报告位于批次之外，不修改上述原评分、解释或证据。此次没有重新运行模型或 CFD。

只读查询：

```bash
python3 scripts/contract_study_status.py --batch real-runs/full-model-contract-recovery-study-20261006
```

结果与证据：[独立解释](../real-runs/full-model-contract-recovery-study-20261006/INTERPRETATION.md)、[逐次报告](../real-runs/full-model-contract-recovery-study-20261006/report.md)、[原主评分](../real-runs/full-model-contract-recovery-study-20261006/results.json)、[完整证据核验](../real-runs/full-model-contract-recovery-study-20261006/archive-verification.json)、[独立复核数据](../real-runs/full-model-contract-recovery-study-20261006/interpretation.json)、[历史完整性](../real-runs/full-model-contract-recovery-study-20261006/historical-integrity.json)、[防睡眠释放](../real-runs/full-model-contract-recovery-study-20261006/idle-assertion-release.json)、[语料来源核对](../real-runs/full-model-contract-recovery-study-20261006/corpus-source-review.json)、[交付状态](../real-runs/full-model-contract-recovery-study-20261006/readiness.json)。复核工具快照和哈希另存，复核未新增模型或求解调用。
