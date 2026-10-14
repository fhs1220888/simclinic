# SimClinic 首版独立试用

公开同步范围、封版差异与当前许可状态见[PUBLICATION](PUBLICATION.md)。

版本：`0.11.0+collect1`。本交付把已有安装包、发现问题→应用补丁→复测演示、Foundation10安装说明及已验证的真实通道证据放在同一个ZIP里。无需维护者全量仓库或历史库存。当前完成度以[STATUS.md](STATUS.md)为准。

## 最短流程：三条命令

解压ZIP，进入解压目录。需要Python3.9+（带venv/pip）、Git、Bash及POSIX环境；本路径不需要Docker、OpenFOAM或模型账号。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps ./simclinic-0.11.0+collect1-py3-none-any.whl
.venv/bin/simclinic showcase --out ./demo
```

终端出现 `PASS` 后，直接打开 `demo/index.html`。同一命令已经串好四步：

| 环节 | 实际动作 | 应看到什么 |
| --- | --- | --- |
| 发现问题 | 体检随包已有记录，并定位执行失败却报告成功的问题 | `error_continuation=finding`及事件/文件证据 |
| 应用补丁 | 在新源码沙箱执行已有0001/0002的git apply，核对候选源码hash | `demo/patch-application.json`应用成功 |
| 复测 | 真实执行原exit7和exit0控制例，调用已应用的结果身份接口 | exit7仍失败，但成功报告由true变false；正常对照保持成功，旧身份被拒绝 |
| 查看质量 | 重读已有通道记录及采样证据 | 正常通道原验收满足；提前结束不能仅凭End判完成 |

`demo/summary.json`必须为 `status=pass`。输出目录必须不存在，重跑请换为 `./demo-2`。补丁只作用于新沙箱；这一步没有修改你的Agent仓库，也没有把exit7任务修复为成功。实际运行四个已有Shell控制进程，CFD部分只读取归档，不启动求解、采样或模型调用。

## 想逐步操作：三条现有采集命令

安装后，也可以只用同一个已有失败例逐步完成现场采集与复测，输出独立于上面的demo：

```sh
# 发现：捕获真实exit7和原执行结论，自动生成体检报告
.venv/bin/simclinic collect --example no-log-failure --variant baseline --out ./before
# 应用+复测：在新沙箱应用已有补丁，再执行相同控制例并体检
.venv/bin/simclinic collect --example no-log-failure --variant patched --out ./after
# 比较两份新采集记录
.venv/bin/simclinic compare ./before/record.json ./after/record.json --out ./comparison.json
```

打开 `before/audit/report.md` 和 `after/audit/report.md`，再查看 `comparison.json`。预期实际退出码两版均7，执行成功报告true→false，错误后继续结论检查finding→not_applicable，比较可比。collect退出0只表示采集完成，不能据此认定任务成功。一步步看证据和失败排查见[WALKTHROUGH.md](WALKTHROUGH.md)。

## 已完成的真实通道验收

已有Foundation10现场证据已随包放在 `evidence/foundation10/`，直接查看[结果说明](evidence/foundation10/RESULTS.md)、[基线报告](evidence/foundation10/before/audit/report.md)、[补丁版报告](evidence/foundation10/after/audit/report.md)即可。此前独立容器中的两次已有正常通道求解与采样均通过三层验收，finding均0。本次整理没有再次求解。

用户确需自己运行同一通道时，按[FOUNDATION10.md](FOUNDATION10.md)准备环境；它明确标出会启动真实CFD的命令。默认试用无需执行该步骤。

## 文档与交付物

- [WALKTHROUGH.md](WALKTHROUGH.md)：问题、补丁、复测的证据位置与常见失败。
- [STATUS.md](STATUS.md)：已完成、待补与范围边界。
- [INSTALL.md](INSTALL.md)、[COLLECT.md](COLLECT.md)：现有CLI完整安装与采集说明。
- [NOTICE.md](NOTICE.md)：归属及许可状态；上游MIT许可在 `THIRD-PARTY-LICENSES/`。
- `receipts/`：已有干净安装/源码重建/通道验收回执，以及本次试用整理验收。
- `SHA256SUMS`：所有交付文件摘要；macOS用 `shasum -a 256 -c SHA256SUMS`，Linux用 `sha256sum -c SHA256SUMS`。

wheel、源码包、两个补丁和原始证据保持原字节。演示生成的 `FIRST-RELEASE.md` 是collect1构建时的范围快照；后续已补齐的Foundation10状态请查看本交付的STATUS。没有新增功能、案例或实验。
