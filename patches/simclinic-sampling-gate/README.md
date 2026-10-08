# SimClinic 采样门禁候选补丁

[candidate.patch](candidate.patch)针对 SimClinic 的注册完成节点和运行入口；它不修改固定 Foam-Agent 源码，也不替代上游 0001–0006 补丁。[manifest.json](manifest.json)记录逐文件基准/交付 SHA-256 和应用/撤销验证。基准来自明确的归档 runtime 及旧版本元数据，不假定存在公开 0.10 Git 标签。当前工作区已经包含此修改。

证据：[历史 fatal 输入重新求解](../../real-runs/sampling-gate-regression-20261004/report.md)在 exit 0 的日志第 46 行出现致命错误；旧 sample 事件仍为 pass，后续发生空 StopIteration。[最终缺失输出回归](../../real-runs/sampling-missing-output-final-20261004/report.md)也显示 exit 0 无 fatal 仍可能没有必需 profile.xy。两类旧策略的完整任务均诚实失败。

原因假设：只凭采样进程退出码判定工具成功，再用迭代器读取输出，无法提供准确的后处理失败原因。预期作用：保持实际退出码，同时检查致命日志及新输出，以明确类别和文件证据停止完成；原验收条件、物理参数和已冻结评分保持。

补丁新增门禁及正负测试/开发回归脚本，允许 SessionRecorder 显式保存错误类别；默认运行选择 content 源码与 sampling 完成策略，仍可选 legacy。测量发布前增加最终身份核验。完整范围与结果见 [说明](../../docs/sampling-gate.md)。

在 manifest 对应的逐文件基准上执行（不是在已经修改的当前目录重复执行）：

```bash
git apply --check /path/to/SimClinic/patches/simclinic-sampling-gate/candidate.patch
git apply /path/to/SimClinic/patches/simclinic-sampling-gate/candidate.patch
python3 -m unittest discover -s tests -v
# 撤销前先检查；仅适用于仍与交付字节一致的文件
git apply --reverse --check /path/to/SimClinic/patches/simclinic-sampling-gate/candidate.patch
git apply --reverse /path/to/SimClinic/patches/simclinic-sampling-gate/candidate.patch
```

8 个文件已在临时目录验证：应用后 SHA 与交付一致，逆向恢复原基准，新文件移除；没有全局安装或新仿真/模型调用。真实复测覆盖正常配对、历史 fatal 输入、明确缺失输出注入及默认入口；新模型 planner 路径、自然成功率、自动修复和总体优化效果仍待独立评估。
