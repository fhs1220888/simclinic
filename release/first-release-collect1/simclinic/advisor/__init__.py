"""Candidate workflow rules, not automated changes or confirmed root causes."""

RULES = {
    "preflight": ("在 solve 前验证必要检查与当前输入版本，并要求记录结果处理。", "前置检查门禁可能未覆盖当前输入。", "检查缺失、失败、版本变化和正常通过各一个案例。"),
    "error_continuation": ("阻止存在未恢复关键错误的成功结论；恢复需绑定成功重试。", "完成声明可能与执行状态脱节。", "失败后直接完成、明确失败结论、修复后成功重试。"),
    "retries": ("为失败调用记录参数和状态指纹；重复相同失败时停止并进入诊断。", "重试策略可能未要求有意义的状态变化。", "相同失败、参数变化、状态变化和未知退出码。"),
    "stale_results": ("后处理接口要求 expected_run_id 和 expected_input_version；不匹配或缺失时拒绝引用。", "后处理接口可能缺少结果归属校验。", "旧运行、旧输入、身份缺失和当前结果；保留原验收条件。"),
    "completion": ("完成门禁逐项核验任务验收条件，并绑定同一运行与输入版本。", "完成门禁可能只检查程序结束。", "执行结束但数值不达标、数值通过但参考验证失败、证据缺失。"),
}


def candidates(report):
    result = []
    for check in report["checks"]:
        if check["status"] != "finding":
            continue
        rule, hypothesis, retest = RULES[check["check_id"]]
        result.append({"check_id": check["check_id"], "status": "candidate_not_applied",
                       "change": rule, "cause_hypothesis": hypothesis,
                       "expected_effect": "减少该类行为问题；需在保留算例上复测。",
                       "retest": retest, "scope": "agent_workflow_or_tool_contract",
                       "evidence": [e for detail in check["details"] if detail["status"] == "finding" for e in detail["evidence"]]})
    return result
