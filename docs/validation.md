# 第一阶段验证记录

执行日期：2026-10-04。本文件只描述软件验证，不表示运行或验证真实仿真。

- Python 3.9.6 与 3.12.14：`python -m unittest discover -s tests`，44 个针对性测试通过。覆盖五类正常/故障/缺失证据、错误恢复、上下文版本、旧结果、验收时序、文件哈希/越界、保留未知消耗、比较条件、CLI 退出码及输入覆盖保护。
- `sh scripts/demo.sh`：六份合成记录体检、实验性 Foam-Agent 日志导入、日志观测和描述比较均运行成功，输出位于 demo-output。故障样例五类 finding，证据缺失样例五类 insufficient_evidence。
- Python 3.12 / setuptools 84：在临时目录离线构建 wheel、`--no-build-isolation --no-deps` 安装；从仓库外执行安装包的 CLI help 与正常记录体检成功。没有全局安装。
- Skill：官方 quick_validate.py 已尝试，但环境缺少 PyYAML，未能执行其正式校验。使用 Ruby YAML.safe_load 校验了真实 frontmatter 的字段、名称及 description 类型/长度，人工检查了引用路径与未完成模板。未把替代检查声称为官方 validator 通过。
- 上游研究：只读 git ls-remote 核验 Foam-Agent v2.1.0 commit；读取该 commit 的 logger、local runner、error collector、reviewer 源码并记录 SHA-256。未运行上游代码，未获得真实运行归档。

以上是第一阶段结束时的验证快照。随后第二阶段已执行真实仿真和通道解析参考验收，新增采集/分层验收测试，详见 [第二阶段结果](stage2-results.md)。

## 第三阶段补充

- Python 3.9.6 与 3.12.14：67 项测试通过，新增退出状态、超时、补丁应用/逆向恢复及结果读取门禁测试。
- 固定 OpenFOAM 容器已执行修改版的三个场景；同条件配对比较及实际 JSON 读取门禁已生成，详见 [第三阶段结果](stage3-results.md)。
- SimClinic 0.3.0：在临时目录离线构建并安装 wheel，从仓库外导入安装包，验证 `guard-result` 正常读取和旧运行拒绝（退出码 1）。临时环境已清理，没有全局安装。
- 完整 LLM Agent 后处理链、多次保留算例与总体优化效果仍未验证。

## 原生后处理接入补充

- 80 项测试在 Python 3.9.6 与 3.12.14 上通过；新增生成时清单、旧时间目录清理、实际输入改变、场文件 hash、快照改变、图输出归属及三项补丁完整应用/逆向恢复测试。
- 完整源码正常导入，实际 LangGraph 从 local_runner 进入，执行固定容器 CFD 和 PyVista/VTK 渲染；已有工具函数也直接复测，未启动 MCP 服务。详见 [原生后处理记录](native-postprocessing.md)。
- 0.4.0 wheel 在临时目录离线构建/安装，仓库外成功导入 foam_runtime；没有全局安装。
- 最终运行保存的 SimClinic runtime 与 native source SHA-256 均与交付代码一致。完整模型规划/生成、模型回退与多次保留算例的优化效果仍未验证。

## 完整模型接入补充

- Python 3.9.6 与 3.12.14：90 项测试通过，新增真实 CLI 后端边界、usage/未知成本、推理事件不落盘、禁止后端工具执行、词项检索无命中、文件路径、固定命令覆盖和绘图请求选择测试。单元测试后端替身仅作软件测试，没有用来生成真实运行证据。
- 完整原生输入生成服务的独立契约测试通过：合法任务后接不支持任务时，在任何模型调用和目录创建前拒绝；原生字段选择也能识别完整任务要求中的 U。
- 四项补丁均可应用到固定完整基准包，与最终候选字节一致，并能逆向恢复基准。
- 0.5.0 wheel 在临时目录离线构建/安装，仓库外成功导入新模块并执行 CLI help；环境已清理，没有全局安装。
- 实际模型链路、首次规划失败、错误绘图字段及入口改变后的真实求解/门禁复测见 [运行记录](full-agent-integration.md)。全量自由文本决定/完成声明仍未规范化，多次保留评估及总体优化效果尚未证明。

