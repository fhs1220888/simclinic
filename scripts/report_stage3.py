"""Verify local candidate results against saved real baselines and exercise the interface."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.evaluation import compare
from simclinic.result_guard import ResultRejected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, default=ROOT / "real-runs/stage2-final-20261004")
    parser.add_argument("--after", type=Path, default=ROOT / "real-runs/stage3-patched-20261004")
    parser.add_argument("--out", type=Path, default=ROOT / "demo-output/stage3")
    args = parser.parse_args()
    changes = []
    for scenario in ("healthy", "ended-not-converged", "no-log-failure"):
        before_case = args.before / scenario / "case"
        after_case = args.after / scenario / "case"
        before = load_record(before_case / ".simclinic/record.json", before_case)
        after = load_record(after_case / ".simclinic/record.json", after_case)
        result = compare(before, after)
        if not result["comparable"]:
            raise ValueError(f"Incomparable {scenario}: {result['incomparability_reasons']}")
        baseline_claim = next(e for e in before.data["events"] if e["kind"] == "claim" and e["claim_type"] == "conclusion")
        candidate_claim = next(e for e in after.data["events"] if e["kind"] == "claim" and e["claim_type"] == "conclusion")
        result.update(scenario=scenario, execution_conclusion_before=baseline_claim["success"],
                      execution_conclusion_after=candidate_claim["success"])
        write_json(args.out / scenario / "comparison.json", result)
        changes.append(dict(scenario=scenario, comparable=True,
                           conclusion_before=baseline_claim["success"], conclusion_after=candidate_claim["success"],
                           check_changes=result["check_changes"]))
    # Load the exact new-file candidate interface from the generated patch snapshot.
    folder = ROOT / "integration/foam_agent_v210/patched"
    manifest = json.loads((folder / "manifest.json").read_text())
    source = folder / "verified_results.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["source_files"][source.name]:
        raise ValueError("Result interface source changed")
    spec = importlib.util.spec_from_file_location("stage3_candidate_result_tool", source)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    case = args.after / "healthy/case"
    path = case / ".simclinic/record.json"
    record = load_record(path, case)
    artifact_id = next(a["id"] for a in record.data["artifacts"] if a["path"] == ".simclinic/acceptance.json")
    # Negative cases deliberately change caller expectations, not the original file
    # or producer identity. They are contract fault injections, not new CFD solves.
    attempts = []
    for name, run_id, version in (("current_result", record.data["run_id"], record.data["input_version"]),
            ("other_run", uuid.uuid4().hex, record.data["input_version"]),
            ("other_input", record.data["run_id"], "sha256:" + hashlib.sha256(b"deliberately different caller input").hexdigest()),
            ("unknown_context", None, record.data["input_version"])):
        try:
            result = tool.read_result_json(path, case, artifact_id, expected_run_id=run_id, expected_input_version=version)
            attempts.append(dict(name=name, allowed=True, consumed=True, error=None,
                                 read_value=result["values"]["profile_error"]))
        except ResultRejected as exc:
            attempts.append(dict(name=name, allowed=False, consumed=False, error=exc.code))
    expected = [(True, None), (False, "stale_run"), (False, "stale_input"), (False, "unknown_expected_identity")]
    if [(a["allowed"], a["error"]) for a in attempts] != expected:
        raise ValueError("Guard contract regression")
    write_json(args.out / "result-guard.json", dict(synthetic=False,
        scope="candidate interface calls on actual CFD results; altered expected identities are deliberate contract fault injections",
        producer_record=str(path.resolve()), artifact_id=artifact_id, attempts=attempts,
        native_postprocessing_tools_wired=False))
    write_json(args.out / "summary.json", dict(comparisons=changes, result_guard=attempts,
        full_agent_workflow=False, optimization_effect_proven=False,
        scope="single paired isolated execution-service regression and result-interface contract tests"))
    print(json.dumps(dict(comparable_pairs=len(changes), result_guard=attempts), ensure_ascii=False))


if __name__ == "__main__":
    main()
