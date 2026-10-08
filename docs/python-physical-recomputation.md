# Python 3.9 / 3.12 物理复算兼容性

本次仅离线读取已有 `bounded-recovery-observation-20261006` 的 76 次真实运行归档。没有新增模型请求、求解、采样或渲染，没有修改冻结读取器、原评分、输入契约、物理参数或验收阈值。当前独立复核器使用冻结读取器计算当前解释器的真实结果，按下述规则比较，不替换归档数值。

## 差异定位

Python 3.9.6 与冻结 Python 3.12.14 对 2,083 份相同来源文件进行了逐值比较：76 份入口面序、76 份九文件核对、76 份求解日志指标、76 份全场稳态指标、64 份成功采样指标和 24 份注入前原采样指标，共 392 组。全部结构、非浮点值、输入判定和原质量判断一致。

共有 647 个派生浮点值不同：入口面心 294 个、入口 Ux 258 个、采样 profile 指标 38 个、pressure 指标 57 个。1/2/3/4 ULP 差异分别为 554/79/12/2 个，最大绝对差为 `1.3877787807814457e-17`；物理误差指标最大绝对差为 `5.048709793414476e-29`。全场稳态、日志和精确有理数输入核对没有差异。Python 3.12.14 与原归档逐值差异为零。

例如 `C9-r3-natural-candidate` 的 profile_error：Python 3.9.6 为 `9.554979988398007e-15`，归档/3.12.14 为 `9.554979988398e-15`，相差 4 ULP。前一次只定位到部分 1 ULP 例子；本次完整清单明确覆盖 2–4 ULP，历史失败报告保留。

冻结参考函数使用内置 `sum` 计算面心、L2 及压强斜率。[Python 3.12 官方变更说明](https://docs.python.org/3.12/whatsnew/3.12.html#other-language-changes)记录了浮点 `sum` 改用 Neumaier 累加。本次定位实验仅在独立分析进程中把该模块的 `sum` 查找改为 `math.fsum`，两套解释器均与归档逐值一致，支持累加算法变化是本批差异的直接来源。这个实验不是新的验收算法；正式复核仍运行未修改的冻结读取器。

原始清单及定位工具见[最终逐值分析](../demo-output/python39-physical-recomputation-compatibility-20261007/agent-difference-analysis-final.json)和[分析文件哈希](../demo-output/python39-physical-recomputation-compatibility-20261007/agent-owned-analysis-manifest.json)。它是已知归档回归，不是新保留数据或新的自然运行。

## 数值比较规则

独立复核的 `derived-binary64-ulp-v1` 规则固定为最多 **4 个相邻 binary64 表示步长**，来自本批完整差异清单的观测上限；没有相对误差、绝对误差下限、统一取整或自动增大容差。

允许使用该规则的字段只有：

- 入口核对中 `west_face_y_centres`、`expected_inlet_x_velocity` 两个数组的浮点元素。
- 成功采样门禁、原验收值及注入前原采样测量中的 `profile_error`、`pressure_gradient_error`。

不同值必须均为有限、同号、非零的正规 Python binary64 float。零、符号变化、次正规数差异、NaN/Inf、int/float 或 bool/int 替换均拒绝。数组顺序/长度、字典字段、标识、哈希、未知值、九文件有理数规则、日志值、全场稳态量、阈值和全部消耗/评分仍精确比较。相同有限 float 以十六进制表示精确核对，正负零也区分。

**每条原验收条件的 True/False/None 判断必须保持一致**，不能仅比较总通过标记；即使任务总体仍失败，一条数值跨越原阈值也拒绝。全场 U/p 的两条稳态判断分别核验。4 ULP 是复现等价规则，不能加入原 `<=0.02`、`<=0.03`、`<=1e-7` 标准，也不能把缺少采样指标的失败变成成功。报告保存所有允许或越界的逐值差异、hex 值和 ULP 步数。

本批面心经验最大为 1 ULP、Ux 为 2 ULP、profile 为 4 ULP、pressure 为 2 ULP；统一规则上限为 4，不表示各字段的所有 4 ULP 情形都已获得跨版本运行实证。超限或判断变化时应保存拒绝报告，研究原因，不自动放宽。

## 离线复现

在仓库根目录运行，输出必须为全新目录：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify_recovery_observation_python.py \
  --python-a /usr/bin/python3 \
  --python-b "$PWD/.venv-native/bin/python" \
  --freeze execution-freezes/bounded-recovery-observation-20261006 \
  --batch real-runs/bounded-recovery-observation-20261006 \
  --out demo-output/my-python-physical-recomputation
```

入口分别启动新的 Python 进程，保存实际版本、代码哈希、两份完整原始证据复核报告及跨版本逐值比较。复核输出在归档和冻结目录之外，写出前后核对源文件清单与哈希。原评分重算与启动分母、顺序、身份、消耗、自然 E=0 边界保持严格一致。不调用会回写原评分的 `report`。

也可只用一个解释器复核：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/review_recovery_observation.py \
  --freeze execution-freezes/bounded-recovery-observation-20261006 \
  --batch real-runs/bounded-recovery-observation-20261006 \
  --out independent-reviews/my-python39-physical-review.json
```

## 支持范围与依赖

本次实证范围是本机 **CPython 3.9.6 与 3.12.14、binary64**，固定 Foundation 10 Couette 工作流和该 76 次归档。其他解释器版本、平台、物理机制及新数据需要自己的兼容性验证；不能由本批推断全部 Python 3.9+ 的物理复算等价性。

离线导入、体检、哈希/账本/评分核验及此物理复算只需 Python 标准库；不需 Docker、模型凭据或第三方 CFD 库。已有核心 CLI 的 Python ≥3.9 支持范围保持。实际 CFD 复测仍需冻结的容器/架构及原生测量、渲染依赖；完整模型执行仍需冻结 Python 3.12.14、模型/CLI 及认证。此次兼容修复不扩大真实运行环境支持范围。

历史 Python 3.9 严格比较[失败报告](../demo-output/bounded-recovery-observation-execution-20261006/independent-review-python39.json)、冻结 3.12 [原独立复核](../independent-reviews/bounded-recovery-observation-20261006.json)及[原评分](../real-runs/bounded-recovery-observation-20261006/results.json)保持原字节。自然未出现符合恢复资格的故障，结论仍为“自然恢复收益无法判断”；注入成功不能替代自然证据。兼容性验证不增加自然收益、稳定节省或综合优化的证据。

## 本次验证结果

[跨版本全量复核](../demo-output/python39-physical-recomputation-compatibility-20261007/cross-version-audit/cross-version-verification.json)通过，两版本各 76/76 条证据复核通过，原评分重算精确一致。复核通过不代表原失败任务变成成功：完整质量仍为 64 次通过、12 次注入基线失败，未知指标及消费金额保持原值。647 个跨版本差异之外的报告字段精确一致；3.9 报告有 714 条比较位置差异，因为成功采样的同一指标同时在门禁与最终验收两处核对，不能当作 714 个独立数值来源。

归档 46,800 文件、执行冻结 264 文件及复核代码的前后字节一致。全软件测试在两套 Python 各 **363/363** 通过，其中本次复核、数值边界及跨版本入口共 40 项。负例覆盖 5 ULP、原阈值跨越、未知变确定、类型/身份/哈希/输入变化、非白名单稳态/消耗漂移、零/次正规数/非有限数，以及输出覆盖和源码/原文件改动拒绝。软件 fixture 仅用于测试，不作为物理或自然故障证据。完整记录见[验证索引](../demo-output/python39-physical-recomputation-compatibility-20261007/README.md)。
