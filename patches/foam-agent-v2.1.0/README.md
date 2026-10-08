# 可应用候选补丁：Foam-Agent v2.1.0

基准 commit：`bfb7f791f77438ff90c1a39ff398739ee97bcc67`。0001–0005 已应用到本项目的隔离候选并实际复测，完整模型正常路径、真实 reviewer 修复及失败耗尽已运行；字段选择已完成限定的 18 次保留评估。新增 0006 有软件契约与实际开发回归，独立保留批次已冻结、尚未执行。未修改或发布上游仓库；模型渲染回退和全部补丁的综合优化效果仍待验证。

## 0006-file-content-contract.patch

**问题与证据**：0.7 的 H2-r3-candidate 首次 controlDict 是 JSON 包裹文本，blockMesh exit 1。call-008 的 text 与实际失败文件逐字相同；初次生成的 parse_context 无法匹配 JSON 中尚未解码的换行，回退并直接落盘。请求、回复、源码及真实日志依据见 [接口诊断](../../docs/content-contract.md)。模型为何选用 JSON 的解释仍是原因假设。

**修改**：在 0001–0005 后应用，要求 SimClinic ≥0.8。仅初次 `_generate_one` 改用 decode_foamfile_response。接受原正文、一个完整代码块或身份精确匹配的单文件 JSON，解码一次；歧义、错误身份、额外字段/重复键、错误转义和不匹配 header object 在 save_file 前拒绝。不改提示、reviewer/rewrite、reuse、Allrun、模型、物理条件或验收。

**预期作用**：已知正确正文被 JSON 封装时保留其内层内容，避免把封装送给 OpenFOAM；格式拒绝不等于任务成功，头部检查不等于完整语法/物理验证。

**复测**：118 项软件测试在两版 Python 通过，0006 可应用/逆向恢复 observed。实际 native writer 的合法/错误回复契约测试验证拒绝前不覆盖文件。两组重放同一真实历史回复、共同禁用 reviewer：原接口重现 blockMesh exit 1；候选只解包 controlDict，首次真实求解、采样、U 图及原任务验收通过，无新模型调用。[开发回归](../../real-runs/content-contract-replay-20261004/report.md) 与 [55 个证据文件核验](../../real-runs/content-contract-replay-20261004/verification.json) 不计入独立保留评估。

新候选位于 `native/content`，旧 observed/upstream 和原冻结批次保持不变。下一轮 I1/I2/I3 [已冻结、未执行](../../real-runs/content-interface-heldout-20261004/READY.md)，不能据开发回归声称自然失败率或消耗改善。

## 0001-execution-status.patch

**问题与证据**：实际 Allrun exit 7，原始 runner 仍输出执行成功。定位到基线 [统一记录](../../real-runs/stage2-final-20261004/no-log-failure/case/.simclinic/record.json) `/events/6`（失败的 Allrun）与 `/events/7`（成功 conclusion）；原始进程观测和 workflow.log 位于同一案例目录。

**原因依据**：固定源中 run_command 未返回 process.returncode，run_allrun_and_collect_errors 只按错误日志列表判断成功。空日志导致空列表，非零退出未参与判断。此原因由源码对照、原始故障执行和修改后的同输入执行支持；推断范围仅限此路径。

**修改**：run_command 返回结构化 exit_code/timed_out；执行服务将非零退出、超时和未知状态纳入错误列表。保留原日志扫描、重试配置、求解器和验收条件。调用者原本忽略返回值仍可调用；消费该返回值的 wrapper 需要按新契约映射。

**预期作用**：阻止这一路径上的错误执行成功结论，不代表修复失败任务或所有完成判定。

**复测**：实际 exit 7 无日志、exit 3 带 End、exit 0 正常日志、exit 0 错误日志、超时、未知状态；固定容器里的正常通道、提前终止通道、无日志 shell 故障。结果见 [第三阶段记录](../../docs/stage3-results.md)。

## 0002-verified-result-interface.patch

