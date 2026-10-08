# 入口数值与九文件输入契约

入口核对采用显式启用的 `foundation10-nine-file-couette-literals-v3`，接入已有原生完成节点。范围限定于已声明的 Foundation 10 / simpleFoam Couette 九文件，不是通用 OpenFOAM 字典解释器。随后[新的完整模型比较](contract-recovery-study.md)已完成；本页仍单列评估器开发证据。

## 核对依据

入口第 i 个面的目标值使用精确有理数计算：`wall_speed*(2*i+1)/(2*ny)`。参考作者原先按 12 位有效数字写出，因此现在的允许误差是**目标精确值在 12 位有效数字下半个末位单位**，含边界。例如目标约为 0.0148 m/s 时，界限为 5e-14 m/s；目标约为 0.000478 m/s 时为 5e-16 m/s。区间根据任务值计算，不依赖实际输入值的数量级，也不根据质量成绩扩大。模板精度及更准确的表示可以通过；改变入口超出区间必须拒绝。每个面保存字面值、公式值、误差和界限。

Uy、Uz、壁速、初始 U/p、其他边界、黏度、量纲、网格、数值控制和采样位置均精确核对。十进制数与科学计数法转成精确有理数，不再全局取整。整数标签保持整数语法。字典按有限结构解析，检查重复键和列表形状；注释不影响核对，带引号的采样库名可读取，头部 class/object/format/version 继续核对，仅允许正确的可选 location。实际入口面序仍由真实生成的网格验证。

