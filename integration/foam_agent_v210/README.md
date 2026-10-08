# Foam-Agent v2.1.0 执行服务集成

已执行完整模型正常链、真实 reviewer 修复/重新求解及持续故障终止，具备限定工作流的决定和显式完成采集。使用 `native/observed` 与 `scripts/run_observed_agent.py`；结果、复现要求和覆盖边界见 [0.6 工作流说明](../../docs/observed-workflow.md)。以下各阶段说明保留历史接入细节。

后续的完整源码导入与原生图后处理接入在 `native`，见 [原生后处理说明](../../docs/native-postprocessing.md)。下面保留第二、三阶段 AST 隔离测试的方法；两种验证范围不同。

固定 commit：`bfb7f791f77438ff90c1a39ff398739ee97bcc67`。upstream 中保留原始 logger、local runner、run_local 和 utils 文件及 MIT License；execute.py 在执行前核验文件 SHA-256。

为避免导入 utils.py 时初始化模型/embedding/RAG，execute.py 通过 AST 选择真实的 remove_files、remove_file、remove_numeric_folders、run_command、check_foam_errors、run_allrun_and_collect_errors 和 local_runner_node 函数，**函数体保持原样**，在标准库命名空间中运行。原始 logger 完整执行。只对 subprocess.Popen 子类的 communicate 返回进行旁路观测，捕获实际 returncode 与耗时，没有改变上游成功/失败判断。

这属于**隔离的上游执行函数集成**，不是整个 Foam-Agent 包的原生导入，也不是完整 planner/reviewer/LLM 工作流；记录中 full_agent_workflow=false。输入案例由 SimClinic 的固定测试工具生成，不归因于 Agent 生成能力。容器断网，无模型调用，不读取本机模型凭据。

第三阶段增加 `--variant patched`：相同 AST 隔离方式读取独立的 patched 快照，核验其 manifest 的基准 commit 和源码 SHA-256，再执行候选函数体。默认 `--variant baseline` 仍使用原始 upstream 文件。修改版的退出状态判断由候选源码完成；旁路观测仍仅记录状态。两项补丁及接入限制见 [应用说明](../../patches/foam-agent-v2.1.0/README.md)。

## 实际执行

`scripts/run_real_integration.py` 使用固定 official Foundation 10 image digest，将源码只读挂载到 /workspace，将新建运行目录单独挂载到 /runs。限制两颗 CPU、2 GiB 内存，单进程求解。容器使用 linux/amd64，在本机 Apple Silicon 上通过容器运行时的兼容机制执行；耗时不能作为原生架构性能基线。

`run_case.py` 依次：生成干净案例 → 静态版本/输入检查 → blockMesh → checkMesh → 原始 local runner/Allrun → 后处理采样 → 分层验收 → 行为体检。

`Collector` 在工具调用前记录输入/状态摘要，捕获实际退出码，快照 stdout/stderr，绑定每个输出的 run ID、input version、SHA-256。初始 coverage 全 false；执行链完整结束才标记 true，原子替换 record.json。工具与声明仅覆盖这个脚本测试流程，不意味着完整 LLM 轨迹已采集。

每个实际目录包含 `.simclinic/record.json`、原始 workflow.log/review.log、上游实际进程状态、输入 manifest、采样数据、验收值与报告。solver_seconds 指上游 Allrun 子进程的墙钟时间，包含其 shell/OpenFOAM 环境初始化；不是裸求解器 CPU 时间。模型消耗仍保持 null，单独注明 model_calls=0；它不能用于评估完整 Agent 的模型成本。

## 三个案例

- healthy：800 个单元的层流通道，原始执行函数调用真实 simpleFoam；速度剖面与运动学压力梯度比较解析参考。
- ended-not-converged：明确标注的故障注入，只允许一次 SIMPLE 迭代，原验收条件保持不变。该修改用于测试“程序结束不等于质量达标”，不是优化建议或自动调参。
- no-log-failure：明确标注的 shell 故障注入，Allrun 立即 exit 7，不运行求解器。用来测原始执行服务是否读取真实退出状态。

故障注入是实际执行的测试场景，不是人工编造日志；不把故障注入结果描述为自主 Agent 随机犯错。第一次和第二次调试归档另有目录，最终可复现结果见 [第二阶段结果](../../docs/stage2-results.md)。
