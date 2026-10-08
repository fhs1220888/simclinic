# SimClinic（仿真诊所）

[English](README.md) | **[简体中文](README.zh-CN.md)**

为仿真 Agent 诊断失败原因、提出工具与流程改进，并验证改进是否有效。

**开发版本：0.11.0。** 产品形态为 Python CLI + 仓库内 Skill。
首个集成对象是 Foam-Agent v2.1.0 与 OpenFOAM Foundation 10 / simpleFoam，
面向小型、稳态、不可压缩、单相算例。

SimClinic 自有代码采用 [MIT 许可证](LICENSE)。随附的第三方源码保留其原始许可证和署名，
详见[第三方声明](THIRD_PARTY_NOTICES.md)。

## 快速开始：无需模型、Docker 或网络

在源码仓库根目录中，使用 Python 3.9 或更高版本运行：

```bash
python3 -m simclinic demo --out demo-output/my-demo
python3 -m simclinic audit fixtures/stale/record.json --case fixtures/stale/case --out demo-output/my-stale-audit
python3 -m simclinic benchmark benchmarks/synthetic-v1/manifest.json --root . --out demo-output/my-benchmark
```

报告包含 Markdown 和 JSON、事件与文件证据，以及候选改进规则。
公开的六个示例均明确标注为**合成数据**，不会执行 CFD。
每次演示或基准检查请使用新的输出目录。

也可以在独立环境中安装 CLI：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/simclinic --help
```

诊断运行时仅使用 Python 标准库；构建安装包需要 setuptools >=61。
安装后运行 `demo` 时，应传入 `--fixtures /path/to/checkout/fixtures`；
wheel 安装包不包含这些示例。

## 检查内容

| 检查 | 使用的证据 |
| --- | --- |
| 运行前遗漏检查 | 每次求解前所需检查及其结果处理情况 |
| 错误未解决却宣称成功 | 工具结果、退出状态、明确的重试关联与完成声明 |
| 无效重复重试 | 错误类别、输入与状态指纹、调用参数 |
| 引用旧结果 | 调用方的运行与输入身份，以及结果生成方的身份和内容哈希 |
| 缺少完成证据 | 任务指定的执行、数值判据与参考验证要求 |

检查结果分为 `finding`（发现问题）、`pass`（检查通过）、
`insufficient_evidence`（证据不足）和 `not_applicable`（不适用）。
缺少日志不能直接判定 Agent 做错；未知指标保留为 null。
候选原因解释在得到确认性证据支持前，仍属于假设。

## 导入运行记录

```bash
python3 -m simclinic import fixtures/healthy/record.json --out demo-output/imported.json
python3 -m simclinic audit demo-output/imported.json --case fixtures/healthy/case --out demo-output/imported-audit
```

详见[统一记录格式](docs/record-format.md)。
已支持规范化 JSON；Foam-Agent 自由文本记录导入仍处于实验阶段。
导入记录中的命令不会被执行。

## 已验证范围与限制

维护者已完成采用冻结配置的 76 次运行研究，并进行了独立核验：
自然观测中，基线与候选均为 24/24 完整质量达标；
明确注入首次采样输出丢失后，基线为 0/12，候选为 12/12。
候选只增加一次采样调用，没有重新求解。
自然观测中的首次采样故障和恢复资格均为 0/48，
因此**自然恢复收益无法判断**。

这些结果来自固定本地工作流程的历史观测，不能证明任意 Agent 兼容性、
统计显著性、稳定节省或综合优化效果。
[验证范围说明](docs/validation-scope.md)记录了消耗、软件检查与跨版本复算情况。

## 测试与可选证据

```bash
python3 -m unittest discover -s tests -v
```

可移植测试使用项目自建样例和模拟进程；补丁应用与撤销测试需要 Git。
测试无需模型或 CFD 运行。历史归档检查缺少所需证据时会明确跳过，
通过与跳过数量分别报告。CI 在 Python 3.9 和 3.12 上运行这套仅依赖源码的测试。

本仓库不包含完整研究归档、虚拟环境和生成的输出。
完整证据的公开分发仍待完成。
[归档要求](docs/archive-access.md)说明了如何恢复原始记录，
以进行重放、15 算例研究基准检查和补丁验证。

## 候选改进集成

- [Foam-Agent 补丁](patches/foam-agent-v2.1.0/README.md)
- [采样门禁](patches/simclinic-sampling-gate/README.md)
- [显式有界恢复](patches/simclinic-bounded-sampling-recovery/README.md)
- [SimClinic Skill](skills/simclinic/SKILL.md)，可在本仓库上下文中使用

有界恢复入口需要主动启用。仅当确认采样输出缺失时，才允许额外采样一次；
恢复前重新核验当前身份，并使用新的可信工作区。
首次失败、额外消耗和第二次失败均保留记录。
该入口不修改物理模型、数值设置或验收阈值，也不重新执行求解器。

实际 Agent / CFD 运行还需要独立的原生依赖、固定容器和模型访问权限。
历史脚本与协议快照需要匹配的归档，不能据此承诺跨环境的一键 CFD 运行。

## 后续开发

详见[贡献指南](CONTRIBUTING.md)、[路线图](docs/public-roadmap.md)
和[第三方声明](THIRD_PARTY_NOTICES.md)。
网页界面、MCP 服务、任意 Agent 适配器、HPC 与并发执行，
以及自动求解器调参，尚未实现。