constant nu 可省略已知黏度量纲，或使用正确显式 `[0 2 -1 0 0 0 0]`；值仍须精确为 0.001。依据为 Foundation 10 的 [常黏度读取器](https://cpp.openfoam.org/v10/constantViscosityModel_8C_source.html)和[带量纲值读取器](https://cpp.openfoam.org/v10/dimensionedType_8C_source.html)。这些规则没有改变物理任务或 CFD 阈值。

文件缺失、符号链接、重复键、未知输入文件、动态指令及不支持表达保持证据不足；范围外文件为不适用。不执行导入字典中的指令。确定的字面量变化为发现问题，合法等值表示为通过。原始字节哈希仍定义输入版本和结果归属，等值表示不能借用旧运行身份。

## 九文件正负矩阵

每个文件有等值格式正例、明确修改负例，及缺失、指令、重复头部三种证据不足检查。

| 文件 | 等值正例 | 真实负例 |
| --- | --- | --- |
| `0/U` | 模板/40 位计算精度入口、零分量等值表示 | 改变一个入口 Ux，超出写出区间 |
| `0/p` | 零值科学计数法 | 改变初始 p，幅度 1e-14 |
| `constant/physicalProperties` | 正确显式量纲及 1e-3 | 改变 nu，幅度 1e-14；另有错误量纲 |
| `constant/momentumTransport` | 注释与正确 location | laminar 改为 RAS |
| `system/blockMeshDict` | 尺度的科学计数法 | nx 从 46 改为 48 |
| `system/controlDict` | deltaT 等值科学计数法 | writePrecision 从 12 改为 10 |
| `system/fvSchemes` | 注释与正确 location | steadyState 改为 Euler |
| `system/fvSolution` | 线性求解器容差等值表示 | 改变一个容差，幅度 1e-14 |
| `system/sample` | 坐标等值科学计数法、原引号库名 | nPoints 从 20 改为 21 |

另有软件检查覆盖壁速、横向速度分量、量纲、列表长度、边界类型、极小坐标/残差变化、非法整数标签、非有限值、符号链接和任务策略未显式启用。上轮 24 份 C3/C4 输入仅作为已知开发回归；结果通过新读取器不能回写旧完整任务成绩。

## 实际开发执行及问题保留

首次冻结批次 [nine-file-input-development-20261006](../real-runs/nine-file-input-development-20261006/report.md)在第二项正例停止：我把 `+0.00` 当作合法的等值格式，但真实求解器将前导加号读成标点而失败；离线解析器当时没有识别这个格式边界。原记录、失败、源码与冻结均保留，未续跑、覆盖或改成成功。Foundation 10 [ISstream 数值词元读取源码](https://cpp.openfoam.org/v10/ISstream_8C_source.html)同样将前导加号作为标点；负号和指数中的符号另行处理。

随后收紧新读取器，前导加号给出证据不足，并去掉错误正例中的加号。重新冻结的 [v3 开发批次](../real-runs/nine-file-input-development-v3-20261006/report.md)包含 C3/C4 各两种正确表示及十项负例。所有顺序、预期、输入字节、运行代码、镜像和资源限制在执行前保存；禁止模型、reviewer 修复与渲染模型回退。入口从原生 local_runner 开始，经过真实求解、U 渲染、采样和完成声明，不是完整模型规划链。

v3 的 **14/14 项开发预期全部满足**：四项正确表示通过完整质量验收，九个文件各一项明确修改及错误黏度量纲共十项均被输入核对拒绝、完成声明为失败。改变数值的负例即使产生符合参考的流场，也没有完成原任务；改变网格或采样点导致参考测量不可建立时保持 null；RAS 设置和错误黏度量纲均真实读取失败。没有为负例制造参考测量或成功声明。

v3 完成 14 次真实求解尝试、11 次采样，零模型调用；**1,357 个注册证据和 14 份输入快照核验通过**。两套 Python（3.9.6、3.12.14）均通过 222 项软件测试；九文件离线矩阵 **45/45** 符合预期，另有诊断基准 **15/15** 匹配。首次停止批次的两次求解尝试、一次采样、131 个注册证据单独核验，仍保持停止与失败状态。合计 16 次新求解尝试、12 次采样、1,488 个证据，全部零模型调用。临时防睡眠已释放。

三个历史批次的冻结、运行代码、原结果以及八文件采样门禁交付均保持；上轮 24 份入口记录在新规则下通过只代表已知开发回归，没有重新评分。v3 冻结 SHA 为 `b55d6fdc0738b3d7bbad0bafff4df4193910fc2a54b1df9f5d8440914857d0a9`。

证据：[真实逐项结果](../real-runs/nine-file-input-development-v3-20261006/results.json)、[实际证据核验](../real-runs/nine-file-input-development-v3-20261006/archive-verification.json)、[离线矩阵](../real-runs/nine-file-input-development-v3-20261006/offline-input-matrix/results.json)、[软件验证](../real-runs/nine-file-input-development-v3-20261006/software-verification.json)、[完整开发复核](../real-runs/nine-file-input-development-v3-20261006/development-review.json)、[首次停止前缀核验](../real-runs/nine-file-input-development-20261006/stopped-prefix-verification.json)、[保护释放](../real-runs/nine-file-input-development-v3-20261006/idle-assertion-release.json)、[诊断基准](../demo-output/nine-file-input-benchmark/benchmark-report.md)。

## 使用与下一步

软件测试和已知归档基准不需要 Docker 或模型：

```bash
python3 -m unittest discover -s tests -p test_input_contract.py -v
python3 -m simclinic benchmark benchmarks/diagnostics-v1/manifest.json --root . --out demo-output/my-input-regression
```

有现成固定镜像和 `.venv-native` 时，可在**新目录**重新冻结开发矩阵，然后使用新目录内的冻结驱动运行：

```bash
.venv-native/bin/python scripts/validate_input_contract.py freeze --batch real-runs/my-input-development
.venv-native/bin/python real-runs/my-input-development/common-runtime/scripts/validate_input_contract.py batch --batch real-runs/my-input-development
.venv-native/bin/python real-runs/my-input-development/common-runtime/scripts/validate_input_contract.py verify --batch real-runs/my-input-development
```

复测属于已知开发数据，不会成为独立保留证据。完成节点使用 `contract_task(spec)`，以及 `ContractReferenceCompletion` 或 `RecoveringContractReferenceCompletion`；任务必须声明策略 ID 和 12 位入口精度，否则拒绝启用。旧策略的评估驱动保留原行为，不能直接复用为新策略的独立完整评估。

后续的[公平完整模型比较](contract-recovery-study.md)已按新协议完成：排除 C3/C4 等已知开发任务，四次开发检查均达标后，执行 C5/C6 的 24 次保留运行。正常两组均 6/6，丢失注入基线 0/6、候选 6/6，预声明有限恢复收益门槛通过；历史评分未改。共有输入核对修复不计为候选差异，本页开发验证也不单独证明优化效果。下一步整理有限恢复补丁及复现交付，总体优化仍未证明。
