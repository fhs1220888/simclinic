# 从问题到复测：约三分钟的试用顺序

先按README的三条命令运行 `showcase`，打开 `demo/index.html`。这份报告按问题、补丁、复测和质量边界串好结果。需要逐条采集时再使用README中的 `collect` 命令，无需两个流程都运行。

## 1. 看清问题

打开 `demo/audits/before/no-log-failure/report.md`，找到 `error_continuation` 的发现及其事件定位。原进程实际exit7，固定执行服务却给出了成功结论。报告针对“错误后仍报告成功”，不是从日志缺失猜测仿真成功或失败。

现场复现回执在 `demo/live-shell/exit7/before/receipt.json`：`actual_exit_code=7`、`reported_execution_success=true`。历史归档与这份现场Shell回执是不同来源，报告中均有说明。

## 2. 检查实际应用的补丁

打开 `demo/patches/0001-execution-status.patch`：纳入真实退出码、超时与未知执行状态。另一个已有 `0002-verified-result-interface.patch` 约束结果的当前run/input身份与内容hash。

`demo/patch-application.json`保留每个补丁实际 `git apply --check` 和 `git apply` 的退出码，并确认应用后源码与已有候选一致；修改后的文件在 `demo/patch-sandbox/`。无需用户再次手动应用这些补丁到该沙箱。本交付未验证对其他Agent版本直接打补丁的兼容性。

## 3. 读复测结果

| 查看位置 | 通过条件 |
| --- | --- |
| `demo/live-shell/exit7/after/receipt.json` | 实际退出仍7，reported_execution_success=false |
| `demo/live-shell/exit0/{before,after}/receipt.json` | 实际退出0，报告成功 |
| `demo/audits/after/no-log-failure/report.md` | 错误后继续结论不再出现；失败任务仍没有完成 |
| `demo/result-guard.json` | 当前身份允许；旧run、旧input和未知身份拒绝 |
| `demo/summary.json` | status=pass；新增模型/CFD/采样调用均0 |

`demo/comparisons/`是已有归档的比较；本次现场Shell的前后观测在 `live-shell/`，不要混成一次新CFD配对。用 `collect` 分步路径时，`before/record.json` 和 `after/record.json`才是该次新采集配对，`comparison.json`应为comparable=true。

## 4. 确认质量和完成边界

`demo/audits/{before,after}/healthy/report.md`显示已有正常通道的执行、数值与参考验收满足。`ended-not-converged`展示日志有End、任务仍未达到数值和参考要求，不能把“执行结束”当成“完成”。

后来已经完成的Foundation10独立安装验收位于 `evidence/foundation10/`：真实基线与补丁版每版27项登记证据核验通过，两次当次求解/采样数值一致，比较可比。这些是随包已有结果，不是本次试用重新求解的结果，也不证明整体优化或稳定节省。

## 失败时怎么处理

| 现象 | 处理 |
| --- | --- |
| 缺少Git/Bash | 补系统依赖；确认 `git --version`、`bash --version`可用 |
| Python不满足要求或缺venv/pip | 使用Python3.9+及其venv/pip；Foundation10旧镜像自带3.8不能直接安装 |
| 输出已存在 | 换新目录；不要覆盖旧记录 |
| showcase非零退出 | 保留终端错误及 `demo/summary.json`；status必须pass才算演示完整通过 |
| collect显示complete，任务仍失败 | 本失败例预期如此；看实际退出、分层验收及源执行结论 |
| audit退出1 | 使用--fail-on-finding时表示发现问题；基线失败例预期退出1，补丁后预期0 |
| 记录或证据不足 | 核对case目录、原文件hash和coverage，不补写虚假的成功观测 |

反馈先保留本地：系统/架构、Python与simclinic版本、命令及退出码、summary或collection-summary、check_id与source/pointer、相关stdout/stderr。不必提供凭据；本交付不会发送或上传报告。
