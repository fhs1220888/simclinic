# Foam-Agent 格式研究与适配边界

资料读取日期：2026-10-04。第一阶段仅做源码研究；随后已运行固定执行函数并保存真实归档，见 [第二阶段结果](stage2-results.md)。尚未运行完整模型驱动的 Foam-Agent 工作流；合成日志始终单独标注。

阅读了上游实际 [src/logger.py](https://github.com/csml-rpi/Foam-Agent/blob/main/src/logger.py) 与 [raw 源码](https://raw.githubusercontent.com/csml-rpi/Foam-Agent/main/src/logger.py)：logger 将 stdout 写入 workflow.log，review.log 单独保留带标签的审查消息。同一 tagged review 可能同时出现在两个文件。源码中的日志机制并不保证结构化工具退出码、输入哈希或输出身份。适配器优先选择 workflow.log，缺失时使用 review.log，避免双计数；以 observation 保存已识别标签及原始行号，所有事件覆盖保持 false。

另阅读了 [src/models.py](https://raw.githubusercontent.com/csml-rpi/Foam-Agent/main/src/models.py)：RunOut 携带 job_id/status，CaseManifest 描述算例及环境信息。这些信息可作为下一阶段结构化采集来源，但不是已审计的运行轨迹；job 状态不能自动换算成求解质量。

最初读取的 main 页面缓存时间不同，不视为不可变源码快照。随后通过 [官方 tags 页面](https://github.com/csml-rpi/Foam-Agent/tags) 和只读 `git ls-remote` 双重核验 v2.1.0 对应 commit `bfb7f791f77438ff90c1a39ff398739ee97bcc67`，并读取下列固定源码。本次只读取这些源码，没有执行上游代码。

| 固定源码 | 下载内容的 SHA-256 |
| --- | --- |
| [logger.py](https://github.com/csml-rpi/Foam-Agent/blob/bfb7f791f77438ff90c1a39ff398739ee97bcc67/src/logger.py) | `82e0e6b2affdf2832a8d01c3946d1034de9d721ee1db7834f28d2f66c194456a` |
| [local_runner_node.py](https://github.com/csml-rpi/Foam-Agent/blob/bfb7f791f77438ff90c1a39ff398739ee97bcc67/src/nodes/local_runner_node.py) | `d01e9259e68d487e40e64a0726135bdc5ef8dbe0a35079eb37f534ccbf2150c6` |
| [run_local.py](https://github.com/csml-rpi/Foam-Agent/blob/bfb7f791f77438ff90c1a39ff398739ee97bcc67/src/services/run_local.py) | `2951991cbbd24b4b89959a90a1a3ae07de40344dccdb040732eea8db1ac57949` |
| [reviewer_node.py](https://github.com/csml-rpi/Foam-Agent/blob/bfb7f791f77438ff90c1a39ff398739ee97bcc67/src/nodes/reviewer_node.py) | `5d2f69358012898e8211145edccf38aadabaacc8a553f053d02e7fce4f5b1816` |

固定版本 logger 确认同样的双日志机制；reviewer 使用 error_logs、review_analysis、rewrite_plan 标签，包含 LLM 分析和计划，不能当成确认根因。local runner 用错误列表是否为空决定输出成功文本；run_local 中还会记录 Allrun 的 attempt 与清理后重试。这些文本可以保留为观测，但不足以独立还原完整工具退出状态、每次输入指纹或数值验收。

当前适配器标记 source-reviewed-v2.1.0，记录 reviewed_source_commit，并保持 verified_run_compatibility=false。第二阶段锁定该 Agent 目标；**源码格式已研究，真实运行兼容性尚未验证**。

运行环境目标为 OpenFOAM Foundation 10，稳态不可压缩单相 simpleFoam；这一选择与 [上游默认环境说明](https://github.com/csml-rpi/Foam-Agent) 一致。OpenCFD/ESI 发行版不纳入第一版目标。

## 下一次接入需要补齐的实际证据

获取固定 commit 源码、读取 runner/reviewer 的实际输出格式，并归档一次真实失败及一次成功轨迹。采集工具开始/结束、检查 outcome 与 exit_code、input manifest、run ID、output manifest、原始日志路径/行号、验收测量和可用消耗。只保留任务与工具证据，不要求采集模型隐藏推理。

重点补足：

| 上游可见材料 | 能保留的事实 | 不能自行推断 |
| --- | --- | --- |
| workflow.log/review.log | 文本、标签、行号、文件哈希 | 工具调用完整性、退出状态、成功声明的精确语义 |
| RunOut | 上游报告的作业标识/状态 | 执行成功、数值达标或参考验证通过 |
| CaseManifest | 上游提供的算例与环境描述 | 历史结果属于当前输入 |
| log.simpleFoam | End、显式 fatal 标记、线性残差观测 | 完整收敛判据、物理正确性或优化效果 |

该研究支撑保守文本适配接口。第二阶段新增的 Collector 已在隔离执行函数测试中捕获真实工具状态与结果归属，通过 normalized 接口审计；完整 Agent 轨迹的兼容性验收仍需模型入口和原生工作流。
