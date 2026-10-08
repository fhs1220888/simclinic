"""Consume only bytes whose recorded run, input and digest match the caller."""
import hashlib
import json
from pathlib import Path
from .core.record import Record


class ResultRejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def read_verified_result(record: Record, artifact_id, expected_run_id, expected_input_version):
    """Return verified bytes, not a path to re-open after validation.

    Producer metadata must be captured at generation time by a trusted collector.
    This function cannot authenticate arbitrary imported provenance statements.
    """
    expected = {"run_id": expected_run_id, "input_version": expected_input_version}
    if any(not isinstance(value, str) or not value for value in expected.values()):
        raise ResultRejected("unknown_expected_identity", "调用者必须提供当前运行 ID 和输入版本。")
    artifact = record.artifact(artifact_id)
    if artifact is None:
        raise ResultRejected("unknown_artifact", "结果不在采集清单内。")
    for key, value in expected.items():
        if not isinstance(artifact.get(key), str) or not artifact[key]:
            raise ResultRejected("unknown_producer_identity", f"结果缺少生成时的 {key}。")
        if artifact[key] != value:
            raise ResultRejected("stale_run" if key == "run_id" else "stale_input", f"结果的 {key} 与调用者期望不匹配。")
    if record.case_dir is None:
        raise ResultRejected("missing_case_directory", "需要原始结果目录以核验文件内容。")
    relative = Path(artifact["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ResultRejected("unsafe_path", "结果路径不能离开采集目录。")
    case_dir = record.case_dir.resolve()
    path = case_dir / relative
    if path.is_symlink() or case_dir not in path.resolve().parents:
        raise ResultRejected("unsafe_path", "结果路径越界或是符号链接。")
    sha = artifact.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise ResultRejected("missing_digest", "结果缺少生成时的 SHA-256。")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ResultRejected("unavailable_result", "结果文件无法读取。") from exc
    if hashlib.sha256(payload).hexdigest() != sha:
        raise ResultRejected("digest_mismatch", "结果内容与采集时的哈希不匹配。")
    return payload


def read_verified_json(record, artifact_id, expected_run_id, expected_input_version):
    payload = read_verified_result(record, artifact_id, expected_run_id, expected_input_version)

    def reject_constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise ResultRejected("invalid_result_json", "校验后的结果不是有效的唯一键、有限值 JSON。") from exc
