# 模型后端失败分类与部分用量

采样策略首次 J 批次在 J1-r1-baseline 的第 11 次模型调用 exit 1 后停止，尚未进入求解；原失败原因和该请求用量没有留存，无法补写。[停止说明](../real-runs/sampling-policy-study-20261004/STOPPED.md)保留该失败、未执行任务和部分核验。旧冻结代码及进度不改，不直接恢复这个批次。

工作区后端现在从 CLI 的 error/turn.failed 事件及 stderr 归约固定类别：限流、认证、模型不可用、请求、服务、传输或未知。仅保存固定类别、白名单 HTTP 状态和事件类型，不保存原始诊断文本或隐藏推理。类别是公开错误信号的确定性归约，不是未经证据确认的根因。格式依据 [OpenAI 官方非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)：JSONL 流包含 error 和 turn.failed。模型、推理设置、提示词和后端禁用工具参数保持，没有自动重试或模型替换。

若 CLI 即使失败仍显式提供合法 usage，先保存该 usage，再处理退出状态；没有 usage 继续 null。`study-status` 对已结束但总量未知的运行读取已完成调用的合法非负数，另报 `finished_partial_known_model_tokens`；不改变协调器累积值，总量与余量继续未知，也不会恢复已停止批次。

实际旧批次含 D0 的已知下限为 582,912 token，其中失败的保留运行已知部分 127,836。另一次独立短就绪检查成功，消耗 10,985 token、零仿真；它不重放旧任务，也不推断旧失败原因。至新批次启动前，以上两项合计已知下限 593,897 token，未知部分仍保留。

新增 4 项针对性软件测试验证固定分类、诊断文本不落盘、失败仍保留显式 usage、无 usage 不猜零及已结束部分下限。152 项测试在 Python 3.9.6/3.12.14 通过。两组共享修复的新 K 协议另行冻结；J 批次始终是停止的失败观察，不能与新批次合并成一次成功评估。总体优化效果尚未证明。

K 的 D0 第 8 次调用仍发生传输错误，7 次成功调用的已知用量为 84,949 token，失败调用未知；[电源核验](../real-runs/sampling-policy-study-20261005/power-evidence.json)显示该请求开始时间与本机 Idle Sleep 重叠。K 未启动保留阶段，原失败独立保留。包括就绪检查，J/K 的已知用量下限合计为 678,846 token，两个失败请求用量未知；不能给出确切总消耗或费用。

新 [L 协议](sampling-policy-study-awake.md)为两组共同增加协调器生命周期内的临时防睡眠断言，固定二进制与命令纳入冻结。系统断言列表已确认开发/保留保护生效和各自结束后释放；不改持久电源设置，不修改模型、提示词或传输配置。两次新 D0 与 18 次独立保留运行全部通过，本批次 340 次模型调用没有新增中断。154 项软件测试在上述两套 Python 通过，新增两项保护契约测试；[软件核验记录](../demo-output/backend-reliability-software-verification.json)和[离线安装核验](../demo-output/backend-reliability-package-verification.json)分别保存。本批次跑通不证明所有网络错误根因消失或总体优化有效，J 原失败原因仍未知。

[最终跨批次用量核对](../real-runs/sampling-policy-study-awake-20261005/family-consumption.json)包含 J、K、独立就绪请求和 L 的已知下限 5,253,905 token；两次旧失败调用仍无 usage，准确总量与费用保持 null。不同冻结的开发/保留结果不合并成一次成功评估。
