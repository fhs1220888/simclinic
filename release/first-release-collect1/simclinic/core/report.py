import json
from pathlib import Path

LABELS = {"finding": "发现问题", "pass": "检查通过", "insufficient_evidence": "证据不足", "not_applicable": "不适用"}


def clean(text):
    return str(text).replace("\n", " ").replace("`", "'")


def markdown(report):
    lines = ["# SimClinic 体检报告", "", f"记录：`{clean(report['record_id'])}`",
             "", "数据性质：" + ("**合成数据（未运行真实仿真）**" if report["synthetic"] else "导入记录（未独立验证仿真）"), "",
             "文件检查：" + ("已对提供的算例目录检查引用文件及可用哈希。" if report["case_files_checked"] else "未提供算例目录；仅审计记录。"), ""]
    for check in report["checks"]:
        lines += [f"## {check['name']}：{LABELS[check['status']]}", ""]
        for detail in check["details"]:
            lines.append(f"- **{LABELS[detail['status']]}**：{clean(detail['message'])}")
            for evidence in detail["evidence"]:
                location = evidence.get("pointer", "")
                lines.append(f"  - 证据：`{clean(evidence['source'])}#{clean(location)}`" + (f"；文件 `{clean(evidence['file'])}`" if "file" in evidence else ""))
        lines.append("")
    acceptance = report.get("acceptance_observations")
    if acceptance and acceptance["criteria"]:
        names = {"met": "满足条件", "not_met": "未满足条件", "unknown": "证据不足", "not_required": "未指定"}
        lines += ["## 分层验收观测", "", acceptance["limitation"], "",
                  "| 层级 | 结果 |", "| --- | --- |"]
        for stage, status in acceptance["stages"].items():
            lines.append(f"| {stage} | {names[status]} |")
        lines.append("")
        for criterion in acceptance["criteria"]:
            lines.append(f"- `{criterion['criterion_id']}`：{names[criterion['status']]}；值 `{criterion['value']}`，要求 `{criterion['operator']} {criterion['target']}`。")
        lines.append("")
    if report.get("candidates"):
        lines += ["## 候选改进（未应用、未验证）", ""]
        for candidate in report["candidates"]:
            lines += [f"- `{candidate['check_id']}`：{candidate['change']}", f"  - 原因假设：{candidate['cause_hypothesis']}", f"  - 预期作用：{candidate['expected_effect']}", f"  - 复测：{candidate['retest']}"]
        lines.append("")
    lines += ["## 限制", ""] + [f"- {x}" for x in report["limitations"]]
    return "\n".join(lines) + "\n"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "report.json", report)
    (directory / "report.md").write_text(markdown(report), encoding="utf-8")
