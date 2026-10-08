# 有限采样恢复候选交付

本包交付已验证恢复策略的显式启用入口、精确源码清单和可逆安装器。核心恢复实现沿用最新冻结批次的原字节。本次打包与核验只做离线工作，新增模型调用、求解和采样均为零。

## 精确基准和候选差异

基准是 [最新完整模型批次](../../real-runs/full-model-contract-recovery-study-20261006/PROTOCOL.md) 的 `common-runtime` 全部 **52 个文件**，由 [manifest.json](manifest.json) 的 `base_files` 完整列出。冻结 SHA-256 为 `5fe1c82fe06adde350c10362dbe7af080cff010327a0752b33803858217bd3f8`，不是推测的公开 release/tag。SimClinic 当前版本 0.11.0 只是仓库元数据，精确可应用基准由上述归档哈希确定。

该基准已经包含 `sampling_recovery.py` 和 `RecoveringContractReferenceCompletion`。实际比较的两组共用所有源码、九文件 v3 评估器、物理与数值设置、验收阈值；[冻结驱动](../../real-runs/full-model-contract-recovery-study-20261006/common-runtime/scripts/run_contract_recovery_study.py) 仅按组别选择基线或恢复 completion class。

本包的 [candidate.patch](candidate.patch) 只新增 `simclinic/domains/openfoam/bounded_sampling_candidate.py`；目标 **53 个文件**由 `target_files` 完整列出。新增入口 `completion_policy(..., enabled=False)` 默认选基线，显式 `enabled=True` 选取已验证恢复类。入口是本次新增的交付适配层，离线选择测试已经核验；其新字节没有被冒充为既有完整模型运行的源码。已验证机制仍是原恢复类。对应 [payload](payload/simclinic/domains/openfoam/bounded_sampling_candidate.py) 与[当前仓库入口](../../simclinic/domains/openfoam/bounded_sampling_candidate.py) 字节一致。

旧 [采样门禁清单](../simclinic-sampling-gate/manifest.json) 的八个受保护文件及版本元数据保持原值。本包不应用到上游 Foam-Agent git 树，不改任何冻结目录或历史成绩。

## 安装、启用和撤销

以下命令从仓库根目录执行，仅复制源码和核验哈希，使用 Python 标准库。`prepare` 要求目标不存在；选一个未使用的临时目录。安装器拒绝本仓库冻结 `real-runs` 树，并在解析后的目标及所有父目录检查 `freeze.json` / `freeze.sha256` 任一标记；迁移 `--repository` 后的批次和其他带标记归档同样只读，`verify --output` 也不能写入它们。已有/被改动的入口、额外源文件和符号链接会被拒绝；出现本地修改时先报告差异，不覆盖它们。Python 导入生成的 `__pycache__` 不计入源码清单。

```bash
python3 patches/simclinic-bounded-sampling-recovery/delivery.py verify
python3 patches/simclinic-bounded-sampling-recovery/delivery.py prepare --root /private/tmp/simclinic-bounded-candidate
python3 patches/simclinic-bounded-sampling-recovery/delivery.py check --root /private/tmp/simclinic-bounded-candidate --state base
python3 patches/simclinic-bounded-sampling-recovery/delivery.py apply --root /private/tmp/simclinic-bounded-candidate
python3 patches/simclinic-bounded-sampling-recovery/delivery.py check --root /private/tmp/simclinic-bounded-candidate --state target
```

`apply` 是 `candidate.patch` 的严格字节安装等价产物，不依赖 `git` 或系统 `patch`，每次操作先核验 `manifest.json` 中全部基准/目标及 payload。对于已经按清单准备的基准，也可人工 `patch -p1 < candidate.patch`，再运行 `check --state target` 核验；推荐使用安装器的防覆盖检查。

开发者在已有固定 Foam-Agent 集成中，保持原任务和环境，用下面的 completion node 选择方式接入。这里的 `main.create_foam_agent_graph` 来自已固定原生 Agent 包；`reference`、`image`、`spec`、`environment`、`agent` 沿用同一运行环境的原值，`output` 必须是新运行目录。task 由 `contract_task(spec)` 生成并向 recorder 注册。每个 run 创建新实例，一次重试预算存在实例上，不跨 run 重用。

