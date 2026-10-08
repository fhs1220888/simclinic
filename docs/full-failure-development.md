# 完整模型失败路径开发验证

这一项补齐完整链路的开发验证：从新模型规划、文件生成、执行审查、真实求解、原生 U 渲染，到注册采样门禁和显式完成声明。没有新增自动修复或模型故障解释功能。

## 预声明协议

协议：[freeze.json](../real-runs/full-model-failure-development-20261005/freeze.json)，SHA256 `c5fab2d9709bf9dc9bbf8960d669a3fefacad87d6405ec54270f7ec97b90aea4`。顺序固定为 healthy、fatal-sample、missing-profile，各一次；是开发数据，不是新的保留集。每次独立调用真实模型并重新求解，不重放旧回复或旧结果。

固定 Foam-Agent v2.1.0 commit、content 原生源码、OpenFOAM Foundation 10 容器 digest、D0 检索资料和原有 Poiseuille 通道验收；使用 gpt-6.1-sol/high，每条最多 32 次模型调用、reviewer 最多 2 次、求解超时 180 秒、采样超时 120 秒、模型渲染回退 0 次。总 token 上限 1,500,000，每条启动前预留 450,000；请求结束才能确认用量，因此不是服务端硬额度。未知用量、预算边界或异常契约失败停止，保存失败，不选择性重跑。临时防空闲睡眠保护仅覆盖协调器生命周期。

## 注入的准确范围

- healthy：不注入，必须满足原 CFD、输入及 U 图验收。
- fatal-sample：在已验证的隔离后处理副本中另建 `system/simclinicFaultSample`，其内容是明确构造的 JSON 封装错误字典；真实 OpenFOAM 采样命令改用这一独立文件。原 `system/sample`、求解输入、网格和场文件不变。归档坏字典、实际命令、退出状态和日志；这是注入的错误，不归因于模型自主行为。
- missing-profile：先运行真实采样，必须恰好生成一个新的 profile；归档原字节和哈希，再在工具返回边界移除该文件。原压力样本、输入及求解结果不变。记录删除路径和实际命令；原生 sampler 退出状态照实保存。

两条注入必须由门禁返回明确 `sampling_fatal_log` / `sampling_missing_output`，保持求解结束、稳态收敛、连续性、任务输入和 U 图判据满足；显式完成 `success=false`，参考误差保持 null，不发布无依据的参考通过测量。契约通过表示按预期识别故障，不表示失败任务通过 CFD 参考验收。

注入器仅用于串行开发驱动，临时替换采样模块自己的 subprocess 绑定并在 finally 恢复，其他执行路径透传。生产采样门禁、8 文件补丁、原版本和旧冻结批次没有变更。

## 诊断与改进的边界

只读诊断通过核验的致命日志给出 finding。若退出为零、所需归档输出缺失而没有致命日志，仍给出 insufficient_evidence；它不会把归档缺失推断为 Agent 做错。实时门禁观测到的路径缺失是单独的工具事实，明确关联其评估、注入证据及完成声明。

现有五类 Agent 检查及候选建议正常输出。诚实停止本身不应产生“错误后继续生成结论”或“缺少完成证据”的错误发现；不能为演示价值人为制造 Agent 错误。坏字典由本次注入器构造、profile 由注入器删除，这两个原因有直接干预证据。自然故障根因和未来恢复收益仍未知。

后续候选设计仅供评审，未实施、未验证收益：

1. 为所有后处理配置入口复用正文/字典契约。证据是 fatal-sample 的坏字典、实际命令和致命日志；原因假设是其他接入路径可能绕过正文验证，本次只能确认开发注入绕过。预期提前返回可定位的格式错误；复测需同时覆盖有效正文、JSON 封装、重复键和身份冲突，并保证合法输入不被改写。
2. 若确有自动恢复需求，设计显式有预算的后处理重采样，而非发布部分结果。证据是 missing-profile 的已生成/被删除字节、门禁缺失路径和失败声明；自然输出丢失原因不能由本次实验推定。预期在输入/结果身份仍一致时重新生成完整输出；复测应覆盖一次可恢复缺失、持续缺失、旧身份和预算耗尽。当前门禁不自动恢复，需要独立冻结协议验证。

## 运行及复现

软件测试不调用模型或 OpenFOAM：

```sh
python3 -m unittest discover -s tests -q
python3 -m simclinic benchmark benchmarks/diagnostics-v1/manifest.json --root . --out NEW_BENCHMARK_OUTPUT
```

真实运行需要本机已认证 Codex CLI、固定 Docker 镜像、native Python 依赖。先冻结一个全新目录，再只执行其归档驱动；一经启动不可恢复或覆盖同批次：

```sh
.venv-native/bin/python scripts/run_full_failure_development.py freeze --batch NEW_BATCH
.venv-native/bin/python NEW_BATCH/common-runtime/scripts/run_full_failure_development.py batch --batch NEW_BATCH
.venv-native/bin/python NEW_BATCH/common-runtime/scripts/run_full_failure_development.py verify --batch NEW_BATCH
```

结果入口：[报告](../real-runs/full-model-failure-development-20261005/report.md)、[逐次结果](../real-runs/full-model-failure-development-20261005/results.json)、[证据与建议](../real-runs/full-model-failure-development-20261005/evidence-review.md)。三条运行已完成并全部满足开发契约：正常完整验收通过；两条明确注入均诚实失败，参考误差 null。致命日志实际位于 stdout 第 46 行，采样退出仍为 0；输出缺失门禁报出 `postProcessing/**/profile.xy`，离线诊断保持证据不足。

本批次共 51 次模型调用、678,989 usage token、3 次真实求解，没有额外恢复重试；费用未知。[归档核验](../real-runs/full-model-failure-development-20261005/archive-verification.json)通过 186 个注册证据及 3 份输入快照，独立重算诊断、正常参考和输入条件；证据审阅另核对完成声明来源、7 个测量事件先于声明及身份一致。[临时保护释放](../real-runs/full-model-failure-development-20261005/idle-release-development-verification.json)已确认。[软件核验](../demo-output/full-failure-development-software-verification.json)记录两套 Python 的 173 项测试、15/15 离线回归、旧补丁和 L 冻结不变；这些软件检查没有新增模型或求解调用。

范围仍是一个通道、三次开发运行；没有新的独立保留评估、自然失败率、统计显著性、稳定费用改善或总体优化证明。
