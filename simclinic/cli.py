import argparse
import json
import sys
from pathlib import Path
from simclinic.adapters.foam_agent import FoamAgentTextAdapter
from simclinic.advisor import candidates
from simclinic.checks import audit
from simclinic.core.record import RecordError, load_record
from simclinic.core.report import write_json, write_report
from simclinic.domains.openfoam.logs import parse_solver_log
from simclinic.evaluation import compare
from simclinic.result_guard import ResultRejected, read_verified_result


def get_report(record):
    report = audit(record)
    report["candidates"] = candidates(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="simclinic", description="离线审计仿真 Agent；不执行仿真。")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("audit", help="输出五项行为检查的 JSON 和 Markdown")
    p.add_argument("record", type=Path)
    p.add_argument("--case", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--fail-on-finding", action="store_true")
    p = sub.add_parser("import", help="将离线记录转换为统一格式")
    p.add_argument("source", type=Path)
    p.add_argument("--adapter", choices=("normalized", "foam-agent-text"), default="normalized")
    p.add_argument("--metadata", type=Path, help="foam-agent-text 所需的 v1 元数据记录")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("parse-log", help="提取 Foundation 10 simpleFoam 日志观测，不判断收敛")
    p.add_argument("log", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare", help="描述性比较两份统一记录")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("demo", help="审计仓库内明确标注的合成示例")
    p.add_argument("--fixtures", type=Path, default=Path(__file__).resolve().parent.parent / "fixtures")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("guard-result", help="验证结果身份和内容，拒绝旧结果或未知归属")
    p.add_argument("record", type=Path)
    p.add_argument("artifact_id")
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--expected-input-version", required=True)
    p = sub.add_parser("study-status", help="只读核验冻结评估并查询进度与已知用量")
    p.add_argument("batch", type=Path)
    p.add_argument("--json", action="store_true", help="向标准输出打印 JSON")
    p.add_argument("--out", type=Path, help="将 JSON 状态快照保存到批次之外")
    p = sub.add_parser("diagnose-sampling", help="只读诊断已注册通道采样的退出、致命日志和输出证据")
    p.add_argument("record", type=Path)
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True, help="JSON 诊断，保存到采集目录之外")
    p = sub.add_parser("benchmark", help="核验版本化案例清单并批量离线体检；不执行模型或仿真")
    p.add_argument("manifest", type=Path)
    p.add_argument("--root", type=Path, default=Path.cwd(), help="清单相对路径的来源根目录，默认当前目录")
    p.add_argument("--out", type=Path, required=True, help="新的输出目录，须位于源案例和清单目录之外")
    p.add_argument("--manifest-sha256", help="显式可信清单 SHA；默认读取同名 .sha256 文件")
    args = parser.parse_args(argv)
    try:
        if args.command == "benchmark":
            from simclinic.evaluation.benchmark import run_benchmark
            result = run_benchmark(args.manifest, args.root, args.out, args.manifest_sha256)
            print(json.dumps(result['summary'], ensure_ascii=False))
            return 2 if result['summary']['rejected'] else 1 if result['summary']['mismatched'] else 0
        if args.command == "diagnose-sampling":
            from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
            if args.out.resolve().is_relative_to(args.case.resolve()) or args.out.resolve() == args.record.resolve():
                raise RecordError("sampling diagnostic output must remain outside the collection directory")
            result = diagnose_sampling(load_record(args.record, args.case))
            write_json(args.out, result)
            print(json.dumps(result['summary'], ensure_ascii=False))
            return 0
        if args.command == "study-status":
            from simclinic.evaluation.study_status import study_status, status_text
            if args.out and args.out.resolve().is_relative_to(args.batch.resolve()):
                raise RecordError("status output must remain outside the study batch")
            status = study_status(args.batch)
            if args.out:write_json(args.out,status)
            print(json.dumps(status,ensure_ascii=False,indent=2) if args.json else status_text(status))
            return 0
        if args.command == "guard-result":
            record = load_record(args.record, args.case)
            try:
                payload = read_verified_result(record, args.artifact_id, args.expected_run_id, args.expected_input_version)
            except ResultRejected as exc:
                print(json.dumps(dict(allowed=False, code=exc.code, message=str(exc)), ensure_ascii=False))
                return 1
            print(json.dumps(dict(allowed=True, artifact_id=args.artifact_id, verified_bytes=len(payload)), ensure_ascii=False))
            return 0
        if args.command == "audit":
            record = load_record(args.record, args.case)
            protected = {args.record.resolve()}
            if record.case_dir:
                protected.update((record.case_dir / a["path"]).resolve() for a in record.data["artifacts"])
            if any((args.out / name).resolve() in protected for name in ("report.json", "report.md")):
                raise RecordError("audit output must not overwrite source record or artifact")
            report = get_report(record)
            write_report(report, args.out)
            print(json.dumps(report["summary"], ensure_ascii=False))
            return 1 if args.fail_on_finding and report["summary"]["finding"] else 0
        if args.command == "import":
            if args.adapter == "foam-agent-text":
                if args.metadata is None:
                    raise RecordError("foam-agent-text requires --metadata")
                record = FoamAgentTextAdapter().import_record(args.source, args.metadata)
                if args.out.resolve() in ((args.source / "workflow.log").resolve(), (args.source / "review.log").resolve()):
                    raise RecordError("import output must not overwrite raw logs")
            else:
                record = load_record(args.source)
            if args.out.resolve() == args.source.resolve() or (args.metadata and args.out.resolve() == args.metadata.resolve()):
                raise RecordError("import output must not overwrite source or metadata")
            write_json(args.out, record.data)
            print(f"Imported {record.data['record_id']} -> {args.out}")
        elif args.command == "parse-log":
            if args.out.resolve() == args.log.resolve():
                raise RecordError("output must not overwrite solver log")
            write_json(args.out, parse_solver_log(args.log.read_text(encoding="utf-8"), str(args.log.resolve())))
        elif args.command == "compare":
            if args.out.resolve() in (args.before.resolve(), args.after.resolve()):
                raise RecordError("comparison must not overwrite source records")
            write_json(args.out, compare(load_record(args.before), load_record(args.after)))
        elif args.command == "demo":
            paths = sorted(args.fixtures.glob("*/record.json"))
            if not paths:
                raise RecordError("No fixtures; use --fixtures to specify repository fixtures directory")
            for path in paths:
                record = load_record(path, path.parent / "case")
                if not record.data["synthetic"]:
                    raise RecordError("demo fixtures must be synthetic")
                report = get_report(record)
                write_report(report, args.out / path.parent.name)
                print(f"{path.parent.name}: " + json.dumps(report["summary"], ensure_ascii=False))
        return 0
    except (RecordError, OSError, ValueError) as exc:
        print(f"simclinic: {exc}", file=sys.stderr)
        return 2
