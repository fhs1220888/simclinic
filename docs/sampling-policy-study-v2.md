# 新 K 采样策略冻结批次

原 J 批次在生成输入时遇到模型 exit 1、缺失 usage，按冻结规则停止于 1/18。[原失败](../real-runs/sampling-policy-study-20261004/STOPPED.md)及未知用量保持，不能直接续跑。一次独立短就绪请求随后成功；旧失败的具体原因仍无法追溯。工作区补充 [公开 CLI 错误的固定分类及已知部分消耗](backend-failure-accounting.md)，两组共享这些基础设施修复。

新批次位于 `real-runs/sampling-policy-study-20261005`，freeze SHA `2c934ee7b220f0df93f508baebb4b8c64c64223596869e758a9153ffbf922ec3`，明确登记原批次 SHA 与停止原因。没有更换模型、提示词/检索或数值阈值，仍是 content 原生源码完全相同、只切换 legacy/sampling 完成策略。没有自动模型重试；缺失 usage 仍停止，不猜零。

| 新任务 | 平均速度 m/s | 网格 | 每组次数 |
| --- | ---: | --- | ---: |
| K1 | 0.024 | 52 × 26 × 1 | 3 |
| K2 | 0.030 | 52 × 26 × 1 | 3 |
| K3 | 0.024 | 68 × 34 × 1 | 3 |

D0、所有 H/I/J 任务、历史回复和开发故障注入均排除在新保留集之外；原 J2/J3 虽未运行也主动排除。新参考输入不提供给模型，检索保持原 D0 语料。解析参考、采样点、几何/物性及原七项完整任务验收保持；只有任务规定的入口速度和网格变化。

每组先实际运行一次新的完整模型 D0，均通过且 usage 完整后，再独立调用 heldout，按预声明交错顺序串行运行 18 次。新批次开发预算 100 万 usage token、含开发的总批次预算 500 万，每次启动预留 45 万，32 次模型调用/2 次 reviewer 上限、模型渲染回退关闭。旧批次未知消耗没有移入新批次作零；跨批次总量仍需报告已知下限与未知部分。

```bash
python3 -m simclinic study-status real-runs/sampling-policy-study-20261005
# 必须使用冻结驱动；既有 development 不得重复启动
python3 real-runs/sampling-policy-study-20261005/common-runtime/scripts/verify_sampling_study.py --batch real-runs/sampling-policy-study-20261005
# 新 D0 阶段完整通过后执行一次：
.venv-native/bin/python real-runs/sampling-policy-study-20261005/common-runtime/scripts/run_sampling_study_batch.py --batch real-runs/sampling-policy-study-20261005 --stage heldout
```

评分、采样原因证据、候选节点/失败分支分别计数，以及只在质量/证据均完整的配对上比较消耗，保持 [原公平协议](sampling-policy-study.md)的约定。失败和缺失在分母，未运行不填零；若没有自然采样故障，不推断诊断收益频率。没有模型可靠 seed 或服务端快照，三种通道参数变化不能证明跨领域泛化。

152 项软件测试在两个 Python 版本通过。后续实际 D0-baseline 的第 8 次模型请求发生 transport_error，按协议停止为 development_contract_failed，保留为 0/18；没有求解。[停止与睡眠重叠证据](../real-runs/sampling-policy-study-20261005/STOPPED.md)保留失败和 84,949 token 已知部分。新增临时防空闲睡眠保护后，另冻结 L 批次 `real-runs/sampling-policy-study-awake-20261005`，J/K 任务全部排除；本页不再作为当前运行批次。总体优化效果尚未证明。
