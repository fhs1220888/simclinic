"""Software-only subprocess and exact-boundary checks; no model/CFD execution."""
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

from scripts import verify_recovery_observation_python as verify


def report():
    return dict(status='pass', all_started=1, python_runtime=dict(version='software-fixture'),
        numeric_comparison_rule=dict(maximum_ulps=4, original_thresholds_changed=False),
        ledger_and_original_score=dict(recomputed_scoring=dict(N=1, S=1, E=0, total_tokens=120)),
        runs=[dict(id='software-run', execution_id='execution-1', native_identity=dict(run_id='run-1', input_version=1),
            status='pass', consumption=dict(model_tokens=120, task_elapsed_seconds=2.0),
            acceptance=dict(numeric_differences=[], values=dict(profile_error=.125, pressure_gradient_error=.25),
                criterion_decisions=dict(profile_error=True, pressure_gradient_error=True),
                original_criteria=[dict(id='profile_error', op='<=', threshold=.01)],
                stationarity=dict(velocity_change_scaled=.5),
                inlet_face_order=dict(west_face_y_centres=[.125, .25], expected_inlet_x_velocity=[.25, .5])),
            sampling_policy=dict(numeric_differences=[], injection_original_measurements=dict(
                profile_error=.125, pressure_gradient_error=.25)))])


def increment(value, count=1):
    for _ in range(count):
        value = math.nextafter(value, math.inf)
    return value


