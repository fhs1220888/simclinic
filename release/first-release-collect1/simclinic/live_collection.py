"""Portable entry for the existing Collector + pinned execution-service chain.

Only the existing fixed channel inputs and two existing shell controls are
supported. The full planner/reviewer workflow is outside this entry's scope.
"""
import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from simclinic.collection import Collector, digest
from simclinic.core.record import load_record
from simclinic.core.report import write_json, write_report
from simclinic.domains.openfoam.acceptance import channel_criteria, pressure_gradient_error, profile_error, solver_measurements
from simclinic.domains.openfoam.channel import create_channel, SPEC

DATA = Path(__file__).resolve().parent / "demo_data"
SHELLS = {"no-log-failure": "#!/bin/bash\nexit 7\n",
          "execution-ok": "#!/bin/bash\nprintf 'End\\n' > log.test\nexit 0\n"}
NO_LOG = "#!/bin/bash\n# Intentional shell failure without solver logs: execution-status test.\nexit 7\n"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def channel_inputs(directory, ended=False):
    case = create_channel(directory)
    if ended:
        path = case / "system/controlDict"
        path.write_text(path.read_text().replace("endTime 2000", "endTime 1").replace("writeInterval 2000", "writeInterval 1"))
    return {str(p.relative_to(case)): p.read_bytes() for p in case.rglob("*") if p.is_file()}


def prepare_inputs(source, example, scratch):
    if example in SHELLS:
        return {"Allrun": SHELLS[example].encode()}, "shell-control"
    expected = channel_inputs(scratch / "reference")
    ended = channel_inputs(scratch / "ended-reference", ended=True)
    if source is None:
        return (ended if example == "ended-not-converged" else expected), "fixed-channel"
    source = source.expanduser().resolve()
    require(source.is_dir(), "Source case does not exist")
    payloads = {}
    for relative in expected:
        path = source / relative
        require(all(not p.is_symlink() for p in [path, *path.parents] if p != source.parent),
                "Input symlinks are not supported: " + relative)
        require(path.is_file(), "Missing fixed-case input: " + relative)
        raw = path.read_bytes()
        allowed = (expected[relative], ended[relative])
        if relative == "Allrun":
            allowed += (NO_LOG.encode(), SHELLS["no-log-failure"].encode())
        require(raw in allowed, "Input differs from the fixed channel profile: " + relative)
        payloads[relative] = raw
    # Old meshes, time directories, logs and .simclinic are deliberately not
    # copied or assigned current identities. Allrun's inputs are this fixed list.
    return payloads, "fixed-channel"


def prepare_runtime(out, variant):
    spec = importlib.util.spec_from_file_location("collection_execution_loader", DATA / "integration/foam_agent_v210/execute.py")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    original = DATA / "integration/foam_agent_v210/upstream"
    for name, expected in loader.HASHES.items():
        require(sha(original / name) == expected, "Pinned upstream changed: " + name)
    runtime = out / "runtime"
    runtime.mkdir()
    shutil.copyfile(DATA / "integration/foam_agent_v210/execute.py", runtime / "execute.py")
    if variant == "patched":
        from simclinic import _showcase_impl as demo
        previous = demo.ROOT
        try:
            demo.ROOT = DATA
            _, sandbox, hashes = demo.apply_patches(out, loader)
        finally:
            demo.ROOT = previous
        target = runtime / "patched"
        target.mkdir()
        for name, relative in (("utils.py", "utils.py"), ("run_local.py", "services/run_local.py"),
                               ("verified_results.py", "services/verified_results.py")):
            shutil.copyfile(sandbox / "src" / relative, target / name)
        shutil.copyfile(DATA / "integration/foam_agent_v210/patched/manifest.json", target / "manifest.json")
    else:
        target = runtime / "upstream"
        target.mkdir()
        hashes = loader.HASHES
        for name in ("utils.py", "run_local.py"):
            shutil.copyfile(original / name, target / name)
    for name in ("logger.py", "local_runner_node.py"):
        shutil.copyfile(original / name, target / name)
        require(sha(target / name) == hashes[name], "Execution source changed: " + name)
    shutil.copyfile(original / "LICENSE", target / "LICENSE")
    write_json(out / "source-manifest.json", {"base_commit": loader.PIN, "variant": variant,
               "source_files": {name: sha(target / name) for name in hashes if (target / name).is_file()},
               "loader_sha256": sha(runtime / "execute.py")})
    return runtime / "execute.py"


