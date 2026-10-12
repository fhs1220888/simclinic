"""Distribution entry point for scoped live collection and offline diagnosis."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
import subprocess

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
    parser = argparse.ArgumentParser(prog="simclinic", description="SimClinic首版：固定Agent现场采集、体检、复测与补丁演示。")
    parser.add_argument("--version", action="version", version="simclinic " + version("simclinic"))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("showcase", help="运行随包附带的发现→补丁→复测演示；不调用模型/CFD")
    p.add_argument("--out", type=Path, help="新输出目录；默认在当前目录自动创建")
    p = sub.add_parser("collect", help="复用固定Foam-Agent执行服务采集并体检；限定既有通道或Shell案例")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", type=Path, help="已有固定通道输入目录；只复制已支持输入，不复用旧输出")
    source.add_argument("--example", choices=("no-log-failure", "execution-ok", "healthy", "ended-not-converged"),
                        help="前两项为已有Shell控制例；后两项实际运行OpenFOAM，需自行准备环境")
    p.add_argument("--variant", choices=("baseline", "patched"), default="patched", help="patched在新沙箱实际应用已有补丁")
    p.add_argument("--out", type=Path, required=True, help="不存在的新目录，保留记录、证据和体检")
    p = sub.add_parser("audit", help="体检统一记录，输出JSON与Markdown")
    p.add_argument("record", type=Path)
    p.add_argument("--case", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--fail-on-finding", action="store_true")
    p = sub.add_parser("import", help="校验并导入已有的统一JSON记录")
    p.add_argument("source", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare", help="描述性比较两份统一记录")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("parse-log", help="解析Foundation10/simpleFoam日志观测")
    p.add_argument("log", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("guard-result", help="核验结果的当前身份与内容哈希")
    p.add_argument("record", type=Path)
    p.add_argument("artifact_id")
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--expected-input-version", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            from simclinic.live_collection import collect
            collect(args.out, variant=args.variant, source=args.case, example=args.example)
            return 0
        if args.command == "showcase":
            from simclinic.showcase import main as showcase
            return showcase([] if args.out is None else ["--out", str(args.out)])
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
        if args.command == "guard-result":
            record = load_record(args.record, args.case)
            try:
                payload = read_verified_result(record, args.artifact_id, args.expected_run_id, args.expected_input_version)
            except ResultRejected as exc:
                print(json.dumps(dict(allowed=False, code=exc.code, message=str(exc)), ensure_ascii=False))
                return 1
            print(json.dumps(dict(allowed=True, artifact_id=args.artifact_id, verified_bytes=len(payload)), ensure_ascii=False))
            return 0
        if args.command == "import":
            if args.out.resolve() == args.source.resolve():
                raise RecordError("import output must not overwrite source record")
            write_json(args.out, load_record(args.source).data)
        elif args.command == "compare":
            if args.out.resolve() in (args.before.resolve(), args.after.resolve()):
                raise RecordError("comparison must not overwrite source records")
            write_json(args.out, compare(load_record(args.before), load_record(args.after)))
        elif args.command == "parse-log":
            if args.out.resolve() == args.log.resolve():
                raise RecordError("output must not overwrite solver log")
            write_json(args.out, parse_solver_log(args.log.read_text(encoding="utf-8"), str(args.log.resolve())))
        return 0
    except (RecordError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("simclinic: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("simclinic: collection interrupted; partial evidence retained", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
