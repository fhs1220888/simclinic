# SimClinic 体检报告

记录：`collected-ece9350d609f4fbbabc8f1a19caf80f5`

数据性质：导入记录（未独立验证仿真）

文件检查：已对提供的算例目录检查引用文件及可用哈希。

## 运行前遗漏检查：检查通过

- **检查通过**：validate_case 已通过；记录了处理结果。
  - 证据：`/runs/channel-demo/after/record.json#/events/6`
  - 证据：`/runs/channel-demo/after/record.json#/task/required_preflight`
  - 证据：`/runs/channel-demo/after/record.json#/events/0`
  - 证据：`/runs/channel-demo/after/record.json#/events/1`
  - 证据：`/runs/channel-demo/after/record.json#/coverage/decisions`
- **检查通过**：checkMesh 已通过；记录了处理结果。
  - 证据：`/runs/channel-demo/after/record.json#/events/6`
  - 证据：`/runs/channel-demo/after/record.json#/task/required_preflight`
  - 证据：`/runs/channel-demo/after/record.json#/events/3`
  - 证据：`/runs/channel-demo/after/record.json#/events/4`
  - 证据：`/runs/channel-demo/after/record.json#/coverage/decisions`

## 错误后继续生成结论：检查通过

- **检查通过**：成功结论前未观察到工具错误。
  - 证据：`/runs/channel-demo/after/record.json#/events/7`
  - 证据：`/runs/channel-demo/after/record.json#/coverage/tools`
- **检查通过**：成功结论前未观察到工具错误。
  - 证据：`/runs/channel-demo/after/record.json#/events/19`
  - 证据：`/runs/channel-demo/after/record.json#/coverage/tools`

## 无效重复重试：不适用

- **不适用**：未观察到失败后的重复调用。
  - 证据：`/runs/channel-demo/after/record.json#/events`
  - 证据：`/runs/channel-demo/after/record.json#/coverage/tools`

## 引用旧结果：检查通过

- **检查通过**：引用结果与引用时运行 ID、输入版本一致。
  - 证据：`/runs/channel-demo/after/record.json#/events/9`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/21`；文件 `log.simpleFoam`
- **检查通过**：引用结果与引用时运行 ID、输入版本一致。
  - 证据：`/runs/channel-demo/after/record.json#/events/11`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/21`；文件 `log.simpleFoam`
- **检查通过**：引用结果与引用时运行 ID、输入版本一致。
  - 证据：`/runs/channel-demo/after/record.json#/events/13`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/21`；文件 `log.simpleFoam`
- **检查通过**：引用结果与引用时运行 ID、输入版本一致。
  - 证据：`/runs/channel-demo/after/record.json#/events/15`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/24`；文件 `postProcessing/sample/103/profile.xy`
- **检查通过**：引用结果与引用时运行 ID、输入版本一致。
  - 证据：`/runs/channel-demo/after/record.json#/events/17`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/25`；文件 `postProcessing/sample/103/pressure.xy`

## 缺少完成证据：检查通过

- **检查通过**：execution_end (execution): 记录中的验收值满足条件。
  - 证据：`/runs/channel-demo/after/record.json#/events/19`
  - 证据：`/runs/channel-demo/after/record.json#/task/acceptance/0`
  - 证据：`/runs/channel-demo/after/record.json#/events/10`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/21`；文件 `log.simpleFoam`
- **检查通过**：steady_converged (numerical): 记录中的验收值满足条件。
  - 证据：`/runs/channel-demo/after/record.json#/events/19`
  - 证据：`/runs/channel-demo/after/record.json#/task/acceptance/1`
  - 证据：`/runs/channel-demo/after/record.json#/events/12`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/21`；文件 `log.simpleFoam`
- **检查通过**：local_continuity (numerical): 记录中的验收值满足条件。
  - 证据：`/runs/channel-demo/after/record.json#/events/19`
  - 证据：`/runs/channel-demo/after/record.json#/task/acceptance/2`
  - 证据：`/runs/channel-demo/after/record.json#/events/14`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/21`；文件 `log.simpleFoam`
- **检查通过**：profile_error (reference): 记录中的验收值满足条件。
  - 证据：`/runs/channel-demo/after/record.json#/events/19`
  - 证据：`/runs/channel-demo/after/record.json#/task/acceptance/3`
  - 证据：`/runs/channel-demo/after/record.json#/events/16`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/24`；文件 `postProcessing/sample/103/profile.xy`
- **检查通过**：pressure_gradient_error (reference): 记录中的验收值满足条件。
  - 证据：`/runs/channel-demo/after/record.json#/events/19`
  - 证据：`/runs/channel-demo/after/record.json#/task/acceptance/4`
  - 证据：`/runs/channel-demo/after/record.json#/events/18`
  - 证据：`/runs/channel-demo/after/record.json#/artifacts/25`；文件 `postProcessing/sample/103/pressure.xy`

## 分层验收观测

仅对最新可归属测量进行验收比较；与 Agent 完成声明审计分开。

| 层级 | 结果 |
| --- | --- |
| execution | 满足条件 |
| numerical | 满足条件 |
| reference | 满足条件 |

- `execution_end`：满足条件；值 `True`，要求 `eq True`。
- `steady_converged`：满足条件；值 `True`，要求 `eq True`。
- `local_continuity`：满足条件；值 `8.10074078918e-13`，要求 `le 1e-07`。
- `profile_error`：满足条件；值 `0.006986786967466754`，要求 `le 0.02`。
- `pressure_gradient_error`：满足条件；值 `0.003935226324550441`，要求 `le 0.03`。

## 限制

- 仅审计已导入证据；记录中的身份和完整性声明需由采集端保证。
- 行为检查通过不等于验证仿真正确性或优化效果；应分别核验领域验收与复测。
- LLM 原因解释只能作为假设。
