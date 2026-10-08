# 完整模型驱动链路接入

本文件保留 0.5 的运行快照。后续已完成限定工作流的决定/显式完成采集、真实模型故障恢复和耗尽终止，见 [0.6 验证](observed-workflow.md)；旧归档及其证据不足结果保持原样。

固定 Foam-Agent v2.1.0 / `bfb7f791f77438ff90c1a39ff398739ee97bcc67`，OpenFOAM Foundation 10 / simpleFoam。使用完整原生源码和实际 LangGraph，从 planner 进入；模型通过真实后端生成规划、路由决定、输入字典与 Allrun，求解和 PyVista 渲染沿用实际节点。没有用模型回复或节点替身产生运行证据。

## 接入方式

- 候选补丁 0004 在前三项之后应用；单独候选包在 `integration/foam_agent_v210/native/full`。既有 `patched` 包与上一轮运行证据保留。
- `configure_llm` 注入真实模型后端，`configure_retriever` 注入显式检索适配器。默认上游后端保持可用；本次使用 `CodexCLI`，通过官方 CLI 的登录会话执行文本生成，不解析、复制或保存 OAuth token。
- 模型固定为 `gpt-6.1-sol`，CLI `0.159.0-alpha.12.1`，每轮最多 32 次调用；模型调用禁用 shell、联网搜索、插件和任务委派，保留最终请求、回复、实际 usage 和耗时，不保存推理事件。成本无可靠数据，保持 null。CLI 使用方式依据 [官方非交互文档](https://developers.openai.com/codex/noninteractive)。
- 原始 FAISS 数据是未下载的 Git LFS 指针。因此本次使用明确标识的词项交集检索，语料只有一个自编、已有真实 CFD 验收的通道模板，以及固定容器实际命令帮助。它不是原始 FAISS/语义检索的兼容性或泛化验证。
- 生成计划的文件路径在写入前整体验证；仅允许 `0`、`constant`、`system` 中的单层文件名。已有算例目录不覆盖。Allrun 与渲染由各自服务负责，不应出现在输入字典任务中。
- 本轮 Allrun 仅允许 `set -eu` 与依次执行 blockMesh、checkMesh、simpleFoam 并重定向日志。生成脚本在固定 digest、无网络、只挂载新算例目录和只读采集辅助程序的容器中执行；记录每个求解命令的实际退出码、日志、输入 hash 和耗时。该约束只适用于首个小型通道演示。
- 模型渲染回退设为 0，原生确定性 PyVista 渲染照常执行。求解 reviewer 最多两轮；正常执行不会人为触发它。

## 实际运行

首轮归档 `real-runs/full-agent-20261004-01`：模型规划产生 11 个任务，包括不在输入写入器职责内的根目录 Allrun 和 Python 渲染脚本。路径门禁拒绝这些任务，流程在求解前失败。保留规划回复、节点轨迹、失败信息、15 次模型调用与 201,992 个实际 usage token。模型调用本身成功不等于 Agent 任务成功。

根据该证据，在规划系统提示中明确输入写入器只负责字典/初始场，并把整份计划的路径检查提前到文件生成前。该修改不改变任务参数或验收条件；单次新运行不能证明所有模型都不会再生成越界任务。

第二轮 `real-runs/full-agent-20261004-02`：17 次真实模型调用完成规划和九份输入字典/Allrun 生成，原生求解通过五项 CFD 验收并产生 PNG。但检查实际图片发现字段为 p，未满足明确的速度 U 绘图要求；见 `render-validation.json`。原启发式只要全文出现 pressure 就优先绘制 p，把验收中的压力描述误当成绘图请求。

候选进一步改为优先识别明确的 render/plot/visualize 指令；多项冲突指令拒绝，保留未知任务的默认行为。最终运行增加 `requested_field_verified` 门禁，不能只以 PNG 存在或进程退出成功认定完成。

最终完整运行归档：`real-runs/full-agent-final-20261004`。实际执行 planner → input_writer → local_runner → visualization，17 次模型调用、226,290 usage token；整个运行约 183 秒，实际求解命令约 0.96 秒，成本 null。九份生成字典与指定参考一致；实际 PNG 显示 U，`requested_field_verified=true`。

| 验收 | 实测 | 要求 |
| --- | --- | --- |
| 求解退出、End | 通过 | 退出 0 且有 End |
| 稳态收敛 | 通过 | SIMPLE convergence |
| 局部连续性 | 8.10e-13 | ≤1e-7 |
| 速度剖面相对误差 | 0.699% | ≤2% |
| 压力梯度相对误差 | 0.394% | ≤3% |
| 绘图字段 | U | U |

[统一体检](../real-runs/full-agent-final-20261004/audit/report.md)、[验收数据](../real-runs/full-agent-final-20261004/acceptance.json)、[完整结果](../real-runs/full-agent-final-20261004/summary.json) 可回溯实际运行。体检汇总为 0 个发现、1 项通过、3 项证据不足、1 项不适用；CFD 验收通过并不把缺少规范化决定/完成声明的行为检查自动算作通过。

三轮开发运行分别保留：15 次调用的规划失败、17 次调用但绘图字段错误的第二轮、17 次调用的最终通过轮。合计 49 次模型调用、654,812 usage token，另有后端连接检查 1 次、10,966 token。不能只展示最终成功轮来统计优化效果。

## 入口变化的实际复测

`real-runs/inlet-revision-20261004` 复制第二轮的真实生成结果与原身份到新目录，明确把新演示任务的平均入口速度改为 0.02 m/s，保持其余物理、求解参数和全部质量阈值。原目录没有改动。

1. 保留旧输出时调用原生后处理，按 stale_input 拒绝，未启动新渲染。
2. 原生 local_runner 在固定容器重新求解，生成新的 run ID/input hash 与 U/p 场文件。
3. 新结果成功经过校验、消费和速度渲染；旧 caller context 此时按 stale_run 拒绝。
4. 新任务速度剖面误差约 0.699%，压力梯度约 0.393%，通过相同阈值的执行、数值与参考验收。

这是明确的文件/接口故障注入与实际复测，不是自主 Agent 发生旧结果事故的观测，也不是新的完整模型规划运行或保留算例评估。该演示模型调用为 0，不把它并入模型优化成功率。

## 采集与验收边界

保存完整节点边界轨迹、实际模型请求/最终回复、usage、求解进程状态、生成时场文件清单和图片归属。统一记录补充当前运行工具和验收事件，输出 Markdown/JSON 体检。

`coverage.decisions=false`：模型自由文本里的完成声明、原因解释和所有内部控制决定尚未规范化。不会根据成功渲染反推 Agent 宣称任务完成，也不会伪造 completion 事件；相关行为检查仍可能显示证据不足。CLI usage 包含每次独立模型会话的上下文开销，不能与不同后端的 token 口径直接比较。

CFD 验收独立于图结束：固定的执行、稳态收敛、连续性、速度剖面和压力梯度条件全部保留。另以保守词元比较验证生成的九份字典符合指定参考内容，忽略注释、FoamFile 头和数字格式；不同字典顺序也可能被拒绝，不把拒绝直接归因于物理错误。

## 复现

需要已启动的 Docker/OrbStack、已有固定镜像、`.venv-native` 依赖和已登录的 Codex CLI。不会覆盖输出目录或全局安装。

```bash
.venv-native/bin/python scripts/build_full_agent_integration.py
.venv-native/bin/python scripts/run_full_agent.py --out real-runs/my-full-agent-run
```

如需要重新建语料，使用新的目录：

```bash
.venv-native/bin/python scripts/build_reference_corpus.py --out /path/to/new-corpus
```

默认运行器固定读取 `integration/foam_agent_v210/reference-corpus-v1`。环境归档保存源码、补丁、语料、运行辅助程序和依赖清单 SHA-256；第二轮及后续运行从每轮自己的源码快照正常导入。

下一步补齐规范化决定/完成声明与失败重试采集，再按 [评估方案](evaluation-plan.md) 冻结基线/候选并执行保留算例的多次配对运行。模型 reviewer、模型渲染回退、原始 FAISS、HPC、并发及总体优化效果均需要独立验证。
