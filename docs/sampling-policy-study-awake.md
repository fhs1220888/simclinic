# 带临时防睡眠保护的 L 采样评估

首次 J 保留运行和后续 K 开发检查遇到模型中断，原批次均按协议停止。K 的失败请求创建时间与 macOS Idle Sleep 相同，随后存在约 17 分钟唤醒间隔，见 [电源证据](../real-runs/sampling-policy-study-20261005/power-evidence.json)。这是可确认的本地影响因素，不证明全部传输故障只有这一原因。

新批次为 `real-runs/sampling-policy-study-awake-20261005`。两组 content 原生源码、模型/提示词/检索、资源、reviewer 和原验收仍相同，只切换 legacy/sampling 完成策略。两组共同使用固定后端错误分类、部分用量查询及协调器的临时 `caffeinate -i -w PID` 保护。保护仅随该协调器存活，退出自动释放，不更改持久电源偏好；固定命令字节 SHA 登记在 freeze.json。

| 新任务 | 平均速度 m/s | 网格 | 每组次数 |
| --- | ---: | --- | ---: |
| L1 | 0.021 | 54 × 27 × 1 | 3 |
| L2 | 0.029 | 54 × 27 × 1 | 3 |
| L3 | 0.021 | 66 × 33 × 1 | 3 |

D0、H/I/J/K 全部排除在新的保留集之外，历史回复不重放，不注入故障。几何、物性、采样点、速度/压力参考误差阈值与任务输入/U 图要求保持，参考字典与结果不送入模型。每次从真实 planner 开始；两次新 D0 必须完整通过，再启动交错串行的 18 次保留任务。开发预算 100 万、含开发总批次预算 500 万 usage token，每次启动预留 45 万，未知或到边界停止。

J/K 的原失败、未执行任务和未知消耗分别留在其批次；没有恢复旧批次或合并为一次成功评估。新批次开始前，其原 D0/失败部分加独立就绪请求的合计已知下限为 678,846 token，另有两个失败请求用量未知；这部分不作为新批次的零成本。CLI 调用次数指采集端 invoke 次数，没有新增自动重试；CLI 自身传输重连不另猜费用或 usage。

```bash
python3 -m simclinic study-status real-runs/sampling-policy-study-awake-20261005
# 只运行冻结驱动，阶段已启动时不要重复调用
python3 real-runs/sampling-policy-study-awake-20261005/common-runtime/scripts/verify_sampling_study.py --batch real-runs/sampling-policy-study-awake-20261005
```

[开发防睡眠断言](../real-runs/sampling-policy-study-awake-20261005/idle-assertion-development-verification.json)从 macOS 断言列表确认 PID 对应的 PreventUserIdleSystemSleep 生效，只保存核验结果，不保存原始系统输出。正常运行中的作用是防止空闲睡眠，不代替模型响应、求解成功或 CFD 验收。

154 项软件测试在 Python 3.9.6/3.12.14 通过，新增防睡眠二进制改变拒绝、启动失败不跑模型及 PID 生命周期契约；模拟数据不是实际仿真。[离线安装验证](../demo-output/backend-reliability-package-verification.json)确认新模块可导入，原停止批次仍可只读查询。

两次新 D0 已全部通过完整任务验收：34 次模型调用、455,864 usage token；123 个注册证据与 2 份输入快照通过冻结驱动的重新核验，速度、压力参考偏差与输入条件均达标。开发阶段不计入保留结果。[开发核验](../real-runs/sampling-policy-study-awake-20261005/development-verification.json)与[开发保护释放核验](../real-runs/sampling-policy-study-awake-20261005/idle-release-development-verification.json)已保存；保留阶段使用新协调器，另有[保护生效核验](../real-runs/sampling-policy-study-awake-20261005/idle-assertion-heldout-verification.json)。

18 次保留运行已按冻结顺序全部完成，完整任务、CFD 与 U 图验收均为基线 9/9、候选 9/9。双方各 153 次模型调用、9 次求解尝试，没有失败/重试或证据缺失。候选节点调用 9 次，失败分支 0 次，经核验的自然 fatal 事件为 0；这支持正常路径兼容性，不能估计自然诊断收益。

[归档核验](../real-runs/sampling-policy-study-awake-20261005/archive-verification.json)通过 1,230 个注册证据、20 份输入快照，含开发共 340 次模型调用、4,575,059 usage token；原冻结、源码、串行顺序、输入与参考误差重算一致。总预算剩余 424,941，不追加样本。[保留保护释放核验](../real-runs/sampling-policy-study-awake-20261005/idle-release-heldout-verification.json)确认协调器退出后断言已释放。

[9 对达标消耗补充](../real-runs/sampling-policy-study-awake-20261005/paired-consumption.json)中，候选减基线 token 差中位数 -368，5 对更少、4 对更多；求解秒差中位数 +0.004317，工作流秒差中位数 -4.107829。描述差值不证明稳定省 token、加速或费用下降。J/K 与就绪检查加新 L 批次的[已知用量下限](../real-runs/sampling-policy-study-awake-20261005/family-consumption.json)为 5,253,905，另有两个旧失败请求未知；准确总量与费用仍为 null。

诊断、完整任务和消耗分别评分，候选节点调用与失败分支触发分列；未知证据不判错误。[完整结论与后续范围](../real-runs/sampling-policy-study-awake-20261005/INTERPRETATION.md)、[主报告](../real-runs/sampling-policy-study-awake-20261005/report.md)和[最终只读状态](../demo-output/sampling-policy-study-awake-status.json)均已保存。总体优化效果尚未证明。
