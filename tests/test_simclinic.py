import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from simclinic.adapters.foam_agent import FoamAgentTextAdapter
from simclinic.advisor import candidates
from simclinic.checks import audit
from simclinic.core.record import Record, RecordError, load_record, validate
from simclinic.domains.openfoam import parse_solver_log
from simclinic.evaluation import compare

ROOT = Path(__file__).resolve().parents[1]


def fixture(name="healthy"):
    return load_record(ROOT / "fixtures" / name / "record.json", ROOT / "fixtures" / name / "case")


def modified(name="healthy"):
    return Record(copy.deepcopy(fixture(name).data), "memory.json")


def statuses(record):
    return {r["check_id"]: r["status"] for r in audit(record)["checks"]}


def event(record, id):
    return next(e for e in record.data["events"] if e["id"] == id)


class BehaviorTests(unittest.TestCase):
    def test_healthy_all_applicable_pass(self):
        values = statuses(fixture())
        self.assertEqual(values["retries"], "not_applicable")
        self.assertEqual(set(values.values()), {"pass", "not_applicable"})

    def test_each_fault_links_resolvable_evidence(self):
        record = fixture("faults")
        report = audit(record)
        self.assertEqual(report["summary"]["finding"], 5)
        for check in report["checks"]:
            for detail in check["details"]:
                self.assertTrue(detail["evidence"])
                for evidence in detail["evidence"]:
                    value = record.data
                    for token in evidence["pointer"].strip("/").split("/"):
                        value = value[int(token)] if isinstance(value, list) else value[token]
                    self.assertIsNotNone(value)

    def test_missing_evidence_is_never_fault(self):
        self.assertEqual(set(statuses(fixture("missing")).values()), {"insufficient_evidence"})

    def test_explicit_no_work_is_not_applicable(self):
        self.assertEqual(set(statuses(fixture("not-applicable")).values()), {"not_applicable"})

    def test_missing_call_requires_complete_tool_stream(self):
        record = modified()
        record.data["events"] = [e for e in record.data["events"] if e["id"] not in ("mesh", "handled-mesh")]
        self.assertEqual(statuses(record)["preflight"], "finding")
        record.data["coverage"]["tools"] = False
        self.assertEqual(statuses(record)["preflight"], "insufficient_evidence")

    def test_handling_missing_distinguishes_coverage(self):
        record = modified()
        record.data["events"] = [e for e in record.data["events"] if e["id"] != "handled-mesh"]
        self.assertEqual(statuses(record)["preflight"], "finding")
        record.data["coverage"]["decisions"] = False
        self.assertEqual(statuses(record)["preflight"], "insufficient_evidence")

    def test_preflight_cannot_use_prior_input(self):
        record = modified()
        event(record, "mesh")["input_version"] = "old-input"
        self.assertEqual(statuses(record)["preflight"], "finding")

    def test_nonzero_exit_overrides_pass_label(self):
        record = modified()
        event(record, "mesh")["exit_code"] = 1
        self.assertEqual(statuses(record)["preflight"], "finding")
        self.assertEqual(statuses(record)["error_continuation"], "finding")

    def test_unknown_exit_cannot_pass(self):
        record = modified()
        event(record, "solve")["exit_code"] = None
        self.assertEqual(statuses(record)["error_continuation"], "insufficient_evidence")

    def test_successful_retry_resolves_error(self):
        self.assertEqual(set(statuses(fixture("recovered")).values()), {"pass"})

    def test_unrelated_success_does_not_resolve_failure(self):
        record = modified("recovered")
        event(record, "retry").pop("retry_of")
        self.assertEqual(statuses(record)["error_continuation"], "finding")

    def test_acknowledging_error_does_not_resolve_it(self):
        record = modified()
        event(record, "solve").update(exit_code=1, outcome="fail")
        record.data["events"].insert(5, {"id": "ack-error", "kind": "decision", "run_id": "run-new", "input_version": "sha256:input-v2", "handles": ["solve"]})
        self.assertEqual(statuses(record)["error_continuation"], "finding")

    def test_explicit_unsuccessful_claim_is_not_false_completion(self):
        record = modified("faults")
        event(record, "complete")["success"] = False
        self.assertEqual(statuses(record)["error_continuation"], "not_applicable")
        self.assertEqual(statuses(record)["completion"], "not_applicable")

    def test_conclusion_also_detects_error_continuation(self):
        record = modified("faults")
        event(record, "complete")["claim_type"] = "conclusion"
        self.assertEqual(statuses(record)["error_continuation"], "finding")
        self.assertEqual(statuses(record)["completion"], "not_applicable")

    def test_changed_arguments_not_flagged_as_identical_retry(self):
        record = modified("faults")
        event(record, "retry")["arguments"] = {"new": True}
        self.assertEqual(statuses(record)["retries"], "pass")

    def test_unknown_retry_fingerprint_is_insufficient(self):
        record = modified("faults")
        event(record, "retry")["input_fingerprint"] = None
        self.assertEqual(statuses(record)["retries"], "insufficient_evidence")

    def test_changed_state_fingerprint_is_not_identical(self):
        record = modified("faults")
        event(record, "retry")["input_fingerprint"] = "changed-state"
        self.assertEqual(statuses(record)["retries"], "pass")

    def test_partial_reference_stream_does_not_get_global_pass(self):
        record = modified()
        record.data["coverage"]["references"] = False
        self.assertEqual(statuses(record)["stale_results"], "insufficient_evidence")

    def test_stale_result_even_when_filename_same(self):
        self.assertEqual(statuses(fixture("stale"))["stale_results"], "finding")

    def test_unknown_result_id_is_insufficient(self):
        record = modified()
        record.data["artifacts"][0]["run_id"] = None
        self.assertEqual(statuses(record)["stale_results"], "insufficient_evidence")

    def test_reference_is_checked_against_context_at_event_time(self):
        record = modified()
        record.data["run_id"] = "later-run"
        record.data["input_version"] = "later-input"
        self.assertEqual(statuses(record)["stale_results"], "pass")

    def test_end_does_not_replace_numerical_and_reference_criteria(self):
        record = modified()
        event(record, "m-residual")["value"] = 1.0
        self.assertEqual(statuses(record)["completion"], "finding")
        event(record, "m-residual")["value"] = 1e-6
        event(record, "m-reference")["value"] = 1.0
        self.assertEqual(statuses(record)["completion"], "finding")

    def test_measurement_after_claim_cannot_justify_completion(self):
        record = modified()
        measure = event(record, "m-reference")
        record.data["events"].remove(measure)
        record.data["events"].append(measure)
        self.assertEqual(statuses(record)["completion"], "finding")

    def test_missing_measurement_respects_acceptance_coverage(self):
        record = modified()
        record.data["events"] = [e for e in record.data["events"] if e["kind"] != "measurement"]
        self.assertEqual(statuses(record)["completion"], "finding")
        record.data["coverage"]["acceptance"] = False
        self.assertEqual(statuses(record)["completion"], "insufficient_evidence")

    def test_no_acceptance_does_not_pass_completion(self):
        record = modified()
        record.data["task"]["acceptance"] = []
        self.assertEqual(statuses(record)["completion"], "insufficient_evidence")

    def test_boolean_is_not_numeric_measurement(self):
        record = modified()
        event(record, "m-residual")["value"] = False
        self.assertEqual(statuses(record)["completion"], "insufficient_evidence")

    def test_missing_or_changed_file_does_not_become_agent_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = modified()
            record.case_dir = Path(tmp)
            self.assertEqual(statuses(record)["stale_results"], "insufficient_evidence")
            (Path(tmp) / "results.json").write_text("changed content")
            self.assertEqual(statuses(record)["completion"], "insufficient_evidence")

    def test_symlink_outside_case_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "case").mkdir()
            (directory / "outside").write_text("data")
            (directory / "case" / "results.json").symlink_to(directory / "outside")
            record = modified()
            record.case_dir = directory / "case"
            self.assertEqual(statuses(record)["completion"], "insufficient_evidence")

    def test_advisor_uses_only_confirmed_findings(self):
        self.assertEqual(candidates(audit(fixture("missing"))), [])
        proposals = candidates(audit(fixture("faults")))
        self.assertEqual(len(proposals), 5)
        self.assertTrue(all(p["evidence"] and p["status"] == "candidate_not_applied" for p in proposals))


