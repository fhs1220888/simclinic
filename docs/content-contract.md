# 文件正文接口修复与保留评估

0.8.0 增加初次 OpenFOAM 文件生成的正文接口，候选独立保存在 `integration/foam_agent_v210/native/content`，不改 observed/full/upstream 或旧冻结批次。[0006 补丁](../patches/foam-agent-v2.1.0/0006-file-content-contract.patch) 可接在 0001–0005 之后应用/撤销。CLI 仍只依赖标准库，Python ≥3.9。

## 已确认的原因

上一轮 H2-r3-candidate 的 [call-008 请求](../real-runs/heldout-field-selection-20261004/runs/H2-r3-candidate/model-calls/call-008/request.json) 要求 controlDict 文件代码；[原始回复](../real-runs/heldout-field-selection-20261004/runs/H2-r3-candidate/model-calls/call-008/response.json) 的 `text` 却是一份 `file_name/folder_name/content` JSON。后端只校验外层 `text` schema，没有校验内层正文。

初次服务将 `generation_response` 交给原 `parse_context`，后者用 `FoamFile\s*\{` 匹配。JSON 中正文换行仍是转义字符，正则未匹配，退回整个原文本并保存。回复 text 的 SHA-256 为 `db0263ac3a725ec010f3e7adeb82f7274c494a06c9fbc20072a9f95c7c5c4ab3`，与首次失败 controlDict 字节完全相同；blockMesh 实际报 `file_name` primitiveEntry 错误。这条落盘原因由请求、回复、源码和实际日志共同支持。

生成上下文含已写文件的 FoamfilePydantic 表示；它是否引导了模型选择 JSON 仍是原因假设，不能当作已确认的模型根因。这次没有修改提示、模型或上下文语料。

## 最小修改与边界

原生 `initial_write/_generate_one` 改用 [decode_foamfile_response](../simclinic/input_content.py)，其余原生源码不变：

- 原始 FoamFile 正文保持逐字内容；允许一个完整代码块，不能混入外围解释或多个代码块。
- JSON 只解码一次，必须恰含 `file_name/folder_name/content`，目标身份须与请求一致，content 必须为文本。重复键、额外字段、错误身份、列表、嵌套/双重编码拒绝。
- 检查 FoamFile 开头和 header object 归属，拒绝空内容、NUL、引号外的字面换行转义。合法引号内的转义及注释原样保留，不使用全局 unescape 或猜测路径修正。
- 拒绝发生在 save_file 前，既有目标文件不覆盖；其他先前生成文件可能已经存在，保留作失败证据。失败不算完整任务成功。

这是响应形态/头部契约，不是完整 OpenFOAM 语法、物理或收敛验证。实际 parser、checkMesh、求解和原任务验收仍需执行。此次不接管 rewrite/reuse、Allrun 文本、其他文件生成入口或模型渲染回退；并行生成另行验证。

## 开发回归证据

[真实回归报告](../real-runs/content-contract-replay-20261004/report.md) 重放同一归档回复，通过实际 native initial_write 落盘 controlDict；其余输入及 Allrun 均来自同一次原失败快照。两组 reviewer 上限共同设为 0，以隔离首次执行；没有新增模型调用或完整 planner 生成。原 H2 已作为开发数据，因此这不算独立保留评估。

| 指标 | 原 observed 接口 | 内容候选 |
| --- | ---: | ---: |
| 实际 Allrun 退出码 | 1 | 0 |
| 实际 simpleFoam 次数 | 0 | 1 |
| 原 CFD、输入、U 图验收 | 未满足 | 满足 |
| 新模型调用 / reviewer 改写 | 0 / 0 | 0 / 0 |

候选只改变 controlDict 外层封装，正文与原回复的内层 content 逐字一致，其他输入与 Allrun 哈希不变。实际速度相对 L2 为 0.00698682521845，压力梯度相对误差为 0.0039283838016，连续性为 1.04171977148e-12，原阈值未放宽。两个真实回归的 55 个证据文件、输入、源码/runtime 哈希及采样参考重算均通过 [离线核验](../real-runs/content-contract-replay-20261004/verification.json)。

118 项测试在 Python 3.9.6/3.12.14 通过，包含真实归档回复解包、合法正文、身份/重复键/转义拒绝、0006 应用/撤销、未执行状态不等于首次成功及新任务配对统计。另用实际 native writer 验证拒绝前不覆盖目标、落盘正文与后续上下文一致；其中合成回复仅是软件测试。[0.8 安装包验证](../demo-output/content-package-verification.json) 在仓库外离线安装并审计真实候选回归，没有新增仿真或模型调用。

复现需原生依赖环境与已启动的固定 Docker 镜像；输出目录必须新建：

