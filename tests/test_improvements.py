import hashlib
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import List
import unittest
from unittest.mock import patch

from simclinic.core.record import load_record
from simclinic.result_guard import ResultRejected, read_verified_json, read_verified_result

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upstream_execution_loader", ROOT / "integration/foam_agent_v210/execute.py")
LOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOADER)


class ExecutionPatchTests(unittest.TestCase):
    def namespace(self, variant="patched"):
        folder = ROOT / "integration/foam_agent_v210" / ("upstream" if variant == "baseline" else "patched")
        hashes = LOADER.HASHES if variant == "baseline" else json.loads((folder / "manifest.json").read_text())["source_files"]
        ns = dict(os=os, re=re, shutil=shutil, signal=signal, subprocess=subprocess, List=List)
        LOADER.load_functions(folder / "utils.py", ["remove_files", "remove_file", "remove_numeric_folders", "run_command", "check_foam_errors"], ns, hashes)
        LOADER.load_functions(folder / "run_local.py", ["run_allrun_and_collect_errors"], ns, hashes)
        return ns

    def run_shell(self, body, variant="patched", timeout=2):
        with tempfile.TemporaryDirectory(prefix="simclinic-patch-") as tmp:
            case = Path(tmp)
            (case / "etc").mkdir()
            (case / "etc/bashrc").write_text(":\n")
            (case / "Allrun").write_text("#!/bin/bash\n" + body + "\n")
            with patch.dict(os.environ, {"WM_PROJECT_DIR": str(case)}), contextlib.redirect_stdout(io.StringIO()):
                return self.namespace(variant)["run_allrun_and_collect_errors"](str(case), timeout)

    def test_original_bug_and_patched_real_exit7(self):
        self.assertEqual(self.run_shell("exit 7", "baseline"), [])
        errors = self.run_shell("exit 7")
        self.assertEqual(errors[0]["exit_code"], 7)
        self.assertFalse(errors[0]["timed_out"])

    def test_exit0_regression_and_nonzero_with_end_marker(self):
        self.assertEqual(self.run_shell("printf 'End\\n' > log.test\nexit 0"), [])
        self.assertEqual(self.run_shell("printf 'End\\n' > log.test\nexit 3")[0]["exit_code"], 3)

    def test_timeout_kills_real_process_and_reports_failure(self):
        errors = self.run_shell("sleep 10", timeout=0.15)
        self.assertTrue(errors[0]["timed_out"])
        self.assertNotEqual(errors[0]["exit_code"], 0)

    def test_zero_exit_with_error_log_remains_failure(self):
        errors = self.run_shell("printf 'ERROR: deliberate failure\\n' > log.test\nexit 0")
        self.assertTrue(errors)
        self.assertIn("deliberate failure", errors[0]["error_content"])

    def test_unknown_exit_status_cannot_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "Allrun").write_text("exit 0\n")
            for status in (None, {}, {"exit_code": None, "timed_out": False}, {"exit_code": False, "timed_out": False}):
                ns = self.namespace()
                ns["run_command"] = lambda *args, observed=status: observed
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertIn("unavailable", ns["run_allrun_and_collect_errors"](str(case), 2)[0]["error_content"])

    def test_patch_applies_to_exact_snapshot_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/services").mkdir(parents=True)
            upstream = ROOT / "integration/foam_agent_v210/upstream"
            shutil.copyfile(upstream / "utils.py", root / "src/utils.py")
            shutil.copyfile(upstream / "run_local.py", root / "src/services/run_local.py")
            patches = ROOT / "patches/foam-agent-v2.1.0"
            for name in ("0001-execution-status.patch", "0002-verified-result-interface.patch"):
                subprocess.run(["git", "apply", "--check", str(patches / name)], cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "apply", str(patches / name)], cwd=root, check=True, capture_output=True)
            for source, destination in (("utils.py", "src/utils.py"), ("run_local.py", "src/services/run_local.py"), ("verified_results.py", "src/services/verified_results.py")):
                self.assertEqual((root / destination).read_bytes(), (ROOT / "integration/foam_agent_v210/patched" / source).read_bytes())
            for name in ("0002-verified-result-interface.patch", "0001-execution-status.patch"):
                subprocess.run(["git", "apply", "--reverse", str(patches / name)], cwd=root, check=True, capture_output=True)
            self.assertEqual((root / "src/utils.py").read_bytes(), (upstream / "utils.py").read_bytes())


class ResultGuardTests(unittest.TestCase):
    def record(self):
        return load_record(ROOT / "fixtures/healthy/record.json", ROOT / "fixtures/healthy/case")

    def test_verified_json_is_consumable(self):
        record = self.record()
        result = read_verified_json(record, "results", record.data["run_id"], record.data["input_version"])
        self.assertTrue(result["ended"])

    def test_stale_run_input_and_unknown_are_blocked_before_read(self):
        record = self.record()
        for run, version, expected in (("new-run", record.data["input_version"], "stale_run"),
                 (record.data["run_id"], "new-input", "stale_input"), (None, None, "unknown_expected_identity")):
            with patch.object(Path, "read_bytes", side_effect=AssertionError("must not read rejected file")):
                with self.assertRaises(ResultRejected) as cm:
                    read_verified_result(record, "results", run, version)
                self.assertEqual(cm.exception.code, expected)

    def test_unknown_producer_identity_and_missing_digest_blocked(self):
        for key, expected in (("run_id", "unknown_producer_identity"), ("sha256", "missing_digest")):
            record = self.record()
            record.data["artifacts"][0][key] = None
            with self.assertRaises(ResultRejected) as cm:
                read_verified_result(record, "results", record.data["run_id"], record.data["input_version"])
            self.assertEqual(cm.exception.code, expected)

    def test_changed_bytes_missing_file_and_symlink_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            record = self.record()
            record.case_dir = case
            for action, expected in (("missing", "unavailable_result"), ("changed", "digest_mismatch"), ("symlink", "unsafe_path")):
                target = case / "results.json"
                if target.exists() or target.is_symlink():
                    target.unlink()
                if action == "changed":
                    target.write_text("modified")
                elif action == "symlink":
                    target.symlink_to(ROOT / "fixtures/healthy/case/results.json")
                with self.assertRaises(ResultRejected) as cm:
                    read_verified_result(record, "results", record.data["run_id"], record.data["input_version"])
                self.assertEqual(cm.exception.code, expected)

    def test_guarded_json_does_not_allow_duplicate_keys_or_nan(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.record()
            record.case_dir = Path(tmp)
            for raw in (b'{"a":NaN}', b'{"a":1,"a":2}'):
                (record.case_dir / "results.json").write_bytes(raw)
                record.data["artifacts"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
                with self.assertRaises(ResultRejected) as cm:
                    read_verified_json(record, "results", record.data["run_id"], record.data["input_version"])
                self.assertEqual(cm.exception.code, "invalid_result_json")

    def test_cli_reports_refusal_with_exit1(self):
        result = subprocess.run([sys.executable, "-m", "simclinic", "guard-result", "fixtures/healthy/record.json", "results", "--case", "fixtures/healthy/case", "--expected-run-id", "new-run", "--expected-input-version", "new-input"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["allowed"])


if __name__ == "__main__":
    unittest.main()
