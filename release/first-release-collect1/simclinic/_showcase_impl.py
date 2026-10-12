"""Small first-release showcase: apply existing patches, run shell regressions,
and audit six existing CFD/service archives. No model or CFD calls.
"""
import argparse
import contextlib
from datetime import datetime
import hashlib
import html
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.cli import get_report
from simclinic.core.record import load_record
from simclinic.core.report import write_json, write_report
from simclinic.evaluation import compare
from simclinic.result_guard import ResultRejected, read_verified_result

SCENARIOS = ("no-log-failure", "healthy", "ended-not-converged")
PHASES = {"before": "stage2-final-20261004", "after": "stage3-patched-20261004"}
PATCHES = ("0001-execution-status.patch", "0002-verified-result-interface.patch")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_loader():
    spec = importlib.util.spec_from_file_location("showcase_execution_loader", ROOT / "integration/foam_agent_v210/execute.py")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    return loader


def read_archives(root):
    """Check only the six selected records and their registered evidence."""
    records = {}
    payloads = {}
    sources = {}
    for arm, phase in PHASES.items():
        for scenario in SCENARIOS:
            case = root / "real-runs" / phase / scenario / "case"
            path = case / ".simclinic/record.json"
            record = load_record(path, case)
            require(record.data["synthetic"] is False, "Showcase requires actual execution archives")
            files = {".simclinic/record.json": path.read_bytes()}
            for artifact in record.data["artifacts"]:
                files[artifact["path"]] = read_verified_result(
                    record, artifact["id"], record.data["run_id"], record.data["input_version"])
            key = arm + "/" + scenario
            records[key] = record
            payloads[key] = files
            sources[key] = {"record": str(path.relative_to(root)), "record_sha256": digest(path),
                            "verified_artifacts": len(record.data["artifacts"])}
    return records, payloads, sources


def archive_regression(out, records, payloads):
    results = {}
    for scenario in SCENARIOS:
        pair = {}
        for arm in PHASES:
            key = arm + "/" + scenario
            case = out / "archives" / arm / scenario
            for name, data in payloads[key].items():
                target = case / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            record = load_record(case / ".simclinic/record.json", case)
            report = get_report(record)
            write_report(report, out / "audits" / arm / scenario)
            claims = {e["claim_type"]: e["success"] for e in record.data["events"] if e["kind"] == "claim"}
            pair[arm] = {"execution_conclusion": claims["conclusion"], "task_completion": claims["completion"],
                         "stages": report["acceptance_observations"]["stages"],
                         "checks": {c["check_id"]: c["status"] for c in report["checks"]}}
        comparison = compare(records["before/" + scenario], records["after/" + scenario])
        require(comparison["comparable"], "Archive pair no longer comparable: " + scenario)
        write_json(out / "comparisons" / (scenario + ".json"), comparison)
        results[scenario] = pair
    bug = results["no-log-failure"]
    require(bug["before"]["checks"]["error_continuation"] == "finding", "Expected baseline finding missing")
    require(bug["before"]["execution_conclusion"] and not bug["after"]["execution_conclusion"], "Exit-status archive regression")
    require(not bug["before"]["task_completion"] and not bug["after"]["task_completion"], "Failure must remain a failed task")
    for arm in PHASES:
        require(results["healthy"][arm]["task_completion"], "Healthy archive no longer passes")
        require(all(v == "met" for v in results["healthy"][arm]["stages"].values()), "Healthy quality regression")
        require(results["ended-not-converged"][arm]["execution_conclusion"] and
                not results["ended-not-converged"][arm]["task_completion"], "End must not mean task success")
        require(results["ended-not-converged"][arm]["stages"]["numerical"] == "not_met" and
                results["ended-not-converged"][arm]["stages"]["reference"] == "not_met", "Quality negative control missing")
    return results


