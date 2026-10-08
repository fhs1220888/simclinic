"""Launch a new isolated real-run directory. Never touches existing cases."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d")
    parser.add_argument("--out", type=Path, default=Path("real-runs") / uuid.uuid4().hex[:8])
    parser.add_argument("--variant", choices=("baseline", "patched"), default="baseline")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.out.resolve()
    if out.exists():
        raise ValueError("Output exists; use a new run directory")
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image], text=True))[0]
    if info["Architecture"] != "amd64":
        raise ValueError("This integration target is fixed to the official amd64 image")
    out.mkdir(parents=True)
    metadata = dict(image_id=info["Id"], image_repo_digests=info["RepoDigests"],
                    architecture=info["Architecture"], platform="linux/amd64", cpu_limit=2,
                    agent_source_commit="bfb7f791f77438ff90c1a39ff398739ee97bcc67")
    (out / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    # Resolve the mutable download tag to the local immutable image ID before running.
    argv = ["docker", "run", "--rm", "--pull", "never", "--platform", "linux/amd64",
            "--network", "none", "--cpus", "2", "--memory", "2g", "--user", "root",
            "--mount", f"type=bind,src={root},dst=/workspace,readonly",
            "--mount", f"type=bind,src={out},dst=/runs", "--workdir", "/workspace",
            "--env", "PYTHONPATH=/workspace", "--entrypoint", "/bin/bash", info["Id"], "-c",
            "source /opt/openfoam10/etc/bashrc && python3 integration/foam_agent_v210/run_case.py --out /runs --environment /runs/environment.json --variant " + args.variant]
    result = subprocess.run(argv)
    if result.returncode == 0:
        from simclinic.cli import get_report
        from simclinic.core.record import load_record
        from simclinic.core.report import write_report
        summary_path = out / "summary.json"
        summary = json.loads(summary_path.read_text())
        for run in summary["runs"]:
            case = out / run["scenario"] / "case"
            path = case / ".simclinic/record.json"
            write_report(get_report(load_record(path, case)), case.parent / "audit")
            run["record_path"] = str(path)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"Real integration artifacts: {out}")
    return result.returncode


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
