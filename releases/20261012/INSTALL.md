# 安装和使用

## 安装条件

- Python3.9或更新版本，带`venv`和`pip`。
- 演示需要系统`git`、`bash`和可写的临时目录`/tmp`；仅运行`audit`等离线命令不需要Git/Bash。
- macOS或Linux等POSIX环境；本交付实际验证macOS arm64的Python3.9.6与3.12.14。wheel是纯Python包，不代表全部操作系统均已验证。
- 无Python运行时第三方依赖。Git/Bash用于在新沙箱应用现成补丁及执行故障控制例；OpenFOAM/CFD证据已随包，不需要安装求解器。

## wheel安装（推荐，离线）

后续Foundation10独立环境安装与真实通道闭环见[环境说明](FOUNDATION10.md)及[验收结果](evidence/foundation10/RESULTS.md)。已有正常通道两版各一次求解/采样已通过；下文“本轮不启动求解”保留collect1最初离线安装验收的历史范围。原wheel字节不变。

将交付ZIP解压到任意可写工作目录，进入该目录。无需原仓库。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps ./simclinic-0.11.0+collect1-py3-none-any.whl
.venv/bin/simclinic --version
.venv/bin/simclinic --help
.venv/bin/simclinic showcase --out ./demo-out
```

最后一个命令应显示`PASS`并打印`index.html`路径。浏览器直接打开该文件；3分钟讲解稿在`demo-out/SHOWCASE.md`。可将整个输出目录复制给别人展示，HTML证据链接是相对路径。

从其他目录运行可使用虚拟环境可执行文件的绝对路径。文件夹和输出路径可以包含空格；在Shell命令中用引号包住这些路径。示例：

```sh
"/path/to/release/.venv/bin/simclinic" showcase --out "./展示 输出"
```

`simclinic-demo`和`python -m simclinic showcase`提供同一入口。没有`--out`时自动在当前目录创建新的时间戳输出，不会向site-packages写入。

已有其他simclinic版本时使用新虚拟环境，避免把研究工程包与本精简包混装。此版本保留基础核心0.11.0，分发版本标记为`0.11.0+collect1`。

## 使用已有CLI

现场采集与复测见[COLLECT.md](COLLECT.md)。无需原仓库的最短闭环：

```sh
.venv/bin/simclinic collect --example no-log-failure --variant baseline --out ./before
.venv/bin/simclinic collect --example no-log-failure --variant patched --out ./after
.venv/bin/simclinic compare ./before/record.json ./after/record.json --out ./comparison.json
```

每次collect自动写出新记录、证据与audit报告。已有Shell控制例不运行模型/CFD；自己的固定通道或healthy/ended-not-converged例会执行原有CFD链，需要用户自行准备Foundation10环境，本轮不启动该分支的新求解。

以下命令以已经跑通的`demo-out`为例，可直接执行。真实用户可将记录路径和case目录替换为自己的统一JSON及原始文件目录。

```sh
# 定位历史基线的错误成功结论
.venv/bin/simclinic audit demo-out/archives/before/no-log-failure/.simclinic/record.json --case demo-out/archives/before/no-log-failure --out ./audit-before

# 体检修复后的相同故障
.venv/bin/simclinic audit demo-out/archives/after/no-log-failure/.simclinic/record.json --case demo-out/archives/after/no-log-failure --out ./audit-after

# 导入已采集的统一JSON（不推测或补写身份）
.venv/bin/simclinic import demo-out/archives/after/healthy/.simclinic/record.json --out ./imported.json

# 描述性比较两个记录
.venv/bin/simclinic compare demo-out/archives/before/no-log-failure/.simclinic/record.json demo-out/archives/after/no-log-failure/.simclinic/record.json --out ./comparison.json