def environment_for(profile):
    tools = ("bash",) if profile == "shell-control" else ("bash", "blockMesh", "checkMesh", "simpleFoam")
    environment = dict(profile=profile, platform=platform.platform(), architecture=platform.machine(),
                       tools={}, model_calls=0, full_agent_workflow=False)
    for name in tools:
        path = shutil.which(name)
        require(path is not None, "Missing dependency: " + name)
        environment["tools"][name] = {"path": path, "sha256": sha(Path(path))}
    if profile == "fixed-channel":
        require(os.environ.get("WM_PROJECT") == "OpenFOAM" and os.environ.get("WM_PROJECT_VERSION") == "10",
                "Source your existing Foundation OpenFOAM 10 environment first")
        project = os.environ.get("WM_PROJECT_DIR")
        require(project is not None, "WM_PROJECT_DIR is not set")
        bashrc = Path(project) / "etc/bashrc"
        require(bashrc.is_file(), "OpenFOAM bashrc is missing")
        environment.update(distribution="OpenFOAM Foundation", version="10", solver="simpleFoam",
                           regime="steady-incompressible-single-phase", threads=1,
                           wm_options=os.environ.get("WM_OPTIONS"), bashrc_sha256=sha(bashrc))
    environment["runtime_digest"] = digest(environment)
    return environment