**问题与证据**：第一阶段 [合成旧结果记录](../../fixtures/stale/record.json) 显式表现引用身份与生成身份不匹配；它不是已观察到的真实 Foam-Agent 旧结果事故。第三阶段以实际 CFD 结果文件做接口测试，刻意提供错误的 caller run/input，确认读取被拒绝，见 [门禁结果](../../demo-output/stage3/result-guard.json)。

**原因假设**：消费结果的接口若缺少调用者当前身份与生成身份的比较，可能使用旧输出；本补丁提供接口防线，尚未证明或修复上游各后处理工具的原生调用链。

**修改**：新增 `src/services/verified_results.py` 的 read_result_json，要求 caller 显式传 expected_run_id/expected_input_version；核验生成身份和内容 SHA-256，然后返回已核验的 JSON。依赖安装 simclinic ≥0.3。该模块需要开发者接入实际工具入口，单独应用文件不会自动接管原有后处理。

**预期作用**：在使用该接口的工具中拒绝旧运行、旧输入、身份缺失和内容变化，保持既有数值/参考验收要求。

**复测**：当前身份允许消费实际 JSON；错误 run、错误 input、unknown context 拒绝且 consumed=false；另测未知 producer、缺少 hash、文件变化、文件缺失、符号链接、无效 JSON。消费的是核验后的 bytes/JSON，不重新打开校验后的路径。

## 0005-observed-decisions-completion.patch

**问题与证据**：0.5 完整运行虽然通过 CFD 和 U 图验收，行为审计仍有三项证据不足；原始决定、首次失败日志、跨 run 重试关系与明确完成事件没有全部进入统一记录。见 [旧报告](../../real-runs/full-agent-final-20261004/audit/report.md)。缺失采集不能作为 Agent 错误。

**修改**：在 0001–0004 后应用，要求 SimClinic ≥0.6。增加可选真实 completion_policy 节点、实际路由观察、完整节点输出上下文、受限逐命令执行入口与定向改写白名单。配合 SessionRecorder 保存逐次输入/日志、实际网格门禁、reviewer 输出、session/retry_of 和测量先于显式完成声明的时序。默认不传 completion_policy 时保留既有图入口与结束行为；新增执行层通过 SIMCLINIC_OBSERVED_EXECUTION=1 显式启用。

**原因依据与预期作用**：原采集缺少控制事件与终止节点，不能支持完整覆盖。新增范围明确的观测及任务门禁，预期让开发者区分真实修复、失败耗尽和缺失证据。reviewer 的根因解释仍是原因假设；本补丁没有证明自然故障率下降或总体效率提高。

**复测**：100 项软件测试；真实完整正常链、注入无效求解器后实际模型定向修复/重新求解、每次重新注入错误后按上限失败，以及明确标注的缺失事件导出。全部证据/范围见 [0.6 工作流记录](../../docs/observed-workflow.md)。持续故障不是自然 Agent 事故；缺失事件导出没有新增仿真。原五项 CFD 条件不放宽，另需任务输入/所要求的 U 图片满足后才完成。

用 `scripts/run_observed_agent.py` 和 `native/observed` 复现；已有 `scripts/run_full_agent.py` 与 `native/full` 保留 0.5 快照。仅安装 wheel 或应用补丁不会自动取得固定源码、原生依赖与参考语料。

## 应用和撤销

新增 **0004-full-agent-backends.patch**：在前三项之后应用，要求 SimClinic ≥0.5 和本仓库的运行辅助程序。提供真实模型/检索适配器注入、节点边界观察和隔离容器执行接口；修正真实规划中的越界任务、Allrun 要求传递/代码块解析、所选求解器传递，以及把压力验收误当作绘图字段的问题。整份计划在生成前校验，已有算例不覆盖。使用当前仓库的 `scripts/run_full_agent.py` 运行，不能仅凭安装 wheel 获得参考语料、辅助程序与完整 Agent 源码。

