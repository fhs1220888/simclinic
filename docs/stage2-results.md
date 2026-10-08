# 第二阶段实际执行结果

日期：2026-10-04。最终归档：`real-runs/stage2-final-20261004`。所有数据为实际进程产生，synthetic=false；故障场景明确标注 fault_injection。完整 LLM Agent 工作流、优化补丁和优化效果没有运行。

## 固定环境

- OpenFOAM Foundation 10，实际 solver Build：`10-c4cf895ad8fa`，simpleFoam，稳态不可压缩单相。
- official image：`openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d`。
- Foam-Agent v2.1.0 执行源 commit：`bfb7f791f77438ff90c1a39ff398739ee97bcc67`。
- 本机 Darwin arm64，OrbStack，容器 linux/amd64，两颗 CPU / 2 GiB，单进程，容器断网。
- 原始执行函数通过 AST 隔离运行，跳过模型/RAG 导入，函数体保持原样；范围见 [集成说明](../integration/foam_agent_v210/README.md)。

## 预先规定的通道问题

长度 1 m，高度 H=0.1 m，二维 empty 前后面，网格 40×20×1；入口指定解析抛物线，平均速度 Umean=0.01 m/s，运动黏度 nu=0.001 m²/s，无滑移上下壁，出口运动学压力 0，Re=Umean H/nu=1。

参考使用平面 Poiseuille 解；平行板压差驱动层流的物理模型可核对 [MIT 课程材料](https://ocw.mit.edu/courses/2-25-advanced-fluid-mechanics-fall-2013/1a114d602956fa0dd328155f9b45f93d_MIT2_25F13_Couet_and_Pois.pdf)。将压力写成 OpenFOAM 的运动学压力 P/rho，解为 `u(y)=6 Umean (y/H)(1-y/H)`，对应 `dp/dx=-12 nu Umean/H²=-0.012 m/s²`。

验收阈值在首次实际求解前写入 SPEC，后续调试只修正采样类型/读取格式，没有放宽条件：

- execution：实际退出码为 0，且求解日志存在 End。
- numerical：SIMPLE residualControl（p、U 初始残差阈值 1e-7）报告收敛，末步局部连续性误差 ≤1e-7。
- reference：中部 20 点速度剖面相对 L2 误差 ≤2%，包含横向速度偏差；中心线 17 点压力拟合梯度相对误差 ≤3%。

## 最终结果

| 案例 | 执行验收 | 数值验收 | 参考验收 | 整体验收 |
| --- | --- | --- | --- | --- |
| healthy | 满足 | 满足 | 满足 | 通过 |
| ended-not-converged | 满足 | 未满足 | 未满足 | 未通过 |
| no-log-failure | 未满足 | 未测量 | 未测量 | 未通过 |

正常通道在 103 次 SIMPLE 迭代后收敛，末步局部连续性误差 `8.10074078918e-13`；速度误差 `0.006986786967466754`（0.699%），压力梯度误差 `0.003935226324550297`（0.394%）。这些结果只说明该指定算例/网格满足上述门槛，不是网格收敛研究或普遍 CFD 正确性认证。

提前结束案例仅执行一次迭代，日志出现 End，但没有 SIMPLE 收敛标记；连续性误差 `1.98370784906e-5`，速度误差约27.8%、压力梯度误差约163.5%，数值及参考验收不通过。测试工具明确报告任务未完成，故五项行为检查不会仅因算例失败就给出“错误完成”的 finding。

## 确定性发现：忽略真实退出状态

no-log-failure 的 Allrun 实际 exit_code=7，原始 `check_foam_errors` 在没有 log 文件时返回空列表，`local_runner_node` 因列表为空输出成功。SimClinic 的实际退出状态采集与成功 conclusion 构成同一 run/input 上的证据冲突，因此检出“错误后继续生成结论”。这不是模型幻觉；是固定执行服务在明确故障注入下的行为。

证据包括：

- [上游进程观测](../real-runs/stage2-final-20261004/no-log-failure/case/.simclinic/upstream.json)
- [真实成功文本](../real-runs/stage2-final-20261004/no-log-failure/case/workflow.log)
- [统一记录](../real-runs/stage2-final-20261004/no-log-failure/case/.simclinic/record.json)
- [体检报告](../real-runs/stage2-final-20261004/no-log-failure/audit/report.md)

候选改进是将真实退出码纳入执行结果、禁止空错误列表替代执行成功。尚未修改上游函数，也未宣称这个问题已经修复。

## 仍需完成

选定模型入口后，接入原生包和完整 planner/reviewer 轨迹，归档其真实输入生成、重试、后处理及模型 usage。当前证据覆盖只属于脚本测试流程。之后对执行状态问题与旧结果身份门禁提供可应用补丁，再用保留算例、多次运行验证质量和行为改善。
