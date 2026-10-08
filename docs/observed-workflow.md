# 0.6：决定采集、模型故障恢复与显式完成

2026-10-04 已执行三次真实 Agent 工作流：完整正常链、可恢复故障、持续故障；另从恢复记录制作了一份明确标注的缺失事件导出。OpenFOAM Foundation 10、Foam-Agent v2.1.0 固定提交、镜像及模型后端沿用 [完整接入说明](full-agent-integration.md)。这是开发与回归验证，多次保留评估尚未执行。

## 结果

| 场景 | 实际求解次数 | 实际模型调用 / usage token | 完成声明 | 行为检查：发现 / 通过 / 不足 / 不适用 |
| --- | ---: | ---: | --- | --- |
| [完整正常链](../real-runs/observed-full-final-20261004/audit/report.md) | 1 | 17 / 226,832 | 成功 | 0 / 4 / 0 / 1 |
| [模型修复后恢复](../real-runs/observed-recovery-20261004/audit/report.md) | 2 | 3 / 45,019 | 成功 | 0 / 5 / 0 / 0 |
| [持续故障耗尽](../real-runs/observed-exhausted-20261004/audit/report.md) | 2 | 5 / 77,032 | 失败 | 1 / 1 / 0 / 3 |
| [缺失事件导出](../real-runs/observed-missing-evidence-20261004/audit/report.md) | 未重新运行 | 未新增调用 | 未导出 | 0 / 0 / 5 / 0 |

正常链实际经过 planner → input_writer → local_runner → visualization → completion。两项故障测试从 local_runner 进入，使用新复制的参考输入，不复用旧解；实际调用模型 reviewer、改写计划及输入改写服务。模型为 `gpt-6.1-sol`，后端 CLI `0.159.0-alpha.12.1`；调用记录保存请求、最终回复、退出状态与 provider usage，不收集隐藏推理。模型调用的执行工具禁用。usage 含会话上下文开销，成本未知，保持 null。

成功的正常链及恢复链均满足：真实进程退出/End、任务指定稳态收敛、局部连续性 `8.10074078918e-13 ≤ 1e-7`、速度剖面相对 L2 `0.006986786967466754 ≤ 0.02`、运动学压力梯度相对误差 `0.003935226324550441 ≤ 0.03`。输入快照逐文件符合原通道任务；生成的实际 PNG 字段为 U，已检查图片。原五项数值/参考条件保持不变，显式完成节点另外要求任务输入符合性和所要求的 U 图片。

正常链总耗时 186.22 秒，求解进程耗时 1.015 秒；恢复链分别为 49.15 / 1.832 秒，持续故障为 92.54 / 1.694 秒。求解耗时累计所有尝试。恢复/故障与正常链入口不同，且部分任务同时运行；这些是逐次观测，不能用于证明加速或消费降低。

## 实际改变

新增 [0005 补丁](../patches/foam-agent-v2.1.0/0005-observed-decisions-completion.patch)，在 0001–0004 后应用；新候选在 `integration/foam_agent_v210/native/observed`。旧 upstream、patched、full 快照和旧真实归档保留。

1. 受限串行 Allrun 逐条执行 blockMesh、checkMesh、simpleFoam。命令启动前落盘 started，结束后保存实际退出码、原始 stdout/stderr、输入摘要和耗时。checkMesh 必须退出 0 且给出 Mesh OK.；否则实际控制门禁停止，未知文本不会作为通过。
2. 每次求解前保存输入快照和清单；结束时立即归档该次日志与 native 记录。下一次重试清理工作目录不会覆盖首次失败证据。结果消费与图片仍使用生成时身份和校验后的字节快照。
3. 观测实际路由目的地、reviewer 输出、定向改写计划及处理的工具 ID。原因分析保存为 `cause_hypothesis`，不提升为事实。改写必须有非空文件白名单；返回未计划文件会拒绝。
4. 通过 `session_id` 和显式 `retry_of` 把一个工作流中的不同 run/input 关联起来。新输入上的同工具成功重试能够证明恢复；仅创建新 run ID 不能隐藏输入、参数及错误类别未变的重复失败。没有共同 session 的旧记录保留原规则。
5. 注册真实 completion 节点：从当前归属的日志和核验快照测量任务条件，先发 measurement，再发显式 success/failed claim。未知数值不能生成成功。它是候选 Agent 的任务完成机制；没有从 PNG、End 或自由文本导入器补造未修改上游的完成声明。