## 0.6 决定采集与实际恢复

- Python 3.9.6 与 3.12.14：100 项针对性软件测试通过。新增 session/显式重试恢复、相同输入跨 run 的重复失败、无终止事件不提升覆盖、未知验收不生成成功、缺失实际门禁/命令元数据降级及第五项补丁逆向恢复。checkMesh exit 0 但无 Mesh OK. 的替身测试核对实际 wrapper 停止，明确只是软件契约测试。
- 三次真实 Agent 工作流及一份缺失事件导出见 [0.6 结果](observed-workflow.md)：完整正常链 4 pass/1 not_applicable；实际模型修复后 5 pass；持续注入故障后 1 finding/1 pass/3 not_applicable，完成明确失败；缺失事件导出 5 insufficient_evidence/0 finding，没有新增仿真。
- [离线归档核验](../demo-output/observed-verification.json)：四份记录全部 206 个 artifact SHA-256、三次实际源码/runtime、逐次输入快照、决定/测量时序、usage 汇总及恢复/持续失败关联一致。五项补丁顺序应用与 observed 候选字节一致，全部逆向恢复基准源码。
- [0.6.0 安装包验证](../demo-output/observed-package-verification.json)：临时目录离线构建/安装 wheel，仓库外导入新采集/执行/完成模块、CLI help、正常真实记录体检返回 0、持续故障体检的 fail-on-finding 返回 1。临时环境清理，无全局安装，无新增模型或仿真调用。
- 实际 U PNG 已检查，成功链保持原五项 CFD 条件并增加任务输入/图字段验收。模型渲染回退和保留任务批次仍未执行；不能据此声称总体优化效果。

## 0.7 冻结保留评估的软件验证

- Python 3.9.6 与 3.12.14：106 项软件测试通过，新增任务参数变更/其他输入保持、原阈值/采样保持、按任务速度计算解析参考、冻结文件增改拒绝、原始基线函数及失败/缺失进入统计分母。测试中的解析样本是明确的合成软件数据，不是保留任务仿真。
- [0.7.0 安装包验证](../demo-output/heldout-package-verification.json)：临时目录离线构建/安装，在仓库外导入新评估模块，CLI help 通过；对实际 H1 两组记录生成正确的四态体检及分层验收。基线诚实报告未完成，行为体检无 finding，执行层任务要求仍未满足；候选全部条件满足。退出码 0 表示报告生成/无行为发现，不表示基线任务成功。
- 固定共同运行协议已在两次 D0 完整模型运行中检查，独立排除在保留统计之外。随后严格按冻结的 18 次顺序执行，实际完成情况与范围见 [保留评估说明](heldout-field-study.md)。冻结快照保留创建时源码和版本，后续交付包的版本号变化没有修改该快照。
- 18 次真实保留运行已全部完成：基线/候选最终 CFD 条件均通过 9/9，完整任务通过为 0/9 和 9/9。基线实际渲染 p，候选渲染 U。候选有一次实际 controlDict 格式错误导致 blockMesh 失败，共享 reviewer 修复后重新执行；失败输入、日志及额外消耗均保留在 [尝试明细](../real-runs/heldout-field-selection-20261004/attempts.md)，不是注入故障或人为选择性重跑。
- [完整归档核验](../real-runs/heldout-field-selection-20261004/archive-verification.json) 通过：冻结源码/语料/任务未变、预声明顺序串行执行，20 份工作流记录中的 1,234 个 artifact 哈希及 21 份尝试输入快照一致；按任务参数从真实采样重算参考误差、从归档字典重算输入符合性；343 次实际模型调用共 4,613,377 usage token（含排除的两次开发检查）。核验没有新增仿真或模型调用。
- 双方完整任务均达标且证据完整的配对为 0，费用无可靠账单仍为 null。结果只支持同一通道三种参数变化上的显式 U 字段选择；零错误成功声明来自共同验收，不能算候选优势，总体优化和消耗改善仍未证明。

## 0.8 初次正文契约与冻结准备

