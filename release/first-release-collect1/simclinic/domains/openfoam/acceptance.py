"""Task-specific channel acceptance using measured fields and analytic reference."""
import math
import re
from pathlib import Path
from .channel import SPEC
from .logs import parse_solver_log


def channel_criteria():
    definitions = [
        ("execution_end", "execution", "eq", True, "求解退出成功且 End 已观测"),
        ("steady_converged", "numerical", "eq", True, "SIMPLE residualControl 收敛标记"),
        ("local_continuity", "numerical", "le", SPEC["continuity_tolerance"], "末步局部连续性误差"),
        ("profile_error", "reference", "le", SPEC["profile_relative_l2_tolerance"], "速度剖面相对 L2 误差"),
        ("pressure_gradient_error", "reference", "le", SPEC["pressure_gradient_relative_tolerance"], "运动学压力梯度相对误差"),
    ]
    return [dict(id=id, stage=stage, operator=op, target=target, description=description)
            for id, stage, op, target, description in definitions]


def rows(path):
    result = []
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(v) for v in line.split()]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("non-finite sample value")
        result.append(values)
    if len(result) < 2:
        raise ValueError("sample requires multiple points")
    return result


def profile_error(path):
    values = projected_rows(path, "y", ("U_x", "U_y", "U_z"), 4)
    y = [row[0] for row in values]
    if len(values) != 20 or any(abs(y[i] - (0.0025 + i * 0.005)) > 1e-6 for i in range(20)):
        raise ValueError("velocity sample does not cover specified channel points")
    reference = [6 * SPEC["mean_velocity"] * (v / SPEC["height"]) * (1 - v / SPEC["height"]) for v in y]
    # Include transverse velocity deviations, not only the streamwise component.
    numerator = sum((row[1] - target)**2 + row[2]**2 + row[3]**2 for row, target in zip(values, reference))
    return math.sqrt(numerator / sum(v*v for v in reference))


def pressure_gradient_error(path):
    values = projected_rows(path, "x", ("p",), 2)
    if len(values) != 17 or any(len(row) != 2 or abs(row[0] - (0.1 + i * 0.05)) > 1e-6 for i, row in enumerate(values)):
        raise ValueError("pressure sample does not cover specified channel points")
    xs, ps = [r[0] for r in values], [r[1] for r in values]
    xm, pm = sum(xs)/len(xs), sum(ps)/len(ps)
    slope = sum((x-xm)*(p-pm) for x, p in zip(xs, ps)) / sum((x-xm)**2 for x in xs)
    expected = -12 * SPEC["nu"] * SPEC["mean_velocity"] / SPEC["height"]**2
    return abs(slope - expected) / abs(expected)


def projected_rows(path, axis, fields, legacy_width):
    """Foundation 10 raw sets combine scalar/vector fields in one labelled file."""
    values = rows(path)
    headers = [line.lstrip()[1:].split() for line in Path(path).read_text().splitlines() if line.lstrip().startswith("#")]
    labels = next((h for h in headers if axis in h and all(field in h for field in fields)), None)
    if labels:
        if len(set(labels)) != len(labels) or any(len(row) != len(labels) for row in values):
            raise ValueError("sample header and data columns differ")
        columns = [labels.index(axis)] + [labels.index(field) for field in fields]
        return [[row[i] for i in columns] for row in values]
    if not headers and all(len(row) == legacy_width for row in values):
        return values
    raise ValueError("sample fields or axis are missing/unknown")


def solver_measurements(text, exit_code):
    parsed = parse_solver_log(text)
    local = re.findall(r"time step continuity errors\s*:\s*sum local\s*=\s*([^,\s]+)", text)
    value = None
    if local:
        try:
            value = float(local[-1])
        except ValueError:
            pass
        if value is not None and (not math.isfinite(value) or value < 0):
            value = None
    return {"execution_end": exit_code == 0 and parsed["execution_status"] == "end_observed",
            "steady_converged": bool(re.search(r"SIMPLE solution converged", text)),
            "local_continuity": value}
