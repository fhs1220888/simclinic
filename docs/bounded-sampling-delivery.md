# 有限采样恢复的候选与离线复现

本交付适用于固定 Foam-Agent v2.1.0 的注册完成节点、Foundation 10 / simpleFoam、稳态不可压缩单相 Couette 工作流。已验证作用是：在明确首次新采样输出丢失条件下，从当前可信结果重新采样一次。它不证明自然故障收益、统计显著性、稳定消耗节省、跨物理效果或整个 Agent 的综合优化。

## 基准与可应用产物

[候选目录](../patches/simclinic-bounded-sampling-recovery/README.md)独立于旧采样门禁交付。基准是最新批次 `common-runtime` 的 52 个冻结文件，以完整逐文件 SHA-256 标识；没有假定公开 Git 标签或本地提交。冻结 SHA-256：

```text
5fe1c82fe06adde350c10362dbe7af080cff010327a0752b33803858217bd3f8
```

基准本身已包含经评估的 `BoundedSamplingRecovery` 和 `RecoveringContractReferenceCompletion`；当时两组只在完成节点类选择上不同。交付补丁新增默认关闭的显式选择器，启用时调用同一已验证类，不重写恢复算法。基准与目标清单、修改字节、严格应用/撤销工具和临时验证结果均在候选目录。已有八个受保护文件、0.11.0 元数据、历史冻结源码与原评分保持。九文件 v3 是基准和候选的共有评估器修复，不能计为候选 Agent 差异。

应用、核验、启用和撤销的可运行命令见[候选 README](../patches/simclinic-bounded-sampling-recovery/README.md)。应用只在新建的集成副本上进行；不要往历史冻结目录应用。运行时关闭显式选择即可回到共有 v3 基线；物理撤销仅在目标字节仍匹配清单时允许，避免删除开发者后续修改。

## 触发和失败边界

实际调用链是 `ContractReferenceCompletion` → `ReferenceTaskCompletion` → `GatedChannelCompletion.sample`，候选在该链前加入 `BoundedSamplingRecovery`。源代码位置：[恢复](../simclinic/domains/openfoam/sampling_recovery.py)、[采样门禁](../simclinic/domains/openfoam/sampling_gate.py)、[身份工作区](../simclinic/foam_runtime.py)、[原验收](../simclinic/domains/openfoam/reference_tasks.py)、[共有输入契约](../simclinic/domains/openfoam/input_contract.py)。

| 条件 | 实际处理 |
| --- | --- |
| 正常采样 | 只采样一次，按原验收判断完成 |
| 明确 `sampling_missing_output` | 最近 sample 必须是 exit 0、outcome fail，且与异常的 assessment artifact 一致；实例还未使用恢复次数才可继续 |
| 当前 run/input 或可信字节不可核验 | 停止，不能以旧身份或结果自身身份代替调用者当前身份 |
| 可恢复分支 | 核验工作区及源 case 的当前记录；从生成时登记并通过哈希的字节构建全新 `verified_workspace`，只额外采样一次 |
| 致命日志、超时、未知退出/日志、输出歧义或空/不安全输出 | 不满足缺失输出恢复类别，诚实失败或保持未知 |
| 额外采样仍失败，或额外采样后身份改变 | 异常向原完成节点传播，参考测量保持 null，完成不能成功；没有第三次采样 |

门禁先核验身份和日志，再判定输出缺失。exit 0 加 fatal 仍属于致命失败。恢复不重新求解，不修改物理参数、数值设置或原验收标准，也不调用模型修复。首次失败日志、gate assessment、sample 事件与 decision 保留；第二次证据位于 `observations/sampling-recovery/attempt-2/observations/sampling`，sample 以 `retry_of` 关联首次事件，采样进程耗时和完整工作流/模型消耗保留。重复失败仍能被无效重试检查发现，一次预算不是隐藏失败的理由。