class ImportAndDomainTests(unittest.TestCase):
    def test_raw_foam_logs_preserve_lines_and_uncertainty_without_duplicates(self):
        path = ROOT / "fixtures" / "foam-agent-text"
        record = FoamAgentTextAdapter().import_record(path, path / "metadata.json")
        self.assertEqual(len(record.data["events"]), 3)
        self.assertFalse(any(record.data["coverage"].values()))
        self.assertEqual(record.data["events"][1]["source"]["line"], 2)
        self.assertEqual(set(statuses(record).values()), {"insufficient_evidence"})
        self.assertIsNone(record.data["artifacts"][0]["run_id"])

    def test_review_only_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "review.log").write_text("<error_logs>\nerror\n</error_logs>\n")
            record = FoamAgentTextAdapter().import_record(path, ROOT / "fixtures/foam-agent-text/metadata.json")
            self.assertEqual(record.data["artifacts"][0]["path"], "review.log")

    def test_end_and_linear_residuals_do_not_prove_convergence(self):
        result = parse_solver_log((ROOT / "fixtures/healthy/case/log.simpleFoam").read_text())
        self.assertEqual(result["execution_status"], "end_observed")
        self.assertEqual(result["numerical_acceptance"], "not_assessed")
        self.assertAlmostEqual(result["final_linear_residuals"]["p"]["value"], 1e-6)

    def test_fatal_error_overrides_end(self):
        result = parse_solver_log("FOAM FATAL IO ERROR\nEnd\n")
        self.assertEqual(result["execution_status"], "fail")
        self.assertEqual(result["fatal_errors"][0]["line"], 1)

    def test_invalid_residual_unknown(self):
        result = parse_solver_log("Solving for p, Initial residual = 1, Final residual = nan, No Iterations 2")
        self.assertIsNone(result["final_linear_residuals"]["p"]["value"])

    def test_duplicate_ids_dangling_refs_and_traversal_rejected(self):
        for mutate in (
            lambda d: d["events"].append(copy.deepcopy(d["events"][0])),
            lambda d: d["events"][5].update(artifact_id="nonexistent"),
            lambda d: d["artifacts"][0].update(path="../outside"),
            lambda d: d["events"][0].update(retry_of="solve"),
            lambda d: d.update(synthetic="true"),
            lambda d: d["coverage"].update(tools="true"),
        ):
            with self.subTest(mutate=mutate):
                d = modified().data
                mutate(d)
                with self.assertRaises(RecordError):
                    validate(d)

    def test_nonfinite_json_and_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            for raw in ('{"a":NaN}', '{"a":1,"a":2}'):
                path.write_text(raw)
                with self.assertRaises(RecordError):
                    load_record(path)

    def test_comparison_keeps_unknown_metrics_unknown(self):
        result = compare(fixture("stale"), fixture())
        self.assertTrue(result["comparable"])
        self.assertIsNone(result["metrics"]["solver_seconds"]["delta"])

    def test_changed_acceptance_environment_and_input_block_comparability(self):
        for mutate in (
            lambda d: d["task"]["acceptance"][1].update(target=10),
            lambda d: d["environment"].update(version="11"),
            lambda d: d.update(input_version="new"),
            lambda d: d["coverage"].update(tools=False),
        ):
            record = modified()
            mutate(record.data)
            self.assertFalse(compare(fixture(), record)["comparable"])

    def test_unknown_environment_cannot_be_comparable(self):
        a, b = modified(), modified()
        a.data["environment"] = b.data["environment"] = {}
        self.assertFalse(compare(a, b)["comparable"])


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "simclinic", *map(str, args)], cwd=ROOT, capture_output=True, text=True)

    def test_audit_outputs_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("audit", ROOT / "fixtures/faults/record.json", "--case", ROOT / "fixtures/faults/case", "--out", tmp, "--fail-on-finding")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads((Path(tmp) / "report.json").read_text())["summary"]["finding"], 5)
            self.assertIn("合成数据", (Path(tmp) / "report.md").read_text())

    def test_bad_input_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{}")
            result = self.run_cli("audit", path, "--out", Path(tmp) / "report")
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)

    def test_normalized_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            result = self.run_cli("import", ROOT / "fixtures/healthy/record.json", "--out", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(load_record(path).data, fixture().data)

    def test_import_cannot_overwrite_raw_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            log = path / "workflow.log"
            log.write_text("original")
            result = self.run_cli("import", path, "--adapter", "foam-agent-text", "--metadata", ROOT / "fixtures/foam-agent-text/metadata.json", "--out", log)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(log.read_text(), "original")

    def test_audit_cannot_overwrite_input_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            artifact = path / "report.json"
            artifact.write_text("original artifact")
            data = modified().data
            data["artifacts"][0]["path"] = "report.json"
            data["artifacts"][0].pop("sha256")
            record = path / "record.json"
            record.write_text(json.dumps(data))
            result = self.run_cli("audit", record, "--case", path, "--out", path)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(artifact.read_text(), "original artifact")


if __name__ == "__main__":
    unittest.main()
