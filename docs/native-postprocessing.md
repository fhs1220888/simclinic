# 原生 Agent 后处理链接入

本文保留该阶段的验证快照。后续模型规划、输入生成与实际执行见 [完整模型链路接入](full-agent-integration.md)。

固定 Foam-Agent v2.1.0 commit `bfb7f791f77438ff90c1a39ff398739ee97bcc67`。已下载该提交的完整 Python 源码并保留来源 SHA-256；原始源码在 `integration/foam_agent_v210/native/upstream`，候选在同级 `patched`。

## 已接通的链路

`local_runner → route_after_runner → visualization_node → run_pyvista_script → PyVista/VTK → PNG 与统一记录`。

这次正常导入完整源码模块，使用上游实际 LangGraph 工厂编译图，从 `local_runner` 进入。没有 AST 提取、节点替身或模型回复替身。默认完整流程仍从 planner 开始；新增入口用于已有算例的运行与后处理。真实测试没有执行 planner、input_writer、模型 reviewer 或生成脚本的模型回退，不能称为完整模型驱动工作流验证。

1. 运行前新建 run ID，以 `0`、`constant`（排除生成的 polyMesh）、`system` 和 Allrun 内容生成输入版本。清除旧的正时间目录，清理失败则停止。
2. 运行结束时保存本次输入、生成的网格和场文件 SHA-256。非零退出、缺少新 U/p 输出、输入在运行中改变均不能提供可消费结果。
3. 当前身份来自图状态 `simclinic_context`，不从待读文件的身份反推。后处理同时检查当前指针、输入现状、时间目录清单及文件哈希。
4. 将已校验的同一次读取字节写入每次新建的后处理目录。PyVista 读取其中的 `.foam` 描述符、网格与场文件；不会让“最新目录”选到原算例中的其他结果。
5. 每次尝试删除预先存在的目标 PNG。进程退出 0 且本次产生 PNG 才算渲染成功；渲染后再次核验输入与快照。图片保存 run/input/hash，统一记录增加求解、引用及后处理事件。
6. 重跑会清空图状态中的旧图片。执行错误耗尽重试后直接结束，不进入可视化；完成输出区分流程执行与 CFD 验收。

依赖延迟初始化：导入确定性节点不初始化模型、RAG 或读取 OAuth。实际模型调用与检索仍使用原后端路径；这些分支没有在本次测试中验证。

## 实际复测

最终图运行归档：`real-runs/native-postprocessing-final-20261004`。已有工具函数归档：`real-runs/native-tool-entrypoints-final-20261004`。

- 正常通道实际运行 blockMesh、checkMesh、simpleFoam 与采样，再由原生图启动真实 PyVista/VTK 渲染。
- 原验收条件保持不变：速度剖面相对误差约 0.699%，压力梯度约 0.394%，通过执行、数值与解析参考验收。
- 旧 run、旧 input、缺少 caller 身份均在渲染前拒绝。
- 实际修改入口文件后拒绝旧结果；修改场文件后按 hash 拒绝。负例是明确的契约/文件故障注入，未把它们描述为自主 Agent 随机事故，也未为入口变化再次求解。
- exit 7 在实际图中进入 reviewer 的已耗尽分支，然后结束；没有模型调用，也没有继续消费旧图片。
- 使用上游注册的 run/visualization 函数直接验证：run 返回生成时身份，visualization 必须带 caller 身份，成功生成图片；旧 run 被拒绝。没有启动 MCP 服务，没有测试协议传输。
- 把已有真实 PNG 放入新快照，让实际 Python 子进程执行 `pass`：退出 0 但没有新 PNG，被判为失败。

图的 [统一报告](../real-runs/native-postprocessing-final-20261004/audit/report.md)、[结果与负例](../real-runs/native-postprocessing-final-20261004/summary.json)、[工具入口结果](../real-runs/native-tool-entrypoints-final-20261004/summary.json) 均可查看。

运行记录没有声称覆盖全量 Agent 行为，coverage 仍为 false，模型 token/成本为 null。数值验收由固定通道测试脚本单独执行；图片身份校验不替代 CFD 验收。

80 项针对性测试在 Python 3.9.6 和 3.12.14 上通过。三项补丁在完整固定源码包上可应用，内容与候选快照一致，逆向撤销可恢复基准。0.4.0 wheel 已离线构建/安装，并从仓库外验证新模块可导入。第一次 shell 桥接未导出函数而失败的目录保留为调试证据；最终通过归档以上述 final 目录为准。

## 使用与复现

CLI 仍只依赖标准库；原生图/渲染的测试依赖在 `.venv-native`，完整版本清单在 `integration/foam_agent_v210/native/requirements.lock`。固定求解容器沿用 Foundation OpenFOAM 10 的镜像 digest。宿主图和渲染是 Darwin arm64，CFD 为 linux/amd64 容器；主机 bashrc 桥接实际 Docker 命令，不代表主机安装了 OpenFOAM。

```bash
python3 scripts/build_native_integration.py
.venv-native/bin/python -m unittest discover -s tests
.venv-native/bin/python scripts/run_native_postprocessing.py --out real-runs/my-native-run
.venv-native/bin/python scripts/test_native_entrypoints.py --out real-runs/my-tool-run --graph-run real-runs/my-native-run
```

运行脚本只接受新的输出目录。初次重建隔离环境需要 Python ≥3.10；本次使用 3.12.14：

```bash
python3 -m venv .venv-native
.venv-native/bin/python -m pip install -r integration/foam_agent_v210/native/requirements.lock
```

应用到目标 Agent 时先安装 SimClinic ≥0.4，然后依次检查并应用补丁 0001、0002、0003，见 [补丁说明](../patches/foam-agent-v2.1.0/README.md)。完整 graph 中身份由 runner 自动写入，无需手工填写。已有 run 工具返回 `run_id`/`input_version`，已有 visualization 工具新增必填 `expected_run_id`/`expected_input_version`，这是工具契约变更。

不追认旧目录中的未登记结果；需要从新的观察运行产生 manifest。本接入针对独占算例目录的串行、不可压缩 U/p 场输出，HPC、并发共享目录和其他输出格式没有验证。可信采集清单不是密码学签名，快照也不是任意模型脚本的安全沙箱。模型回退按原入口生成脚本，并使用相同快照/输出检查；其模型质量与完整规划流程仍待单独验证。
