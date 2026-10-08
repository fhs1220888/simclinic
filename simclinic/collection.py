"""Collect actual subprocess observations; never import arbitrary commands from records."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid
from .core.record import Record, validate
from .core.report import write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def input_manifest(case):
    result = {}
    for folder in ("0", "constant", "system"):
        for path in sorted((case / folder).rglob("*")):
            if path.is_file() and not path.is_symlink():
                result[str(path.relative_to(case))] = hashlib.sha256(path.read_bytes()).hexdigest()
    allrun = case / "Allrun"
    if allrun.is_file():
        result["Allrun"] = hashlib.sha256(allrun.read_bytes()).hexdigest()
    return result


class Collector:
    def __init__(self, case, environment, task):
        self.case = Path(case).resolve()
        evidence = self.case / ".simclinic"
        evidence.mkdir(exist_ok=False)
        manifest = input_manifest(self.case)
        self.data = {"schema_version": "1.0", "record_id": "collected-" + uuid.uuid4().hex,
                     "run_id": uuid.uuid4().hex, "input_version": "sha256:" + digest(manifest),
                     "synthetic": False, "environment": environment, "task": task,
                     "agent": {"name": "SimClinic scripted execution harness", "version": "0.2",
                               "upstream_execution_commit": "bfb7f791f77438ff90c1a39ff398739ee97bcc67",
                               "full_agent_workflow": False},
                     "coverage": dict(tools=False, decisions=False, references=False, acceptance=False),
                     "collection_status": "in_progress",
                     "metrics": dict(wall_seconds=None, solver_seconds=None, model_tokens=None, model_cost=None),
                     "input_manifest": manifest, "events": [], "artifacts": []}
        self.started = time.monotonic()
        self.save()

    def event(self, kind, **fields):
        event = dict(id=f"e{len(self.data['events']) + 1}", kind=kind,
                     run_id=self.data["run_id"], input_version=self.data["input_version"], **fields)
        self.data["events"].append(event)
        self.save()
        return event

    def save(self):
        validate(self.data)
        temporary = self.case / ".simclinic" / "record.tmp.json"
        write_json(temporary, self.data)
        temporary.replace(self.case / ".simclinic" / "record.json")

    def artifact(self, path):
        path = Path(path).resolve()
        if self.case not in path.parents or not path.is_file() or path.is_symlink():
            raise ValueError("artifact must be a regular file inside collected case")
        entry = dict(id=f"a{len(self.data['artifacts']) + 1}", path=str(path.relative_to(self.case)),
                     run_id=self.data["run_id"], input_version=self.data["input_version"],
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        # Files are registered only at observed generation/snapshot time; never
        # call this on unverified outputs found in an old user case.
        self.data["artifacts"].append(entry)
        self.save()
        return entry["id"]

    def tool(self, operation, phase, argv, outcome_parser=None):
        directory = self.case / ".simclinic" / f"call-{len(self.data['events']) + 1}"
        directory.mkdir()
        fingerprint = digest({"state": input_manifest(self.case), "argv": argv, "environment": self.data["environment"]})
        start = time.monotonic()
        try:
            result = subprocess.run(argv, cwd=self.case, capture_output=True, text=True, timeout=180)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr, code = "Collector timeout after 180 seconds", 124
        (directory / "stdout.log").write_text(stdout)
        (directory / "stderr.log").write_text(stderr)
        outcome = "fail" if code else (outcome_parser(stdout + stderr) if outcome_parser else "pass")
        evidence_id = self.artifact(directory / "stdout.log")
        stderr_id = self.artifact(directory / "stderr.log")
        event = self.event("tool", operation=operation, phase=phase, arguments={"argv": argv},
                           exit_code=code, outcome=outcome, input_fingerprint=fingerprint,
                           error_category=None if outcome == "pass" else "execution_failure",
                           stdout_artifact=evidence_id, stderr_artifact=stderr_id,
                           elapsed_seconds=time.monotonic() - start)
        return event

    def record(self):
        self.data["coverage"] = dict(tools=True, decisions=True, references=True, acceptance=True)
        self.data["collection_status"] = "complete"
        self.data["metrics"]["wall_seconds"] = time.monotonic() - self.started
        self.save()
        return Record(self.data, str(self.case / ".simclinic/record.json"), self.case)
