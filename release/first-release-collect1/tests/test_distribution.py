"""Acceptance tests against the installed minimal package, not the repository."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class InstalledDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="simclinic installed demo ")
        cls.work = Path(cls.tmp.name)
        cls.out = cls.work / "展示 输出"
        result = cls.command("showcase", "--out", str(cls.out))
        if result.returncode != 0:
            cls.tmp.cleanup()
            raise AssertionError(result.stderr + result.stdout)
        cls.summary = json.loads((cls.out / "summary.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def command(cls, *args):
        return subprocess.run([sys.executable, "-I", "-B", "-m", "simclinic", *args],
                              cwd=cls.work, capture_output=True, text=True, timeout=30)

    def test_complete_installed_demo_and_links(self):
        self.assertEqual(self.summary["status"], "pass")
        self.assertEqual(self.summary["new_shell_processes"], 4)
        self.assertEqual(self.summary["new_model_calls"], 0)
        self.assertEqual(self.summary["new_cfd_runs"], 0)
        for arm in ("before", "after"):
            self.assertEqual(self.summary["live_shell"]["exit7"][arm]["actual_exit_code"], 7)
            self.assertTrue(self.summary["live_shell"]["exit0"][arm]["reported_execution_success"])
        self.assertTrue(self.summary["live_shell"]["exit7"]["before"]["reported_execution_success"])
        self.assertFalse(self.summary["live_shell"]["exit7"]["after"]["reported_execution_success"])
        self.assertEqual([a["allowed"] for a in self.summary["result_guard"]], [True, False, False, False])
        self.assertFalse(self.summary["archives"]["no-log-failure"]["after"]["task_completion"])
        self.assertTrue(self.summary["archives"]["healthy"]["after"]["task_completion"])
        application = json.loads((self.out / "patch-application.json").read_text())
        self.assertEqual(len(application["commands"]), 4)
        self.assertTrue(all(c["exit_code"] == 0 for c in application["commands"]))
        for link in re.findall(r'href="([^"]+)"', (self.out / "index.html").read_text()):
            self.assertTrue((self.out / link).is_file(), link)

    def test_audit_finding_and_retest(self):
        for arm, code, status in (("before", 1, "finding"), ("after", 0, "not_applicable")):
            case = self.out / "archives" / arm / "no-log-failure"
            folder = self.work / ("audit-" + arm)
            result = self.command("audit", str(case / ".simclinic/record.json"), "--case", str(case),
                                  "--out", str(folder), "--fail-on-finding")
            self.assertEqual(result.returncode, code, result.stderr)
            report = json.loads((folder / "report.json").read_text())
            self.assertEqual(next(c["status"] for c in report["checks"] if c["check_id"] == "error_continuation"), status)

    def test_import_compare_and_parse_log(self):
        before = self.out / "archives/before/no-log-failure/.simclinic/record.json"
        after = self.out / "archives/after/no-log-failure/.simclinic/record.json"
        imported = self.work / "imported.json"
        self.assertEqual(self.command("import", str(after), "--out", str(imported)).returncode, 0)
        self.assertEqual(json.loads(imported.read_text()), json.loads(after.read_text()))
        comparison = self.work / "comparison.json"
        self.assertEqual(self.command("compare", str(before), str(after), "--out", str(comparison)).returncode, 0)
        self.assertTrue(json.loads(comparison.read_text())["comparable"])
        parsed = self.work / "log.json"
        self.assertEqual(self.command("parse-log", str(self.out / "archives/after/healthy/log.simpleFoam"),
                                      "--out", str(parsed)).returncode, 0)
        self.assertTrue(parsed.is_file())

    def test_guard_rejects_wrong_identity(self):
        case = self.out / "archives/after/healthy"
        record = json.loads((case / ".simclinic/record.json").read_text())
        artifact = next(a["id"] for a in record["artifacts"] if a["path"] == ".simclinic/acceptance.json")
        result = self.command("guard-result", str(case / ".simclinic/record.json"), artifact, "--case", str(case),
                              "--expected-run-id", "another-run", "--expected-input-version", record["input_version"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["code"], "stale_run")

    def test_version_help_and_output_not_overwritten(self):
        self.assertIn("0.11.0+collect1", self.command("--version").stdout)
        self.assertIn("collect", self.command("--help").stdout)
        self.assertIn("showcase", self.command("--help").stdout)
        before = (self.out / "summary.json").read_bytes()
        self.assertEqual(self.command("showcase", "--out", str(self.out)).returncode, 2)
        self.assertEqual((self.out / "summary.json").read_bytes(), before)

    def test_live_collection_and_patch_retest(self):
        from simclinic.core.record import load_record
        from simclinic.result_guard import read_verified_result
        records = {}
        for variant in ("baseline", "patched"):
            out = self.work / ("现场 " + variant)
            result = self.command("collect", "--example", "no-log-failure", "--variant", variant, "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            record = load_record(out / "record.json", out / "case")
            records[variant] = record
            self.assertFalse(record.data["synthetic"])
            self.assertFalse(record.data["agent"]["full_agent_workflow"])
            self.assertEqual(record.data["collection_status"], "complete")
            self.assertTrue(record.data["scope_terminal_observed"])
            self.assertTrue(all(record.data["coverage"].values()))
            tool = next(e for e in record.data["events"] if e.get("operation") == "Allrun")
            self.assertEqual(tool["exit_code"], 7)
            self.assertEqual(tool["outcome"], "fail")
            claim = next(e for e in record.data["events"] if e["kind"] == "claim")
            self.assertEqual(claim["success"], variant == "baseline")
            for artifact in record.data["artifacts"]:
                self.assertTrue(read_verified_result(record, artifact["id"], record.data["run_id"], record.data["input_version"]) is not None)
            report = json.loads((out / "audit/report.json").read_text())
            self.assertEqual(next(c["status"] for c in report["checks"] if c["check_id"] == "error_continuation"),
                             "finding" if variant == "baseline" else "not_applicable")
            summary = json.loads((out / "collection-summary.json").read_text())
            self.assertEqual(summary["new_cfd_runs"], 0)
            self.assertEqual(summary["new_model_calls"], 0)
            self.assertEqual(summary["acceptance_stages"]["execution"], "not_met")
            self.assertFalse(any(e.get("claim_type") == "completion" for e in record.data["events"]))
            original = (out / "record.json").read_bytes()
            self.assertEqual(self.command("collect", "--example", "execution-ok", "--out", str(out)).returncode, 2)
            self.assertEqual((out / "record.json").read_bytes(), original)
        self.assertNotEqual(records["baseline"].data["run_id"], records["patched"].data["run_id"])
        self.assertEqual(records["baseline"].data["input_version"], records["patched"].data["input_version"])
        comparison = self.work / "live-comparison.json"
        self.assertEqual(self.command("compare", records["baseline"].source, records["patched"].source,
                                      "--out", str(comparison)).returncode, 0)
        self.assertTrue(json.loads(comparison.read_text())["comparable"])
        proof = json.loads((self.work / "现场 patched/patch-application.json").read_text())
        self.assertEqual(len(proof["commands"]), 4)
        self.assertTrue(all(c["exit_code"] == 0 for c in proof["commands"]))
        normal = self.work / "现场 正常"
        result = self.command("collect", "--example", "execution-ok", "--out", str(normal))
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((normal / "collection-summary.json").read_text())
        self.assertEqual(summary["actual_exit_code"], 0)
        self.assertTrue(summary["reported_execution_success"])
        self.assertEqual(summary["audit_summary"]["finding"], 0)

    def test_interrupted_capture_keeps_partial_record_and_unknown_coverage(self):
        from simclinic.live_collection import collect, observe_execution
        out = self.work / "中断采集"
        real_communicate = subprocess.Popen.communicate
        observed = []
        def interrupt_once(process, *args, **kwargs):
            if not observed:
                observed.append(process)
                raise KeyboardInterrupt
            return real_communicate(process, *args, **kwargs)
        def interrupt_execution(*args):
            with patch.object(subprocess.Popen, "communicate", new=interrupt_once):
                return observe_execution(*args)
        with patch("simclinic.live_collection.observe_execution", side_effect=interrupt_execution):
            with self.assertRaises(KeyboardInterrupt):
                collect(out, variant="baseline", example="no-log-failure")
        self.assertEqual(len(observed), 1)
        self.assertIsNotNone(observed[0].poll(), "Interrupted collection must not leave its Allrun process running")
        record = json.loads((out / "record.json").read_text())
        self.assertEqual(record["collection_status"], "aborted")
        self.assertFalse(any(record["coverage"].values()))
        self.assertFalse(any(e["kind"] in ("tool", "claim") for e in record["events"]))
        self.assertEqual(json.loads((out / "collection-summary.json").read_text())["status"], "aborted")

    def test_fixed_case_copy_rejects_changed_inputs_and_ignores_old_results(self):
        from simclinic.domains.openfoam.channel import create_channel
        from simclinic.live_collection import prepare_inputs, collect
        source = create_channel(self.work / "用户已有case")
        (source / "log.simpleFoam").write_text("old log must not be given current identity")
        (source / ".simclinic").mkdir()
        (source / ".simclinic/record.json").write_text("old record")
        original = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        scratch = self.work / "输入检查"
        scratch.mkdir()
        inputs, profile = prepare_inputs(source, None, scratch)
        self.assertEqual(profile, "fixed-channel")
        self.assertEqual(len(inputs), 10)
        self.assertNotIn("log.simpleFoam", inputs)
        self.assertNotIn(".simclinic/record.json", inputs)
        with self.assertRaises(ValueError):
            collect(source / "new-output", source=source)
        self.assertEqual(original, {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()})
        path = source / "system/fvSolution"
        path.write_bytes(path.read_bytes() + b"\n// changed by user\n")
        with self.assertRaises(ValueError):
            prepare_inputs(source, None, self.work / "changed-input-check")
        path.write_bytes(original["system/fvSolution"])
        path.unlink()
        path.symlink_to(scratch / "reference/system/fvSolution")
        with self.assertRaises(ValueError):
            prepare_inputs(source, None, self.work / "symlink-input-check")

    def test_native_assessment_reuses_archive_without_running_solver(self):
        """Replay tool boundaries with archived bytes; explicitly synthetic, no subprocess."""
        from simclinic.collection import Collector
        from simclinic.domains.openfoam.acceptance import channel_criteria
        from simclinic.domains.openfoam.channel import create_channel
        from simclinic.live_collection import run_chain
        archive = self.out / "archives/after/healthy"
        original = json.loads((archive / ".simclinic/record.json").read_text())
        case = create_channel(self.work / "离线评估回归")
        task = dict(id="channel-poiseuille-v1", objective="synthetic replay of archived observations",
                    required_preflight=["validate_case", "checkMesh"], acceptance=channel_criteria())
        collector = Collector(case, {}, task)
        collector.data["synthetic"] = True
        collector.save()
        artifacts = {a["id"]: a for a in original["artifacts"]}
        def copy_output(relative):
            target = case / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((archive / relative).read_bytes())
        def observed_tool(operation, phase, argv, outcome_parser=None):
            old = next(e for e in original["events"] if e.get("operation") == operation)
            if operation == "sample":
                for a in artifacts.values():
                    if a["path"].startswith("postProcessing/"):
                        copy_output(a["path"])
            return collector.event("tool", operation=operation, phase=phase, exit_code=old["exit_code"],
                outcome=old["outcome"], input_fingerprint=old["input_fingerprint"], error_category=old["error_category"],
                arguments={"argv": argv}, replayed_from_archive=True)
        def archived_execution(*args):
            for name in (".simclinic/upstream.json", "workflow.log", "Allrun.out", "Allrun.err", "log.simpleFoam"):
                if (archive / name).is_file():
                    copy_output(name)
        with patch.object(collector, "tool", side_effect=observed_tool), \
                patch("simclinic.live_collection.observe_execution", side_effect=archived_execution):
            _, values = run_chain(collector, self.work / "unused-execute.py", "patched", "fixed-channel")
        self.assertTrue(values["execution_end"])
        self.assertTrue(values["steady_converged"])
        self.assertLess(values["local_continuity"], 1e-7)
        self.assertLess(values["profile_error"], 0.02)
        self.assertLess(values["pressure_gradient_error"], 0.03)
        self.assertTrue(next(e["success"] for e in collector.data["events"] if e.get("claim_type") == "completion"))


if __name__ == "__main__":
    unittest.main()
