# 共有观察、评分与执行冻结

已实现[设计协议 v1](../protocols/bounded-recovery-observation-v1/PROTOCOL.md)的共有资格观察器、保守评分器和新串行驱动，并建立独立的[执行快照](../execution-freezes/bounded-recovery-observation-20261006/freeze.json)。实现交付阶段只做离线软件验证与环境只读探测；随后获用户明确授权，完成四次 C0、48 次自然与 24 次注入运行及独立复核，见[最终解读](../real-runs/bounded-recovery-observation-20261006/INTERPRETATION.md)。原设计、执行冻结和全部历史评分不回写；冻结文件中的未授权、未开发及“待实现”描述是创建时状态，实际授权与开发证据保存在新批次中。

观察器在两组相同的首次 sample 调用内部工作：记录函数入口，再通过真实 `Popen` PID、返回状态或启动异常登记启动证据。旧 `execution_started` 不用作启动证明。首次门禁日志、assessment、run/input、工作区和原来源全部可信字节核验后，在候选恢复 catch **之前**登记恢复资格及证据不足原因。资格未知或不满足时，两组均诚实保留首次失败；候选仅对确认的 `sampling_missing_output`、exit 0、非致命日志和当前身份允许一次新的可信工作区采样。

原恢复实现、九文件契约、模型规划、求解和质量标准没有修改。新增类的继承顺序让共有观察先运行，再进入原一次恢复策略；默认基线没有恢复。完整记录的采样超时、致命日志、非零退出、已知启动失败属于明确不可恢复结果；日志不可读、真实启动不明或门禁证据缺失保留未知。未知或旧身份、哈希变化不能包装成成功。首次失败、原输入/输出、第二次失败和全部额外开销归档。第二次仍失败即终止该任务，边界内的无效重试发现仍单列，不把它改成成功。

明确注入模式另有干预前核验：原采样完整门禁本可通过，`profile.xy` **及** `pressure.xy` 均唯一、新生成、安全、非空且通过原测量读取器，日志和当前身份可信。物理参考误差此时不要求已经通过。登记两份原字节、哈希、argv、PID、exit 与计划后，只删一次 profile；另一输出或任何门禁条件不明时不注入，保存拒绝证据并按不可比干预停止。自然模式没有删除路径。

评分器必须接收独立的 STARTED 清单。全部已启动任务进入阶段/组别的 N，包括规划、输入、求解、采样失败、未知质量和启动后中断；未启动任务单列，绝不补成失败。M 只计首次函数调用，额外采样不增加 M；真实启动、失败、未尝试、启动未知另列。F、E、U_F、U_E、质量 S 及恢复 A、R_sample、R_full 分别报告。B 是进入恢复分支的任务，包含复制/核验失败而尚未启动额外 sampler 的开销。

**自然 E=0 时，自然恢复收益为 `not_assessable`（无法判断）**，即使注入全部恢复成功也保持这个结论。自然与注入分别报告 S/N、组间百分点差及故障/恢复指标；未知分母与未知范围明示，零分母 null，识别范围不称为置信区间。全部成本包含失败和恢复，未知总量保留 null、已知下界与未知任务数；零成功时单位成功成本 null。按质量、恢复状态及失败类别分层；双方都成功的配对仅是有选择条件的副表。

`workflow_elapsed_seconds` 是父协调器记录的 STARTED 分派至子进程归档及父核验墙钟；`agent_workflow_elapsed_seconds` 是内部 Agent/采集墙钟。`recovery_branch_elapsed_seconds` 含新复制、全部身份核验和恢复簿记，`recovery_branch_overhead_seconds` 是其减去额外门禁步骤的混合开销，不能称为纯复制耗时。sample 步骤也包含门禁内部核验和归档，不是 CPU 时间。这些嵌套量不能相加；报告另列整个批次墙钟及未归入已封存任务的残余墙钟；残余包含没有 FINAL 的中断任务，不能称为纯协调器开销，中断任务另保留墙钟已知下界。计费金额仍未知。

新驱动在分派前写入并 fsync 哈希链 STARTED，结束后绑定 FINAL 与 summary 哈希；没有 FINAL 的启动仍进入分母。每次评分核验固定顺序前缀、全部运行文件、注册证据、任务/环境、实际 native 源码、每次输入快照及 run/input 哈希，预算取已封存 FINAL，不能信任可改 progress 的用量。每阶段启动前保留 45 万 token，不借用阶段预算；模型调用前后检查已知消耗和墙钟，已发请求仍可能越过软边界。完整诚实失败继续 N，开发失败、后端失败、未知模型用量、采集缺失、身份损坏、越界、环境/保护失效或预算停止均如实记录。无 resume、选择性重跑、寻找自然故障的延长或重新求解恢复。未来真实执行才启动与协调器 PID 绑定的临时 `caffeinate`，异常出口也归档释放。

