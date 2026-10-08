# 只读采样失败诊断（0.10）

`diagnose-sampling` 支持当前已注册的 OpenFOAM Foundation 10 通道采样归档。它补充五项 Agent 行为检查，不参与已冻结批次的运行或验收。

```bash
python3 -m simclinic diagnose-sampling \
  real-runs/content-interface-heldout-20261004/runs/I1-r2-baseline/record.json \
  --case real-runs/content-interface-heldout-20261004/runs/I1-r2-baseline \
  --out demo-output/sampling-failure-diagnostic.json
```

只读取记录中 sample/postprocess 工具事件及其 stdout/stderr、profile.xy、pressure.xy。每份实际读取的日志/结果核验运行 ID、输入版本、生成时哈希和路径。输出保存在采集目录之外，不修改归档。

- **发现问题**：已记录非零退出，或经核验的日志含致命错误。退出 0 与致命日志并存会单独标记，错误包含事件、文件、行号和摘要。
- **检查通过**：退出 0、两份日志和所需非空输出均有可核验字节证据。此状态不评估数据格式、数值收敛或参考误差，不能用来宣布 CFD 正确。
- **证据不足**：退出未知、日志缺失/无法核验，或所需输出没有唯一可核验的登记。缺少文件不直接判定 Agent 做错。
- **不适用**：记录没有已注册的 sample/postprocess 事件。

真实 I1-r2-baseline 的工具事件 e55 记录 exit 0/outcome pass，但 a56 对应 stdout.log 第 46 行含 FOAM FATAL IO ERROR，且 profile/pressure 输出缺失；[离线诊断](../demo-output/sampling-failure-diagnostic.json) 据此显示发现问题。原始完成声明为失败，原验收和证据缺失状态均保留。I1-r1-candidate 的[正常归档](../demo-output/sampling-pass-diagnostic.json)通过字节证据核验。这两次读取没有新增求解或模型调用。

7 项新测试验证退出成功加致命日志、正常输出、缺失证据、篡改/旧运行日志、非零退出、无采样事件及只读输出保护；Python 3.9.6 与 3.12.14 的全部 132 项测试通过。[0.10 安装包验证](../demo-output/sampling-package-verification.json)在仓库外离线安装 wheel 后导入新模块，并查询实际归档和冻结批次；临时环境已清理。

0.11 已在新的开发运行中将同一原则接到实际采样工具返回路径，正负回归及默认入口验证见 [采样门禁](sampling-gate.md)。该运行门禁还验证实际输出格式与测量发布身份；本只读命令继续保持上面的证据语义。原参考阈值和诚实失败规则保持；旧冻结批次未改。下一步另行冻结候选、新的完整模型开发检查和新保留任务；I1/I2/I3 已进入开发数据，不能重复作为独立保留验证。
