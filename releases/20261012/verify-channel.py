"""Acceptance receipt for the two existing channel runs; no execution here."""
import hashlib
import json
from pathlib import Path
import sys
from simclinic.checks.behavior import matches
from simclinic.core.record import load_record
from simclinic.result_guard import read_verified_result

root = Path(sys.argv[1]).resolve()
read = lambda path: json.loads(path.read_text())
records = []
results = []
for name, variant in (("before", "baseline"), ("after", "patched")):
    directory = root / name
    record = load_record(directory / "record.json", directory / "case")
    data = record.data
    if data["synthetic"] or not all(data["coverage"].values()):
        raise RuntimeError("Expected complete, non-synthetic collection")
    values = read(directory / "case/.simclinic/acceptance.json")["values"]
    if not all(matches(values[c["id"]], c) is True for c in data["task"]["acceptance"]):
        raise RuntimeError("An existing channel criterion was not met")
    report = read(directory / "audit/report.json")
    if report["summary"]["finding"]:
        raise RuntimeError("Unexpected diagnostic finding")
    tools = [e for e in data["events"] if e["kind"] == "tool"]
    for operation in ("blockMesh", "checkMesh", "Allrun", "sample"):
        observations = [e for e in tools if e["operation"] == operation]
        if len(observations) != 1 or observations[0]["exit_code"] != 0 or observations[0]["outcome"] != "pass":
            raise RuntimeError("Missing successful tool observation: " + operation)
    for artifact in data["artifacts"]:
        read_verified_result(record, artifact["id"], data["run_id"], data["input_version"])
    if (directory / "record.json").read_bytes() != (directory / "case/.simclinic/record.json").read_bytes():
        raise RuntimeError("Record copies differ")
    records.append(data)
    results.append({"variant": variant, "run_id": data["run_id"], "input_version": data["input_version"],
        "measurements": values, "audit_summary": report["summary"], "verified_artifacts": len(data["artifacts"]),
        "record_sha256": hashlib.sha256((directory / "record.json").read_bytes()).hexdigest(),
        "acceptance_stages": report["acceptance_observations"]["stages"], "record": name + "/record.json"})
comparison = read(root / "comparison.json")
if records[0]["run_id"] == records[1]["run_id"] or records[0]["input_version"] != records[1]["input_version"] or not comparison["comparable"]:
    raise RuntimeError("Expected fresh identities and comparable inputs/environment")
patch = read(root / "after/patch-application.json")
receipt = {"status": "pass", "purpose": "Independent Foundation10 installation acceptance for the existing healthy channel",
    "full_agent_workflow": False, "model_calls": 0, "solver_runs": 2, "sampling_calls": 2,
    "new_cases": 0, "runs": results, "comparison": comparison, "patch_application": patch,
    "limitations": ["One existing case, two runs; no optimization or performance claim.",
        "Tested Linux amd64 container on macOS arm64; no native macOS solver or other platform claim."]}
(root / "verification.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
print("Foundation10 existing-channel acceptance: pass")
