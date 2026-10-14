"""Conservative deterministic checks over the ordered v1 event stream."""
from simclinic.core.evidence import artifact_available, artifact_evidence, failed, provenance, succeeded

NAMES = {
    "preflight": "运行前遗漏检查",
    "error_continuation": "错误后继续生成结论",
    "retries": "无效重复重试",
    "stale_results": "引用旧结果",
    "completion": "缺少完成证据",
}
STATUSES = ("finding", "pass", "insufficient_evidence", "not_applicable")


def item(status, message, evidence):
    return {"status": status, "message": message, "evidence": evidence}


def aggregate(check_id, details):
    statuses = {d["status"] for d in details}
    status = next((s for s in ("finding", "insufficient_evidence", "pass") if s in statuses), "not_applicable")
    return {"check_id": check_id, "name": NAMES[check_id], "status": status, "details": details}


def preflight(record):
    d, details = record.data, []
    solves = [(i, e) for i, e in enumerate(d["events"]) if e["kind"] == "tool" and e["phase"] == "solve"]
    if not solves:
        status = "not_applicable" if d["coverage"]["tools"] else "insufficient_evidence"
        return aggregate("preflight", [item(status, "没有记录求解调用。", [record.ref("/coverage/tools")])])
    if not d["task"]["required_preflight"]:
        return aggregate("preflight", [item("not_applicable", "任务未指定运行前检查。", [record.ref("/task/required_preflight")])])
    for index, solve in solves:
        for operation in d["task"]["required_preflight"]:
            candidates = [e for e in d["events"][:index] if e["kind"] == "tool" and e["phase"] == "preflight" and e["operation"] == operation and provenance(record, e, solve) is True]
            ev = [record.event_ref(solve), record.ref("/task/required_preflight")]
            if not candidates:
                possible = any(e["kind"] == "tool" and e.get("phase") == "preflight" and e.get("operation") == operation and provenance(record, e, solve) is None for e in d["events"][:index])
                status = "finding" if d["coverage"]["tools"] and not possible else "insufficient_evidence"
                details.append(item(status, f"求解前缺少可归属于当前输入的 {operation}。", ev + [record.ref("/coverage/tools")]))
                continue
            check = candidates[-1]
            ev.append(record.event_ref(check))
            if failed(check):
                details.append(item("finding", f"{operation} 失败后仍开始求解。", ev))
            elif not succeeded(check):
                details.append(item("insufficient_evidence", f"{operation} 的退出状态或检查结果未知。", ev))
            else:
                handlers = [e for e in d["events"][:index] if e["kind"] == "decision" and check["id"] in e["handles"] and provenance(record, e, solve) is True]
                status = "pass" if handlers else ("finding" if d["coverage"]["decisions"] else "insufficient_evidence")
                details.append(item(status, f"{operation} 已通过；" + ("记录了处理结果。" if handlers else "没有结果处理证据。"), ev + [record.event_ref(e) for e in handlers] + [record.ref("/coverage/decisions")]))
    return aggregate("preflight", details)


def same_session(left, right):
    return isinstance(left.get("session_id"), str) and bool(left["session_id"]) and left["session_id"] == right.get("session_id")


def recovered(record, error, before):
    # Recovery requires an explicit successful retry chain, not unrelated success.
    lineage = {error["id"]}
    for event in before:
        if event["kind"] == "tool" and event.get("retry_of") in lineage:
            if event["operation"] != error["operation"] or not (
                provenance(record, event, error) is True or (same_session(event, error)
                and all(event.get(k) and error.get(k) for k in ("run_id", "input_version")))):
                continue
            lineage.add(event["id"])
            if succeeded(event):
                return event
    return None