# 提取正常求解器日志观测；这不是新求解或独立物理复算
.venv/bin/simclinic parse-log demo-out/archives/after/healthy/log.simpleFoam --out ./solver-observations.json
```

体检写出`report.md`与`report.json`，五项结果分为发现问题、检查通过、证据不足、不适用。正常退出并不等于任务数值或参考验收通过。候选建议不自动应用。

`guard-result`要求调用者提供当前run/input及artifact ID；不能为了过门禁从待读取的artifact抄取当前身份。用法见`simclinic guard-result --help`。本演示的四种身份调用及实际拒绝代码已保存在`demo-out/result-guard.json`。

## 记录准备

首版读取已经采集的统一JSON。不会从任意日志猜测真实退出码、输入版本、决定或完成声明，也不会执行记录中的命令。

参考演示输出中的`.simclinic/record.json`和`FIRST-RELEASE.md`了解范围。完整记录格式说明随安装包放在`simclinic/demo_data/docs/record-format.md`。记录至少需要：

- `schema_version="1.0"`、明确`synthetic`标记、record/run/input身份。
- task目标、必要前置检查和execution/numerical/reference验收条件。
- 有序tool/decision/claim/reference/measurement事件；真实退出码、重试关联和来源。
- artifacts的case内相对路径、生成时run/input和可用内容哈希。
- coverage按实际完整度填写；未知值使用null，不完整覆盖使用false。

`audit --case`用于核验文件存在性和已登记哈希；不提供时只体检记录里的声明。身份必须由采集端在生成时登记，不能给历史文件追认当前身份。`collect --case`另指已有固定通道输入，仅复制支持的输入后在新目录执行并采集，详见COLLECT；不是任意日志的通用适配器。

## 退出码和常见问题

| 情况 | 退出码/处理 |
| --- | --- |
| 演示完整通过、体检成功生成 | 0 |
| `audit --fail-on-finding`存在发现；`guard-result`拒绝 | 1 |
| 输入无效、文件缺失、补丁失败或演示失败 | 2；查看stderr和已生成的失败summary |
| collect完成捕获与报告 | 0；任务失败也可能正常完成采集，须读实际退出码和验收 |
| collect中断 | 130；检查保留的aborted记录及coverage=false |

- **Output already exists**：换一个新`--out`；演示不覆盖已有结果。
- **Missing dependency: git/bash**：先通过系统工具安装相应依赖。只用离线audit时无需这些工具。
- **No module named simclinic**：使用安装时同一个`.venv/bin/python`；不要混用系统Python。
- **pip找不到包/试图联网**：使用解压目录内wheel的具体路径，并保留`--no-index --no-deps`。
- **浏览器不能打开file协议**：用本机浏览器打开HTML，或阅读同目录的SHOWCASE.md、各体检报告和JSON；不需要启动服务。
- **记录/证据损坏或哈希不匹配**：重新取得完整安装包；不要修改证据来绕过检查。ZIP附带`SHA256SUMS`，可在macOS用`shasum -a 256 -c SHA256SUMS`，Linux用`sha256sum -c SHA256SUMS`核对。

## 源码安装和包内测试

交付也附带精简源码压缩包，可在没有全仓库的目录解压、检查或重建。构建时需要setuptools>=40.8和wheel；这些是构建依赖，不是运行依赖。离线环境需自行预装构建依赖，首选直接安装已构建wheel。

```sh
tar -xzf simclinic-0.11.0+collect1.tar.gz
cd simclinic-0.11.0+collect1
# 使用已有构建依赖的虚拟环境
python3 -m pip install --no-index --no-deps --no-build-isolation .
python3 -m unittest discover -s tests -v
```

包内测试针对已安装包：运行同一个演示、已有CLI与新增采集封装，核对实际退出捕获/补丁复测、身份拒绝、正常质量、不覆盖、输入隔离和中断记录。通道验收分支复用已有归档作合成离线回放，不运行求解器。它们不依赖维护者库存，也不代表全量研究仓库的默认测试全部通过。

完整验收记录附在交付目录的[receipts/clean-install-verification.json](receipts/clean-install-verification.json)；没有新增模型、CFD、采样、渲染或真实批量实验。
