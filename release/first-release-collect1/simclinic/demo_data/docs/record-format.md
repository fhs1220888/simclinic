# 首版统一记录格式 v1.0

包内权威校验逻辑为`simclinic/core/record.py`。最方便的完整示例是演示生成的`archives/after/healthy/.simclinic/record.json`；可复制查看结构，但它仍是历史任务，不能改身份后当作新任务证据。

## 顶层字段

| 字段 | 要求 |
| --- | --- |
| schema_version | 字符串`1.0` |
| synthetic | 明确的布尔值；合成示例为true，真实执行归档为false |
| record_id | 非空字符串 |
| run_id / input_version | 非空字符串或null；输入版本通常为生成时输入内容摘要 |
| task | id、objective；required_preflight为唯一操作名列表；acceptance为验收条件列表 |
| environment / agent | 对象，保存实际环境与Agent身份；配对比较需固定runtime_digest |
| coverage | tools、decisions、references、acceptance四个布尔值；不能证明完整时为false |
| metrics | wall_seconds、solver_seconds、model_tokens、model_cost；非负有限数或null |
| artifacts / events | 有序事件与文件证据列表 |

验收条件含`id`、`description`、`stage`（execution/numerical/reference）、`operator`（eq/le/ge）和有限标量`target`。不得为了让修改版成功放宽原条件。

## 事件与来源

每个事件有唯一`id`、`kind`、当时的`run_id`和`input_version`。kind可为：

- tool：operation、phase（preflight/solve/postprocess/other）、outcome（pass/fail/unknown）、exit_code（整数或null）、input_fingerprint和error_category（字符串或null）。可带arguments、critical和指向此前tool的retry_of。
- decision：handles指向此前实际处理的tool ID；制定计划不等于恢复成功。
- claim：claim_type为completion或conclusion，success为布尔值。工具成功与任务完成分别记录。
- reference：artifact_id以及引用时调用者的当前身份。
- measurement：criterion_id、有限标量value或null、artifact_id；需先于对应成功完成声明，来源绑定同一运行/输入。
- observation：原始文本等观测；不会据此猜测真实退出或完成状态。

artifact包含唯一id、case内相对path、生成时run_id/input_version，以及可用的SHA-256。路径不能绝对、不能含`..`。身份需由采集端生成时捕获；不要把当前身份补贴到目录里的旧结果。

工具失败为outcome=fail或已知非零退出；工具通过需outcome=pass且exit_code=0。成功重试需显式retry_of关系，不能用无关工具成功替代。无法确认的字段保留null，缺失完整采集保留false。

## 如何使用

```sh
simclinic audit /path/to/record.json --case /path/to/case --out /path/to/new-report
simclinic import /path/to/record.json --out /path/to/imported.json
```

`--case`核对登记文件是否存在和可用哈希；文件缺失/变化归为证据不足。没有case时只检查记录声明，不证明真实来源。`guard-result`再核验调用者当前身份和结果哈希，拒绝旧身份或未知上下文。

报告每项明细给出记录路径、JSON Pointer、可用事件ID与文件路径。行为通过不代表物理正确；验收观测读取记录值，不从任意日志重算物理结果。首版尚未提供通用原始日志采集器。

固定执行服务现场采集可用`simclinic collect`，细节见同目录`COLLECT.md`。该入口复用已有Collector，先保存coverage=false，观测完限定链才标complete；异常/中断为aborted且coverage=false。真实Shell控制例synthetic=false，但不是新的CFD运行或完整模型Agent；数值/参考验收不从它们推断。身份与hash只登记本次生成的证据，旧结果不复制或追认。
