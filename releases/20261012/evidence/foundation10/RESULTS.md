# Foundation10 独立环境现场验收结果

2026-10-11完成，`verification.json`状态为 `pass`。从仓库外仅含Dockerfile和旧wheel的目录构建新镜像；运行只挂载交付资料与新输出目录，禁止网络，没有挂载SimClinic源码或历史库存。SimClinic实际加载位置为 `/opt/simclinic-venv/lib/python3.12/site-packages/simclinic/`，pip check通过。

| 项目 | 基线 | 应用已有补丁后 | 原验收标准 |
| --- | --- | --- | --- |
| Allrun实际退出码 | 0 | 0 | 0且日志有End |
| 稳态收敛 | true | true | true |
| 局部连续性误差 | 8.10074078918e-13 | 同左 | ≤1e-7 |
| 速度剖面相对L2误差 | 0.006986786967466754（约0.699%） | 同左 | ≤0.02（2%） |
| 压力梯度相对误差 | 0.003935226324550441（约0.394%） | 同左 | ≤0.03（3%） |
| 执行/数值/参考验收 | 全部met | 全部met | 沿用原五项标准 |
| 体检finding | 0 | 0 | 0 |
| 原始文件身份/hash核验 | 27项通过 | 27项通过 | 当次run/input/SHA-256 |

两个新run ID分别为 `82d58ad1a9c147c799843ff5e43046d9`、`eabfbc6d74334b7b8588f6d9fa77fd77`，输入版本相同：`sha256:803d3a01b44f7e862611fceab30da2ae82c72fa003eecc5dc60cc6ff9529e80b`。第二次从第一版case中只取十份原始输入，原结果未复用；补丁版实际git apply，源码hash与已有候选一致。`comparison.comparable=true`。

环境：Foundation OpenFOAM10 `20230119`，Ubuntu20.04，Linux amd64容器，Python3.12.11，Git2.25.1，求解单线程；宿主macOS arm64 / OrbStack。Foundation基础镜像摘要固定，派生镜像ID为 `sha256:1191b04fa4f8bb906608c86ceef257abf5d529f53b4adf8c62b90d25040786f5`。详见 `environment.json` 和 `docker-image.json`。

安装包仍为 `0.11.0+collect1`，wheel SHA-256仍为 `14f6893e07e547a7d74fa2453c3ab0292d0d7dbc011613b4b6d14ece7ff962f3`，源码包仍为 `e5cddf89bf2fd03ab27baf50ddec057e418f78e8aad9a0e864101d509d359f99`。没有改产品功能、通道输入、诊断核心或已有补丁。

实际调用计数：已有通道求解2次，采样2次，模型0次，新增案例0个。一次早期启动在source环境时因示例shell的 `set -e` 提前停止，没有建立采集目录或运行求解器；已修正运行脚本先source再启用失败退出。wheel本身无需修正。

安装包ZIP附使用说明、环境脚本与本次验收摘要；完整证据ZIP另附两份case、记录、报告、源码沙箱、原始日志/采样、镜像与环境回执。解压完整证据后打开 `before/audit/report.md` 和 `after/audit/report.md`，可用安装包重新体检或比较，无需求解。

这关闭了同一已有通道的独立安装→真实采集→体检→补丁复测缺口。正常案例的两版质量保持不代表补丁提升物理精度，不根据一次配对耗时推断稳定节省或整体优化。完整模型图、任意任务、原生/其他平台验证和自有代码公开开源许可仍不在已完成范围。