def apply_patches(out, loader):
    upstream = ROOT / "integration/foam_agent_v210/upstream"
    expected = json.loads((ROOT / "integration/foam_agent_v210/patched/manifest.json").read_text())
    require(expected["base_commit"] == loader.PIN, "Patch baseline commit changed")
    sandbox = out / "patch-sandbox"
    (sandbox / "src/services").mkdir(parents=True)
    baseline = out / "baseline-source"
    baseline.mkdir()
    for name, destination in (("utils.py", "src/utils.py"), ("run_local.py", "src/services/run_local.py")):
        require(digest(upstream / name) == loader.HASHES[name], "Pinned upstream changed: " + name)
        shutil.copyfile(upstream / name, sandbox / destination)
        shutil.copyfile(upstream / name, baseline / name)
    shutil.copyfile(upstream / "LICENSE", sandbox / "LICENSE")
    shutil.copyfile(upstream / "LICENSE", baseline / "LICENSE")
    patches = out / "patches"
    patches.mkdir()
    commands = []
    for name in PATCHES:
        source = ROOT / "patches/foam-agent-v2.1.0" / name
        require(digest(source) == expected["patches"][name], "Existing patch bytes changed: " + name)
        shutil.copyfile(source, patches / name)
        for options in (("--check",), ()):
            command = ["git", "apply", *options, str(patches / name)]
            completed = subprocess.run(command, cwd=sandbox, capture_output=True, text=True, timeout=20)
            commands.append({"command": command, "exit_code": completed.returncode,
                             "stdout": completed.stdout, "stderr": completed.stderr})
            require(completed.returncode == 0, "Patch application failed: " + completed.stderr)
    for name, relative in (("utils.py", "src/utils.py"), ("run_local.py", "src/services/run_local.py"),
                           ("verified_results.py", "src/services/verified_results.py")):
        require(digest(sandbox / relative) == expected["source_files"][name], "Applied patch differs from tested snapshot: " + name)
    write_json(out / "patch-application.json", {"base_commit": loader.PIN, "commands": commands,
               "applied_snapshot_matches_existing_candidate": True})
    return baseline, sandbox, expected["source_files"]


def shell_run(out, loader, arm, source, hashes, name, body):
    destination = out / "live-shell" / name / arm
    destination.mkdir(parents=True)
    captures = []

    class ObservedProcess(subprocess.Popen):
        def communicate(self, *args, **kwargs):
            result = super().communicate(*args, **kwargs)
            captures.append({"exit_code": self.returncode})
            return result

    proxy = SimpleNamespace(Popen=ObservedProcess, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
                            TimeoutExpired=subprocess.TimeoutExpired)
    ns = dict(os=os, re=re, shutil=shutil, signal=signal, subprocess=proxy, List=List)
    loader.load_functions(source / "utils.py", ["remove_files", "remove_file", "remove_numeric_folders",
                          "run_command", "check_foam_errors"], ns, hashes)
    runner = source / ("run_local.py" if arm == "before" else "services/run_local.py")
    loader.load_functions(runner, ["run_allrun_and_collect_errors"], ns, hashes)
    transcript = io.StringIO()
    # Upstream constructs an unquoted shell command. Keep its function unchanged
    # and execute this shell-only fixture in a private path without spaces, so
    # an installed user's chosen output path may contain spaces.
    with tempfile.TemporaryDirectory(prefix="simclinic-shell-", dir="/tmp") as scratch:
        case = Path(scratch) / "case"
        case.mkdir()
        environment = Path(scratch) / "environment"
        (environment / "etc").mkdir(parents=True)
        (environment / "etc/bashrc").write_text(": # shell-only fixture; no OpenFOAM initialized\n")
        (case / "Allrun").write_text("#!/bin/bash\n" + body + "\n")
        old = os.environ.get("WM_PROJECT_DIR")
        try:
            os.environ["WM_PROJECT_DIR"] = str(environment)
            with contextlib.redirect_stdout(transcript):
                errors = ns["run_allrun_and_collect_errors"](str(case), timeout=5, max_retries=1)
        finally:
            if old is None:
                os.environ.pop("WM_PROJECT_DIR", None)
            else:
                os.environ["WM_PROJECT_DIR"] = old
        for path in case.iterdir():
            shutil.copyfile(path, destination / path.name)
    (destination / "transcript.txt").write_text(transcript.getvalue())
    require(len(captures) == 1, "Expected one real shell process per regression")
    receipt = {"kind": "live-shell-fault-injection" if name == "exit7" else "live-shell-control",
               "actual_exit_code": captures[0]["exit_code"], "reported_execution_success": not errors,
               "errors": errors, "new_model_calls": 0, "new_cfd_runs": 0, "full_agent_workflow": False,
               "environment": "empty bashrc fixture; isolated original function bodies"}
    write_json(destination / "receipt.json", receipt)
    return receipt


