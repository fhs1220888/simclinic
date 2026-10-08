# 实际采样工具门禁（0.11）

新的 `GatedChannelCompletion` 已注册到默认运行入口。原生 local_runner 执行真实 OpenFOAM 求解和 PyVista U 渲染，完成节点在当前身份的隔离工作区执行采样，再判断任务是否达标。默认源码为已有的 content 候选；原 `ChannelCompletion`、已冻结保留批次和上游六项补丁保持。

## 工具契约与证据

- 采样前核对 run ID、输入版本和工作区快照，并拒绝已有 postProcessing 输出。
- 固定 Foundation 10 镜像执行 `simpleFoam -postProcess -func sample -latestTime`，保留真实退出码、stdout/stderr 和超时。致命日志优先报告失败，即使退出为 0；未知退出或不可读取日志不会提升为通过。
- 要求唯一、非空、合法的 profile.xy 和 pressure.xy，拒绝不安全路径和无效数值。从注册归档字节计算原参考误差；离开工作区并再次核对身份后才发布测量。
- 注册 sample 工具事件、带原因的 `gate-assessment.json` 和关联事件的 continue/stop 决定。完成验收保留原任务条件；失败时不发布参考数值，references 覆盖保持 false。

`SamplingError` 包含 code、非空消息和评估 artifact ID。类别涵盖 `sampling_fatal_log`、`sampling_missing_output`、`sampling_process_exit`、`sampling_timeout`、`sampling_log_encoding`、空/歧义/不安全/无效输出及身份错误。致命日志证据含文件、artifact ID 和行号；缺失输出给出所需路径。工具不自动修改字典、物理模型或求解参数，也不调用修复模型。

只读 [diagnose-sampling](sampling-diagnostics.md)检查已登记字节证据；运行门禁还观测实际新采样进程及其工作区。离线归档缺少文件属于证据不足，运行门禁确认本次进程没有生成必要文件时属于工具失败。这两个范围不能互相替代。

## 已完成的真实开发回归

| 批次 | 新求解次数 | 注册证据 | 结果与范围 |
| --- | ---: | ---: | --- |
| [正常/历史 fatal 输入](../real-runs/sampling-gate-regression-20261004/report.md) | 4 | 150 | 正常两策略均达标；历史失败输入重新求解后采样 exit 0，旧工具 pass，新工具 sampling_fatal_log，日志第 46 行 |
| [缺失 profile 注入初次回归](../real-runs/sampling-missing-output-regression-20261004/report.md) | 2 | 73 | 明确将 sample 集合 profile 改名；双方任务失败，新工具明确 missing output |
| [缺失 profile 最终回归](../real-runs/sampling-missing-output-final-20261004/report.md) | 2 | 73 | 当前代码，缺失路径明确、错误前缀只出现一次；双方任务失败 |
| [默认入口](../real-runs/sampling-default-chain-20261004/report.md) | 1 | 39 | 默认 content/sampling、local_runner，通过原参考、任务输入及 U 图验收 |

共 9 次实际新求解、335 个注册证据，零新模型调用。8 次配对回归冻结了共同源码、环境、任务及输入，实际输入哈希配对一致；每次新 run ID 均不同于历史归档，没有复用旧求解输出。正常配对的速度误差均为 0.004103791308728428、压力梯度误差均为 0.002548753714165488。故障运行仍没有参考误差，文件哈希核验通过不代表 CFD 参考条件通过。

前 6 次冻结门禁的错误消息存在重复前缀显示问题，原始记录保留；交付代码仅修正这一格式，随后另冻结完整 2 次回归。核验程序只接受这一明确的代码差异，最终批次与交付门禁完全一致。9 次运行均为开发检查，其中 4 次为明确输出故障注入；没有新模型规划或独立保留任务。

旧策略本来就没有误报完整任务成功。本次证明正常路径兼容，及两类故障的工具状态、具体原因和停止证据改善；没有证明成功率、自然故障率、自动恢复、加速或模型消耗改善。

## 使用与复现

默认入口与旧策略选择见 [README](../README.md)。原生执行需要现有 `.venv-native`、固定依赖和已安装的不可变 Docker 镜像，环境见 [集成说明](../integration/foam_agent_v210/README.md)。正常 local_runner 的零调用结果已验证；带故障的共享 reviewer 可能调用模型。`--entry planner` 的新门禁已接线，本轮未运行其新的真实模型规划。

配对开发脚本禁止模型调用，重新准备时须使用新目录。它需要现有历史输入归档，不能充当独立新任务验证。以下按冻结的顺序逐条执行：

```bash
.venv-native/bin/python scripts/run_sampling_gate_regression.py --prepare --out real-runs/my-gate-regression
.venv-native/bin/python real-runs/my-gate-regression/common-runtime/scripts/run_sampling_gate_regression.py --batch real-runs/my-gate-regression --run healthy-legacy
.venv-native/bin/python real-runs/my-gate-regression/common-runtime/scripts/run_sampling_gate_regression.py --batch real-runs/my-gate-regression --run healthy-sampling
.venv-native/bin/python real-runs/my-gate-regression/common-runtime/scripts/run_sampling_gate_regression.py --batch real-runs/my-gate-regression --run archived-fatal-sampling
.venv-native/bin/python real-runs/my-gate-regression/common-runtime/scripts/run_sampling_gate_regression.py --batch real-runs/my-gate-regression --run archived-fatal-legacy
python3 scripts/verify_sampling_gate_regression.py --batch real-runs/my-gate-regression
```

`--prepare --scenarios missing-profile` 单独冻结显式输出故障注入，顺序为 missing-profile-legacy → missing-profile-sampling；不得混入自然故障率。核验只读已注册证据，输出补充 verification/report，不修改冻结协议或原始记录。

## 交付与下一步

142 项软件测试在 Python 3.9.6 和 3.12.14 通过；替身和解析样本明确仅为软件测试。覆盖超时、未知日志、异常退出、输出缺失/歧义/格式/路径、旧上下文及采样后身份改变不发布测量。[离线安装证明](../demo-output/sampling-gate-package-verification.json)确认 0.11 wheel 在仓库外可导入、查询真实归档和已完成冻结批次；临时环境已清理。

[可应用补丁](../patches/simclinic-sampling-gate/README.md)包含具体逐文件基准哈希、关联证据、预期作用和复测；已核验应用/撤销。下一步先冻结新策略与公平基线，做新的完整模型 D0 检查；再使用未参与开发的新参考任务、多次保留运行。H/I 任务及历史回复均排除在新的独立保留集之外，候选分支实际触发与未知指标需单列。总体优化效果尚未证明。

后续进展：[同源码策略对照已冻结、两次真实完整模型 D0 均通过](sampling-policy-study.md)，123 个注册证据核验通过。下一步为该新协议的 18 次保留运行；本页的 9 次零模型开发回归仍单独保留，不混入新评估。
