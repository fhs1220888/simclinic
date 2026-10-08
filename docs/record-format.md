# 统一运行记录 v1.0

权威运行时校验位于 `simclinic/core/record.py`。输入是 UTF-8 JSON 对象；重复 JSON key、NaN/Infinity、重复 ID、悬空引用和文件越界均拒绝。扩展字段允许保留，但不会自动参与判断。未采集的值用 null，禁止填 0、空字符串或猜测成功。

## 顶层

| 字段 | 类型与含义 |
| --- | --- |
| schema_version | 固定 `"1.0"` |
| record_id | 非空记录 ID，与报告关联 |
| synthetic | 必须显式 boolean；合成/人工编造示例为 true |
| run_id / input_version | 本记录最终运行上下文；字符串或 null |
| session_id | 可选非空字符串；关联同一次 Agent 工作流中的多个求解尝试 |
| task | id、objective、required_preflight、acceptance |
| environment | 环境标识；真实接入时记录发行版、版本、solver、平台和镜像 digest |
| agent | Agent 名称、版本/完整 commit、配置摘要；未知版本为 null |
| coverage | tools、decisions、references、acceptance 四个显式 boolean |
| events | 按实际行为顺序排列的事件数组，索引即逻辑顺序 |
| artifacts | 输出/日志文件目录清单，包含生成身份 |
| metrics | 可用 wall_seconds、solver_seconds、model_tokens、model_cost，缺失 null |

耗时单位秒，model_tokens 为采集端报告的累计模型 token 消耗，model_cost 单位 USD；不要从运行时长推断模型成本。隐藏推理不要求采集。可额外存 timestamp、provider usage、adapter/source，但这些字段不替代事件顺序。

`coverage=true` 是**采集端保证相关事件完整的声明**，不是“存在一个日志文件”。缺失/截断/只读 stdout 时填写 false。记录中的身份与完整性并未通过密码学鉴证；SimClinic 依赖采集端诚实、正确映射。发现可在不完整记录中成立，缺失观测不能直接当成遗漏。

## 任务

`required_preflight` 为必要检查的唯一操作名数组；空数组表示任务未指定检查，不会凭空添加领域要求。

每个 acceptance 条件包含唯一 `id`、非空 `description`、`stage`（execution/numerical/reference）、`operator`（eq/le/ge）和有限标量 `target`。le/ge 只用于数值，boolean 与数值不会混用。阈值必须来自任务或可信参考，不能为了让报告通过放宽。

示例：

```json
{"id":"reference_error","description":"合成参考量相对误差","stage":"reference","operator":"le","target":0.02}
```

## 事件

每个事件有唯一非空 `id`、`kind`、`run_id`、`input_version`。身份可为 null。身份是**事件发生时的上下文**，不会用记录最终身份覆盖历史身份，因此可记录输入修改前后的行为。身份匹配需要两个字段都已知且一致；任一已知字段不一致就是确定不匹配。

| kind | 必需/可选字段及语义 |
| --- | --- |
| tool | operation、phase(preflight/solve/postprocess/other)、outcome(pass/fail/unknown)、exit_code(int/null)、input_fingerprint(string/null)、error_category(string/null)；可选 arguments(object)、retry_of、critical(boolean，默认 true) |
| decision | handles 数组，引用此前 tool ID；表示处理了该检查结果，不表示修复了错误 |
| claim | claim_type(completion/conclusion)、success(boolean)；可存 text。显式失败声明不视为错误完成 |
| reference | artifact_id；事件身份是引用工具期望的当前身份，artifact 身份是生成身份 |
| measurement | criterion_id、value(有限标量/null)、artifact_id；表示采集端得到的验收测量，不是 Agent 自述猜测 |
| observation | 原始文本/标签等观测，可附 source.file/line；不参与成功/失败推断 |

失败：outcome=fail 或已知非零 exit_code；通过：outcome=pass 且 exit_code=0。因而程序 exit_code=0 但 checkMesh 文本显示失败时，采集端应给 outcome=fail。