- Python 3.9.6/3.12.14：118 项测试通过，新增正文/JSON/代码块、文件身份、重复键、错误转义、真实历史回复解包、0006 应用/撤销、新任务参数独立性、首次未执行/未知不算成功及自定义 task/repeat 配对统计。合成回复仍仅用于软件测试。
- 实际 native initial_write 的 5 项软件契约通过，错误回复不覆盖既有目标，合法回复的落盘/上下文一致；没有模型或仿真调用。
- [实际开发回归](../real-runs/content-contract-replay-20261004/report.md)：两组原生 local_runner → 完成节点执行，重放归档 controlDict 回复、共同关闭 reviewer；基线 blockMesh exit 1，候选首次 CFD/采样/U 图及原输入验收通过。没有新模型生成/规划，不属于独立保留评估。初次本地 Docker socket 权限设置失败另存，未计作 Agent 故障。
- [离线回归核验](../real-runs/content-contract-replay-20261004/verification.json)：55 个注册 artifact 哈希、源码/runtime、实际输入快照与参考误差重算一致。原 0.7 的 1,234 个证据/冻结版本核验再次通过，旧源码和批次未改。
- [0.8.0 安装包验证](../demo-output/content-package-verification.json)：临时目录离线构建/安装 wheel，仓库外导入新正文接口、解包契约与 CLI help 通过；对真实候选回归体检为 4 pass/1 not_applicable、零 finding/证据不足。临时环境已清理，无全局安装，无新模型或仿真调用。
- I1/I2/I3 新批次 [已冻结](../real-runs/content-interface-heldout-20261004/freeze.json)，全部文件哈希、唯一原生源码差异和 18 次顺序已核验。冻结准备时为 **0/18**；现已完成两组 D0 和全部 18 次，结果与核验见下节。

## 0.9 只读评估进度查询

- Python 3.9.6/3.12.14：125 项测试通过。新增冻结未启动、实时未知用量、失败/质量未知、外部目录/顺序拒绝、冻结文件修改、模型元数据暂时写入与输出保护等 7 项契约。合成状态仅用于软件测试。
- `study-status` 只读检查 common-runtime、两组源码、语料、任务、辅助文件与冻结哈希，读取协调器进度及当前模型 metadata；不执行或恢复批次，不写入批次。开发检查和保留结果分列，未结束或用量缺失保持未知，已知用量下限独立报告。
- [0.9.0 安装包验证](../demo-output/status-package-verification.json)：临时目录离线构建/安装，从仓库外导入新模块并执行 CLI help；查询正在运行的新接口批次和已结束的 0.7 批次成功，后者确认 18 次及 4,613,377 token。临时环境已清理，没有新增模型或仿真调用。
- 新接口批次已完成；最终执行状态见 [progress.json](../real-runs/content-interface-heldout-20261004/progress.json)。旧 0.8 的“尚未执行”是冻结准备阶段快照。当前工作区的 0.9 功能没有进入已冻结的运行代码。

## 0.10 采样证据诊断

- Python 3.9.6/3.12.14：132 项测试通过，新增 7 项采样证据测试，包括致命日志与 exit 0 并存、篡改/旧运行日志拒绝、缺失证据不判错及采集目录输出保护。
- [真实失败诊断](../demo-output/sampling-failure-diagnostic.json)定位采样日志第 46 行，保留原完整任务失败；[正常诊断](../demo-output/sampling-pass-diagnostic.json)通过字节证据核验。它们均为只读查询，没有新模型或仿真调用，不等于 CFD 参考验收。
- [0.10.0 安装包验证](../demo-output/sampling-package-verification.json)：临时目录离线构建/安装，从仓库外导入模块、运行 CLI help、读取两份真实归档和核验冻结批次成功；临时环境已清理。
- [使用与后续接入范围](sampling-diagnostics.md)。本功能没有进入 0.8 冻结运行，五项行为检查及主要评分未改。

## 文件正文接口的 18 次保留评估