def error_continuation(record):
    d, details = record.data, []
    claims = [(i, e) for i, e in enumerate(d["events"]) if e["kind"] == "claim" and e["success"]]
    if not claims:
        status = "not_applicable" if d["coverage"]["decisions"] else "insufficient_evidence"
        return aggregate("error_continuation", [item(status, "没有记录成功结论或完成声明。", [record.ref("/coverage/decisions")])])
    for index, claim in claims:
        errors = [e for e in d["events"][:index] if failed(e) and e.get("critical", True)
                  and (provenance(record, e, claim) is not False or same_session(e, claim))]
        for error in errors:
            evidence = [record.event_ref(error), record.event_ref(claim)]
            recovery = recovered(record, error, d["events"][:index])
            if recovery:
                details.append(item("pass", "错误已有显式关联的成功重试记录。", evidence + [record.event_ref(recovery)]))
            else:
                known = provenance(record, error, claim) is True or (same_session(error, claim)
                    and all(error.get(k) and claim.get(k) for k in ("run_id", "input_version")))
                message = f"成功结论前存在尚无恢复证据的工具错误：{error['operation']}，exit_code={error.get('exit_code')}，outcome={error.get('outcome')}。"
                details.append(item("finding" if known and d["coverage"]["tools"] else "insufficient_evidence", message, evidence + [record.ref("/coverage/tools")]))
        if not errors:
            unknown_tools = any(e["kind"] == "tool" and e.get("critical", True)
                and (provenance(record, e, claim) is not False or same_session(e, claim))
                and not succeeded(e) for e in d["events"][:index])
            status = "pass" if d["coverage"]["tools"] and not unknown_tools else "insufficient_evidence"
            details.append(item(status, "成功结论前未观察到工具错误。", [record.event_ref(claim), record.ref("/coverage/tools")]))
    return aggregate("error_continuation", details)


def retries(record):
    d, details = record.data, []
    previous = {}
    for event in d["events"]:
        if event["kind"] != "tool":
            continue
        prior = previous.get(event["operation"])
        if event.get("retry_of"):
            prior = next(e for e in d["events"] if e["id"] == event["retry_of"])
        if prior and failed(prior) and (event.get("retry_of") or provenance(record, prior, event) is True):
            ev = [record.event_ref(prior), record.event_ref(event)]
            keys = ("input_version", "input_fingerprint", "error_category", "run_id")
            if event.get("retry_of") and same_session(prior, event):
                keys = ("input_version", "input_fingerprint", "error_category")
            if succeeded(event):
                details.append(item("pass", "失败后的重试成功。", ev))
            elif any(prior.get(k) is None or event.get(k) is None for k in keys):
                details.append(item("insufficient_evidence", "重试缺少输入指纹、错误类别或运行归属。", ev))
            elif (failed(event) and prior["operation"] == event["operation"] and all(prior[k] == event[k] for k in keys) and prior.get("arguments", {}) == event.get("arguments", {})):
                details.append(item("finding", "相同输入、参数和错误类别的失败再次出现；候选无效重试。", ev))
            elif event["outcome"] == "unknown" and event["exit_code"] is None:
                details.append(item("insufficient_evidence", "重试结果未知。", ev))
            else:
                details.append(item("pass", "重试输入或错误发生变化，或重试成功。", ev))
        previous[event["operation"]] = event
    if not details:
        status = "not_applicable" if d["coverage"]["tools"] else "insufficient_evidence"
        details = [item(status, "未观察到失败后的重复调用。", [record.ref("/events"), record.ref("/coverage/tools")])]
    return aggregate("retries", details)


def stale_results(record):
    details = []
    for event in record.data["events"]:
        if event["kind"] != "reference":
            continue
        artifact = record.artifact(event["artifact_id"])
        expected = event  # explicit context at time of reference, not the final input revision
        relations = [provenance(record, artifact, expected)]
        evidence = [record.event_ref(event), artifact_evidence(record, artifact)]
        if False in relations:
            details.append(item("finding", "引用结果的运行 ID 或输入版本与引用时任务不匹配。", evidence))
        elif None in relations or not artifact_available(record, artifact):
            details.append(item("insufficient_evidence", "结果归属未知，或文件缺失/哈希不匹配。", evidence))
        else:
            details.append(item("pass", "引用结果与引用时运行 ID、输入版本一致。", evidence))
    if not details:
        status = "not_applicable" if record.data["coverage"]["references"] else "insufficient_evidence"
        details = [item(status, "未记录结果引用。", [record.ref("/coverage/references")])]
    return aggregate("stale_results", details)


def matches(value, criterion):
    if value is None:
        return None
    target, op = criterion["target"], criterion["operator"]
    numeric = type(value) in (int, float) and type(target) in (int, float)
    if not numeric and type(value) is not type(target):
        return None
    if op == "eq":
        return value == target
    if not numeric:
        return None
    return value <= target if op == "le" else value >= target