证据：首轮实际规划回复含根目录 Allrun/Python 任务并被拒绝，见 [契约诊断](../../real-runs/full-agent-20261004-01/plan-diagnosis.json)；第二轮 CFD 正确但图显示 p，见 [字段验收](../../real-runs/full-agent-20261004-02/render-validation.json)。职责提示不足是原因假设；pressure 优先导致字段误选由源码与实际图配置支持。预期减少不支持的生成任务并按明确绘图指令选择字段，不承诺总体成功率提高。

复测：90 项软件测试、原生“模型调用前拒绝整份错误计划”契约测试、完整模型正常路径、实际 U 图片检查、入口变化后的新求解与旧身份拒绝。模型/语料限制与全部失败开发轮见 [完整接入说明](../../docs/full-agent-integration.md)。未修改物理参数或放宽验收。

新增 **0003-native-postprocessing.patch**：在 0001/0002 之后应用，要求 SimClinic ≥0.4。将运行身份采集、校验快照与图片归属接到实际 local runner、LangGraph 状态、路由、PyVista 节点/子进程以及已有 run/visualization 工具。延迟模型/RAG 初始化，保持默认 planner 入口，另提供 local_runner 入口。证据、预期作用、正负复测与兼容限制详见 [原生链路接入](../../docs/native-postprocessing.md)。其中运行成功与渲染成功都不等于任务 CFD 验收通过。

在目标源码根目录先核验上述 commit，再使用补丁文件的绝对路径：

```bash
git apply --check /path/to/SimClinic/patches/foam-agent-v2.1.0/0001-execution-status.patch
git apply /path/to/SimClinic/patches/foam-agent-v2.1.0/0001-execution-status.patch
git apply --check /path/to/SimClinic/patches/foam-agent-v2.1.0/0002-verified-result-interface.patch
git apply /path/to/SimClinic/patches/foam-agent-v2.1.0/0002-verified-result-interface.patch
git apply --check /path/to/SimClinic/patches/foam-agent-v2.1.0/0003-native-postprocessing.patch
git apply /path/to/SimClinic/patches/foam-agent-v2.1.0/0003-native-postprocessing.patch
git apply --check /path/to/SimClinic/patches/foam-agent-v2.1.0/0004-full-agent-backends.patch
git apply /path/to/SimClinic/patches/foam-agent-v2.1.0/0004-full-agent-backends.patch
git apply --check /path/to/SimClinic/patches/foam-agent-v2.1.0/0005-observed-decisions-completion.patch
git apply /path/to/SimClinic/patches/foam-agent-v2.1.0/0005-observed-decisions-completion.patch
git apply --check /path/to/SimClinic/patches/foam-agent-v2.1.0/0006-file-content-contract.patch
git apply /path/to/SimClinic/patches/foam-agent-v2.1.0/0006-file-content-contract.patch
```

退出状态补丁不依赖第二项。第二项需要安装 SimClinic、提供生成时捕获的运行清单，并把原有工具读取接到新接口；expected 身份必须来自调用者当前上下文，不能从待读取 artifact 自己抄取。未知生成身份不能通过“补写当前 run_id”绕过门禁。通过身份校验也不等于数值验收通过。

撤销时按 0006 → 0005 → 0004 → 0003 → 0002 → 0001 逆序 `git apply --reverse PATCH`。前五项与 observed 候选字节一致，并能逆向恢复基准，见 [离线核验](../../demo-output/observed-verification.json)；第六项单独验证 observed → content → observed 的全部源码哈希。前两项快照用 `python3 scripts/build_candidate_patches.py`；前三项用 `python3 scripts/build_native_integration.py`，0.5 完整模型候选用 `python3 scripts/build_full_agent_integration.py`，0.6 observed 候选用 `python3 scripts/build_observed_integration.py`，新正文候选用 `python3 scripts/build_content_integration.py`（已有目标/补丁时拒绝覆盖）。原始 upstream 文件保持原样。