- 全部按冻结顺序完成；两组 D0 通过且排除在统计外。基线完整任务/CFD 参考条件通过 8/9、候选 9/9；基线一次参考证据缺失保留，行为指标未知不填零。
- [归档核验](../real-runs/content-interface-heldout-20261004/archive-verification.json)：1,235 个证据文件、21 份输入快照、343 次模型调用，共 4,614,774 token，冻结/源码/顺序/参数及可用参考误差一致。缺失参考输出的运行没有重算误差，核验不代表其验收通过。
- [正文补充](../real-runs/content-interface-heldout-20261004/content-observations.md)：两份基线 JSON 原样落盘导致 sample/fvSchemes 故障；冻结前解码器离线检查可接受正文。候选 81 份回复均为原始正文，本批次没有直接观察自然 JSON 解包分支，不能直接归因成功率差异。
- [全部尝试](../real-runs/content-interface-heldout-20261004/attempts.md)保留一次失败后的恢复；[完整报告](../real-runs/content-interface-heldout-20261004/report.md)列出 8 对合格消耗比较与解释。总体优化、稳定加速或节约 token 仍未证明。

## 0.11 实际采样工具门禁

- Python 3.9.6/3.12.14：142 项测试通过。新增 10 项软件测试，覆盖正常原参考、exit 0 加 fatal、明确缺失路径、输出格式/数值/歧义/安全路径、超时、异常退出、旧上下文、快照改变、无效日志编码及采样后身份改变不发布测量。合成替身仅为软件契约测试。
- [开发回归汇总](sampling-gate.md)：8 次配对输入重新求解、渲染和采样，296 个注册证据核验；另 [默认入口](../real-runs/sampling-default-chain-20261004/report.md)实际通过原 CFD/输入/U 图要求，39 个证据及 38 个 runtime 源文件核验。合计 9 次新求解、335 个注册证据、零新模型调用；4 次缺失输出故障明确注入，所有运行均为 local_runner 开发检查，不计入新保留统计。
- 正常配对与默认入口共 3 次完整任务通过；6 次故障运行均诚实失败。新策略将 fatal/缺失输出的 sample 工具判定由旧 pass 改为 fail，并关联具体错误和停止决定；旧策略本来就没有误报完整任务成功。参考缺失仍为 null/references=false，没有放宽阈值或自动修改参数。
- 前 6 次冻结代码的消息存在重复前缀显示问题，记录保持原样；修正后另冻结 2 次完整复测，最终交付字节与该批次一致。核验仅允许这一明确格式差异。
- [8 文件候选补丁验证](../patches/simclinic-sampling-gate/manifest.json)：临时逐文件基准应用后与交付 SHA 一致，逆向恢复基准；目标为 SimClinic 注册链路，上游六项源码补丁未改。
- [0.11 离线安装验证](../demo-output/sampling-gate-package-verification.json)：仓库外构建/安装 wheel，导入新策略、确认错误前缀幂等、CLI help、查询正常/失败实际归档及已完成的 18 次冻结评估成功；临时环境清理，没有全局安装或新模型/仿真查询调用。
- [旧冻结状态核验](../demo-output/sampling-gate-study-status.json)确认仍为 18/18，原 freeze SHA 不变。新的模型 planner 路径、自动恢复与总体优化效果尚未证明。

## 采样策略公平冻结与完整模型开发检查

- 新增 6 项合成软件协议测试，全部 148 项在 Python 3.9.6/3.12.14 通过；见 [软件核验](../demo-output/sampling-study-software-verification.json)。新增评估模块可从仓库外离线安装导入并查询实际冻结状态，见 [安装核验](../demo-output/sampling-study-package-verification.json)，没有全局安装。
- [独立冻结](sampling-policy-study.md)：baseline/candidate 原生源码哈希完全相同，唯一差异为 legacy/sampling 完成策略；J1/J2/J3 的参数与 H/I/D0 不同，18 次交错顺序预声明。评分将完整任务、诊断原因准确性与消耗分列，不以失败标签改善充当成功率提升。
- [两次真实完整模型 D0](../real-runs/sampling-policy-study-20261004/DEVELOPMENT.md)均通过原 CFD/输入/U 图验收、coverage 全部 true，无失败/重试；共 34 次模型调用、455,076 usage token、2 次新求解。候选节点实际调用，正常失败分支未触发；这验证新的完整模型正常路径兼容性，不证明总体优化。
- [只读开发归档核验](../real-runs/sampling-policy-study-20261004/development-verification.json)：123 个注册证据文件、2 份输入快照、运行源码、串行顺序和 usage 一致，参考与输入条件重算通过，未新增求解或模型调用。冻结 SHA 不变，保留状态仍为 0/18；下一阶段才能按原顺序启动。