def completion(record):
    d, details = record.data, []
    claims = [(i, e) for i, e in enumerate(d["events"]) if e["kind"] == "claim" and e["claim_type"] == "completion" and e["success"]]
    if not claims:
        status = "not_applicable" if d["coverage"]["decisions"] else "insufficient_evidence"
        return aggregate("completion", [item(status, "没有记录成功完成声明。", [record.ref("/coverage/decisions")])])
    for index, claim in claims:
        if not d["task"]["acceptance"]:
            details.append(item("insufficient_evidence", "任务没有验收条件，无法核验完成。", [record.event_ref(claim), record.ref("/task/acceptance")]))
        for ci, criterion in enumerate(d["task"]["acceptance"]):
            measures = [e for e in d["events"][:index] if e["kind"] == "measurement" and e["criterion_id"] == criterion["id"] and provenance(record, e, claim) is not False]
            ev = [record.event_ref(claim), record.ref(f"/task/acceptance/{ci}")]
            if not measures:
                status = "finding" if d["coverage"]["acceptance"] else "insufficient_evidence"
                details.append(item(status, f"完成声明缺少 {criterion['id']} 验收证据。", ev + [record.ref("/coverage/acceptance")]))
                continue
            measure = measures[-1]
            artifact = record.artifact(measure["artifact_id"])
            ev += [record.event_ref(measure), artifact_evidence(record, artifact)]
            relation = [provenance(record, measure, claim), provenance(record, artifact, claim)]
            passed = matches(measure["value"], criterion)
            if False in relation:
                status, message = "finding", "验收证据属于其他运行或输入版本。"
            elif None in relation or passed is None or not artifact_available(record, artifact):
                status, message = "insufficient_evidence", "验收值、来源或文件内容无法核实。"
            elif passed:
                status, message = "pass", "记录中的验收值满足条件。"
            else:
                status, message = "finding", "完成声明前的验收值未满足条件。"
            details.append(item(status, f"{criterion['id']} ({criterion['stage']}): {message}", ev))
    return aggregate("completion", details)


def audit(record):
    results = [check(record) for check in (preflight, error_continuation, retries, stale_results, completion)]
    coverage_needed = {"preflight": ("tools", "decisions"), "error_continuation": ("tools", "decisions"),
                       "retries": ("tools",), "stale_results": ("references",),
                       "completion": ("decisions", "acceptance")}
    for index, result in enumerate(results):
        missing = [key for key in coverage_needed[result["check_id"]] if not record.data["coverage"][key]]
        if missing and result["status"] == "pass":
            details = result["details"] + [item("insufficient_evidence", "观察到的事件满足检查，但相关事件流不完整。", [record.ref(f"/coverage/{key}") for key in missing])]
            results[index] = aggregate(result["check_id"], details)
    return {
        "report_version": "1.0", "record_id": record.data["record_id"],
        "synthetic": record.data["synthetic"], "run_id": record.data["run_id"],
        "environment": record.data["environment"], "checks": results,
        "summary": {s: sum(r["status"] == s for r in results) for s in STATUSES},
        "limitations": ["仅审计已导入证据；记录中的身份和完整性声明需由采集端保证。",
                        "行为检查通过不等于验证仿真正确性或优化效果；应分别核验领域验收与复测。",
                        "LLM 原因解释只能作为假设。"],
        "case_files_checked": record.case_dir is not None,
        "acceptance_observations": acceptance_observations(record),
    }


def acceptance_observations(record):
    """Assess latest task measurements even when no successful completion is claimed.

    A failed benchmark criterion is not automatically an Agent behavior fault.
    """
    details = []
    for ci, criterion in enumerate(record.data["task"]["acceptance"]):
        measures = [e for e in record.data["events"] if e["kind"] == "measurement" and e["criterion_id"] == criterion["id"] and provenance(record, e, record.data) is True]
        status, value = "unknown", None
        evidence = [record.ref(f"/task/acceptance/{ci}")]
        if measures:
            measure = measures[-1]
            value = measure["value"]
            artifact = record.artifact(measure["artifact_id"])
            evidence += [record.event_ref(measure), artifact_evidence(record, artifact)]
            match = matches(value, criterion)
            if provenance(record, artifact, record.data) is True and artifact_available(record, artifact) and match is not None:
                status = "met" if match else "not_met"
        details.append(dict(criterion_id=criterion["id"], description=criterion["description"],
                            stage=criterion["stage"], status=status, value=value,
                            operator=criterion["operator"], target=criterion["target"], evidence=evidence))
    stages = {}
    for stage in ("execution", "numerical", "reference"):
        statuses = {d["status"] for d in details if d["stage"] == stage}
        stages[stage] = ("not_required" if not statuses else "not_met" if "not_met" in statuses else "unknown" if "unknown" in statuses else "met")
    return dict(stages=stages, criteria=details,
                limitation="仅对最新可归属测量进行验收比较；与 Agent 完成声明审计分开。")