离线入口仅需 Python 3.9+ 标准库，无 Docker、模型凭据或第三方库。在仓库根执行：

```bash
python3 scripts/run_recovery_observation.py offline-check
python3 scripts/run_recovery_observation.py check-freeze \
  --freeze execution-freezes/bounded-recovery-observation-20261006
python3 -m unittest tests.test_recovery_observer tests.test_recovery_observation_score \
  tests.test_recovery_observation_driver tests.test_recovery_execution_freeze -v
```

实际环境复核需要当前 native Python、CLI 与本地 Docker，仍只调用 metadata/version/info/image inspect，不启动容器或模型：

```bash
.venv-native/bin/python scripts/freeze_recovery_observation_execution.py verify \
  --freeze execution-freezes/bounded-recovery-observation-20261006 --current-environment
```

快照包含实际 Python 3.12.14、完整依赖及 requirements.lock、Codex CLI **0.160.1** 与二进制哈希、固定 Foundation 10 镜像 digest/架构/层、Docker/宿主配置、native commit/源码、运行源码、C0-only corpus、六份任务定义、原设计和候选依据。模型配置固定 `gpt-6.1-sol/high`；快照创建时模型访问未知，后续真实 C0 调用才提供访问与完整链路证据。实际 CFD 复测需要 Docker 固定镜像、amd64 执行支持、native 渲染/测量依赖；完整模型另需 CLI 认证与模型权限。授权评估实际使用 1,292 次模型调用、17,481,440 usage token、76 次求解、88 次采样、219.3 分钟，计费金额未知；全部消耗保留在原评分，阶段软预算和 6 小时墙钟边界均满足。

快照的创建时状态 `execution_snapshot_only`、`execution_authorized=false`、`heldout_ready=false` 保持原字节。后续用户明确授权的唯一批次已完成，四项 C0 原始质量/观察/评分/消耗检查全部通过后才进入保留阶段。当前只读复核命令：

```bash
.venv-native/bin/python \
  execution-freezes/bounded-recovery-observation-20261006/common-runtime/scripts/run_recovery_observation.py \
  verify --freeze "$PWD/execution-freezes/bounded-recovery-observation-20261006" \
  --batch "$PWD/real-runs/bounded-recovery-observation-20261006"

.venv-native/bin/python scripts/review_recovery_observation.py \
  --freeze execution-freezes/bounded-recovery-observation-20261006 \
  --batch real-runs/bounded-recovery-observation-20261006 \
  --out independent-reviews/my-observation-review.json
```

独立复核输出必须为全新文件；不运行会回写原评分的 `report`。初次物理逐值复算的 Python 3.9 严格比较误拒、冻结 Python 3.12.14 通过，历史失败输出保留；当时仅定位到部分 1 ULP 例子。后续[跨版本兼容性修复](python-physical-recomputation.md)全量定位 647 个、最多 4 ULP 的派生浮点差异，当前复核器只在限定字段允许有限等价，每条原阈值判断与全部身份/哈希/原评分保持严格一致。仅依赖标准库；原冻结运行环境仍为 3.12.14。Python 3.9 的实际归档/账本/评分核验及首注入对导入体检也已通过。执行前已重扫历史并确认 C7–C10 新颖，现已进入已使用数据，不能再次用本设计证明其新颖或作为新保留任务。无 resume、选择性重跑或寻找故障的延长；新评估需新协议、执行冻结和成本范围授权。初次独立复核 13 项针对性测试在 Python 3.9.6、3.12.14 通过，见[原预算与测试记录](../demo-output/bounded-recovery-observation-execution-20261006/review-budget-verification.json)。

见[实现交付的软件与保存核验](../demo-output/recovery-observation-implementation-20261006/README.md)和[授权执行验证记录](../demo-output/bounded-recovery-observation-execution-20261006/README.md)。软件假进程、合成验收 fixture、已知归档均不称自然运行。真实自然观测首次采样缺失及恢复资格为 0/48，**自然恢复收益无法判断**；注入基线 0/12、候选 12/12 不能替代自然证据。长期实际故障率、统计显著性、稳定节省、跨物理及综合优化仍等待新证据。
