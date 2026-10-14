# 独立用户任务卡

材料版本：20261012，配套 `product/`来自同日首版交付包，产品版本仍为 `0.11.0+collect1`。当前试用暂缓；以下任务供组织者后续决定恢复试用时使用。

请只用随包资料，在自己的新工作目录完成下面四个任务。参考预留30–45分钟；遇到问题可以停止或求助，先记录卡点即可。无需维护者仓库、历史库存、Docker、OpenFOAM或模型账号。本次只使用已有Shell控制例，不运行新的物理任务。

先打开[首版README](../README.md)和[分步说明](../WALKTHROUGH.md)。本轮使用README的“分步操作”路线：安装后直接运行collect基线、collect补丁版和compare；showcase不是必做项。命令和参考解释都可以查文档，不需要凭记忆操作。

编号：________　开始时间：________　反馈文件：`feedback/你的编号.md`

| 任务 | 要做什么 | 请留下什么 |
| --- | --- | --- |
| T1 安装 | 在product目录的新venv中从本地wheel安装；确认实际版本可用 | Python/系统/Git/Bash版本，执行的安装命令及结果，实际包位置，是否需要帮助 |
| T2 发现问题 | 用README的基线collect命令运行已有no-log-failure，打开本次体检报告 | 找到的一项问题、事件/文件位置；用自己的话解释为什么这是问题 |
| T3 应用补丁 | 用README的patched collect命令执行同一案例；查看本次补丁回执 | 补丁实际应用的证据，以及修改发生在哪个目录 |
| T4 复测 | 比较本次before/after记录，阅读两版报告和采集摘要 | 前后实际退出与执行结论、是否可比及依据；说明失败任务是否已经成功、采集完成表示什么 |

补丁由已有patched入口在新沙箱中应用，不需要将补丁手工再打到同一沙箱或你的其他项目。每次输出使用不存在的新目录；不要拿随包 `evidence/` 或别人的记录当作自己本次输出。

安装后可用 `.venv/bin/simclinic --version` 确认版本；用 `.venv/bin/python -c 'import simclinic; print(simclinic.__file__)'` 记录本次实际包位置，填入反馈表。

## 填反馈与保留结果

填写[反馈表](FEEDBACK-TEMPLATE.md)或组织者分配的编号表，即使没完成也请填写已做到哪一步。记录你实际看到的结果，不必把它改成文档中的预期值。特别保留需要帮助的步骤、查了哪些资料和仍不理解的地方。

完成时保留本次以下文件；目录名若不同请在表中注明：

```text
before/record.json
before/collection-summary.json
before/audit/report.json、report.md
after/record.json
after/collection-summary.json
after/audit/report.json、report.md
after/patch-application.json
comparison.json
```

完整before/after目录也请保留在本地，以便必要时查当次文件hash和工具日志。失败时保留原命令、退出码及stderr，安装失败没有这些输出也可反馈。反馈只需必要环境和错误信息，不需要凭据；本资料不会自动上传。
