# Foundation10 独立安装与已有通道复测

这一交付补齐 `0.11.0+collect1` 的真实求解环境。wheel 与上一轮相同；没有更改产品命令、物理输入、验收阈值或两个补丁。只运行已有正常固定通道：基线一次、补丁版一次，每次使用新case并采样当次结果。无模型账号，无全量仓库或历史库存要求。

## 安装：推荐 Docker 路径

先安装并启动能运行 `linux/amd64` 容器的 Docker 兼容引擎，确认 `docker info` 成功。Apple Silicon 需要引擎的 amd64 兼容支持。本次在 macOS arm64 / OrbStack 上执行 Linux amd64 容器；这不是 macOS 原生求解器验收。

解压本ZIP并进入该目录。首次构建需要联网获取镜像、Git和独立Python；需要数GB磁盘空间。已有固定 Foundation10 基础镜像只有Python3.8，且没有pip/Git，不能直接装要求Python3.9+的wheel。随附Dockerfile保留原Foundation10，添加Git及Python3.12.11虚拟环境，再从本地wheel离线安装SimClinic。

```sh
# macOS用shasum；Linux可用sha256sum -c SHA256SUMS
shasum -a 256 -c SHA256SUMS
docker build --platform linux/amd64 --progress plain -t simclinic-foundation10:collect1 .

# 单独准备新输出目录；不要挂载全量仓库
mkdir channel-output
docker run --rm --pull never --platform linux/amd64 --network none \
  --cpus 2 --memory 2g --user root \
  --mount "type=bind,src=$(pwd),dst=/kit,readonly" \
  --mount "type=bind,src=$(pwd)/channel-output,dst=/runs" \
  simclinic-foundation10:collect1 /kit/run-channel.sh /runs/channel-demo
```

`.dockerignore`把构建上下文限定为Dockerfile和wheel。运行容器只挂载交付目录（只读）及新输出目录；运行时禁止网络。Linux输出可能由root持有，需要修改时可执行 `sudo chown -R "$(id -u):$(id -g)" channel-output`。不会向宿主机安装Python或OpenFOAM。求解完成后一次性容器自动移除，镜像和输出保留。

`run-channel.sh`先source `/opt/openfoam10/etc/bashrc`，再调用已安装包的隔离模式CLI，依次完成：

1. `collect --example healthy --variant baseline`：已有800单元通道，blockMesh→checkMesh→原执行服务Allrun/simpleFoam→本次采样→体检。
2. `collect --case .../before/case --variant patched`：只复制同一组十份输入；忽略上一步网格、时间目录、日志、采样和记录；在新沙箱实际应用已有补丁并重新采集。
3. 两份新记录分别 `audit --fail-on-finding`，然后 `compare`；核验原有五项通道验收、当次工具退出、全部登记文件hash及新run身份，生成 `verification.json`。

基线和补丁版均应通过此正常通道，体检finding均为0，`comparison.comparable=true`。该对照说明正常质量保持；不从两次耗时差推断优化收益。原 `exit 7` 的问题发现与修复演示仍在 `COLLECT.md`，本次不增加故障案例。

## 查看结果与再次使用

打开 `channel-output/channel-demo/before/audit/report.md` 和 `after/audit/report.md`。`verification.json`汇总本次测量与验收；`comparison.json`保留描述性比较。原始证据在每版 `case/`，包括 `log.simpleFoam`、`postProcessing/sample/*/profile.xy` 与 `pressure.xy`、`.simclinic/call-*/stdout.log`、`workflow.log`。输入执行前快照在 `.simclinic/input-snapshot/`；补丁应用回执在 `after/patch-application.json`。

通用体检报告保留“导入记录（未独立验证仿真）”措辞，表示audit本身只检查记录及文件，不会运行求解器。本次实际求解与安装的证明来自collect采集的原始日志、工具观测、环境事实和 `verification.json`，不是靠这句报告文字认定现场运行。

再次运行须使用新输出路径，例如 `/runs/channel-demo-2`，不能覆盖旧记录。如果命令失败，保留输出并先查看 `before.log` 或 `after.log`，再看 `.simclinic/service.stderr.log` 和对应工具stderr。脚本任何一步失败都会非零退出；仅collect退出0不代表数值验收通过，最终以verification为准。

只体检已有记录不会运行CFD，例如：

```sh
docker run --rm --pull never --platform linux/amd64 --network none --user root \
  --mount "type=bind,src=$(pwd)/channel-output,dst=/runs" \
  simclinic-foundation10:collect1 -c \
  'python -I -B -m simclinic audit /runs/channel-demo/after/record.json --case /runs/channel-demo/after/case --out /runs/re-audit --fail-on-finding'
```

## 已安装 Foundation10 的 Linux 用户

官方提供Ubuntu安装及Docker安装路径，参见[Foundation10 Ubuntu说明](https://openfoam.org/download/10-ubuntu/)和[Foundation10 Linux容器说明](https://openfoam.org/download/10-linux/)。该入口要求Foundation发行版10，不接受其他发行版/版本代替。原生路线没有在本轮另行验证；推荐使用上面实际验证的容器路径。

如果本机已有Foundation10、Python3.9+、Git和Bash，可按以下步骤安装与使用。Ubuntu系统可先安装 `python3-venv` 和 `git`；不要使用固定旧镜像中的Python3.8。

```sh
bash
source /opt/openfoam10/etc/bashrc    # 其他安装路径须改为实际bashrc
test "$WM_PROJECT" = OpenFOAM && test "$WM_PROJECT_VERSION" = 10
command -v blockMesh checkMesh simpleFoam git
python3 --version                   # 确认3.9+
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps ./simclinic-0.11.0+collect1-py3-none-any.whl
.venv/bin/python -m pip check
.venv/bin/simclinic collect --example healthy --variant baseline --out ./native-before
.venv/bin/simclinic collect --case ./native-before/case --variant patched --out ./native-after
.venv/bin/simclinic compare ./native-before/record.json ./native-after/record.json --out ./native-comparison.json
```

环境变量正确还需要可用的工具PATH和动态库；仅设置 `WM_PROJECT_VERSION=10` 无法代替source。输入须严格对应已有固定通道字节，任意修改或输入符号链接会被拒绝；范围见 `COLLECT.md`。安装过程使用[uv的独立Python分发](https://docs.astral.sh/uv/guides/install-python/)；uv不是SimClinic运行时Python依赖。

## 固定环境与剩余范围

Foundation镜像固定摘要为 `sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d`，uv固定为0.7.20镜像摘要，Python固定为3.12.11，求解单线程。apt安装的Git/CA包依赖仓库当时版本；基础镜像和这些版本约束不等于重建镜像逐字节相同。实际镜像ID、系统包版本、工具hash与安装包hash记录在附带验收回执中。

首版尚不包含完整planner/reviewer模型图、任意任务或脚本、其他物理机制、其他平台的独立验证。自有代码公开开源许可仍待项目所有者确定；上游MIT说明随包。没有向包索引发布，也不在本次验证中续跑研究批次。