```python
from simclinic.domains.openfoam.bounded_sampling_candidate import completion_policy
from simclinic.domains.openfoam.input_contract import contract_task
from simclinic.session import SessionRecorder
from main import create_foam_agent_graph

recorder = SessionRecorder(output, contract_task(spec), environment, agent)
policy = completion_policy(recorder, reference, image, spec, enabled=True)
graph = create_foam_agent_graph(observer=recorder.observe, completion_policy=policy).compile()
# graph.invoke(...) 属实际运行，按既有环境、采集与预算要求单独执行。
```

当前仓库也已提供同一入口，可直接导入。隔离源码树使用时，进程须从隔离树启动或明确将其放在 import 路径首位；不要加载当前仓库同名包来假装验证隔离版本。上述准备的源码树没有技能、语料、原生 Agent 包、模型环境或 CFD 镜像，不能直接当作新评估批次运行。尤其不能运行归档 coordinator 的 `freeze/run/start` 来重用 C5/C6。

运行层撤销：下一次运行选择 `enabled=False`，恢复同一九文件 v3 基线类。撤销已安装入口：

```bash
python3 patches/simclinic-bounded-sampling-recovery/delivery.py reverse --root /private/tmp/simclinic-bounded-candidate
python3 patches/simclinic-bounded-sampling-recovery/delivery.py check --root /private/tmp/simclinic-bounded-candidate --state base
```

`reverse` 先核验全部目标字节，只移除新增入口，不删除运行证据。如果任一目标文件已修改，撤销会拒绝；需由开发者先处理明确的本地差异。`verify` 自行创建临时隔离树、应用、逐文件检查、逆向还原，退出时清理，详见 [isolation-verification.json](isolation-verification.json)。可用 `--output /private/tmp/new-verification.json` 保存新报告；已有报告和归档路径不能覆盖。

## 触发条件、证据和诚实失败

实际触发路径是 [sampling_gate.py](../../simclinic/domains/openfoam/sampling_gate.py) 发出 `SamplingError('sampling_missing_output')`，不是根据 stderr 文本猜测可恢复。门禁已核验工作区当前 run/input 身份、没有旧 `postProcessing`、采样进程 exit 0、无致命日志、超时和未知执行状态，随后重新核验工作区；只有必需 `profile.xy` 或 `pressure.xy` 确认不存在才进入这一类型。

[sampling_recovery.py](../../simclinic/domains/openfoam/sampling_recovery.py) 再要求最近采样事件为 `exit_code=0/outcome=fail` 且 `source_artifact` 匹配首次失败 assessment，重新核验 workspace 和 source case 的当前 run/input。成功进入恢复时将预算设为 1，保存恢复 decision 和首次失败引用；额外采样从新 `verified_workspace` 的已验证求解字节建立，不重新求解。输入、物理参数、数值设置和原验收全部保持。重试后再次核验当前身份，最终只有原验收全部满足才能声明完成。

首次失败日志与门禁 assessment 保留在 `observations/sampling/`。重试的日志、assessment 和新输出使用 `observations/sampling-recovery/attempt-2/observations/sampling/`，并注册 artifact/hash、`retry_of`、`output_archive_root`、实际 sample 步骤耗时；全部消耗继续计入记录。`elapsed_seconds` 包含门禁内部身份核验、Docker 执行/清理、日志解析、归档和测量，不等于纯进程耗时；建立新副本及恢复 mixin 的末次身份核验另计入完整工作流时间。归档路径变化不是有效输入/参数变化，持续失败仍可被无效重试检查发现。

致命日志、非零 exit、超时、未知/旧身份、已存在输出、空/歧义/越界/无效输出均不属于缺失类型。验证或重试失败直接上抛，completion 节点诚实失败，参考测量仍为 null，不发布部分测量。第二次仍失败时不再重试；`finally` 恢复 recorder，首次失败不会被改写成功。

## 已有证据、假设与复测边界

确认事实：最新批次四项 C0 完整模型开发检查通过后按冻结顺序执行 C5/C6 的 24 次保留比较。[原评分](../../real-runs/full-model-contract-recovery-study-20261006/results.json)、[证据核验](../../real-runs/full-model-contract-recovery-study-20261006/archive-verification.json)、[独立解释](../../real-runs/full-model-contract-recovery-study-20261006/INTERPRETATION.md) 和[完整性记录](../../real-runs/full-model-contract-recovery-study-20261006/historical-integrity.json) 哈希均列入本包 manifest。

