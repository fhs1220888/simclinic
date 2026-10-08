# 冻结版本的字段选择保留评估

2026-10-04 已完成冻结批次 [`real-runs/heldout-field-selection-20261004`](../real-runs/heldout-field-selection-20261004/freeze.json)：18 次真实保留工作流全部按预定顺序执行，另有 2 次开发协议检查、不计入保留结果。冻结后没有修改 Agent、提示、语料、任务验收或执行顺序。

[逐次主报告](../real-runs/heldout-field-selection-20261004/report.md) / [机器可读结果](../real-runs/heldout-field-selection-20261004/results.json)、[全部尝试与故障恢复](../real-runs/heldout-field-selection-20261004/attempts.md)、[归档核验](../real-runs/heldout-field-selection-20261004/archive-verification.json) 均已生成。

## 本批次结果

| 指标 | 基线 | 候选 |
| --- | ---: | ---: |
| 完成的保留工作流 | 9 | 9 |
| 最终 CFD 条件通过 | 9/9 | 9/9 |
| 全部任务条件通过（含 U 图） | 0/9 | 9/9 |
| 错误成功声明 / 证据缺失 | 0 / 0 | 0 / 0 |
| Allrun 执行尝试 / 失败尝试 | 9 / 0 | 10 / 1 |
| 实际 simpleFoam 调用 / 失败 | 9 / 0 | 9 / 0 |
| 模型调用 | 153 | 156 |

基线 9 次均实际绘制 p，未满足明确的 U 图要求；候选 9 次均绘制 U，保留原 CFD 和输入验收。结果支持本批次声明的字段选择策略，仍不能证明整个 Agent 或五项补丁的综合优化效果。

`H2-r3-candidate` 首次 Allrun 在 blockMesh 阶段因 controlDict 为 JSON 包裹文本而退出，尚未执行 simpleFoam。双方共用的既有 reviewer 生成改写计划，将内层字典恢复为真实换行的文件，保留字典设置，第二次执行通过全部原验收。两次输入、日志、跨尝试关联及全部 20 次模型调用（274,240 token）均保留，没有人为选择性重跑。这是一次自然格式故障及共享机制恢复，发生在字段选择之前，不能归因于候选字段改动。

全部保留工作流消耗 4,157,197 usage token，加上开发检查共 4,613,377 token、343 次模型调用。双方同时完成全部任务的合格配对为 0，因此本批次没有加速或节省消耗的结论；费用保持未知。全部记录的 1,234 个 artifact 哈希、21 份输入快照、冻结源码/语料/参考、串行顺序及实际模型 usage 已离线核验，解析参考误差由归档采样重新计算。核验没有调用模型或新增仿真。

## 公平比较的范围

这次是受控的单项策略消融。双方沿用已验证 observed Agent；唯一源码差异是 `_guess_primary_field`：基线恢复固定 v2.1.0 补丁前三项快照中原有的 pressure 优先匹配函数，候选使用已验证的显式 render/plot/visualize 字段选择。精确差异在 [arm-difference.patch](../real-runs/heldout-field-selection-20261004/arm-difference.patch)。基线不是未修改上游；这次不比较五项补丁的整体效果。

模型/推理配置、完整规划/生成服务、RAG 语料、受限 Allrun、退出状态与网格门禁、结果归属、原生渲染、采集和显式完成验收完全共用。双方都只有满足全部任务条件才声明成功，因此不能把共同门禁产生的诚实失败或零错误成功声明归因于候选字段规则。

模型固定 `gpt-6.1-sol`、reasoning effort high；后端只生成文本，执行工具禁用。OpenFOAM Foundation 10 固定镜像 digest、linux/amd64、2 CPU/2 GiB、无网络、单进程求解，渲染回退禁用。Python/依赖、CLI 二进制与版本、两套完整源码、共同运行模块、语料、任务/验收参考、执行顺序均在 freeze.json 中保存哈希。每次运行只执行归档的代码，检查冻结文件和依赖未变；每次输出目录全新。

## 预先声明的任务与次数

| 任务 | 平均入口速度 | 网格 | 每组次数 |
| --- | ---: | --- | ---: |
| H1 | 0.015 m/s | 40×20×1 | 3 |
| H2 | 0.025 m/s | 40×20×1 | 3 |
| H3 | 0.015 m/s | 60×30×1 | 3 |

几何、黏度、层流、边界类型、数值配置和采样坐标不变。按任务速度重新定义入口列表、U 初场与 Poiseuille 解析速度/压力梯度；H3 同时改变列表长度及网格。质量条件仍为实际求解退出/End、SIMPLE 收敛、连续性≤1e-7、速度相对 L2≤2%、压力梯度相对偏差≤3%，还需任务输入符合性与当前 U 图片。

验收参考在首次运行前生成并冻结，只由共同验收节点读取。Agent 只检索原 0.01 m/s 开发语料；保留任务的字典、结果或错误不加入检索语料、不用于调整提示或补丁。任务请求明确给出新物理参数和公式，双方收到相同请求。

已有 D0（0.01 m/s、40×20×1）在每组各做一次完整模型工作流，作为共同协议检查，独立保存在 development，不计入保留结果。18 次保留运行以 task/repeat 为配对，交错首发组，串行执行；失败不选择性重跑。

## 采集与分析

每次保留真实输入、所有求解尝试、工具退出码、模型最终输出/实际 usage、渲染与采样 bytes、测量和显式完成事件。失败/中断也进入逐次清单及分母；缺失行为证据保持 unknown，不归为零错误。JSON/Markdown 同时报告任务质量、CFD 条件、错误成功声明、候选无效重试、证据缺失、图验收和所有可用消耗。

消耗以全部已运行样本的均值/中位数/范围描述，并保留 null；只有同任务同重复的双方都质量达标、证据完整时，才允许质量达标前提下的消耗比较。若基线始终未完成 U 图任务，将没有这种合格配对，不能声称候选加速或更省。模型费用未知保持 null。

预计 18 次保留完整链约 400 万 usage token，开发检查另计。批次在运行之间检查累计 usage，保留下一轮 45 万 token 余量，预算边界为 500 万；这是运行间限制，单轮仍有最多 32 次模型调用，不能当作逐 token 硬限制。用量未知时停止并保留未执行项，不猜测为零。

没有可靠的模型 seed 或供应商后端快照。三项参数变化仍属于同一通道；多次运行只作探索性描述，不声称统计显著或跨领域效果。自然运行与故障注入不混合，本批次没有注入旧结果、求解故障或重试故障；不能用它证明这些其他改进有效。

## 复现

已有冻结目录禁止覆盖、禁止改源码或选择性重试。新批次可使用：

```bash
.venv-native/bin/python scripts/freeze_heldout.py --out real-runs/heldout-new
.venv-native/bin/python real-runs/heldout-new/common-runtime/scripts/run_heldout_batch.py --batch real-runs/heldout-new
```

离线重建报告不调用模型或求解器：

```bash
python3 real-runs/heldout-field-selection-20261004/common-runtime/scripts/report_heldout.py --batch real-runs/heldout-field-selection-20261004
python3 scripts/report_heldout_attempts.py --batch real-runs/heldout-field-selection-20261004
python3 scripts/verify_heldout_archives.py --batch real-runs/heldout-field-selection-20261004
```

若出现采集软件错误，保留本批次并创建新的冻结批次；不要用保留结果调整 Agent。结果文件为批次根目录的 results.json/report.md，所有运行证据在 runs，开发检查在 development，完整运行输出在 execution-logs。
