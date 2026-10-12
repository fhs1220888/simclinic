# 固定 Agent 现场采集、体检与复测

`0.11.0+collect1`新增 `simclinic collect`，复用既有 `Collector`、固定Foam-Agent执行函数/原始logger和通道验收逻辑。每次创建新run/input身份，捕获实际退出状态、stdout/stderr、原执行结论，登记当次证据及SHA-256，再生成体检报告。采集范围为固定执行服务段，记录明确 `full_agent_workflow=false`；tools/decisions等coverage只指所声明的采集范围。

## 无原仓库的现场闭环

在交付ZIP解压目录建立新环境并离线安装，然后使用同一已有 `exit 7` 控制例：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps ./simclinic-0.11.0+collect1-py3-none-any.whl

# 现场采集基线并生成体检：错误成功结论会被发现
.venv/bin/simclinic collect --example no-log-failure --variant baseline --out ./before

# 在新源码沙箱实际应用两个已有补丁，重新采集同一个控制例
.venv/bin/simclinic collect --example no-log-failure --variant patched --out ./after

# 用新采集的两份记录复测；不是重读旧record当作现场运行
.venv/bin/simclinic compare ./before/record.json ./after/record.json --out ./comparison.json

# 已有exit 0正常控制例
.venv/bin/simclinic collect --example execution-ok --out ./normal
```

这四步不需要OpenFOAM、模型账号、Docker或联网，只有三个已有Shell控制例的实际执行。需要Python3.9+、Git、Bash和可写 `/tmp`。输出可包含中文或空格；必须是不存在的新目录。

| 结果 | 基线exit7 | 补丁后exit7 | 正常exit0 |
| --- | --- | --- | --- |
| 实际Allrun退出码 | 7 | 7 | 0 |
| 原Agent执行结论 | 错误报告成功 | 正确报告失败 | 成功 |
| error_continuation体检 | finding | not_applicable | pass |
| execution验收 | not_met | not_met | met |
| 任务数值/参考完成 | Shell例没有配置此类验收 | 同左 | 同左 |

补丁修正了执行结论，没有修复 `exit 7` 的故障任务。Shell例只采集原执行服务的conclusion，不伪造完整Agent的completion节点或CFD测量。`collection-summary.json`的 `status=complete`表示本次限定采集流程完成，不等于任务通过；`compare.comparable=true`也不是整体优化或稳定节省结论。

## 每次输出

```text
before/
  record.json                  直接给audit/compare使用的统一记录
  case/.simclinic/record.json   同一记录在证据根内的副本
  case/.simclinic/call-*/       Collector捕获的实际stdout/stderr
  case/.simclinic/input-snapshot/ 执行前的原始输入字节与已登记hash
  case/.simclinic/upstream.json 既有执行旁路观测的实际Allrun退出状态
  case/workflow.log            固定local_runner_node原始执行结论
  case/.simclinic/acceptance.json
  audit/report.md、report.json  本次记录的体检和事件/文件定位
  collection-summary.json      采集状态、退出码、覆盖与分层验收
  source-manifest.json          固定commit、实际执行源码hash
  runtime/                     当次独立源码副本
```

patched输出另外有 `patch-application.json`、`patches/`、`patch-sandbox/`，保留实际git apply及hash与既有候选一致的回执。原始包内源码、用户输入和旧结果保持只读。

可独立重新体检并以发现控制退出码：

```sh
.venv/bin/simclinic audit ./before/record.json --case ./before/case --out ./before-audit-again --fail-on-finding
.venv/bin/simclinic audit ./after/record.json --case ./after/case --out ./after-audit-again --fail-on-finding
```

两者预期退出1和0。`collect`本身退出0仅表示采集与报告生成完成，负向案例也可正常退出；输入/依赖/采集错误退出2，中断退出130。

## 已有固定通道的现场入口

此分支会实际执行blockMesh、checkMesh、固定Agent的Allrun/simpleFoam及已有采样命令。需要独立用户已经安装并source **Foundation OpenFOAM10**，使 `WM_PROJECT=OpenFOAM`、`WM_PROJECT_VERSION=10`、`WM_PROJECT_DIR`、PATH及库环境可用；系统需Git/Bash。wheel不含求解器和安装环境，本轮没有启动这类新CFD运行。

已有通道输入可以直接使用：

```sh
.venv/bin/simclinic collect --case "/path/to/fixed-channel-case" --variant baseline --out ./channel-before
.venv/bin/simclinic collect --case "/path/to/fixed-channel-case" --variant patched --out ./channel-after
.venv/bin/simclinic compare ./channel-before/record.json ./channel-after/record.json --out ./channel-comparison.json
```

不需要原SimClinic仓库或历史库存；输入必须对应既有固定通道。读取九个物理输入文件和Allrun，复制到私有新case；不复制旧网格、时间目录、postProcessing、日志或 `.simclinic`，也不为旧文件登记新身份。固定服务仍有清理行为，但只发生在这个新case。

物理文件使用既有 `create_channel` 输出的相同字节；仅允许已有正常控制Dict和“只迭代一次”的负例版本。Allrun仅允许原simpleFoam脚本及已有exit7脚本。不同输入或符号链接拒绝，不对其他任务套用通道的解析参考与阈值。已配置的三层验收沿用原标准：退出/End、稳态标志/连续性、速度剖面/运动学压力梯度。采样来自本次结果，无法取得的量保持null。

同一个入口提供 `--example healthy` 与 `--example ended-not-converged` 生成既有输入；选择它们也会真实运行OpenFOAM，不能把它们当成离线展示命令。本轮交付验收使用已有归档对这条分层验收路径做明确标为synthetic的离线回放回归，并未声称在新的物理环境完成现场求解验证。

## 完整度与失败处理

采集开始即原子保存统一记录，coverage全false。当前范围的执行观测、源结论、结果引用与验收写完后才标完整。执行失败可以是完整的负向记录；采集自身异常或中断则保留 `collection_status=aborted` 和coverage=false，不把未看到的状态补成成功。检查输出中的 `collection-summary.json` 与原stderr。

真实输入在执行前快照并取摘要，输出在观测到生成时登记run/input/hash。后续同输入复测获得新run ID但相同input_version，记录不覆盖。原Allrun超时120秒；被原函数终止且实际状态已观测的超时可形成完整失败记录。原生前置/采样工具的Collector超时180秒。没有完整退出观测或采集中断时保留部分记录；封装在异常/中断时清理原执行进程组，不自动续跑或重试模型。

固定执行链仍使用原函数体，未导入模型/RAG初始化。完整planner/reviewer、隐藏推理、外部Agent、任意Allrun脚本、HPC和其他物理机制不属于此采集范围。已有完整图的SessionRecorder研究工程未混入精简分发；本次也没有增加模型后端、网页或自动调参。