| 情景 | 基线完整质量 | 候选完整质量 | 证据类别 |
| --- | --- | --- | --- |
| 正常 | 6/6 | 6/6 | 新完整模型运行，无故障注入 |
| 首次新采样输出丢失 | 0/6 | 6/6 | 明确故障注入，两个任务各三次 |
| 每次输出均丢失 | 旧开发检查 0/2 | 旧开发检查 0/2 | [开发归档](../../real-runs/sampling-recovery-development-20261005/archive-review.json)，候选各两次采样后诚实失败 |

原因假设：当求解字节及身份仍可信，丢失发生在采样输出阶段，重新建立干净工作区并有限重采样可能恢复后处理。明确删除当次新输出的注入证实这一条件下的恢复作用；没有确认自然丢失的根因或自然故障收益。

正向复测边界是正常一次采样，以及明确首次输出丢失时最多两次采样、同一 run/input、相同原验收通过。负向边界包括持续丢失、fatal、timeout、未知或改变身份、伪造 assessment、旧/空/歧义/无效输出，以及第二次失败；这些应保持失败/未知、不得完成。现有 [恢复软件测试](../../tests/test_sampling_recovery.py) 使用合成进程和 provenance stub；新增[负边界测试](../../tests/test_sampling_recovery_boundaries.py) 使用真实 provenance 文件及替代 Docker 的合成进程；[交付测试](../../tests/test_bounded_sampling_delivery.py) 使用离线真实源码字节。不能将软件测试计为新自然运行或新保留证据。

本批只支持固定 Foundation 10/simpleFoam、稳态、不可压缩、单相 Couette 的明确缺失条件收益。九文件 v3 是两组共有的评估器修复，不计候选 Agent 差异。自然故障收益、统计显著性、稳定消耗节省、跨物理效果与整个 Agent 综合优化均未证明。C5/C6 现在只能作为开发数据，新的独立保留证据必须使用另行冻结、此前未使用任务。

## 分层依赖和消耗

| 工作 | 依赖 | 是否执行新物理/模型工作 |
| --- | --- | --- |
| 安装、撤销、哈希核验、离线归档导入/诊断 | Python 3.9+ 标准库及本仓库和完整归档；无需 Docker、OpenFOAM、渲染或模型凭据 | 否 |
| 本交付针对性软件测试 | 同上，Python 标准库 unittest；无需 Git | 否 |
| 完整软件测试套件 | Python 标准库及 Git（已有上游补丁测试调用 Git） | 否 |
| 实际 CFD 正负复测 | Docker、固定 Foundation 10 镜像/架构、原任务和 solver/采样环境；若复测完整渲染/验收链则还需固定原生 Agent 环境、PyVista/VTK 等依赖 | 有真实求解/采样；纯工具链验证可不调用模型 |
| 完整模型评估 | 上述实际 CFD 环境，加固定 Foam-Agent 源码、模型 backend/认证、Codex CLI、LangGraph、检索语料、冻结预算与序列 | 有模型及 CFD 消耗，须另设协议 |

精确真实依赖列表、CLI SHA、Agent commit、镜像 ID 与资源约束取自 [freeze.json](../../real-runs/full-model-contract-recovery-study-20261006/freeze.json)，不由本包安装或自动拉取。最新完整评估实际消耗 476 次模型调用、6,437,556 usage token、28 次求解、34 次采样、3,950 个注册证据、28 份输入快照，约 73.9 分钟；费用未知。这是已执行批次账本，不是下一批固定预算或节省结论。

针对交付与已验证恢复边界的离线验证命令只需要标准库，不依赖 Git；完整套件的上游补丁回归另需 Git：

```bash
python3 -m unittest tests.test_bounded_sampling_delivery tests.test_sampling_recovery tests.test_sampling_recovery_boundaries tests.test_input_contract tests.test_contract_recovery_study
```

将整个最新批次的归档导入独立输出、完成体检并定位首次失败与恢复 evidence：

```bash
python3 scripts/replay_contract_recovery.py --batch real-runs/full-model-contract-recovery-study-20261006 --out demo-output/my-bounded-recovery-replay
```

可增加 `--run C5-r1-transient_missing-baseline --run C5-r1-transient_missing-candidate` 选择一对已知归档用于开发核验。具体输出和身份/路径核验见[交付与离线复现指南](../../docs/bounded-sampling-delivery.md)。最新评分只读入口另见[评估说明](../../docs/contract-recovery-study.md)及[仓库 README](../../README.md)。本包只复现应用、撤销与来源哈希；完整运行证据的逐项离线核验由上述仓库复现入口完成。