def live_regression(out, loader, baseline, sandbox, hashes):
    result = {}
    for name, body in (("exit7", "exit 7"), ("exit0", "printf 'End\\n' > log.test\nexit 0")):
        result[name] = {arm: shell_run(out, loader, arm, source, pin, name, body)
                       for arm, source, pin in (("before", baseline, loader.HASHES), ("after", sandbox / "src", hashes))}
    require(all(result["exit7"][arm]["actual_exit_code"] == 7 for arm in PHASES), "Exit-7 fault was not reproduced")
    require(result["exit7"]["before"]["reported_execution_success"] and
            not result["exit7"]["after"]["reported_execution_success"], "Applied patch failed to correct execution status")
    require(all(result["exit0"][arm]["actual_exit_code"] == 0 and
                result["exit0"][arm]["reported_execution_success"] for arm in PHASES), "Normal shell control regression")
    return result


def guard_regression(out, sandbox):
    spec = importlib.util.spec_from_file_location("showcase_applied_result_interface", sandbox / "src/services/verified_results.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    case = out / "archives/after/healthy"
    path = case / ".simclinic/record.json"
    record = load_record(path, case)
    artifact = next(a["id"] for a in record.data["artifacts"] if a["path"] == ".simclinic/acceptance.json")
    attempts = []
    for label, run, version, expected in (
        ("current", record.data["run_id"], record.data["input_version"], None),
        ("old-run", "different-caller-run", record.data["input_version"], "stale_run"),
        ("old-input", record.data["run_id"], "different-caller-input", "stale_input"),
        ("unknown", None, None, "unknown_expected_identity")):
        try:
            value = tool.read_result_json(path, case, artifact, expected_run_id=run, expected_input_version=version)
            observed = None
            attempt = {"context": label, "allowed": True, "profile_error": value["values"]["profile_error"]}
        except ResultRejected as exc:
            observed = exc.code
            attempt = {"context": label, "allowed": False, "error": exc.code}
        require(observed == expected, "Applied result interface regression: " + label)
        attempts.append(attempt)
    write_json(out / "result-guard.json", {"scope": "actual archived CFD result; deliberately altered caller identities",
               "native_graph_reexecuted": False, "attempts": attempts})
    return attempts


def render(out, summary):
    live = summary["live_shell"]
    rows = "".join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
        html.escape(a["context"]), "允许" if a["allowed"] else "拒绝",
        html.escape(a.get("error", "身份与内容哈希均通过")),
        "{:.3%}".format(a["profile_error"]) if a["allowed"] else "未消费结果") for a in summary["result_guard"])
    links = "".join(f'<li><a href="audits/{arm}/{name}/report.md">{arm} / {name} 体检报告</a> · '
                    f'<a href="archives/{arm}/{name}/.simclinic/record.json">原始记录副本</a></li>'
                    for name in SCENARIOS for arm in PHASES)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SimClinic · 首版完整演示</title><style>
