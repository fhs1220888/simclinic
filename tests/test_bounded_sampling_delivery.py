"""Offline source-delivery checks: no model, Docker, sampling, or CFD calls."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from simclinic.domains.openfoam.bounded_sampling_candidate import completion_policy
from simclinic.domains.openfoam.input_contract import (
    ContractReferenceCompletion,
    RecoveringContractReferenceCompletion,
)


ROOT = Path(__file__).resolve().parents[1]
LOCATION = ROOT / "patches/simclinic-bounded-sampling-recovery/delivery.py"
SPEC = importlib.util.spec_from_file_location("bounded_delivery", LOCATION)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


class BoundedSamplingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = delivery.package_manifest()

    def migrated_repository(self, temporary):
        repository = Path(temporary) / "migrated-repository"
        basis = self.manifest["basis"]["runtime_root"]
        names = {str(Path(basis) / name) for name in self.manifest["base_files"]}
        names.update(self.manifest["evidence_files"])
        names.update(self.manifest["protected_repository_files"])
        for name in names:
            target = repository / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return repository

    def test_explicit_opt_in_selects_exact_frozen_classes_without_execution(self):
        arguments = (object(), Path("reference"), "not-launched", dict(id="C0", kind="couette", wall_speed=.02, nx=40, ny=20))
        self.assertIs(type(completion_policy(*arguments)), ContractReferenceCompletion)
        candidate = completion_policy(*arguments, enabled=True)
        self.assertIs(type(candidate), RecoveringContractReferenceCompletion)
        self.assertFalse(hasattr(candidate, "recovery_attempts"))
        for ambiguous in (1, "true", None):
            with self.assertRaises(TypeError):
                completion_policy(*arguments, enabled=ambiguous)

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_isolated_install_and_reverse_restore_all_frozen_source_bytes(self):
        report = delivery.isolated_verification(self.manifest, ROOT)
        self.assertEqual(report["base_file_count"], 52)
        self.assertEqual(report["target_file_count"], 53)
        for name in ("target_bytes_match", "reverse_restored_all_basis_bytes", "temporary_environment_removed"):
            self.assertTrue(report[name])
        self.assertEqual(report["new_model_calls"], 0)
        self.assertEqual(report["new_simulations"], 0)

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_modified_basis_is_refused_without_overwriting_user_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = delivery.prepare(Path(temporary) / "runtime", self.manifest, ROOT)
            changed = root / "simclinic/domains/openfoam/sampling_recovery.py"
            payload = changed.read_bytes() + b"\n# local edit\n"
            changed.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "Tree differs"):
                delivery.apply(root, self.manifest)
            self.assertEqual(changed.read_bytes(), payload)
            self.assertFalse((root / next(iter(self.manifest["added_files"]))).exists())

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_reverse_refuses_modified_target_and_second_apply_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = delivery.prepare(Path(temporary) / "runtime", self.manifest, ROOT)
            delivery.apply(root, self.manifest)
            with self.assertRaisesRegex(ValueError, "Tree differs"):
                delivery.apply(root, self.manifest)
            changed = root / next(iter(self.manifest["added_files"]))
            payload = changed.read_bytes() + b"\n# local edit\n"
            changed.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "Tree differs"):
                delivery.reverse(root, self.manifest)
            self.assertEqual(changed.read_bytes(), payload)

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_existing_destination_unknown_files_and_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = delivery.prepare(Path(temporary) / "runtime", self.manifest, ROOT)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                delivery.prepare(root, self.manifest, ROOT)
            extra = root / "unexpected.py"
            extra.write_bytes(b"local data")
            with self.assertRaisesRegex(ValueError, "Tree differs"):
                delivery.apply(root, self.manifest)
            extra.unlink()
            extra.symlink_to(root / "simclinic/__init__.py")
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                delivery.apply(root, self.manifest)

    def test_archived_trees_and_path_traversal_are_never_mutable(self):
        with self.assertRaisesRegex(ValueError, "read-only"):
            delivery.mutable_root(ROOT / self.manifest["basis"]["runtime_root"])
        with tempfile.TemporaryDirectory() as temporary:
            for unsafe in ("../outside", "/absolute", "simclinic/../outside"):
                with self.assertRaises(ValueError):
                    delivery.path_in(Path(temporary), unsafe)

    def test_repository_entrypoint_equals_shipped_target(self):
        for name, item in self.manifest["added_files"].items():
            self.assertEqual((ROOT / name).read_bytes(), (delivery.PACKAGE / item["payload"]).read_bytes())

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_prepare_rejects_migrated_archive_but_allows_plain_temporary_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.migrated_repository(temporary)
            delivery.verify_sources(self.manifest, repository)
            batch = (repository / self.manifest["basis"]["freeze_path"]).parent
            blocked = batch / "new-runtime"
            before = delivery.files(repository)
            with self.assertRaisesRegex(ValueError, "read-only"):
                delivery.prepare(blocked, self.manifest, repository)
            self.assertFalse(blocked.exists())
            self.assertEqual(before, delivery.files(repository))
            allowed = delivery.prepare(Path(temporary) / "plain-runtime", self.manifest, repository)
            delivery.assert_tree(allowed, self.manifest["base_files"])

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_other_archives_with_either_marker_reject_apply_and_reverse(self):
        for marker in ("freeze.json", "freeze.sha256"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary) / "unrelated-frozen-batch"
                archive.mkdir()
                (archive / marker).write_bytes(b"frozen identity marker")
                base = delivery.prepare(Path(temporary) / "base", self.manifest, ROOT)
                frozen_base = archive / "base"
                shutil.move(str(base), str(frozen_base))
                with self.assertRaisesRegex(ValueError, "read-only"):
                    delivery.apply(frozen_base, self.manifest)
                delivery.assert_tree(frozen_base, self.manifest["base_files"])
                candidate = delivery.prepare(Path(temporary) / "candidate", self.manifest, ROOT)
                delivery.apply(candidate, self.manifest)
                frozen_candidate = archive / "candidate"
                shutil.move(str(candidate), str(frozen_candidate))
                with self.assertRaisesRegex(ValueError, "read-only"):
                    delivery.reverse(frozen_candidate, self.manifest)
                delivery.assert_tree(frozen_candidate, self.manifest["target_files"])

    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical delivery basis; see docs/archive-access.md')
    def test_verify_cli_never_creates_output_inside_migrated_or_other_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = self.migrated_repository(temporary)
            batch = (repository / self.manifest["basis"]["freeze_path"]).parent
            other = Path(temporary) / "other-archive"
            other.mkdir()
            (other / "freeze.sha256").write_bytes(b"frozen")
            before = delivery.files(repository)
            for archive in (batch, other):
                with self.subTest(archive=archive):
                    output = archive / "new-output/report.json"
                    result = subprocess.run([sys.executable, str(LOCATION), "verify", "--repository", str(repository),
                                             "--output", str(output)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("read-only", result.stderr)
                    self.assertFalse(output.parent.exists())
            self.assertEqual(before, delivery.files(repository))


if __name__ == "__main__":
    unittest.main()
