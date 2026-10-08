"""Real CFD + pinned execution-function integration; no model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from simclinic.advisor import candidates
from simclinic.checks import audit
from simclinic.collection import Collector, digest
from simclinic.core.report import write_json, write_report
from simclinic.domains.openfoam.acceptance import channel_criteria, profile_error, pressure_gradient_error, solver_measurements
from simclinic.domains.openfoam.channel import create_channel, SPEC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "patched"), default="baseline")
    args = parser.parse_args()
    if os.environ.get("WM_PROJECT_VERSION") != "10" or os.environ.get("WM_PROJECT") != "OpenFOAM":
        raise ValueError("Requires sourced Foundation OpenFOAM 10")
    environment = json.loads(args.environment.read_text())
    environment.update(distribution="OpenFOAM Foundation", version="10", solver="simpleFoam",
                       regime="steady-incompressible-single-phase", threads=1,
                       wm_options=os.environ.get("WM_OPTIONS"))
    environment["runtime_digest"] = digest(environment)
    summaries = []
    for scenario in ("healthy", "ended-not-converged", "no-log-failure"):
        case = create_channel(args.out / scenario / "case")
        if scenario == "ended-not-converged":
            path = case / "system/controlDict"
            path.write_text(path.read_text().replace("endTime 2000", "endTime 1").replace("writeInterval 2000", "writeInterval 1"))
        if scenario == "no-log-failure":
            (case / "Allrun").write_text("#!/bin/bash\n# Intentional shell failure without solver logs: execution-status test.\nexit 7\n")
        task = dict(id="channel-poiseuille-v1", objective="固定低 Re 层流通道，满足执行、稳态和解析参考验收。",
                    required_preflight=["validate_case", "checkMesh"], acceptance=channel_criteria())
        collector = Collector(case, environment, task)
        collector.data["benchmark_spec"] = SPEC
        collector.data["agent"]["execution_variant"] = args.variant
        if args.variant == "patched":
            collector.data["agent"]["candidate_patch_manifest"] = json.loads((Path(__file__).resolve().parent / "patched/manifest.json").read_text())
        collector.data["fault_injection"] = None if scenario == "healthy" else scenario
        # Explicit static/environment check performed by this harness.
        check = collector.event("tool", operation="validate_case", phase="preflight", arguments={},
            outcome="pass", exit_code=0, input_fingerprint=digest(collector.data["input_manifest"]),
            error_category=None, validation="version guard and generated benchmark inputs")
        collector.event("decision", handles=[check["id"]], action="allow_mesh")
        mesh = collector.tool("blockMesh", "other", ["blockMesh"])
        if mesh["outcome"] != "pass":
            raise ValueError("blockMesh failed; inspect evidence in " + str(case))
        check = collector.tool("checkMesh", "preflight", ["checkMesh", "-allTopology", "-allGeometry"],
            lambda text: "pass" if "Mesh OK." in text else ("fail" if "Failed" in text else "unknown"))
        collector.event("decision", handles=[check["id"]], action="allow_solve" if check["outcome"] == "pass" else "stop")
        if check["outcome"] != "pass":
            raise ValueError("checkMesh not passed; inspect " + str(case))
        upstream_call = collector.tool("Foam-Agent execution service", "other",
            [sys.executable, "/workspace/integration/foam_agent_v210/execute.py", str(case), "--variant", args.variant])
        upstream_path = case / ".simclinic/upstream.json"
        if not upstream_path.exists() or upstream_call["exit_code"] != 0:
            raise ValueError("Upstream harness failed; inspect captured stderr")
        upstream = json.loads(upstream_path.read_text())
        evidence_id = collector.artifact(upstream_path)
        if len(upstream["processes"]) != 1:
            raise ValueError("Expected exactly one actual Allrun process")
        code = upstream["processes"][0]["returncode"]
        collector.event("tool", operation="Allrun", phase="solve", arguments={"script": "Allrun"},
            outcome="fail" if code or upstream["errors"] else "pass", exit_code=code,
            input_fingerprint=digest(collector.data["input_manifest"]),
            error_category="nonzero_exit" if code else ("openfoam_error" if upstream["errors"] else None),
            result_artifact=evidence_id, captured_by="upstream Popen.communicate observation")
        collector.data["metrics"]["solver_seconds"] = upstream["processes"][0]["elapsed_seconds"]
        workflow_id = collector.artifact(case / "workflow.log")
        # Native runner text represents an execution conclusion, not CFD completion.
        collector.event("claim", claim_type="conclusion", success=not upstream["errors"],
                        text="Pinned local_runner_node execution verdict", artifact_id=workflow_id)
        values = {criterion["id"]: None for criterion in task["acceptance"]}
        measure_sources = {}
        log_path = case / "log.simpleFoam"
        if log_path.exists():
            log_id = collector.artifact(log_path)
            values.update(solver_measurements(log_path.read_text(), code))
            measure_sources.update({key: log_id for key in values if key in ("execution_end", "steady_converged", "local_continuity")})
            if code == 0:
                post = collector.tool("sample", "postprocess", ["simpleFoam", "-postProcess", "-func", "sample", "-latestTime"])
                if post["outcome"] == "pass":
                    for key, pattern, calculate in (("profile_error", "profile.xy", profile_error),
                                                     ("pressure_gradient_error", "pressure.xy", pressure_gradient_error)):
                        files = sorted((case / "postProcessing").rglob(pattern))
                        if len(files) == 1:
                            measure_sources[key] = collector.artifact(files[0])
                            values[key] = calculate(files[0])
        else:
            values["execution_end"] = False
        acceptance_path = case / ".simclinic/acceptance.json"
        write_json(acceptance_path, dict(values=values, specification=SPEC, methods={
            "profile": "relative L2 against u=6 Umean (y/H)(1-y/H), including Uy/Uz",
            "pressure": "least-squares dp/dx against -12 nu Umean/H^2; kinematic pressure",
            "numerical": "SIMPLE residualControl marker plus final local continuity"}))
        acceptance_id = collector.artifact(acceptance_path)
        for criterion in task["acceptance"]:
            key = criterion["id"]
            source = measure_sources.get(key, acceptance_id)
            collector.event("reference", artifact_id=source)
            collector.event("measurement", criterion_id=key, value=values[key], artifact_id=source)
        from simclinic.checks.behavior import matches
        passed = all(matches(values[c["id"]], c) is True for c in task["acceptance"])
        collector.event("claim", claim_type="completion", success=passed,
                        text="Scripted harness acceptance verdict, not a model-generated statement")
        record = collector.record()
        report = audit(record)
        report["candidates"] = candidates(report)
        write_report(report, case.parent / "audit")
        summaries.append(dict(scenario=scenario, values=values, accepted=passed, checks=report["summary"],
                              execution_variant=args.variant,
                              acceptance_stages=report["acceptance_observations"]["stages"],
                              record_path=str(case / ".simclinic/record.json"), full_agent_workflow=False))
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
    write_json(args.out / "summary.json", dict(synthetic=False, environment=environment, runs=summaries,
               scope="real CFD plus pinned isolated Foam-Agent execution functions; no model workflow"))


if __name__ == "__main__":
    main()
