# 采样完成策略的公平冻结评估

0.11 已完成采样门禁的实际开发回归；本轮使用新的冻结协议验证完整模型链路，并为独立保留评估准备公平对照。[冻结目录](../real-runs/sampling-policy-study-20261004/READY.md)保存当前创建时的源码，后续工作区改动不进入该批次。

## 对照与新任务

两组都使用同一 Foam-Agent content 候选（固定上游 commit `bfb7f791f77438ff90c1a39ff398739ee97bcc67`），包括正文接口修复、U 字段选择和同一 reviewer。原生源码逐文件哈希完全相同；唯一切换是注册完成策略：基线 `HeldoutCompletion`，候选 `GatedHeldoutCompletion`。两组共同冻结模型 gpt-6.1-sol/high、CLI 二进制、Python/依赖、D0 检索语料、固定 Foundation 10 镜像、2 CPU/2 GiB 和原验收阈值。

`arm-difference.patch` 是完成策略选择声明，不能作为源码补丁应用。实际选择逻辑在冻结的 sampling_study 模块和单次运行脚本中。基线是受控策略比较，不能称为未修改上游。

| 任务 | 平均速度 m/s | 网格 | 每组次数 |
| --- | ---: | --- | ---: |
| J1 | 0.022 | 56 × 28 × 1 | 3 |
| J2 | 0.028 | 56 × 28 × 1 | 3 |
| J3 | 0.022 | 64 × 32 × 1 | 3 |

参数组合不同于 D0、H1/H2/H3 和 I1/I2/I3。几何、物性、参考点和阈值保持，解析参考使用各任务规定速度。H/I、历史回复和门禁回归已进入开发数据，排除在独立保留集之外。保留任务的参考字典/结果不送入模型上下文，检索只使用已有 D0 语料；重复参数变化不等于跨领域泛化。

## 分阶段执行与预算

1. `development`：基线 → 候选各一次 D0，均从真实 planner 开始，执行输入生成 → 求解 → U 渲染 → 采样 → 完成。两次都必须通过原完整任务且证据/usage 完整。此阶段结束后不会自动启动保留任务。
2. `heldout`：独立调用协调器，先核验 D0 归档，再按冻结顺序执行 J1/J2/J3，每任务每组 3 次，共 18 次。不得重跑挑选失败，也不得按保留结果调整补丁。

每次最多 32 次模型调用、2 次 reviewer 循环，关闭模型渲染回退。D0 阶段预算 100 万 usage token，含 D0 的总批次预算 500 万；每次启动前预留 45 万，未知 usage 或到边界停止。预算在运行间检查，预留不是单次 token 硬上限；每次实际耗用、失败、恢复及未知费用均保留。

```bash
# 当前批次已冻结并开始开发检查，禁止再次运行 development
# 仅用于新的独立批次：
.venv-native/bin/python scripts/freeze_sampling_study.py --out real-runs/NEW_BATCH
.venv-native/bin/python real-runs/NEW_BATCH/common-runtime/scripts/run_sampling_study_batch.py --batch real-runs/NEW_BATCH --stage development
# 两次 D0 成功、已核验且无保留运行时，才允许：
.venv-native/bin/python real-runs/NEW_BATCH/common-runtime/scripts/run_sampling_study_batch.py --batch real-runs/NEW_BATCH --stage heldout
```

协调器持久化唯一运行目录、串行顺序和用量；单次脚本只接受协调器登记的当前活动运行。已有开发 progress 不能重复启动；保留阶段只能从两次 D0 完整通过的状态进入。失败或软件问题须保留原批次，另立协议。

## 评分与解释

主要指标仍为完整任务质量：执行、收敛、守恒、速度/压力参考、任务输入与 U 图均通过。失败和证据缺失保留在分母。次要指标单列采样诊断：核验原始 fatal 日志与工具 outcome/error_category 是否一致、必要输出的可用字节证据、明确停止决定、错误完成声明和重试；同时报告求解耗时和模型消耗。消费比较只采用两组质量达标且证据完整的配对。

