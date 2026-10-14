"""Descriptive comparison, not a causal optimization claim."""
from simclinic.checks import audit


def compare(before, after):
    reasons = []
    if before.data["task"] != after.data["task"]:
        reasons.append("任务或验收条件不同")
    if before.data["environment"] != after.data["environment"]:
        reasons.append("环境不同")
    if not all(isinstance(r.data["environment"].get("runtime_digest"), str) and r.data["environment"]["runtime_digest"] for r in (before, after)):
        reasons.append("缺少固定运行环境摘要 runtime_digest")
    if before.data["input_version"] is None or before.data["input_version"] != after.data["input_version"]:
        reasons.append("输入版本未知或不同")
    if before.data["coverage"] != after.data["coverage"] or not all(before.data["coverage"].values()):
        reasons.append("证据覆盖不同或不完整")
    a, b = audit(before), audit(after)
    changes = [{"check_id": x["check_id"], "before": x["status"], "after": y["status"]}
               for x, y in zip(a["checks"], b["checks"])]
    metric_deltas = {}
    for key in ("wall_seconds", "solver_seconds", "model_tokens", "model_cost"):
        x, y = before.data["metrics"].get(key), after.data["metrics"].get(key)
        metric_deltas[key] = {"before": x, "after": y, "delta": None if x is None or y is None else y - x}
    return {"comparison_version": "1.0", "before_record_id": before.data["record_id"],
            "after_record_id": after.data["record_id"], "synthetic": before.data["synthetic"] or after.data["synthetic"],
            "comparable": not reasons, "incomparability_reasons": reasons,
            "check_changes": changes, "metrics": metric_deltas,
            "limitations": ["单对离线记录只作描述比较，不能证明优化效果。", "缺少完成声明可能意味着任务未完成，不能按问题减少声称质量提升。", "下一阶段需要相同环境、质量门禁、保留算例与多次真实运行。"]}