确认事实：明确丢失注入使首次门禁失败；新可信工作区的额外采样在六条最新候选运行中恢复输出并通过原验收。原因假设：当求解字节仍完整可信时，一次后处理重做可能恢复短暂输出丢失。此次证据未确认现实中某个自然缺失故障的唯一根因，也未测得自然发生率。

## 离线复现

在仓库根目录运行，只用 Python ≥3.9 标准库。归档必须完整，输出必须为不存在的新目录：

```bash
python3 scripts/replay_contract_recovery.py \
  --batch real-runs/full-model-contract-recovery-study-20261006 \
  --out demo-output/my-bounded-recovery-replay
```

只导出一对明确失败与恢复的详细报告，可重复 `--run`；全批证据仍核验：

```bash
python3 scripts/replay_contract_recovery.py \
  --batch real-runs/full-model-contract-recovery-study-20261006 \
  --run C5-r1-transient_missing-baseline \
  --run C5-r1-transient_missing-candidate \
  --out demo-output/my-bounded-recovery-pair
```

复现器不执行归档内命令、Skill、脚本或求解器。它核验冻结身份、源码/任务/语料树、注册证据字节、输入快照与原评分，将 normalized 记录导入新目录，生成五项体检、采样诊断和恢复证据索引。默认锁定本批冻结及主结果 SHA；证据缺失、哈希变化、未知或不一致身份不能产生“已核验成功”。原始归档仍是报告引用的 case 根，导入不会重新归属其证据。

查看输出目录的 `replay.md` / `replay.json`，再按运行索引进入详细报告：`imported-record.json`、`audit/report.md` / `report.json`、`sampling-diagnostic.json`、`recovery-evidence.json`。恢复索引关联首次失败、决策、额外 sample、gate assessment、输出和完成证据，以及全部已知消耗；未知费用继续 null。它不改变五项检查、原 CFD 评分或采样诊断语义。

预期全批核验为 28 次运行、3,950 个注册证据、28 份输入快照、476 次模型调用记录、6,437,556 usage token、28 次求解和 34 次采样。保留正常两组均 6/6；丢失情景基线 0/6、候选 6/6。候选首次失败仍是 fail；首次缺失输出的只读采样诊断为 `insufficient_evidence`，额外输出完整的采样为 pass。sample pass 与完整 CFD 质量、行为检查 pass 分别展示，不能相互替代。

也可单独使用既有 CLI，以归档运行目录作为 case 根：

```bash
python3 -m simclinic import \
  real-runs/full-model-contract-recovery-study-20261006/runs/C5-r1-transient_missing-candidate/record.json \
  --out demo-output/my-recovery-record.json
python3 -m simclinic audit demo-output/my-recovery-record.json \
  --case real-runs/full-model-contract-recovery-study-20261006/runs/C5-r1-transient_missing-candidate \
  --out demo-output/my-recovery-audit
python3 -m simclinic diagnose-sampling demo-output/my-recovery-record.json \
  --case real-runs/full-model-contract-recovery-study-20261006/runs/C5-r1-transient_missing-candidate \
  --out demo-output/my-recovery-sampling.json
```

`audit` 的退出 0 表示报告成功生成；应读取四态判定。只读状态可用 `python3 scripts/contract_study_status.py --batch real-runs/full-model-contract-recovery-study-20261006`。旧评估驱动的 `verify` 和独立 reviewer 会写回批次，不作为此次原归档的只读复现命令。

## 依赖与消耗分层