```bash
.venv-native/bin/python scripts/test_content_native_contract.py
.venv-native/bin/python scripts/run_content_replay.py --arm baseline --out real-runs/content-replay-new/baseline-executed
.venv-native/bin/python scripts/run_content_replay.py --arm candidate --out real-runs/content-replay-new/candidate
python3 scripts/verify_content_replay.py --directory real-runs/content-replay-new
```

开发回归只支持这个已知回复的接口修复，不证明自然失败率、总体优化或消耗降低。初次沙箱访问 Docker socket 失败的设置记录另存，尚未执行 Agent，未计为 Agent 故障。

## 独立任务冻结与执行

[`content-interface-heldout-20261004`](../real-runs/content-interface-heldout-20261004/freeze.json) 的 freeze SHA-256 为 `6ad8fc0ad2b08dabf35a6e0193054f6700b58e9224ea28ed8a48b92447c50a55`。批次已完成两组 D0 检查和全部 18 次保留任务。完整任务/CFD 参考条件通过为基线 8/9、候选 9/9；基线保留一次参考证据缺失。0.10 的只读 study-status 可以核验完成状态，不修改归档的 0.8 代码。

| 任务 | 平均入口速度 | 网格 | 每组重复 |
| --- | ---: | --- | ---: |
| I1 | 0.018 m/s | 48×24×1 | 3 |
| I2 | 0.032 m/s | 48×24×1 | 3 |
| I3 | 0.018 m/s | 72×36×1 | 3 |

两组都沿用 observed 的 U 字段选择、reviewer/重试和完成验收，唯一原生差异是初次正文解析。共同模块包含内容 helper，但基线不调用它。模型/推理配置、依赖、CLI 二进制、镜像、2 CPU/2 GiB/无网络、原 RAG、任务及解析参考、全部源码和交错串行顺序已冻结。I1/I2/I3 的验收参考不提供给模型；检索仍只有旧 D0 语料。H1/H2/H3 和已知故障重放排除在外。

保留原几何、黏度、层流、数值配置、采样坐标及质量阈值，按显式任务调整网格、入口列表和 U 初场。先执行各组一次 D0 完整真实模型协议检查，双方应满足全部原条件，不计入 18 次。然后按 schedule 执行；不能调提示、修改冻结文件或选择性重跑。

主指标为完整任务质量，次指标另列首次真实 Allrun 失败、全部失败尝试、初次 InputContentError 终止、错误成功声明、无效重试、缺失证据和全部消耗。没有 Allrun 的运行保持首次状态未知/未执行，不归为首次成功；提前格式拒绝也不算任务成功。恢复成本全部计入，仅在同任务同重复双方质量均通过且证据完整时作消耗比较。自然运行不注入或重放已知故障；若没有观察到格式故障，效果仍可能未确定。

下一次执行入口：

```bash
.venv-native/bin/python real-runs/content-interface-heldout-20261004/common-runtime/scripts/run_heldout_batch.py --batch real-runs/content-interface-heldout-20261004
```

累计 usage 运行间边界 500 万 token（包含开发检查，下一轮预留 45 万），每次最多 32 模型调用；不是逐 token 硬限制。没有可靠 model seed/后端快照，仍是同一通道的三种参数变化，只作探索性描述。冻结后发现采集/接口软件问题需保留批次并另建新批次。

## 已结束的真实保留结果

[完整报告](../real-runs/content-interface-heldout-20261004/report.md)和[正文证据](../real-runs/content-interface-heldout-20261004/content-observations.md)保留两处基线自然 JSON 落盘故障：system/sample 导致采样致命日志及参考输出缺失，最终诚实失败；system/fvSchemes 导致首次 simpleFoam 失败，共享 reviewer 恢复后通过。两者原始回复的正文均可被冻结前解码器接受且符合任务参考，但离线解包没有执行修复后的算例。

候选初次文件回复为 81/81 原始正文，没有自然触发 JSON 分支；因此本批次证明正常正文路径未出现任务验收退化，并观察到基线接口故障，不能从 8/9 对 9/9 直接证明解包提高自然成功率。已有真实 controlDict 开发回归仍单列，不充当保留样本。

[归档核验](../real-runs/content-interface-heldout-20261004/archive-verification.json)通过 1,235 个证据文件、21 份输入快照和 343 次模型调用，总量 4,614,774 token（含开发检查）。缺失参考数据的一次不重算误差、不补成功；只有 8 个双方质量与证据均达标的配对参与消耗补充。token 配对差中位数 +458.5，不能声称稳定节约消耗。

0.10 的[采样诊断](sampling-diagnostics.md)是独立的只读开发交付，没有改变冻结运行或主要评分。下一步接入明确采样错误及所需输出证据；I1/I2/I3 已使用，后续需新保留任务。总体优化效果尚未证明。