候选完成节点被调用不代表其故障分支被触发。报告分别记录调用次数、自然失败分支和可核验的 fatal 事件。离线归档缺少输出时仍为证据不足，不能猜测进程未生成文件；更准确的失败原因不计为任务成功。若新任务没有自然采样故障，诊断收益的自然频率仍未知。没有模型可靠 seed 或服务端快照，串行交错顺序仅减少时间漂移。

## 查询与证据

```bash
python3 -m simclinic study-status real-runs/sampling-policy-study-20261004
python3 real-runs/sampling-policy-study-20261004/common-runtime/scripts/report_sampling_study.py --batch real-runs/sampling-policy-study-20261004
# 完成 D0 阶段或完整保留阶段后：
python3 real-runs/sampling-policy-study-20261004/common-runtime/scripts/verify_sampling_study.py --batch real-runs/sampling-policy-study-20261004
```

[freeze.json](../real-runs/sampling-policy-study-20261004/freeze.json)和 freeze.sha256 为协议依据；progress.json 是执行状态，sampling-report.md 列开发与采样，report.md 列保留质量/消耗，开发核验输出 development-verification.json。原始模型请求/最终回复、公开 usage、工具日志与运行身份保留，不收集隐藏推理。

新增 6 项软件测试验证任务未重复、交错配对、旧/新选择、原物理/采样/阈值保持、开发失败或未知用量不进入保留以及源码/冻结篡改拒绝。全部 148 项测试在 Python 3.9.6/3.12.14 通过；[软件核验记录](../demo-output/sampling-study-software-verification.json)明确不包含新保留任务。新增评估模块在仓库外 [离线安装验证](../demo-output/sampling-study-package-verification.json)通过，该安装查询发生在开发阶段运行中。

## 完整模型开发检查已完成

freeze SHA 为 `ccee0532272ac292e4100cbd7a4d1906b0b9637a0db6c1ff44e51d544f5445f4`。两次 D0 按基线 → 候选执行，均完成真实 planner → 生成 → 求解 → U 图 → 采样 → 完成，全部原任务条件通过；没有故障注入、历史回复重放、失败、重试或模型渲染回退。

| D0 | 模型调用 | usage token | 完整任务 | 证据完整 |
| --- | ---: | ---: | --- | --- |
| baseline/legacy | 17 | 227353 | 通过 | 是 |
| candidate/sampling | 17 | 227723 | 通过 | 是 |

总计 34 次模型调用、455,076 token，两次真实求解。双方 local continuity 为 8.10074078918e-13、速度误差 0.006986786967466754、压力梯度误差 0.003935226324550441；原任务输入和 U 图均符合。候选节点实际调用，但失败分支未触发；不能用这一次正常 D0 配对声称诊断收益、稳定消耗改善或总体优化。

[实际结果](../real-runs/sampling-policy-study-20261004/DEVELOPMENT.md)和 [开发归档核验](../real-runs/sampling-policy-study-20261004/development-verification.json)确认 123 个注册证据文件、2 份输入快照、原生源码/冻结哈希及公开模型 usage 一致，参考误差重算通过。[状态快照](../demo-output/sampling-policy-study-status.json)为 development_complete，保留仍为 0/18。

后续已启动保留阶段，但首项 J1 的第 11 次模型请求 exit 1 且 usage 未知，协调器按原规则停止为 `usage_unknown`，保留 1/18、没有求解。上述 development_complete/0/18 是开发结束时的历史快照，不能作为当前状态。[停止与部分核验](../real-runs/sampling-policy-study-20261004/STOPPED.md)保留全部失败和未知用量；旧批次不能直接重启。工作区补充后端错误分类/部分消耗后，另建 [新的 K 协议](sampling-policy-study-v2.md)，J 任务全部排除，结果单列。总体优化效果尚未证明。