## 后端中断观测与带防睡眠保护的新评估

- 上段的 0/18 是 J 开发完成时快照。J 随后在首条保留任务模型调用中断，按协议停止于 1/18；[部分核验](../real-runs/sampling-policy-study-20261004/partial-verification.json)保存冻结与已有证据一致性，没有 CFD 通过声明。K 的 D0 又发生传输错误，与本机 Idle Sleep 重叠；[停止记录](../real-runs/sampling-policy-study-20261005/STOPPED.md)保留失败和 0/18 未执行任务。没有恢复旧批次或把失败消耗写成零。
- 后端增加固定公开错误分类及合法 usage 优先留存，只读状态增加已结束运行的部分用量下限。协调器增加固定二进制的临时防睡眠保护，随 PID 退出释放，不改持久设置。154 项软件测试在 Python 3.9.6/3.12.14 通过，[软件核验](../demo-output/backend-reliability-software-verification.json)与[离线安装核验](../demo-output/backend-reliability-package-verification.json)均保存；测试没有新增模型或求解调用。
- [新 L 协议](sampling-policy-study-awake.md)排除 H/I/J/K/D0，双方共同修复运行环境，仍只改变完成策略；新的两次完整模型 D0 全部达标，34 次调用、455,864 usage token。123 个证据、2 份输入快照及参考/输入重算通过[开发核验](../real-runs/sampling-policy-study-awake-20261005/development-verification.json)。
- [开发保护释放](../real-runs/sampling-policy-study-awake-20261005/idle-release-development-verification.json)、[保留保护生效](../real-runs/sampling-policy-study-awake-20261005/idle-assertion-heldout-verification.json)、中途生效及[保留保护释放](../real-runs/sampling-policy-study-awake-20261005/idle-release-heldout-verification.json)由系统断言列表独立确认，未改变持久电源设置。
- 保留阶段按冻结顺序完成 18/18，基线/候选完整任务与 CFD 均为 9/9，各 9 次求解尝试、153 次模型调用；没有失败/重试、错误完成声明或证据缺失。候选节点调用 9 次，失败分支 0 次，经核验的自然 fatal 事件 0，不能估计自然诊断收益。
- [归档核验](../real-runs/sampling-policy-study-awake-20261005/archive-verification.json)：含开发共 20 次运行、1,230 个证据、20 份输入快照、340 次模型调用、4,575,059 usage token；输入条件与参考误差重算通过，原冻结/顺序/源码一致。剩余 424,941 token 不追加样本；[最终状态](../demo-output/sampling-policy-study-awake-status.json)已保存。
- [9 对达标消耗补充](../real-runs/sampling-policy-study-awake-20261005/paired-consumption.json)中 token 差中位数 -368，5 对更少、4 对更多；求解秒差中位数 +0.004317、工作流秒差 -4.107829，描述差值不证明稳定收益。J/K 与就绪请求加新 L 的[已知用量下限](../real-runs/sampling-policy-study-awake-20261005/family-consumption.json)为 5,253,905，两个旧失败请求未知，准确总量/费用仍 null。详见[结论与后续范围](../real-runs/sampling-policy-study-awake-20261005/INTERPRETATION.md)。

## 版本化离线诊断基准 1.0.0

