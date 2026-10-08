# 离线诊断基准 1.0.0

这一基准把已有归档整理为固定诊断回归案例。它不执行归档中的命令、不调用模型或求解器，也不把旧运行重新计算为保留评估。清单版本与软件版本分别记录。

[清单](manifest.json)及[清单 SHA](manifest.sha256)固定案例来源、原记录标签、文件状态和预期判定。当前 SHA 为 `28a3c7d92ab79381fa2836f997dfe52ca01ad22a2875f11b4455daea744f3046`。预期判定根据案例契约和原始证据明确编写，运行入口不会用当前检查器结果生成或更新预期。标签依据在每项 `basis` 与 `provenance` 中，可独立复核；哈希核验本身不证明标签或 CFD 正确性。

## 案例来源

| 类型 | 数量 | 覆盖 |
| --- | ---: | --- |
| 合成 `synthetic` | 6 | 正常、五类行为故障、未知证据、旧结果、成功重试、不适用 |
| 真实正常 `real_normal` | 2 | 完整模型正常链路及冻结 L 候选归档 |
| 真实自然故障 `real_failure` | 1 | 采样 exit 0，但已核验日志第 46 行出现 fatal；原任务诚实失败 |
| 明确注入 `injected_fault` | 3 | 恢复成功、重复失败耗尽、采样 profile 缺失 |
| 证据删减 `evidence_ablation` | 2 | 已有缺失事件导出，以及本次明确移除采样日志的派生归档 |
| 中断运行 `incomplete_run` | 1 | J 首条模型中断，未执行求解；行为证据不足 |

15 个案例分别固定五项行为检查的四态结果，以及采样汇总、事件状态、原因类别、exit 0 与 fatal 是否并存、致命日志行号。没有采样事件的案例单列不适用；不把它算作发生了正确的采样故障诊断。

所有 `fixtures` 是合成数据。真实故障、故障注入和证据删减不是同一来源类型。`data/sampling-logs-ablated` 从原 I1-r2-baseline 复制注册证据，只显式省略采样 stdout/stderr，保留事件与退出状态；[派生说明](data/sampling-logs-ablated/derivation.json)登记原记录和省略的 artifact ID。原真实归档未修改，派生归档没有新增仿真或模型请求。

自然 fatal 归档中，采样诊断为 finding；日志删减后为 insufficient_evidence。原 fatal 日志虽然作为清单的来源依据保留，但不会被拿来替代派生案例缺失的采样日志。缺失 profile 案例同样保持离线采样证据不足，同时原工具包装层的明确失败仍在记录中；两种判断不混淆。

## 批量运行

在 SimClinic 来源根目录执行，输出目录必须尚不存在：

```bash
python3 -m simclinic benchmark benchmarks/diagnostics-v1/manifest.json --root . --out demo-output/my-diagnostic-benchmark
```

仓库外安装 CLI 后，显式提供清单和原始来源根目录：

```bash
simclinic benchmark /path/to/SimClinic/benchmarks/diagnostics-v1/manifest.json --root /path/to/SimClinic --out /path/to/new-benchmark-output
```

CLI 安装包不内置本清单和历史归档。运行时需要清单列出的来源文件；不需要 Docker、原生 Agent 依赖、模型服务或网络。来源可以迁移到新根目录，只要保持清单相对路径、字节内容和已声明的缺失状态；案例目录也须保留。缺失原始来源时会拒绝评分，不自动下载或重新运行。

默认先核对同名 `.sha256` 文件；也可用 `--manifest-sha256` 显式指定已审核的清单 SHA。清单文件有重复 JSON 键、无效预期、重复案例 ID、路径越界或不完整的五项判定时，运行在写报告前终止。registered artifact 必须在清单中登记，不能漏掉后继续评分。

文件哈希为字符串时表示固定的存在字节；为 null 时表示明确的缺失状态。未声明的缺失、文件内容变化、声明缺失的文件出现、来源标签不一致或非法记录，属于基准输入拒绝，不算 Agent 行为发现。有效的其他案例继续体检，拒绝案例保留在总表中。

输出位于源案例与清单目录之外；已有输出不覆盖。体检前后检查文件状态，观测过程中源文件变化会被拒绝。归档只读，原五项检查、采样诊断及 CFD 验收规则保持。

## 输出与退出码

- `benchmark-summary.json`：案例结果、预期/实际差异、来源分组、逐项四态匹配、采样匹配和实际检查器源码 SHA。
- `benchmark-report.md`：整体结果及每个案例的索引。
- `cases/<ID>/result.json`：预期、实际、来源、差异或输入拒绝说明。
- 有效案例另有 `audit/report.md`、`audit/report.json` 和 `sampling.json`，保留原证据关联与候选建议；建议没有自动应用。

| 退出码 | 含义 |
| --- | --- |
| 0 | 所有案例与固定预期匹配；预期 finding 也能正确通过回归 |
| 1 | 输入有效，但至少一个判定不符合预期；不会自动修改标签 |
| 2 | 清单/路径/输出无效，或至少一个来源案例被拒绝 |

案例 `matched` 不等于任务成功。`insufficient_evidence`、`not_applicable` 与 `finding` 都可能是正确的固定预期；真实旧任务的失败不会因回归匹配而升级为成功。

## 当前验收

[正式批量报告](../../demo-output/diagnostic-benchmark-v1-final/benchmark-report.md)中 15/15 匹配；五项检查共 75 个案例判定全部匹配，采样案例预期 15/15 匹配（6 项有采样事件、9 项不适用）。清单登记 481 个文件状态，473 次存在字节及 8 次明确缺失状态核验；去重存在路径为 469，重复来源不当作独立证据。

[软件验收](../../demo-output/diagnostic-benchmark-software-verification.json)记录 166 项测试在 Python 3.9.6/3.12.14 通过，新增 12 项覆盖篡改、显式缺失、预期不符、标签冲突、未登记 artifact、输出保护、路径/符号链接越界、无执行及源字节保持。

[离线安装与迁移验收](../../demo-output/diagnostic-benchmark-package-verification.json)确认仓库外导入安装模块，仅复制清单需要的存在文件并迁移根目录后仍匹配 15/15，原来源字节保持，临时安装环境已清理。

这些是已知案例的离线回归证据，不证明自然诊断准确率、新的 CFD 正确性、独立保留成功率或总体优化。
