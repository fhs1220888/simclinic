# SimClinic 最小首版安装包

公开同步范围、封版差异与当前许可状态见[PUBLICATION](../../releases/20261012/PUBLICATION.md)。

本包可以在没有SimClinic全量仓库或历史库存的目录中安装，完成固定Agent执行服务的现场采集、体检、复测及完整演示。版本`0.11.0+collect1`，运行时Python第三方依赖为0；复用已有Collector、两个补丁、固定源码及精选证据。

需要Python3.9+、Git、Bash及POSIX环境（macOS/Linux）。本次实际验证环境是macOS arm64、Python3.9.6和3.12.14；未据此声明其他系统已验证。Windows原生环境未验证，可在有Git/Bash的WSL环境另行验证。

## 快速安装与运行

在解压后的交付目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps ./simclinic-0.11.0+collect1-py3-none-any.whl
.venv/bin/simclinic --version
.venv/bin/simclinic showcase --out ./demo-out
```

打开`demo-out/index.html`查看展示报告；`summary.json`中`status=pass`表示演示完整通过。输出目录必须是新目录。无需pip联网、Docker、OpenFOAM、模型账号或API key。

同一演示也可通过`.venv/bin/simclinic-demo --out ./another-demo`运行；`python -m simclinic showcase`与控制台命令等价。省略`--out`会在当前工作目录创建新的时间戳目录，不写安装目录。

完整的环境要求、命令、退出码、故障排查与源码安装见[INSTALL.md](INSTALL.md)。

## 现场采集→体检→复测

复用已有Shell控制例，无需求解器或模型：

```sh
.venv/bin/simclinic collect --example no-log-failure --variant baseline --out ./before
.venv/bin/simclinic collect --example no-log-failure --variant patched --out ./after
.venv/bin/simclinic compare ./before/record.json ./after/record.json --out ./comparison.json
```

两次collect均捕获新的实际进程状态并生成 `audit/report.md`。应看到实际退出均为7，执行结论从错误成功变为诚实失败，error_continuation从finding变为not_applicable；故障任务仍未解决。patched在新沙箱实际应用两个已有补丁。完整用法、自己的固定通道输入与覆盖范围见[COLLECT.md](COLLECT.md)。

## 已有能力与演示边界

1. 实际执行小型`exit 7`故障脚本，展示原工具错误报告成功。
2. 在新沙箱实际应用已有0001/0002补丁，再执行相同脚本，正确报告失败；`exit 0`对照仍成功。
3. 通过已应用接口读取真实CFD归档结果，当前身份允许，旧运行/输入/未知身份拒绝。
4. 重新体检六份精选历史记录，展示正常验收保持和“End不等于质量达标”。

本次现场只有Shell及结果接口调用。CFD部分是已有归档重读，无新增求解、采样、渲染或模型调用。有限恢复的自然收益、稳定节省和整体优化仍未证明。

CLI为`collect`、`showcase`、`audit`、`import`（统一JSON）、`compare`、`parse-log`、`guard-result`。采集限定现有固定Foam-Agent执行服务与通道/Shell案例，明确full_agent_workflow=false。完整仓库的研究批次、预算/库存工程、模型后端、实验性纯文本适配、网页/MCP和自动调参未打包。

## 随包内容与后续缺口

wheel内有诊断核心、已有采集/通道验收模块、采集封装、演示入口、两个补丁、所需上游源码片段与MIT许可、六份原记录及其74个注册证据。不含全量工作目录、原生库或模型配置。

固定执行服务的采集/体检/复测入口已提供；本轮用已有Shell控制例现场验证，用已有CFD归档回归，未启动新模型或CFD实验。真实通道环境、其他平台及更广适用性不推定为已验证，自有代码公开开源许可待决定。归属与许可状态见[NOTICE.md](NOTICE.md)。
