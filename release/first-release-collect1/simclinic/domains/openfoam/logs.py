"""Extract observations only. End is execution evidence, not convergence."""
import math
import re

RESIDUAL = re.compile(r"Solving for (\w+), Initial residual = ([^,]+), Final residual = ([^,]+), No Iterations ([0-9]+)")
ERROR = re.compile(r"FOAM FATAL (?:IO )?ERROR|Floating point exception|Segmentation fault")


def parse_solver_log(text, source="log.simpleFoam"):
    final, errors, ends = {}, [], []
    for line_no, line in enumerate(text.splitlines(), 1):
        evidence = {"source": source, "line": line_no, "excerpt": line.strip()}
        if ERROR.search(line):
            errors.append(evidence)
        if line.strip() == "End":
            ends.append(evidence)
        match = RESIDUAL.search(line)
        if match:
            try:
                value = float(match[3])
            except ValueError:
                value = None
            if value is not None and (not math.isfinite(value) or value < 0):
                value = None
            final[match[1]] = {"value": value, "evidence": evidence}
    return {"execution_end_observed": bool(ends), "fatal_errors": errors,
            "execution_status": "fail" if errors else ("end_observed" if ends else "unknown"),
            "end_evidence": ends, "final_linear_residuals": final,
            "numerical_acceptance": "not_assessed", "reference_validation": "not_assessed",
            "limitation": "End 和线性求解器残差不能单独证明稳态收敛或参考验证。"}