`coverage=true` 只覆盖已注册的本地工作流：受策略校验的串行命令、实际模型调用、原生渲染、任务验收、网格门禁、路由、reviewer/输入生成输出及 completion。排除隐藏推理、外部 Agent、任意脚本、HPC 与未测试的模型渲染回退。中断、缺失命令元数据或缺失门禁会降级相关覆盖；未观测终止事件不能把记录标为完整。

## 故障证据与边界

恢复测试明确注入 `system/fvSolution` 的 `SimClinicInvalidSolver`，真实 OpenFOAM 首次 exit 1。模型据错误日志诊断并提出只改该文件的计划，实际恢复 GAMG 后重新运行，最终通过原验收。两次输入清单仅此文件不同；首次失败日志和真实 reviewer 回复都已保存。它证明实际模型修复链可工作，不能估计自然故障率。

持续故障测试在每次 local_runner 启动前明确重新写入相同错误字节；模型提出的修复被测试注入器再次破坏。两次实际输入清单完全相同，run ID 不同；审计检出候选无效重复重试。达到两次循环上限后 completion 明确失败，无后处理引用、无成功声明；参考数值保持 null。这个发现描述注入产生的重复模式，不证明 Agent 在正常条件下会自行无效重试。驱动程序退出 0 表示负向场景符合预期，任务本身仍失败。

缺失事件场景删除恢复记录的规范化 tool/decision/reference/claim 事件，保留真实测量和归档文件，相关 coverage=false。记录标注 `evidence_ablation=true`、`new_simulation_run=false`，保留原身份；没有把它当作第四次仿真或判定 Agent 出错。首轮 `observed-healthy-20261004` 为开发草稿，最终正常依据使用上表完整链。

## 复现与离线审计

实际运行已完成。离线检查不需要 Docker 或模型服务：

```bash
python3 scripts/verify_observed_runs.py
python3 -m simclinic audit real-runs/observed-recovery-20261004/record.json --case real-runs/observed-recovery-20261004 --out demo-output/observed-recovery-audit
python3 -m unittest discover -s tests
```

[归档核验结果](../demo-output/observed-verification.json) 核对四份记录全部 206 个 artifact 的 SHA-256、三次执行的源代码/运行辅助模块、每次输入快照、事件时序、usage 汇总、恢复时仅定向文件变化、持续故障相同输入及五项补丁的应用/逆向恢复。它核验归档一致性，不提供密码学来源鉴证，也不重新求解。

重新运行需要已有 Docker/OrbStack、固定镜像、本项目隔离的 `.venv-native` 依赖、固定源码/参考语料及已登录的 Codex CLI。先参考 [原生环境说明](native-postprocessing.md) 和 [模型接入说明](full-agent-integration.md)。使用新的输出目录：

```bash
.venv-native/bin/python scripts/run_observed_agent.py --entry planner --fault none --out real-runs/observed-full-new
.venv-native/bin/python scripts/run_observed_agent.py --fault recoverable --out real-runs/observed-recovery-new
.venv-native/bin/python scripts/run_observed_agent.py --fault persistent --out real-runs/observed-exhausted-new
python3 scripts/ablate_observed_evidence.py --source real-runs/observed-recovery-new --out real-runs/observed-missing-new
```

输出目录必须不存在。模型调用上限完整链 32 次，局部故障链 8 次；最多两次 reviewer 循环，模型渲染回退禁用。每个归档包含 `record.json`、`summary.json`、`audit/report.md/json`、`environment.json`、`runtime-source`、`source-package`、`attempts`、`observations` 和 `model-calls`。

下一步冻结共同观测层及公平的基线/候选差异，再按 [保留评估方案](evaluation-plan.md) 运行独立任务。当前三次执行都是开发数据，`optimization_effect_proven=false`。
