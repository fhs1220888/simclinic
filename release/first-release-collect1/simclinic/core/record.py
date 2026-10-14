"""Strict v1 input contract. Unknown observations are null, never success."""
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


class RecordError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RecordError(message)


def scalar(value):
    return type(value) in (bool, int, float, str) and not (
        type(value) is float and not math.isfinite(value))


def text_field(obj, key, location, nullable=False):
    value = obj.get(key)
    require((nullable and value is None) or (isinstance(value, str) and bool(value)),
            f"{location}.{key}: expected nonempty string" + (" or null" if nullable else ""))


@dataclass
class Record:
    data: Dict[str, Any]
    source: str
    case_dir: Optional[Path] = None

    def ref(self, pointer, event_id=None):
        result = {"source": self.source, "pointer": pointer}
        if event_id:
            result["event_id"] = event_id
        return result

    def event_ref(self, event):
        index = next(i for i, e in enumerate(self.data["events"]) if e["id"] == event["id"])
        return self.ref(f"/events/{index}", event["id"])

    def artifact(self, artifact_id):
        return next((a for a in self.data["artifacts"] if a["id"] == artifact_id), None)


def validate(data):
    require(isinstance(data, dict), "record: expected object")
    require(data.get("schema_version") == "1.0", "schema_version must be 1.0")
    require(type(data.get("synthetic")) is bool, "synthetic must be explicit boolean")
    for key in ("record_id", "run_id", "input_version"):
        text_field(data, key, "record", nullable=(key != "record_id"))
    if "session_id" in data:
        text_field(data, "session_id", "record")
    require(isinstance(data.get("task"), dict), "task must be object")
    for key in ("id", "objective"):
        text_field(data["task"], key, "task")
    for key in ("environment", "agent", "coverage", "metrics"):
        require(isinstance(data.get(key), dict), f"{key} must be object")
    for key in ("tools", "decisions", "references", "acceptance"):
        require(type(data["coverage"].get(key)) is bool, f"coverage.{key} must be boolean")
    for key in ("wall_seconds", "solver_seconds", "model_tokens", "model_cost"):
        value = data["metrics"].get(key)
        require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0),
                f"metrics.{key} must be nonnegative finite number or null")
    for key in ("required_preflight", "acceptance"):
        require(isinstance(data["task"].get(key), list), f"task.{key} must be array")
    checks = data["task"]["required_preflight"]
    require(all(isinstance(x, str) and x for x in checks) and len(set(checks)) == len(checks),
            "required_preflight must contain unique nonempty operation names")
    criteria = data["task"]["acceptance"]
    ids = set()
    for criterion in criteria:
        require(isinstance(criterion, dict), "acceptance criterion must be object")
        for key in ("id", "description"):
            text_field(criterion, key, "acceptance")
        require(criterion["id"] not in ids, "duplicate criterion id")
        ids.add(criterion["id"])
        require(criterion.get("stage") in ("execution", "numerical", "reference"), "invalid acceptance stage")
        require(criterion.get("operator") in ("eq", "le", "ge"), "invalid acceptance operator")
        require(scalar(criterion.get("target")), "acceptance target must be finite scalar")
        if criterion["operator"] != "eq":
            require(type(criterion["target"]) in (int, float), "ordered target must be numeric")
    require(isinstance(data.get("artifacts"), list), "artifacts must be array")
    artifact_ids = set()
    for a in data["artifacts"]:
        require(isinstance(a, dict), "artifact must be object")
        for key in ("id", "path", "run_id", "input_version"):
            text_field(a, key, "artifact", nullable=key in ("run_id", "input_version"))
        require(a["id"] not in artifact_ids, "duplicate artifact id")
        artifact_ids.add(a["id"])
        path = Path(a["path"])
        require(not path.is_absolute() and ".." not in path.parts, "artifact path must stay inside case directory")
        sha = a.get("sha256")
        require(sha is None or (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)), "invalid sha256")
    require(isinstance(data.get("events"), list), "events must be array")
    event_ids = set()
    seen = {}
    for e in data["events"]:
        require(isinstance(e, dict), "event must be object")
        text_field(e, "id", "event")
        require(e["id"] not in event_ids, "duplicate event id")
        event_ids.add(e["id"])
        kind = e.get("kind")
        require(kind in ("tool", "decision", "claim", "reference", "measurement", "observation"), "invalid event kind")
        for key in ("run_id", "input_version"):
            text_field(e, key, "event", nullable=True)
        if "session_id" in e:
            text_field(e, "session_id", "event", nullable=True)
            if data.get("session_id") and e["session_id"] is not None:
                require(e["session_id"] == data["session_id"], "event session_id differs from record session")
        if kind == "tool":
            text_field(e, "operation", "tool")
            require(e.get("phase") in ("preflight", "solve", "postprocess", "other"), "invalid tool phase")
            require(e.get("outcome") in ("pass", "fail", "unknown"), "invalid tool outcome")
            require(e.get("exit_code") is None or type(e["exit_code"]) is int, "invalid exit_code")
            for key in ("input_fingerprint", "error_category"):
                text_field(e, key, "tool", nullable=True)
            require(isinstance(e.get("arguments", {}), dict), "tool arguments must be object")
            require(type(e.get("critical", True)) is bool, "tool critical must be boolean")
            previous = e.get("retry_of")
            require(previous is None or (previous in seen and seen[previous]["kind"] == "tool"), "retry_of must reference an earlier tool")
        if kind == "claim":
            require(e.get("claim_type") in ("completion", "conclusion"), "invalid claim_type")
            require(type(e.get("success")) is bool, "claim.success must be boolean")
        if kind == "decision":
            require(isinstance(e.get("handles"), list), "decision.handles must be array")
            require(all(x in seen and seen[x]["kind"] == "tool" for x in e["handles"]), "handles must reference earlier tools")
        if kind in ("reference", "measurement"):
            require(e.get("artifact_id") in artifact_ids, "unknown artifact_id")
        if kind == "measurement":
            require(e.get("criterion_id") in ids, "unknown criterion_id")
            require(e.get("value") is None or scalar(e["value"]), "measurement must be finite scalar or null")
        seen[e["id"]] = e
    return data


def load_record(path, case_dir=None):
    path = Path(path).resolve()
    def reject_constant(value):
        raise RecordError(f"non-finite JSON constant: {value}")
    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            require(key not in obj, f"duplicate JSON key: {key}")
            obj[key] = value
        return obj
    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                      object_pairs_hook=unique_pairs)
    if case_dir is not None:
        case_dir = Path(case_dir).resolve()
        require(case_dir.is_dir(), "case directory does not exist")
    return Record(validate(data), str(path), case_dir)