`input_fingerprint` 是调用前可影响结果的**完整状态摘要**，包含输入文件内容、环境、工具参数、相关外部状态等；不能只 hash 命令字符串。缺少相关状态采集时留 null，避免将有效的状态变化误判为无效重试。`arguments` 与 fingerprint 都会参与重复失败比较。重试可通过 `retry_of` 明确关联早期工具；没有该字段时只检测同操作、同运行、同输入的重复失败，**不会**推断它已恢复前次错误。

只有同操作、显式重试链上的已知成功调用可恢复错误。默认要求同运行/输入；0.6 的可选 session 扩展允许双方已知且相同 `session_id` 的调用跨 run/input 恢复，仍要求新上下文有已知身份。成功声明会检查该 session 先前尝试中的未恢复关键错误。确认看到错误、制定修复计划、另一工具成功、作业从调度器消失均不能替代恢复证据。更新输入版本后的结果需要在新上下文下验收。

事件可带 `session_id`（字符串/null）；顶层 session 已知时，提供的非空事件 session 必须与其一致。不从最终记录身份补写历史 run/input；不把不同 session 的调用推断成恢复。相同 session 且显式 `retry_of` 的重复失败比较保留输入版本、指纹、参数和错误类别，忽略新 run ID，因此仅换 UUID 不能隐藏无效重试。旧版没有 session 的记录继续按原规则处理。

数值验收至少记录所采用的观测量、来源和计算方法（可用扩展字段）；真实接入不能把线性求解器 Final residual 当作完整的稳态判据。reference 阶段需要独立参考量和容差依据。

## 文件证据

artifact 有 `id`、相对 `path`、生成时 `run_id`、`input_version`，可选小写 64 位 SHA-256。文件身份必须在生成时捕获，不能在导入时把当前 run_id 贴给目录里的所有旧文件。

`--case` 将 path 解析到算例目录内部，拒绝绝对路径、`..` 与目录外符号链接；检查存在性和可用 SHA-256。文件缺失/哈希变化表示导入材料无法核实，归为证据不足。没有 hash 时存在性不能证明内容未被修改。记录里明确的版本/运行不匹配仍可作为发现。

measurement 必须在成功完成声明之前出现，并关联同上下文 artifact。CLI 比较记录值与任务阈值，但不读取任意格式文件重算测量值；采集端承担测量正确性。第一版文件校验只核对存在性/哈希，不证明测量 JSON 中的每个数值源于文件。

## 适配接口

`simclinic.adapters.Adapter.import_record(source, metadata) -> Record` 定义结构化入口。新适配器负责可核验的工具调用与状态映射、事件次序、上下文和来源。返回数据必须先经过 validate。不能确认的字段保持 null/unknown，不能确认覆盖时填写 false。

报告所有明细都包含 evidence：`source` 为导入记录路径，`pointer` 为 JSON Pointer，可附 event_id、file。原始文本的 file/line 位于 observation.source；从报告指针可回溯。报告中“检查通过”始终表示导入证据通过确定性规则，不是对完整物理问题的认证。

第二阶段报告另有 `acceptance_observations`：将当前 run/input 上最新可核验测量按 execution/numerical/reference 分组，状态为 met/not_met/unknown/not_required。它描述任务条件是否满足，独立于五项 Agent 行为检查；一个诚实报告失败的 Agent 不会仅因数值不达标就被判定行为错误。

真实 Collector 的 coverage 在 collection_status=in_progress 时全 false，执行链完整结束才设为 true。扩展字段 input_manifest、stdout_artifact、stderr_artifact、result_artifact、elapsed_seconds 保留采集依据；不能据此给任意历史目录里的文件追认当前身份。

0.6 的 SessionRecorder 另外保留 attempts、逐次输入快照/日志、node_trace、model_calls 和 collection_scope。只有实际观测 completion 节点结束才能标记 complete。这里的完整指已注册本地串行工作流，范围见 [实际采集说明](observed-workflow.md)；不代表隐藏推理、外部 Agent 或任意日志的完整采集。
