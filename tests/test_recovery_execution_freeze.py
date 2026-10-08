"""Meaningful offline tests for immutable execution snapshots and honest probes."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.evaluation import recovery_execution_freeze as freeze


ROOT = Path(__file__).resolve().parents[1]


def environment():
    config = freeze.read(ROOT / freeze.PROTOCOL_RELATIVE / 'protocol.json')
    return dict(schema='test-only-environment-fixture', observation_mode='software_fixture',
                binaries={name: dict(requested='/fixture/' + name, path='/fixture/' + name,
                                    resolved_path='/fixture/' + name, sha256='a' * 64, status='pass')
                          for name in ('python', 'codex', 'docker', 'idle_guard')},
                idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256='a' * 64,
                                     argv_template=['/usr/bin/caffeinate', '-i', '-w', '<coordinator_pid>'],
                                     started=False, persistent_settings_changed=False),
                python=dict(version='fixture-python', executable='/fixture/python', dependencies=['sample==1']),
                pip_freeze=['sample==1'], codex_version='fixture-codex',
                docker_version=dict(Client=dict(Version='fixture'), Server=dict(Version='fixture')),
                docker_info=dict(ID='fixture-daemon'), image=config['environment_basis']['image'],
                image_inspection=dict(Id=config['environment_basis']['image_id'], Architecture='amd64', Os='linux'),
                configured_model=config['environment_basis']['model'],
                configured_reasoning_effort=config['environment_basis']['model_reasoning_effort'],
                requested_resources=config['environment_basis']['resources'], environment_gaps=[],
                environment_preflight_pass=True,
                model_availability=dict(status='unknown', probed=False),
                execution_authorized=False, new_model_calls=0, new_simulations=0, new_sampling_calls=0)


class RecoveryExecutionFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / freeze.PRIOR_RELATIVE / 'freeze.json').is_file():
            raise unittest.SkipTest('Optional historical freeze basis; see docs/archive-access.md')
        cls.workspace = tempfile.TemporaryDirectory(prefix='simclinic-execution-freeze-tests-')
        cls.root = Path(cls.workspace.name) / 'source'
        cls.root.mkdir()
        for relative in (freeze.PROTOCOL_RELATIVE, freeze.NATIVE_RELATIVE,
                         freeze.PRIOR_RELATIVE + '/corpus', 'simclinic'):
            freeze._copy_tree(ROOT / relative, cls.root / relative)
        for relative in (freeze.REQUIREMENTS_RELATIVE, freeze.MANIFEST_RELATIVE,
                         freeze.PRIOR_RELATIVE + '/freeze.json'):
            target = cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        for relative in freeze.RUNTIME_SCRIPTS:
            target = cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = ROOT / relative
            if source.is_file():
                shutil.copyfile(source, target)
            else:
                target.write_text('# Inert coordinator fixture; this is not executed.\n')

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def build(self, output):
        with patch.object(freeze, 'observe_environment', return_value=environment()):
            return freeze.build_freeze(self.root, Path(output).resolve())

    def test_snapshot_pins_runtime_native_corpus_tasks_and_does_not_grant_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'bundle'
            result = self.build(output)
            frozen = freeze.read(output / 'freeze.json')
            self.assertTrue(result['archive_integrity_verified'])
            self.assertFalse(result['execution_authorized'])
            self.assertFalse(result['heldout_ready'])
            self.assertFalse(result['model_availability_verified'])
            self.assertFalse(result['new_c0_development_checks_passed'])
            self.assertEqual(result['new_model_calls'], 0)
            self.assertEqual(frozen['counts']['total'], 76)
            self.assertEqual(set(frozen['trees']), {'common-runtime', 'native', 'corpus',
                             'definitions', 'protocol-design', 'basis'})
            self.assertIn('simclinic/evaluation/recovery_execution_freeze.py', frozen['common_runtime'])
            self.assertIn('scripts/run_recovery_observation.py', frozen['common_runtime'])

    def test_existing_output_and_historical_destination_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'bundle'
            self.build(output)
            original = freeze.sha(output / 'freeze.json')
            with self.assertRaisesRegex(ValueError, 'destination must be new'):
                self.build(output)
            self.assertEqual(original, freeze.sha(output / 'freeze.json'))
        with self.assertRaisesRegex(ValueError, 'historical/source trees'):
            self.build(self.root / 'real-runs/new-freeze')

    def test_symlinked_destination_parent_is_rejected_before_any_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            real = parent / 'real'
            real.mkdir()
            alias = parent / 'alias'
            alias.symlink_to(real, target_is_directory=True)
            with patch.object(freeze, 'observe_environment', side_effect=AssertionError('must not probe')):
                with self.assertRaisesRegex(ValueError, 'Symlink execution freeze'):
                    freeze.build_freeze(self.root, alias / 'bundle')
            self.assertFalse((real / 'bundle').exists())

    def assert_migrated_archive_marker_blocks_nested_build(self, marker):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            original = parent / 'original'
            self.build(original)
            other_marker = 'freeze.sha256' if marker == 'freeze.json' else 'freeze.json'
            (original / other_marker).unlink()
            original_hashes = freeze._hashes(original)
            migrated = parent / 'migrated-archive'
            shutil.move(str(original), migrated)
            self.assertEqual(original_hashes, freeze._hashes(migrated))
            output = migrated / 'nested/new-bundle'
            with patch.object(freeze, 'observe_environment', side_effect=AssertionError('must not probe')) as probe:
                with self.assertRaisesRegex(ValueError, 'nested inside an existing frozen archive'):
                    freeze.build_freeze(self.root, output)
                probe.assert_not_called()
            self.assertFalse((migrated / 'nested').exists())
            self.assertEqual(original_hashes, freeze._hashes(migrated))

    def test_migrated_freeze_json_marker_blocks_nested_build_before_probe(self):
        self.assert_migrated_archive_marker_blocks_nested_build('freeze.json')

    def test_migrated_freeze_sha256_marker_blocks_nested_build_before_probe(self):
        self.assert_migrated_archive_marker_blocks_nested_build('freeze.sha256')

    def test_portable_archive_validation_needs_no_runtime_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'original'
            self.build(original)
            moved = Path(directory) / 'moved'
            shutil.move(str(original), moved)
            with patch.object(freeze, 'observe_environment', side_effect=AssertionError('must not probe')):
                result = freeze.verify_freeze(moved)
            self.assertTrue(result['archive_integrity_verified'])
            self.assertEqual(result['current_environment_verification']['status'], 'not_checked')

    def test_byte_tampering_extra_files_and_symlink_are_refused(self):
        for mode in ('changed', 'extra', 'symlink'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'bundle'
                self.build(output)
                target = output / 'definitions/C7/spec.json'
                if mode == 'changed':
                    target.write_text('{}')
                elif mode == 'extra':
                    (output / 'undocumented.json').write_text('{}')
                else:
                    source = Path(directory) / 'outside.json'
                    source.write_bytes(target.read_bytes())
                    target.unlink()
                    target.symlink_to(source)
                with self.assertRaises(ValueError):
                    freeze.verify_freeze(output)

    def test_unknown_current_environment_is_not_a_match_or_an_archive_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'bundle'
            self.build(output)
            current = environment()
            current.update(environment_gaps=['docker_image_inspect_probe_unknown'], environment_preflight_pass=False)
            with patch.object(freeze, 'observe_environment', return_value=current):
                result = freeze.verify_freeze(output, check_current_environment=True)
            self.assertTrue(result['archive_integrity_verified'])
            self.assertEqual(result['current_environment_verification']['status'], 'unknown')
            self.assertIsNone(result['current_environment_verification']['matches'])

    def test_current_dependency_cli_or_image_drift_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'bundle'
            self.build(output)
            current = environment()
            current['pip_freeze'] = ['sample==2']
            current['codex_version'] = 'changed-codex'
            current['image_inspection']['Id'] = 'sha256:' + 'b' * 64
            with patch.object(freeze, 'observe_environment', return_value=current):
                result = freeze.verify_freeze(output, check_current_environment=True)
            self.assertTrue(result['archive_integrity_verified'])
            self.assertEqual(result['current_environment_verification']['status'], 'mismatch')
            self.assertEqual(set(result['current_environment_verification']['mismatches']),
                             {'pip_freeze', 'codex_version', 'image_inspection'})

    def test_resolved_executable_spelling_is_not_environment_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'bundle'
            self.build(output)
            current = environment()
            current['binaries']['docker']['requested'] = 'docker'
            with patch.object(freeze, 'observe_environment', return_value=current):
                result = freeze.verify_freeze(output, check_current_environment=True)
            self.assertEqual(result['current_environment_verification']['status'], 'pass')

    def test_current_source_drift_is_not_overwritten_or_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'bundle'
            self.build(output)
            source = self.root / 'scripts/run_recovery_observation.py'
            original = source.read_bytes()
            try:
                source.write_bytes(original + b'\n# later developer change\n')
                result = freeze.verify_freeze(output, root=self.root)
                self.assertTrue(result['archive_integrity_verified'])
                self.assertEqual(result['current_source_verification']['status'], 'mismatch')
                self.assertIn('scripts/run_recovery_observation.py', result['current_source_verification']['mismatches'])
                self.assertEqual(source.read_bytes(), original + b'\n# later developer change\n')
            finally:
                source.write_bytes(original)

    def test_probe_allowlist_contains_only_metadata_versions_and_inspection(self):
        config = freeze.read(ROOT / freeze.PROTOCOL_RELATIVE / 'protocol.json')
        locked = (ROOT / freeze.REQUIREMENTS_RELATIVE).read_text()

        def command(argv, **kwargs):
            if argv[1:2] == ['-c']:
                payload = dict(version='fixture', executable='/fixture/python', dependencies=['sample==1'])
            elif argv[1:4] == ['-m', 'pip', 'freeze']:
                return subprocess.CompletedProcess(argv, 0, locked, '')
            elif argv[1:] == ['--version']:
                return subprocess.CompletedProcess(argv, 0, 'fixture-codex\n', '')
            elif argv[1:3] == ['image', 'inspect']:
                payload = [dict(Id=config['environment_basis']['image_id'], Architecture='amd64', Os='linux')]
            elif argv[1] in ('info', 'version'):
                payload = {'fixture': True}
            else:
                raise AssertionError('Non-metadata command attempted: ' + str(argv))
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')

        with patch.object(freeze.subprocess, 'run', side_effect=command) as run, \
                patch.object(freeze, '_binary', return_value=dict(status='pass', path='/fixture', sha256='a' * 64)):
            result = freeze.observe_environment(ROOT)
        self.assertEqual(run.call_count, 6)
        self.assertTrue(result['environment_preflight_pass'])
        self.assertEqual(result['model_availability']['status'], 'unknown')
        self.assertFalse(result['model_availability']['probed'])
        self.assertEqual(result['new_model_calls'], 0)
        self.assertTrue(result['dependency_lock']['matches_installed'])

    def test_probe_failure_preserves_unknown_without_filling_in_old_image(self):
        with patch.object(freeze.subprocess, 'run', side_effect=FileNotFoundError('missing tool')), \
                patch.object(freeze, '_binary', return_value=dict(status='unknown', path=None, sha256=None)):
            result = freeze.observe_environment(ROOT)
        self.assertFalse(result['environment_preflight_pass'])
        self.assertIsNone(result['image_inspection'])
        self.assertIsNone(result['python'])
        self.assertIsNone(result['pip_freeze'])
        self.assertTrue(result['environment_gaps'])
        self.assertFalse(result['execution_authorized'])


if __name__ == '__main__':
    unittest.main()