body{margin:0;background:#f3f6f8;color:#182d37;font:16px/1.75 system-ui,-apple-system,sans-serif}
main{max-width:1040px;margin:auto;padding:48px 24px}h1{font-size:42px;line-height:1.2;margin:14px 0}h2{font-size:26px}
.tag{color:#096b62;font-weight:700;letter-spacing:.08em}.lead{font-size:19px;color:#45606b}
section{background:white;border:1px solid #dce5e8;border-radius:16px;padding:26px;margin:24px 0}
.flow{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.step{border-top:3px solid #138b80;padding:16px;background:#f6faf9}
.bad{color:#b23c33}.good{color:#096b62}small,.muted{color:#58717a}code{font-family:ui-monospace,monospace;background:#edf2f4;padding:2px 5px;border-radius:4px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 8px;border-bottom:1px solid #dce5e8}a{color:#006b8f;text-underline-offset:3px}
@media(max-width:700px){.flow{grid-template-columns:1fr}main{padding:24px 16px}h1{font-size:32px}section{padding:20px}table{font-size:13px}}
</style><main><div class="tag">SIMCLINIC / FIRST RELEASE DEMO</div><h1>发现问题 → 应用补丁 → 复测</h1>
<p class="lead">一次可运行的工具修复，配合真实 CFD 归档与结果身份门禁，展示仿真 Agent 的诊断闭环。</p>
<p>本次演示已通过 · 4 个现场 Shell 进程 · 6 份历史记录 · 2 个实际应用的补丁 · 新增模型与 CFD 调用均为 0</p>
<section id="exit"><small>01 / 执行状态修复 · 现场运行</small><h2>进程失败，工具却报告成功</h2><div class="flow">
<div class="step"><b>发现问题</b><p>现场执行 <code>Allrun: exit 7</code>，不生成求解日志。原工具返回空错误列表。</p><strong class="bad">实际退出 7 → 错误报告成功</strong></div>
<div class="step"><b>应用补丁</b><p>在新沙箱实际执行 <code>git apply</code>。补丁 0001 将真实退出码、超时与未知状态纳入错误判断。</p><a href="patches/0001-execution-status.patch">查看补丁</a> · <a href="patch-application.json">应用回执</a></div>
<div class="step"><b>同条件复测</b><p>再次执行相同 exit 7；新工具正确报告失败。exit 0 正常对照两版均成功。</p><strong class="good">实际退出 7 → 正确报告失败</strong></div></div>
<p class="muted">这是明确的 Shell 故障注入及原函数隔离执行。修复的是错误成功结论，失败任务仍然失败；未启动整个模型 Agent 或新求解器。</p>
<p><a href="live-shell/exit7/before/receipt.json">现场基线</a> · <a href="live-shell/exit7/after/receipt.json">现场复测</a> · <a href="comparisons/no-log-failure.json">历史真实执行对照</a></p></section>
<section id="guard"><small>02 / 结果接口修复 · 现场调用</small><h2>当前结果可读，旧身份被拒绝</h2><p>实际应用补丁 0002，并调用新接口读取正常 CFD 的已注册验收文件。正确上下文来自调用者；负例刻意改变调用者的运行或输入身份。</p>
<table><thead><tr><th>调用上下文</th><th>结果</th><th>依据</th><th>读取</th></tr></thead><tbody>GUARD_ROWS</tbody></table>
<p><a href="patches/0002-verified-result-interface.patch">查看补丁</a> · <a href="result-guard.json">现场接口回执</a></p>
<p class="muted">真实结果文件 + 身份故障注入。没有声称自然发生了旧结果事故；单独应用接口仍需接入目标 Agent 的实际工具入口。</p></section>
<section id="quality"><small>03 / 质量对照 · 历史归档重读</small><h2>成功退出，还要满足数值和参考条件</h2>
<table><thead><tr><th>案例</th><th>执行</th><th>数值</th><th>解析参考</th><th>任务</th></tr></thead><tbody>
<tr><td>正常通道 · 两版</td><td>通过</td><td>通过</td><td>通过</td><td class="good">完成</td></tr>
<tr><td>仅一次迭代 · 两版</td><td>退出成功 / End</td><td class="bad">未通过</td><td class="bad">未通过</td><td>未完成</td></tr>
<tr><td>exit 7 · 补丁后</td><td class="bad">失败</td><td>未知</td><td>未知</td><td>未完成</td></tr></tbody></table>
<p>固定 OpenFOAM Foundation 10 / simpleFoam 小型 Poiseuille 通道。正常归档速度误差 0.699%、压力梯度误差 0.394%，原质量门槛保持。这里只重读已有验收观测和证据，未重新求解或独立复算物理结果。</p>
<p><a href="comparisons/healthy.json">正常配对比较</a> · <a href="comparisons/ended-not-converged.json">提前结束配对比较</a></p></section>
<section><h2>首版状态与缺口</h2><p>当前可以交付本地诊断与修复演示。安装、使用步骤和当前缺口见FIRST-RELEASE.md；新用户从原始日志到统一记录的采集入口仍需完善。</p>
<p>自然恢复收益、稳定节省、整体 Agent 优化及跨 Agent / 跨物理机制适用性尚未证明。网页、MCP 和自动调参留待后续；预算核验优化暂不扩大。</p>
<a href="FIRST-RELEASE.md">交付范围、缺口与验收</a> · <a href="SHOWCASE.md">3分钟讲解稿</a> · <a href="summary.json">本次机器可读结果</a></section>
<section><h2>证据入口</h2><ul>EVIDENCE_LINKS</ul><p class="muted">归档来自2026年10月4日，副本仅包含所选记录及其注册证据。本次读取逐文件核对已有身份/哈希，未扩大到全项目核验。</p></section></main></html>'''
    page = page.replace("GUARD_ROWS", rows).replace("EVIDENCE_LINKS", links)
    (out / "index.html").write_text(page, encoding="utf-8")
    markdown = f'''# SimClinic 首版展示稿（约3分钟）

打开 index.html，按三个环节讲解；summary.json 的 status 必须为 pass。

1. **发现问题（45秒）**：Allrun 真正退出7，原工具却返回空错误列表。历史体检定位到真实失败事件与成功结论的冲突；它没有把任务本身判为完成。
2. **应用补丁与复测（45秒）**：打开0001补丁和应用回执。现场运行基线：exit={live['exit7']['before']['actual_exit_code']}、报告成功；补丁后：exit={live['exit7']['after']['actual_exit_code']}、报告失败。正常exit 0对照仍成功。强调失败任务没有被修好，错误成功结论被修好。
3. **旧结果保护（45秒）**：0002也已实际应用。读取真实CFD验收文件，当前身份允许、不同run/input及未知身份拒绝。这是主动设计的身份负例，不是自然事故。
4. **质量与边界（45秒）**：正常通道历史归档两版均达标；提前结束虽有End，数值与参考不达标。打开FIRST-RELEASE.md说明首版范围和缺口。

本次只有四个新Shell进程和结果接口调用；模型、CFD、采样、渲染新增调用均为0。归档重读不等于重新执行历史实验。不要宣称总体优化、自然恢复收益或稳定节省已证明。

输出目录中的 archives、audits、comparisons、patches、live-shell、patch-sandbox 分别提供原证据副本、诊断、比较、实际补丁、现场回执与修改后的源码。
'''
    (out / "SHOWCASE.md").write_text(markdown, encoding="utf-8")
    shutil.copyfile(ROOT / "docs/first-release-scope.md", out / "FIRST-RELEASE.md")


def run(out):
    out = out.expanduser().resolve()
    if out.is_relative_to(ROOT):
        require(out.is_relative_to(ROOT / "demo-output") and out != ROOT / "demo-output",
                "Repository outputs must be a new child of demo-output; source/archive directories are read-only")
    require(not out.exists(), "Output already exists; choose a new --out directory")
    for tool in ("git", "bash"):
        require(shutil.which(tool) is not None, "Missing dependency: " + tool)
    loader = load_loader()
    records, payloads, sources = read_archives(ROOT)
    out.mkdir(parents=True)
    try:
        print("[1/4] 重读六份真实归档与注册证据，生成问题定位及质量对照。", flush=True)
        archives = archive_regression(out, records, payloads)
        print("[2/4] 在新沙箱实际应用0001/0002补丁。", flush=True)
        baseline, sandbox, hashes = apply_patches(out, loader)
        print("[3/4] 现场复现exit 7并复测；运行exit 0正常对照。", flush=True)
        live = live_regression(out, loader, baseline, sandbox, hashes)
        print("[4/4] 调用已应用的结果身份接口，生成展示报告。", flush=True)
        guard = guard_regression(out, sandbox)
        summary = {"status": "pass", "scope": "first-release local showcase", "archives": archives,
                   "archive_sources": sources, "live_shell": live, "result_guard": guard,
                   "applied_patches": list(PATCHES), "new_shell_processes": 4,
                   "new_model_calls": 0, "new_cfd_runs": 0, "new_sampling_calls": 0, "new_render_calls": 0,
                   "natural_recovery_benefit": "无法判断", "overall_optimization_proven": False,
                   "historical_sources_modified": False}
        render(out, summary)
        write_json(out / "summary.json", summary)
    except Exception as exc:
        write_json(out / "summary.json", {"status": "failed", "error": str(exc)})
        raise
    print("PASS · 展示报告：" + str(out / "index.html"))
    print("发现：exit 7却报告成功 → 实际应用补丁 → 同条件复测正确报告失败。")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New output directory; default: demo-output/first-release-<timestamp>")
    args = parser.parse_args(argv)
    out = args.out or ROOT / "demo-output" / ("first-release-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    try:
        run(out)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("first-release-demo: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
