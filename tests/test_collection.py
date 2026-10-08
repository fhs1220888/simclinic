import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from simclinic.collection import Collector, input_manifest
from simclinic.checks import audit
from simclinic.core.record import load_record
from simclinic.domains.openfoam.acceptance import pressure_gradient_error, profile_error, solver_measurements
from simclinic.domains.openfoam.channel import create_channel, SPEC


class CollectionTests(unittest.TestCase):
    def task(self):
        return dict(id="capture-test", objective="test observed processes", required_preflight=[], acceptance=[])

    def test_real_subprocess_failure_has_actual_exit_and_log_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = create_channel(Path(tmp) / "case")
            collector = Collector(case, {"runtime_digest": "test"}, self.task())
            e = collector.tool("test", "other", [sys.executable, "-c", "print('observed'); raise SystemExit(7)"])
            self.assertEqual(e["exit_code"], 7)
            self.assertEqual(e["outcome"], "fail")
            record = load_record(case / ".simclinic/record.json", case)
            self.assertFalse(record.data["synthetic"])
            self.assertEqual(record.data["artifacts"][0]["run_id"], record.data["run_id"])
            self.assertIn("observed", (case / record.data["artifacts"][0]["path"]).read_text())

    def test_versions_change_with_input_and_runs_are_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = create_channel(Path(tmp) / "a")
            b = create_channel(Path(tmp) / "b")
            x, y = Collector(a, {}, self.task()), Collector(b, {}, self.task())
            self.assertEqual(x.data["input_version"], y.data["input_version"])
            self.assertNotEqual(x.data["run_id"], y.data["run_id"])
            before = input_manifest(a)
            path = a / "0/U"
            path.write_text(path.read_text() + "\n// modified input\n")
            self.assertNotEqual(before, input_manifest(a))

    def test_existing_cases_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = create_channel(Path(tmp) / "case")
            with self.assertRaises(ValueError):
                create_channel(case)
            Collector(case, {}, self.task())
            with self.assertRaises(FileExistsError):
                Collector(case, {}, self.task())

    def test_mesh_check_exit_zero_can_still_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = create_channel(Path(tmp) / "case")
            collector = Collector(case, {}, self.task())
            e = collector.tool("mesh", "preflight", [sys.executable, "-c", "print('Failed 1 mesh checks.')"], lambda text: "fail")
            self.assertEqual(e["exit_code"], 0)
            self.assertEqual(e["outcome"], "fail")

    def test_partial_collection_never_claims_complete_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = create_channel(Path(tmp) / "case")
            collector = Collector(case, {}, self.task())
            self.assertFalse(any(load_record(case / ".simclinic/record.json").data["coverage"].values()))
            self.assertTrue(all(collector.record().data["coverage"].values()))

    def test_acceptance_failure_is_separate_from_false_completion(self):
        from test_simclinic import modified, event
        record = modified()
        event(record, "m-residual")["value"] = 0.1
        event(record, "complete")["success"] = False
        report = audit(record)
        self.assertEqual(report["acceptance_observations"]["stages"], {"execution": "met", "numerical": "not_met", "reference": "met"})
        self.assertEqual(next(c for c in report["checks"] if c["check_id"] == "completion")["status"], "not_applicable")


class ChannelAcceptanceTests(unittest.TestCase):
    def test_exact_analytic_profile_then_wrong_transverse_velocity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile_U.xy"
            data = []
            for i in range(20):
                y = 0.0025 + i*0.005
                u = 6*SPEC["mean_velocity"]*(y/SPEC["height"])*(1-y/SPEC["height"])
                data.append(f"{y:.12g} {u:.12g} 0 0")
            path.write_text("\n".join(data))
            self.assertLess(profile_error(path), 1e-12)
            path.write_text("\n".join(row.rsplit(" 0 0", 1)[0] + " 0.01 0" for row in data))
            self.assertGreater(profile_error(path), 0.1)

    def test_kinematic_pressure_gradient_ignores_gauge_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure_p.xy"
            slope = -12*SPEC["nu"]*SPEC["mean_velocity"]/SPEC["height"]**2
            path.write_text("\n".join(f"{0.1+i*0.05:.12g} {3+slope*(0.1+i*0.05):.12g}" for i in range(17)))
            self.assertLess(pressure_gradient_error(path), 1e-10)
            path.write_text("\n".join(f"{0.1+i*0.05:.12g} {3-slope*(0.1+i*0.05):.12g}" for i in range(17)))
            self.assertAlmostEqual(pressure_gradient_error(path), 2.0)

    def test_partial_profile_or_nan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile_U.xy"
            for value in ("0.1 0.01 0 0\n0.2 0.01 0 0", "0.1 nan 0 0\n0.2 0.01 0 0"):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    profile_error(path)

    def test_actual_foundation10_combined_column_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.xy"
            lines = ["# y U_x U_y U_z p"]
            for i in range(20):
                y = 0.0025 + i*0.005
                u = 6*SPEC["mean_velocity"]*(y/SPEC["height"])*(1-y/SPEC["height"])
                lines.append(f"{y:.12g} {u:.12g} 0 0 999")
            path.write_text("\n".join(lines))
            self.assertLess(profile_error(path), 1e-12)
            path.write_text(path.read_text().replace("U_x", "wrong_field"))
            with self.assertRaises(ValueError):
                profile_error(path)

    def test_execution_end_and_steady_acceptance_are_separate(self):
        a = solver_measurements("End\n", 0)
        self.assertTrue(a["execution_end"])
        self.assertFalse(a["steady_converged"])
        self.assertIsNone(a["local_continuity"])
        b = solver_measurements("time step continuity errors : sum local = 1e-9, global = 0, cumulative = 0\nSIMPLE solution converged in 100 iterations\nEnd\n", 0)
        self.assertTrue(b["steady_converged"])
        self.assertAlmostEqual(b["local_continuity"], 1e-9)
        self.assertFalse(solver_measurements("End\n", 7)["execution_end"])


if __name__ == "__main__":
    unittest.main()