| 用途 | 必需环境 | 模型与真实 CFD 消耗 |
| --- | --- | --- |
| 离线导入、体检、复现、候选应用/撤销及本次针对性软件测试 | Python ≥3.9 标准库、仓库源码、完整归档；安装 CLI 才需 setuptools ≥61 | 零模型调用、零求解、零采样；读取已有字节 |
| 完整软件测试、额外系统 patch 验证 | 上述环境；完整测试还需 Git（已有上游补丁测试），独立 patch 验证还需系统 patch | 零模型调用、零求解、零采样；在临时目录操作源码 |
| 实际 CFD 开发复测 | Docker/OrbStack 已启动；固定 Foundation 10 镜像与 linux/amd64；2 CPU / 2 GiB；九文件输入、生成时来源记录；原生 U 渲染所需 PyVista/VTK 和隔离 Python 环境 | 新求解/渲染/采样有实际消耗；从 solver 入口的受控开发可零模型，planner/reviewer 可能调用模型，必须明示 |
| 完整模型受控评估 | 上述 CFD 环境，加固定 Foam-Agent commit、native content 源码、检索语料、Codex CLI/模型访问、完整依赖锁、冻结新任务/预算/停止规则和用量采集 | 昂贵；本次不运行新批次 |

实际镜像 digest、Agent commit、模型和 99 项 Python 依赖以[原 freeze.json](../real-runs/full-model-contract-recovery-study-20261006/freeze.json)为权威；原依赖与[requirements.lock](../integration/foam_agent_v210/native/requirements.lock)逐项一致。原完整模型环境为 Python 3.12.14、Foam-Agent commit `bfb7f791f77438ff90c1a39ff398739ee97bcc67`、Codex CLI `0.159.0-alpha.12.1`、`gpt-6.1-sol/high`。镜像为 `openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d`。无需为离线诊断安装这套原生运行环境。

完整模型旧批次实际约 73.9 分钟、476 次调用、6,437,556 token、28 求解/34 采样；不是新运行预算或节省承诺，费用未知。原预算为 750 万 token、开发上限 150 万、每次运行前保留 45 万余量；额外六次已登记 sample 步骤秒 min/median/max 为 2.618/2.835/2.853。该字段包括采样函数内的工作区核验、Docker 执行/清理、日志解析、证据归档和测量，不是纯采样进程 CPU 时间；新副本构建与恢复末端身份核验另包含在完整工作流时间。原冻结解释中的“进程秒”措辞保留，此处按代码说明指标边界。所有首次失败和额外消耗保留。新真实复测须新目录、重新冻结环境，C5/C6 只能作开发回归，不能再次作独立保留证据。

## 验证与后续

候选隔离核验报告与[本次软件/离线验证](../demo-output/bounded-sampling-delivery-20261006/README.md)已保存。Python 3.9.6 和 3.12.14 各 255 项完整软件测试通过；完整 28 次归档导入/体检和 15/15 诊断基准通过。严格安装器及系统 `patch` 均在临时隔离目录验证了 52 文件基准 → 53 文件目标的字节一致性和逆向恢复。保护八文件、52 个既有冻结 runtime 对应的当前文件、54 项工作前快照和四批历史冻结树/原评分均保持。

交付负例覆盖重复应用、被改动目标的撤销、篡改/路径越界拒绝。恢复负边界使用[合成进程与真实 provenance 文件的软件测试](../tests/test_sampling_recovery_boundaries.py)；最新真实证据来自既有明确注入，不把测试模拟说成新 CFD 或自然运行。此次新增模型调用、求解和采样均为零。

真实开发正负范围另见[有限恢复开发验证](reference-recovery-development.md)：正常、首次丢失及持续丢失；致命日志、超时、未知/旧身份、哈希变化和重试后的身份变化由软件负例覆盖。最新完整模型证据、预声明门槛和限制见[比较说明](contract-recovery-study.md)、[原协议](../real-runs/full-model-contract-recovery-study-20261006/PROTOCOL.md)、[原解释](../real-runs/full-model-contract-recovery-study-20261006/INTERPRETATION.md)。

下一步缺口是自然采样失败观测、未使用任务、跨物理适用性及稳定消耗收益。先另立协议再做昂贵评估；暂不扩展网页/MCP、其他 Agent、HPC/并发或自动调参。历史两轮评估器误拒仍保留原成绩，原因假设和未知用量不改写为确认事实或零消耗。