class RecoveryObservationPythonTests(unittest.TestCase):
    def test_only_audited_float_paths_accept_four_ulps_and_report_each_difference(self):
        a, b = report(), report()
        b['runs'][0]['acceptance']['values']['profile_error'] = increment(.125, 4)
        b['runs'][0]['acceptance']['inlet_face_order']['west_face_y_centres'][0] = increment(.125, 3)
        b['runs'][0]['sampling_policy']['injection_original_measurements']['pressure_gradient_error'] = increment(.25, 2)
        result = verify.compare_reports(a, b)
        self.assertEqual(result['differing_derived_values'], 3)
        self.assertEqual(result['maximum_observed_ulps'], 4)
        self.assertTrue(all(row['within_limit'] for row in result['differences']))

    def test_beyond_limit_nonfinite_and_unsupported_zero_are_rejected(self):
        for value in (increment(.125, 5), math.inf, 0.0):
            with self.subTest(value=value):
                a, b = report(), report()
                b['runs'][0]['acceptance']['values']['profile_error'] = value
                with self.assertRaises(ValueError):
                    verify.compare_reports(a, b)

    def test_stationarity_and_all_other_float_costs_stay_exact(self):
        for component, key in (('stationarity', 'velocity_change_scaled'), ('consumption', 'task_elapsed_seconds')):
            with self.subTest(component=component):
                a, b = report(), report()
                target = b['runs'][0]['acceptance'][component] if component == 'stationarity' else b['runs'][0][component]
                target[key] = increment(target[key])
                with self.assertRaises(ValueError):
                    verify.compare_reports(a, b)

    def test_identity_score_order_decisions_threshold_types_and_unknown_are_exact(self):
        mutations = (
            lambda b: b['runs'][0].update(execution_id='other-execution'),
            lambda b: b['runs'][0]['native_identity'].update(run_id='old-run'),
            lambda b: b['ledger_and_original_score']['recomputed_scoring'].update(total_tokens=121),
            lambda b: b['runs'][0]['acceptance']['criterion_decisions'].update(profile_error=False),
            lambda b: b['runs'][0]['acceptance']['original_criteria'][0].update(threshold=increment(.01)),
            lambda b: b['runs'][0]['acceptance']['values'].update(profile_error=0),
            lambda b: b['runs'][0]['acceptance']['values'].update(profile_error=None),
            lambda b: b['runs'].insert(0, dict(id='unexpected')),
        )
        for mutate in mutations:
            a, b = report(), report()
            mutate(b)
            with self.assertRaises(ValueError):
                verify.compare_reports(a, b)

    def test_ignored_runtime_audits_remain_in_raw_report_and_no_other_metadata_is_removed(self):
        a, b = report(), report()
        b['python_runtime']['version'] = 'other-software-fixture'
        b['runs'][0]['acceptance']['numeric_differences'].append(dict(path='retained-only'))
        original = copy.deepcopy(b)
        self.assertEqual(verify.compare_reports(a, b)['status'], 'pass')
        self.assertEqual(b, original)
        b['numeric_comparison_rule']['maximum_ulps'] = 5
        with self.assertRaises(ValueError):
            verify.compare_reports(a, b)

    def test_output_rejects_existing_input_trees_and_either_migrated_freeze_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            batch, freeze = root / 'batch', root / 'freeze'
            batch.mkdir(); freeze.mkdir()
            existing = root / 'existing'; existing.mkdir()
            for target in (existing, batch / 'new', freeze / 'new'):
                with self.assertRaises(ValueError):
                    verify.fresh_directory(target, batch, freeze)
            for marker in ('freeze.json', 'freeze.sha256'):
                archive = root / marker.replace('.', '-')
                archive.mkdir()
                (archive / marker).write_text('immutable')
                with self.assertRaises(ValueError):
                    verify.fresh_directory(archive / 'nested' / 'new', batch, freeze)
            self.assertEqual(verify.fresh_directory(root / 'plain-new', batch, freeze), root / 'plain-new')

    def test_symlink_output_and_input_inventory_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            batch, freeze, plain = root / 'batch', root / 'freeze', root / 'plain'
            batch.mkdir(); freeze.mkdir(); plain.mkdir()
            alias = root / 'alias'; alias.symlink_to(plain, target_is_directory=True)
            with self.assertRaises(ValueError):
                verify.fresh_directory(alias / 'new', batch, freeze)
            (batch / 'link').symlink_to(plain, target_is_directory=True)
            with self.assertRaises(ValueError):
                verify.inventory(batch)

    def fixture(self, root, mode='pass'):
        batch, freeze = root / 'batch', root / 'freeze'
        batch.mkdir(); freeze.mkdir()
        (batch / 'fixture.json').write_text(json.dumps(report()))
        (batch / 'mode.txt').write_text(mode)
        (freeze / 'freeze.json').write_text('{}')
        reviewer = root / 'software-reviewer.py'
        reviewer.write_text('''import argparse, hashlib, json, os, platform, sys
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--batch'); p.add_argument('--freeze'); p.add_argument('--out'); a=p.parse_args()
batch=Path(a.batch); mode=(batch/'mode.txt').read_text()
if mode=='fail':
    print('software fixture failure', file=sys.stderr); raise SystemExit(7)
r=json.loads((batch/'fixture.json').read_text())
r['review_script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
r['python_runtime']=dict(implementation=platform.python_implementation(), version=platform.python_version(), executable=sys.executable, platform=platform.platform(), binary64=True)
if mode=='mutate-original': (batch/'fixture.json').write_text('{}')
if mode=='mutate-code': Path(__file__).write_text(Path(__file__).read_text()+'\\n# changed by software fixture\\n')
Path(a.out).write_text(json.dumps(r)); print(json.dumps(dict(process_id=os.getpid())))
''')
        return batch, freeze, reviewer

    def test_fresh_subprocesses_retain_reports_versions_and_all_original_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            batch, freeze, reviewer = self.fixture(root)
            original = verify.inventory(batch)
            out = root / 'new-audit'
            result = verify.verify(sys.executable, sys.executable, freeze, batch, out, reviewer_path=reviewer)
            self.assertEqual(result['status'], 'pass', result)
            self.assertTrue(result['original_files_unchanged'])
            self.assertEqual(original, verify.inventory(batch))
            self.assertEqual(result['reports']['a']['runtime_probe']['version'], result['reports']['b']['runtime_probe']['version'])
            pids = [verify.read(out / ('review-python-' + arm + '.stdout.txt'))['process_id'] for arm in ('a', 'b')]
            self.assertNotEqual(*pids)
            self.assertEqual(verify.read(out / 'original-files-before.json'), verify.read(out / 'original-files-after.json'))
            self.assertEqual(result, verify.read(out / 'cross-version-verification.json'))
            self.assertEqual(result['reports']['a']['sha256'], hashlib.sha256((out / 'review-python-a.json').read_bytes()).hexdigest())
            with self.assertRaises(ValueError):
                verify.verify(sys.executable, sys.executable, freeze, batch, out, reviewer_path=reviewer)

    def test_failed_subprocess_keeps_diagnostics_and_never_claims_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            batch, freeze, reviewer = self.fixture(root, 'fail')
            out = root / 'failed-audit'
            result = verify.verify(sys.executable, sys.executable, freeze, batch, out, reviewer_path=reviewer)
            self.assertEqual(result['status'], 'fail')
            self.assertEqual(result['processes']['review_python_a']['returncode'], 7)
            self.assertIn('software fixture failure', (out / 'review-python-a.stderr.txt').read_text())
            self.assertTrue(result['original_files_unchanged'])
            self.assertTrue((out / 'cross-version-verification.json').is_file())

    def test_original_file_or_review_code_mutation_is_detected_and_retained(self):
        for mode, flag in (('mutate-original', 'original_files_unchanged'), ('mutate-code', 'code_unchanged')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                batch, freeze, reviewer = self.fixture(root, mode)
                out = root / 'mutation-audit'
                result = verify.verify(sys.executable, sys.executable, freeze, batch, out, reviewer_path=reviewer)
                self.assertEqual(result['status'], 'fail')
                self.assertFalse(result[flag])
                self.assertTrue((out / 'cross-version-verification.json').is_file())


if __name__ == '__main__':
    unittest.main()