def observe_execution(execute, case, variant):
    """Invoke the unchanged pinned loader; clean up its process group on abort."""
    spec = importlib.util.spec_from_file_location("live_pinned_execution", execute)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    processes = []

    class ManagedProcess(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            processes.append(self)

    module.subprocess = SimpleNamespace(Popen=ManagedProcess, PIPE=subprocess.PIPE,
                                       DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
    previous = sys.argv
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        sys.argv = [str(execute), str(case), "--variant", variant]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            module.main()
    finally:
        sys.argv = previous
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
        (case / ".simclinic/service.stdout.log").write_text(stdout.getvalue())
        (case / ".simclinic/service.stderr.log").write_text(stderr.getvalue())


def run_chain(collector, execute, variant, profile):
    """Same observation boundaries and channel assessment as run_case.py."""
    case, task = collector.case, collector.data["task"]
    if profile == "fixed-channel":
        check = collector.event("tool", operation="validate_case", phase="preflight", arguments={},
            outcome="pass", exit_code=0, input_fingerprint=digest(collector.data["input_manifest"]),
            error_category=None, validation="Foundation10 environment and exact fixed-profile input bytes")
        collector.event("decision", handles=[check["id"]], action="allow_mesh")
        mesh = collector.tool("blockMesh", "other", ["blockMesh"])
        require(mesh["outcome"] == "pass", "blockMesh failed; captured evidence retained")
        check = collector.tool("checkMesh", "preflight", ["checkMesh", "-allTopology", "-allGeometry"],
            lambda text: "pass" if "Mesh OK." in text else "fail" if "Failed" in text else "unknown")
        collector.event("decision", handles=[check["id"]], action="allow_solve" if check["outcome"] == "pass" else "stop")
        require(check["outcome"] == "pass", "checkMesh did not pass; captured evidence retained")
    collector.event("observation", observation_type="execution_service_start", variant=variant)
    observe_execution(execute, case, variant)
    upstream_path = case / ".simclinic/upstream.json"
    require(upstream_path.is_file(), "Execution collector did not finish; captured evidence retained")
    upstream = json.loads(upstream_path.read_text())
    require(len(upstream["processes"]) == 1, "Expected one observed Allrun attempt")
    code = upstream["processes"][0]["returncode"]
    evidence = collector.artifact(upstream_path)
    collector.event("tool", operation="Allrun", phase="solve", arguments={"script": "Allrun"},
        exit_code=code, outcome="fail" if code or upstream["errors"] else "pass",
        input_fingerprint=digest(collector.data["input_manifest"]),
        error_category="nonzero_exit" if code else "openfoam_error" if upstream["errors"] else None,
        result_artifact=evidence, captured_by="existing upstream Popen.communicate observer")
    for name in ("Allrun.out", "Allrun.err", "review.log", ".simclinic/service.stdout.log", ".simclinic/service.stderr.log"):
        if (case / name).is_file():
            collector.artifact(case / name)
    workflow = collector.artifact(case / "workflow.log")
    collector.event("claim", claim_type="conclusion", success=not upstream["errors"],
                    text="Pinned local_runner_node execution verdict", artifact_id=workflow)
    if profile == "fixed-channel":
        collector.data["metrics"]["solver_seconds"] = upstream["processes"][0]["elapsed_seconds"]
        values = {c["id"]: None for c in task["acceptance"]}
        sources = {}
        log = case / "log.simpleFoam"
        if log.is_file():
            source = collector.artifact(log)
            values.update(solver_measurements(log.read_text(), code))
            sources.update({key: source for key in ("execution_end", "steady_converged", "local_continuity")})
            if code == 0:
                sampled = collector.tool("sample", "postprocess", ["simpleFoam", "-postProcess", "-func", "sample", "-latestTime"])
                if sampled["outcome"] == "pass":
                    for key, name, calculate in (("profile_error", "profile.xy", profile_error),
                                                  ("pressure_gradient_error", "pressure.xy", pressure_gradient_error)):
                        files = sorted((case / "postProcessing").rglob(name))
                        if len(files) == 1:
                            sources[key] = collector.artifact(files[0])
                            values[key] = calculate(files[0])
        else:
            values["execution_end"] = False
    else:
        values, sources = {"execution_exit": code == 0}, {}
        if (case / "log.test").is_file():
            collector.artifact(case / "log.test")
    assessment = case / ".simclinic/acceptance.json"
    write_json(assessment, {"values": values, "profile": profile,
               "specification": SPEC if profile == "fixed-channel" else None,
               "methods": "Existing fixed channel assessment" if profile == "fixed-channel" else "Actual observed Allrun returncode equals zero"})
    assessment_id = collector.artifact(assessment)
    for criterion in task["acceptance"]:
        key = criterion["id"]
        source = sources.get(key, assessment_id)
        collector.event("reference", artifact_id=source)
        collector.event("measurement", criterion_id=key, value=values[key], artifact_id=source)
    if profile == "fixed-channel":
        from simclinic.checks.behavior import matches
        collector.event("claim", claim_type="completion", success=all(matches(values[c["id"]], c) is True for c in task["acceptance"]),
                        text="Existing scripted channel acceptance verdict, not a model-generated statement")
    # The shell control records only the native runner conclusion. It does not
    # manufacture an Agent completion node or numerical/reference measurements.
    collector.data["scope_terminal_observed"] = True
    collector.record()
    return upstream, values


def collect(out, variant="patched", source=None, example=None):
    out = out.expanduser().resolve()
    require(not out.exists(), "Output already exists; choose a new --out directory")
    if source is not None:
        root = source.expanduser().resolve()
        require(not out.is_relative_to(root), "Output must be outside the source case")
    require(source is not None or example is not None, "Choose --case or --example")
    require(variant in ("baseline", "patched"), "Unknown execution variant")
    require(example is None or example in (*SHELLS, "healthy", "ended-not-converged"), "Unknown example")
    require(variant != "patched" or shutil.which("git") is not None, "Patched collection requires git")
    # Keep pinned, unquoted upstream shell paths unchanged and execute in a
    # private safe path. Final outputs may contain spaces/non-ASCII characters.
    with tempfile.TemporaryDirectory(prefix="simclinic-collect-", dir="/tmp") as temporary:
        scratch = Path(temporary)
        inputs, profile = prepare_inputs(source, example, scratch)
        environment = environment_for(profile)
        out.mkdir(parents=True)
        collector = None
        case = scratch / "case"
        case.mkdir()
        for relative, raw in inputs.items():
            path = case / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        previous = os.environ.get("WM_PROJECT_DIR")
        bridge = scratch / "environment"
        if profile == "shell-control":
            (bridge / "etc").mkdir(parents=True)
            (bridge / "etc/bashrc").write_text(": # same shell-only control environment; no OpenFOAM initialization\n")
        else:
            # A short alias keeps the upstream shell command unchanged while
            # retaining the real installation tree used by native tools.
            bridge.symlink_to(Path(previous).resolve(), target_is_directory=True)
        try:
            execute = prepare_runtime(out, variant)
            task = dict(id="channel-poiseuille-v1", objective="固定低 Re 层流通道，满足执行、稳态和解析参考验收。",
                        required_preflight=["validate_case", "checkMesh"], acceptance=channel_criteria()) if profile == "fixed-channel" else dict(
                        id="pinned-execution-shell-control-v1", objective="已有Shell控制例的实际退出状态与固定Agent执行结论。",
                        required_preflight=[], acceptance=[dict(id="execution_exit", stage="execution", operator="eq", target=True, description="Allrun实际退出0；不声明CFD数值或参考质量")])
            collector = Collector(case, environment, task)
            collector.data["agent"]["execution_variant"] = variant
            collector.data["agent"]["source_manifest"] = json.loads((out / "source-manifest.json").read_text())
            collector.data["collection_scope"] = dict(profile=profile,
                tools="Fixed execution-service boundaries; explicit channel preflight/sampling when selected",
                decisions="Registered harness gates and actual pinned runner conclusion; no planner/reviewer",
                references="Results produced and consumed in this fresh case only",
                acceptance="Declared fixed profile only; shell controls have no numerical/reference acceptance")
            for relative, raw in inputs.items():
                snapshot = case / ".simclinic/input-snapshot" / relative
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_bytes(raw)
                collector.artifact(snapshot)
            ended = scratch / "ended-reference/system/controlDict"
            collector.data["fault_injection"] = example in ("no-log-failure", "ended-not-converged") or \
                inputs["Allrun"] in (NO_LOG.encode(), SHELLS["no-log-failure"].encode()) or \
                (ended.is_file() and inputs.get("system/controlDict") == ended.read_bytes())
            collector.save()
            os.environ["WM_PROJECT_DIR"] = str(bridge)
            upstream, values = run_chain(collector, execute, variant, profile)
        except BaseException as exc:
            if collector is not None:
                collector.data["coverage"] = dict(tools=False, decisions=False, references=False, acceptance=False)
                collector.data["collection_status"] = "aborted"
                collector.data["metrics"]["wall_seconds"] = time.monotonic() - collector.started
                collector.save()
            write_json(out / "collection-summary.json", {"status": "aborted", "error": str(exc), "type": type(exc).__name__, "coverage_complete": False})
            raise
        finally:
            if previous is None:
                os.environ.pop("WM_PROJECT_DIR", None)
            else:
                os.environ["WM_PROJECT_DIR"] = previous
            shutil.copytree(case, out / "case")
            if collector is not None:
                shutil.copyfile(out / "case/.simclinic/record.json", out / "record.json")
        from simclinic.cli import get_report
        record = load_record(out / "record.json", out / "case")
        report = get_report(record)
        write_report(report, out / "audit")
        summary = dict(status="complete", profile=profile, variant=variant,
            record_path=str(out / "record.json"), case_dir=str(out / "case"), audit_path=str(out / "audit/report.md"),
            actual_exit_code=upstream["processes"][0]["returncode"], reported_execution_success=not upstream["errors"],
            coverage=record.data["coverage"], audit_summary=report["summary"], acceptance_stages=report["acceptance_observations"]["stages"],
            full_agent_workflow=False, new_model_calls=0, new_cfd_runs=0 if profile == "shell-control" else None,
            interpretation="complete means this scoped collection finished, not that the task succeeded")
        write_json(out / "collection-summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
        return summary