- [案例清单与规则](../benchmarks/diagnostics-v1/README.md)固定 15 个案例：6 合成、2 真实正常、1 自然采样故障、3 明确注入、2 证据删减、1 模型中断；预期明确声明，不从本次输出生成。清单 SHA 为 `28a3c7d92ab79381fa2836f997dfe52ca01ad22a2875f11b4455daea744f3046`。
- 新 `benchmark` CLI 先核验清单与来源，执行原五项检查/采样诊断，输出逐例 Markdown/JSON 与总表；有效但与预期不符返回 1，输入/完整性拒绝返回 2。来源异常单列拒绝，不转成 Agent 发现；既有输出、目录重叠和路径越界拒绝。
- [正式批量报告](../demo-output/diagnostic-benchmark-v1-final/benchmark-report.md)：15/15 匹配，75 个行为案例判定和 15 个采样案例预期匹配（6 个有采样事件、9 个不适用）。登记 481 个文件状态，其中 473 次存在哈希/8 次显式缺失核验，去重存在路径 469。原自然故障与删去采样日志的派生记录分别得到 finding 和 insufficient_evidence，不借用原日志补全派生证据。
- [166 项软件测试](../demo-output/diagnostic-benchmark-software-verification.json)在 Python 3.9.6/3.12.14 通过；新增 12 项验证匹配/不符/拒绝、预期 finding 不等于回归失败、缺失不判错、源/清单篡改、显式缺失文件出现、非法记录/标签、未登记 artifact、源变化、越界及禁止执行。
- [安装与迁移验证](../demo-output/diagnostic-benchmark-package-verification.json)：离线构建/安装 wheel，在仓库外导入实际安装模块，将 469 个所需存在文件迁移到新的来源根目录后 CLI 仍匹配 15/15；原来源字节保持，临时环境清理。
- 本次零新模型调用、零新求解；原采样补丁交付 8 个文件和 L 冻结批次仍一致。这里验证的是已知案例的离线诊断回归，不是新的自然保留评估、CFD 参考验证或总体优化证据。

## 完整模型失败路径开发验证

- [预声明协议](full-failure-development.md)冻结正常、明确致命采样、输出丢失三条路径，各一次。从新 planner 开始生成输入、reviewer、真实求解和 U 图；固定 native content、模型、检索、环境、原物理输入与验收阈值。协议 SHA `c5fab2d9709bf9dc9bbf8960d669a3fefacad87d6405ec54270f7ec97b90aea4`，150 万 token 上限；没有重放回复或旧求解结果、选择性重跑、追加样本。
- [三条实际结果](../real-runs/full-model-failure-development-20261005/report.md)均满足开发契约。正常完整任务达标；坏的独立采样字典触发真实 OpenFOAM 致命日志，stdout 第 46 行，即使 exit 0 仍返回 `sampling_fatal_log`；实际采样后移除新 profile 返回 `sampling_missing_output` 和具体路径。两条注入都显式失败、原求解/输入/U 图条件满足、参考误差保持 null；不是三个完整任务全部成功。
- 缺失输出的只读诊断仍为 insufficient_evidence，不能从归档缺失认定 Agent 做错。致命日志为 finding；三条路径的五类行为检查均没有发现 Agent 错误，诚实停止不被误判。故障类型、直接注入原因和自然原因假设分开。
- 共 51 次真实模型调用、678,989 usage token、3 次新求解，费用 null；每条 17 次调用/1 次求解。临时防睡眠保护覆盖协调器，[退出后释放核验](../real-runs/full-model-failure-development-20261005/idle-release-development-verification.json)通过，没有持久电源设置更改。
- [只读归档核验](../real-runs/full-model-failure-development-20261005/archive-verification.json)通过 186 个注册证据和 3 份输入快照；冻结/原生源码/顺序/usage 一致，原输入及正常参考偏差重算，采样决定与错误关联。[证据审阅](../real-runs/full-model-failure-development-20261005/evidence-review.json)还核对声明的注册来源、测量顺序/身份、致命行指针和被移除字节；两个可评审候选包含证据、已知注入原因、原因假设、预期作用和复测要求，未应用、收益未验证。
- [173 项软件测试](../demo-output/full-failure-development-software-verification.json)在 Python 3.9.6/3.12.14 通过，新增 7 项检查注入限定范围、恢复模块绑定、失败时保留干预意图、诚实失败契约、缺失证据不升级为 finding、错误成功/未知用量/发布未验证测量拒绝；[原离线基准回归](../demo-output/full-failure-development-benchmark/benchmark-report.md)仍匹配 15/15。生产门禁、8 文件补丁和旧 L 冻结保持不变。
- 范围是一个通道的三次开发运行，两条故障明确注入。未新增 LLM 故障解释、自动恢复、新独立保留任务或总体优化证明；自然失败率和费用改善仍未知。
