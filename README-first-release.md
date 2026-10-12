# SimClinic 首版交付入口

本地首版交付完成，以20261012封版、`0.11.0+collect1`为基线。当前GitHub提供可公开的独立安装文件、源码、演示说明、摘要与校验记录，范围见[公开同步说明](releases/20261012/PUBLICATION.md)。

完整原ZIP含3份带本机用户目录路径的回执，按贡献规则留在本地，原包未修改。本仓库中的wheel和源码包与封版原件字节一致；公开文件选集使用独立SHA256SUMS。

## 使用

进入 `releases/20261012`，按[README](releases/20261012/README.md)离线安装wheel并运行现有showcase，或按分步collect/compare路线发现问题、应用已有补丁与复测。仅需Python3.9+、Git和Bash，默认不需要模型、Docker或求解器。

- [完整安装说明](releases/20261012/INSTALL.md)
- [问题→补丁→复测说明](releases/20261012/WALKTHROUGH.md)
- [已有Foundation10结果](releases/20261012/evidence/foundation10/RESULTS.md)
- [试用任务卡](releases/20261012/pilot/PARTICIPANT.md)、[通过标准](releases/20261012/pilot/ACCEPTANCE.md)、[反馈模板](releases/20261012/pilot/FEEDBACK-TEMPLATE.md)
- [公开文件摘要](releases/20261012/SHA256SUMS)、[公开同步校验](releases/20261012/PUBLIC-SYNC-VERIFICATION.json)

失败任务两版仍失败，补丁纠正其错误成功报告；不将采集命令退出0当作任务验收通过。独立用户验收继续暂缓，维护者回执不代替用户结果。本次同步没有扩展功能、运行新案例或物理实验。
