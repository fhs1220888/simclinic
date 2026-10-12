import hashlib


def provenance(record, item, expected):
    """True: both identifiers agree; False: known mismatch; None: unknown."""
    unknown = False
    for key in ("run_id", "input_version"):
        actual, wanted = item.get(key), expected.get(key)
        if actual is not None and wanted is not None and actual != wanted:
            return False
        if actual is None or wanted is None:
            unknown = True
    return None if unknown else True


def artifact_evidence(record, artifact):
    index = record.data["artifacts"].index(artifact)
    result = record.ref(f"/artifacts/{index}")
    result["file"] = artifact["path"]
    return result


def artifact_available(record, artifact):
    # Without a case directory the audit assesses recorded evidence only.
    if record.case_dir is None:
        return True
    path = (record.case_dir / artifact["path"]).resolve()
    if record.case_dir not in path.parents or not path.is_file():
        return False
    sha = artifact.get("sha256")
    if sha is not None and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        return False
    return True


def failed(event):
    return event["kind"] == "tool" and (event.get("outcome") == "fail" or (
        event.get("exit_code") is not None and event["exit_code"] != 0))


def succeeded(event):
    return event["kind"] == "tool" and event.get("outcome") == "pass" and event.get("exit_code") == 0
