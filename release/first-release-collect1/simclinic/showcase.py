"""Locate bundled showcase resources; outputs belong to the user's workdir."""
import argparse
from datetime import datetime
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="simclinic-demo", description="运行首版完整演示；需要Git/Bash，不需要模型或CFD。")
    parser.add_argument("--out", type=Path, help="新目录；默认在当前工作目录创建simclinic-demo-<时间戳>")
    args = parser.parse_args(argv)
    from simclinic import _showcase_impl as demo
    demo.ROOT = Path(__file__).resolve().parent / "demo_data"
    output = args.out or Path.cwd() / ("simclinic-demo-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    return demo.main(["--out", str(output)])


if __name__ == "__main__":
    sys.exit(main())
