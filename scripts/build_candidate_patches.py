"""Produce reproducible, applicable diffs without editing pinned baseline files."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "integration/foam_agent_v210"
PIN = "bfb7f791f77438ff90c1a39ff398739ee97bcc67"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Pinned source context differs; patch generation stopped")
    return text.replace(old, new, 1)


def main():
    target = INTEGRATION / "patched"
    target.mkdir(exist_ok=True)
    patches = ROOT / "patches/foam-agent-v2.1.0"
    patches.mkdir(parents=True, exist_ok=True)
    original_utils = (INTEGRATION / "upstream/utils.py").read_text()
    original_run = (INTEGRATION / "upstream/run_local.py").read_text()
    expected = {"utils.py": "96769493618040f60b87dc870b377369ab0d23b692df8d9db6b9aab57154510c",
                "run_local.py": "2951991cbbd24b4b89959a90a1a3ae07de40344dccdb040732eea8db1ac57949"}
    for name, sha in expected.items():
        if hashlib.sha256((INTEGRATION / "upstream" / name).read_bytes()).hexdigest() != sha:
            raise ValueError(f"Pinned baseline source changed: {name}")
    utils = replace_once(original_utils,
        "def run_command(script_path: str, out_file: str, err_file: str, working_dir: str, max_time_limit: int) -> None:",
        "def run_command(script_path: str, out_file: str, err_file: str, working_dir: str, max_time_limit: int) -> dict:")
    utils = replace_once(utils, "        try:\n            stdout, stderr = process.communicate(timeout=max_time_limit)",
                         "        timed_out = False\n        try:\n            stdout, stderr = process.communicate(timeout=max_time_limit)")
    utils = replace_once(utils, "        except subprocess.TimeoutExpired:\n            os.killpg",
                         "        except subprocess.TimeoutExpired:\n            timed_out = True\n            os.killpg")
    utils = replace_once(utils, '    print(f"Executed script {script_path}")\n',
                         '    print(f"Executed script {script_path}")\n    return {"exit_code": process.returncode, "timed_out": timed_out}\n')
    run = replace_once(original_run, "        run_command(allrun_file_path, out_file, err_file, case_dir, timeout)",
                       "        execution = run_command(allrun_file_path, out_file, err_file, case_dir, timeout)")
    run = replace_once(run, "        error_logs = check_foam_errors(case_dir)\n        if len(error_logs) == 0:", """        error_logs = check_foam_errors(case_dir)
        if (not isinstance(execution, dict)
                or type(execution.get("exit_code")) is not int
                or type(execution.get("timed_out")) is not bool):
            error_logs.insert(0, {"file": "Allrun", "error_content": "Execution status is unavailable."})
        elif execution["timed_out"] or execution["exit_code"] != 0:
            error_logs.insert(0, {
                "file": "Allrun",
                "error_content": "Allrun timed out." if execution["timed_out"] else
                    f"Allrun exited with code {execution['exit_code']}.",
                "exit_code": execution["exit_code"],
                "timed_out": execution["timed_out"],
            })
        if len(error_logs) == 0:""")
    diffs = []
    for name, before, after, destination in (("utils.py", original_utils, utils, "src/utils.py"),
            ("run_local.py", original_run, run, "src/services/run_local.py")):
        (target / name).write_text(after)
        diffs.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                         fromfile="a/" + destination, tofile="b/" + destination))
    execution_patch = patches / "0001-execution-status.patch"
    execution_patch.write_text("".join(diffs))
    for name in ("logger.py", "local_runner_node.py", "LICENSE"):
        shutil.copyfile(INTEGRATION / "upstream" / name, target / name)
    tool_contract = '''"""Candidate postprocessing interface; install simclinic>=0.3 before use.

Callers must pass current context from the orchestrator, not from the result's
own manifest. This module is opt-in and does not wire existing tools by itself.
"""
from simclinic.core.record import load_record
from simclinic.result_guard import read_verified_json


def read_result_json(record_path, case_dir, artifact_id, *, expected_run_id, expected_input_version):
    record = load_record(record_path, case_dir)
    return read_verified_json(record, artifact_id, expected_run_id, expected_input_version)
'''
    tool_patch = patches / "0002-verified-result-interface.patch"
    tool_patch.write_text("".join(difflib.unified_diff([], tool_contract.splitlines(True),
        fromfile="/dev/null", tofile="b/src/services/verified_results.py")))
    (target / "verified_results.py").write_text(tool_contract)
    manifest = dict(base_commit=PIN, variant="patched", source_files={
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.glob("*.py"))},
        patches={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (execution_patch, tool_patch)})
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Generated two candidate patches; pinned baseline unchanged.")


if __name__ == "__main__":
    main()
