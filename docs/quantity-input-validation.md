# 量纲感知输入核对与新比较

本页记录 nu 兼容性修复、六个真实 OpenFOAM 正负开发算例和后续比较的冻结安排。随后四次开发加 24 次完整模型运行已完成，暴露入口列表精度的另一处误判；见[完整结果与下一步](quantity-recovery-results.md)。旧批次及新批次原评分保持，总体优化效果仍未证明。

## 修复范围

Foundation 10 的 constant viscosity 模型按要求的黏度量纲读取 `nu`；dimensioned 读取器允许省略量纲，也允许与要求相同的显式量纲，错误量纲会触发读取错误。依据：[constantViscosityModel 实现](https://cpp.openfoam.org/v10/constantViscosityModel_8C_source.html)、[dimensionedType 读取实现](https://cpp.openfoam.org/v10/dimensionedType_8C_source.html)。

新策略 `foundation10-constant-nu-literal-v1` 仅接受 `constant/physicalProperties` 中 constant 模型的标量字面量：`nu 0.001;` 与 `nu [0 2 -1 0 0 0 0] 0.001;` 可等价，十进制数值必须精确相同。改变黏度、错误量纲或改变黏度模型记录为发现问题。缺失文件、符号链接、重复条目、复杂表达、动态指令、不支持的字典结构或非有限字面量保持证据不足，不能通过完成门禁。当前限制还包括带引号的属性/头部内容；它不是通用 OpenFOAM 字典解释器。

其他八个输入文件保留原 canonical token 比较。旧完成策略仍使用原比较器；新策略必须由任务契约显式声明才可启用。新完成节点报告逐文件四态判定、原因、来源路径和实际/预期 SHA。已确定的不匹配使输入验收为 false，仅有未知而没有已确定不匹配时为 null；两者均不能声明任务成功。检查适用范围由明确的 Foundation 10 任务契约限定，本轮没有不适用的输入项。

本次修改的是两组共用的评估器。候选 Agent 的唯一差异仍是已验证的一次有限重采样；没有修改求解参数、物理目标或质量阈值。三条旧误拒绝记录仅作为已知开发回归，历史主评分不改。

## 实际开发验证

[冻结执行协议](../real-runs/quantity-input-development-20261006/freeze.json)在执行前声明六个正负预期。固定 Foundation 10 镜像与 Foam-Agent v2.1.0 commit，2 CPU / 2 GB / linux amd64 / 无网络；native local_runner → 求解 → U 图 → 采样 → 注册完成，模型拒绝器、reviewer 和模型渲染回退均为零。

| 输入 | 网格 | 完整任务 | 输入门禁 | 实际读取/求解 |
| --- | --- | --- | --- | --- |
| 省略量纲，nu=.001 | 40×20 | 通过 | 通过 | 正常结束 |
| 正确显式量纲，nu=.001 | 40×20 | 通过 | 通过 | 正常结束 |
| 省略量纲，nu=.001 | 80×40 | 通过 | 通过 | 正常结束 |
| 正确显式量纲，nu=.001 | 80×40 | 通过 | 通过 | 正常结束 |
| 改变 nu=.002 | 40×20 | 拒绝 | 发现问题 | 正常结束且其他质量判据通过 |
| 错误量纲 [0 2 -2 0 0 0 0] | 40×20 | 拒绝 | 发现问题 | 原始 fatal 日志、非零退出 |

六项开发预期全部满足，不能将两个应拒绝负例计为质量成功。改变黏度的结果仍能通过这个 Couette 流场参考，说明输入验收必须单独保留。共六次真实求解尝试、五次采样、零模型调用，599 个注册证据和六份输入快照核验；临时防睡眠已释放。见[逐次报告](../real-runs/quantity-input-development-20261006/report.md)、[离线核验](../real-runs/quantity-input-development-20261006/archive-verification.json)和[历史完整性](../real-runs/quantity-input-development-20261006/historical-integrity.json)。

运行现场与注册归档各有一份输入快照，核验器分别检查两份快照身份和字节，随后重新计算判定；已保存单独的 [离线核验器清单](../real-runs/quantity-input-development-20261006/offline-verifier-manifest.json)。执行器冻结副本原样保留。软件正负回归覆盖精确数值变化、错量纲、复杂/重复/缺失输入、其他字典变化、旧比较器行为与旧三条误拒绝记录；两套 Python 的 207 项测试及 15/15 诊断基准通过。

从项目目录运行离线核验，不新增求解或模型调用：

```bash
.venv-native/bin/python real-runs/quantity-input-development-20261006/offline-verifier/scripts/validate_quantity_inputs.py verify --batch real-runs/quantity-input-development-20261006
python3 -m unittest discover -s tests
```

## 后续新比较：已完成，原冻结安排如下

[新协议](../real-runs/full-model-quantity-recovery-study-20261006/PROTOCOL.md)及[冻结清单](../real-runs/full-model-quantity-recovery-study-20261006/freeze.json)固定两组共用新输入核对，仍只比较是否有限重采样。先四次 C0 正常完整模型开发检查，全部达标后才执行 24 次保留任务：

| 保留任务 | 壁面速度 m/s | 网格 | 情景与重复 |
| --- | --- | --- | --- |
| C3 | .022 | 46×23 | 正常/首次采样输出短暂丢失，各三次，每次两组 |
| C4 | .035 | 52×26 | 正常/首次采样输出短暂丢失，各三次，每次两组 |

C1/C2 转为开发数据，旧 H/I/J/K/L、C0/F0 及历史回复/求解结果排除在新保留证据之外。检索仅含已验证 C0 模板和既有实际命令帮助。C3/C4 是同一 Couette 机制的新参数组合，不是新物理机制。

固定 gpt-6.1-sol/high、实际 CLI 二进制及依赖、镜像 digest、Agent commit、2 CPU/2 GB 与时间限制。预算 7,500,000 usage token，开发子预算 1,500,000，每次开始前预留 450,000；费用未知保持 null。正常两组各 6/6、无错误完成声明、完整预声明分母/已知消耗，且丢失情景候选通过数多于基线，才标记本批明确注入条件下的恢复收益。求解/工作流时间和模型用量只在两组质量都通过时作配对差，同时保留全部尝试与失败消耗。

开发质量失败、基础设施/采集/模型传输错误、未知用量或预算边界立即停止。保留任务的诚实质量失败留在分母；不选择性重跑或续跑。本轮已按这 4+24 次安排完整执行并核验：候选采样恢复 6/6，正常/注入两种情景的原完整质量均为两组各 0/6，入口数值表示被共有比较器误拒，收益门槛未通过。没有推断自然故障率、稳定消耗节省或总体优化。

实际运行 82.5 分钟、476 次模型调用、6,413,038 token、3,950 个注册证据，保护已释放；不能重复执行 batch。以下命令仅作离线冻结/结果核验，不新增模型或求解：

```bash
.venv-native/bin/python real-runs/full-model-quantity-recovery-study-20261006/common-runtime/scripts/run_quantity_recovery_study.py check-freeze --batch real-runs/full-model-quantity-recovery-study-20261006
.venv-native/bin/python real-runs/full-model-quantity-recovery-study-20261006/common-runtime/scripts/run_quantity_recovery_study.py verify --batch real-runs/full-model-quantity-recovery-study-20261006
```
