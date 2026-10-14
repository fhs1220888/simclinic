"""Run pinned upstream execution functions, isolated from model/RAG imports.

Function AST bodies are unmodified. This is service-level integration under a
scripted harness, NOT the full Foam-Agent planner/reviewer workflow.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import List

PIN = "bfb7f791f77438ff90c1a39ff398739ee97bcc67"
HASHES = {
    "logger.py": "82e0e6b2affdf2832a8d01c3946d1034de9d721ee1db7834f28d2f66c194456a",
    "local_runner_node.py": "d01e9259e68d487e40e64a0726135bdc5ef8dbe0a35079eb37f534ccbf2150c6",
    "run_local.py": "2951991cbbd24b4b89959a90a1a3ae07de40344dccdb040732eea8db1ac57949",
    "utils.py": "96769493618040f60b87dc870b377369ab0d23b692df8d9db6b9aab57154510c",
}


def load_functions(path, names, namespace, hashes=None):
    raw = path.read_bytes()
    expected = (hashes or HASHES)[path.name]
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"Upstream source changed: {path.name}")
    tree = ast.parse(raw.decode(), filename=str(path))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise ValueError("Upstream function missing")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    parser.add_argument("--variant", choices=("baseline", "patched"), default="baseline")
    args = parser.parse_args()
    case = args.case.resolve()
    upstream = Path(__file__).resolve().parent / ("upstream" if args.variant == "baseline" else "patched")
    hashes = HASHES
    if args.variant == "patched":
        manifest = json.loads((upstream / "manifest.json").read_text())
        if manifest["base_commit"] != PIN:
            raise ValueError("Patch base commit differs")
        hashes = manifest["source_files"]
    captures = []

    class ObservedProcess(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            self.observed_start = time.monotonic()
            super().__init__(*args, **kwargs)

        def communicate(self, *args, **kwargs):
            result = super().communicate(*args, **kwargs)
            captures.append({"returncode": self.returncode, "args": self.args,
                             "elapsed_seconds": time.monotonic() - self.observed_start})
            return result

    proxy = SimpleNamespace(Popen=ObservedProcess, PIPE=subprocess.PIPE,
                            DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
    namespace = {"os": os, "re": re, "shutil": shutil, "signal": signal,
                 "subprocess": proxy, "List": List}
    load_functions(upstream / "utils.py", ["remove_files", "remove_file", "remove_numeric_folders", "run_command", "check_foam_errors"], namespace, hashes)
    load_functions(upstream / "run_local.py", ["run_allrun_and_collect_errors"], namespace, hashes)
    # Logger has only stdlib imports, so loading its complete file is safe.
    raw = (upstream / "logger.py").read_bytes()
    if hashlib.sha256(raw).hexdigest() != hashes["logger.py"]:
        raise ValueError("Upstream logger hash mismatch")
    logger_ns = {"__name__": "simclinic_upstream_logger"}
    exec(compile(raw, str(upstream / "logger.py"), "exec"), logger_ns)
    namespace["log_review"] = logger_ns["log_review"]
    load_functions(upstream / "local_runner_node.py", ["local_runner_node"], namespace, hashes)
    logger_ns["setup_logging"](str(case))
    try:
        state = namespace["local_runner_node"]({"config": SimpleNamespace(max_time_limit=120), "case_dir": str(case)})
    finally:
        logger_ns["close_logging"]()
    output = {"source_commit": PIN, "execution_scope": "isolated-upstream-execution-functions",
              "variant": args.variant, "source_hashes": hashes,
              "errors": state["error_logs"], "processes": captures,
              "model_calls": 0, "full_agent_workflow": False}
    (case / ".simclinic" / "upstream.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
