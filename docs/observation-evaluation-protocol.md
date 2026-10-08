# 自然故障观测协议与执行结果

[协议 v1](../protocols/bounded-recovery-observation-v1/PROTOCOL.md)及[机器计划](../protocols/bounded-recovery-observation-v1/protocol.json)已保存为设计冻结。创建时库存扫描、输入字典生成与设计核验都是离线工作，任务未执行，设计核验的 `execution_ready=false` 和 `execution_authorized=false` 保持原值。随后独立执行冻结获用户明确授权，[四次 C0、48 次自然及 24 次注入](../real-runs/bounded-recovery-observation-20261006/INTERPRETATION.md)已按原序完成并独立核验；实际授权、开发及运行证据保存在新批次，不回写设计历史。

本设计选择四个执行前未见的 Couette 参数任务 C7–C10，排除 C5/C6 及全部已见完整任务/变异输入组合。历史库存除了原 C0–C6，还找到一个仅在输入负例中出现的 `0.022 / 48 / 23`，没有遗漏它。原始单文件矩阵 fixture 的缺失参数仍保留未知；“未见”仅指当时本地归档的完整任务与输入对目录，不是外部历史或不可得材料的证明。C7–C10 现已使用，不能再次声明为新独立保留任务。

原计划为 4 次旧 C0 开发检查、48 次未注入自然观测和 24 次明确注入，共 76 次；固定任务、重复与组别先后顺序，不为等到自然故障延长，也不选择性重跑。预算规划总边界为 2,200 万 usage token，分阶段 150/1,350/700 万，保留每次启动前 45 万余量；这些是软规划与停止边界，不是保证不超出的硬计费限额。授权批次实际 17,481,440 tokens、219.3 分钟，全部预算及储备检查通过，费用未知。[实现及实际环境快照](recovery-observation-implementation.md)保持冻结；该批已结束，新批次另立协议与授权。

只读核验原设计：

```bash
python3 scripts/check_recovery_observation_protocol.py --protocol protocols/bounded-recovery-observation-v1
```

执行前曾加 `--rescan-archive` 确认新颖，结果见[冻结前重扫](../demo-output/bounded-recovery-observation-execution-20261006/protocol-rescan.json)。现在重扫应发现 C7–C10 已使用，不能将该设计重新作为执行许可，也不改旧库存以掩盖使用历史。

自行重建库存可写入新的非历史目录：

```bash
python3 scripts/inventory_couette_tasks.py --root . --out demo-output/my-task-inventory.json
```

每组单列全部已启动任务 N、首次 sample 调用尝试的任务 M、明确首次缺失 F 和共有证据确认的恢复资格 E。M 包括 launch failure，额外 sample 不增加 M；真实启动/未启动/启动未知另报，旧 execution_started 字段不能独自证明启动。自然组报告 `F/M`、`E/M`、`E/N`；首次结果未知 U_F、确认 F 中资格未知 U_E、未到采样和不可恢复类别分别保留。已知首次结果子集为 `F/(M-U_F)`，最宽首次缺失范围为 `[F/M, (F+U_F)/M]`，资格范围为 `[E/M, (E+U_F+U_E)/M]`，零分母 null。这些是采样调用中未知观测的识别范围，不是统计置信区间。

主收益是每阶段每组完整任务成功率 `S/N` 及组间差；恢复另报 `A/E`、第二次采样通过 `R_sample/A`、最终原完整质量通过 `R_full/E`。未到采样的规划/输入/求解失败仍进入 N，缺失或未知质量不算成功。完整记录的采样超时/未知退出也继续计分，不能误当采集失败。自然 E=0 时恢复收益不可评；零次观测不是自然故障不存在的证明。自然与注入结果不混合；干预前必须确认日志/身份及两份输出均完整安全可解析，原门禁可通过，才能删 profile。注入未施加的情况不能伪装为预期缺失。

全部失败、重试和中断消耗进入主表：总量、每启动任务消耗、全部消耗除以成功任务数。未知量保留 null 和已知下界，零成功时单位成功成本未定义。双方都成功的配对耗时表只能作为带选择条件的副表，不能用它宣布总体或稳定节省。

[共有资格观察器、完整恢复分支计时、分母/未知评分器和新运行驱动](recovery-observation-implementation.md)现已实现。旧 coordinator 对子进程非零的笼统基础设施分类不能直接复用：诚实任务失败应计分继续，后端/采集/来源损坏和未知用量应停止。离线软件路径验证与独立执行 freeze 已完成；四次新 C0 开发均通过才进入保留。任一保留阶段开始后 C7–C10 转为已使用数据，不能调好策略后重复声明新独立任务。

协议只覆盖固定机制的探索性观测。授权结果为自然两组各 24/24、首次故障及资格均为 0/48，因此**自然恢复收益无法判断**；注入基线 0/12、候选 12/12，不能替代自然证据。长期实际故障频率、统计显著性、稳定节省、跨物理效果和综合优化仍需新证据。

[协议设计交付阶段的离线验证](../demo-output/recovery-observation-protocol-20261006/README.md)：当时归档重扫与设计完整性核验通过，Python 3.9.6 和 3.12.14 各 268 项软件测试通过，历史/交付哈希保持。设计 SHA 为 `1dc94a7992f389d1a3cd466fd7678f35f814fddb8699b86e4088022831d17321`；该设计阶段新增模型调用、求解和采样均为零。之后的[授权执行验证](../demo-output/bounded-recovery-observation-execution-20261006/README.md)另存，不把软件测试或已知归档回归当作自然样本。
