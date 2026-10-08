"""Offline harness contracts using synthetic fixtures; no model/solver calls."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from simclinic.cli import main
from simclinic.core.record import RecordError
from simclinic.evaluation.benchmark import run_benchmark


ROOT = Path(__file__).resolve().parents[1]
CHECKS = ('preflight', 'error_continuation', 'retries', 'stale_results', 'completion')
STATES = ('finding', 'pass', 'insufficient_evidence', 'not_applicable')
EXPECTED = dict(healthy=('pass', 'pass', 'not_applicable', 'pass', 'pass'),
                faults=('finding',) * 5, missing=('insufficient_evidence',) * 5)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'source'
        self.catalogue = self.root / 'catalogue'
        self.catalogue.mkdir(parents=True)
        self.manifest = self.catalogue / 'manifest.json'
        self.out = self.base / 'results'
        self.cases = []

    def case(self, name):
        folder = self.root / 'cases' / name
        shutil.copytree(ROOT / 'fixtures' / name, folder)
        case = dict(id=name, source_kind='synthetic', basis='Explicit synthetic fixture contract.',
                    record=f'cases/{name}/record.json', case_dir=f'cases/{name}/case',
                    record_labels=dict(synthetic=True, fault_injection=None),
                    provenance=[dict(path=f'cases/{name}/record.json', role='synthetic contract')],
                    files={}, expected=dict(behavior=dict(zip(CHECKS, EXPECTED[name])),
                    sampling=dict(summary={s: int(s == 'not_applicable') for s in STATES}, events={})))
        self.cases.append(case)
        self.pin_case(case)
        return case

    def pin_case(self, case):
        data = json.loads((self.root / case['record']).read_text())
        names = [case['record']] + [(Path(case['case_dir']) / a['path']).as_posix() for a in data['artifacts']]
        case['files'] = {n: sha(self.root / n) if (self.root / n).is_file() else None for n in names}

    def save(self):
        value = dict(schema_version='1.0', benchmark_id='synthetic-test', version='1.0.0', cases=self.cases)
        self.manifest.write_text(json.dumps(value, indent=2))
        self.repin_manifest()

    def repin_manifest(self):
        self.manifest.with_suffix('.sha256').write_text(sha(self.manifest) + '\n')

    def evaluate(self):
        self.save()
        return run_benchmark(self.manifest, self.root, self.out)

    def cli(self, extra=()):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(['benchmark', str(self.manifest), '--root', str(self.root),
                         '--out', str(self.out)] + list(extra))

    def sampler(self, missing_log=False, code=0):
        case = self.case('healthy')
        record_path = self.root / case['record']
        record = json.loads(record_path.read_text())
        run, version = record['run_id'], record['input_version']
        for identity, name, payload in [('sample-out', 'stdout.log', 'End\n'),
                                        ('sample-err', 'stderr.log', ''),
                                        ('profile', 'profile.xy', '0 0 0\n'),
                                        ('pressure', 'pressure.xy', '0 0 0\n')]:
            relative = 'observations/sampling/' + name
            path = self.root / case['case_dir'] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload)
            record['artifacts'].append(dict(id=identity, path=relative, run_id=run,
                                           input_version=version, sha256=sha(path)))
        record['events'].append(dict(id='sample-test', kind='tool', operation='sample', phase='postprocess',
            run_id=run, input_version=version, exit_code=code, outcome='pass',
            stdout_artifact='sample-out', stderr_artifact='sample-err'))
        record_path.write_text(json.dumps(record))
        if missing_log:
            (self.root / case['case_dir'] / 'observations/sampling/stdout.log').unlink()
        status = 'finding' if code else 'insufficient_evidence' if missing_log else 'pass'
        case['expected']['sampling'] = dict(summary={s: int(s == status) for s in STATES},
            events={'sample-test': dict(status=status, reasons=['nonzero_exit'] if code else [],
                                       zero_exit_with_fatal_log=False, fatal_lines=[])})
        self.pin_case(case)
        return case

    def test_expected_findings_and_unknowns_match_without_executing_or_changing_sources(self):
        for name in ('healthy', 'faults', 'missing'):
            self.case(name)
        self.save()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('subprocess.run', side_effect=AssertionError('must not execute')), \
             patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            result = run_benchmark(self.manifest, self.root, self.out)
        self.assertEqual(result['summary'], dict(total=3, matched=3, mismatched=0, rejected=0))
        self.assertEqual(result['new_model_calls'], 0)
        self.assertEqual(result['new_simulations'], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        for score in result['behavior_scores'].values():
            self.assertEqual(score['matches'], 3)
            self.assertEqual(score['by_expected_state']['insufficient_evidence'], dict(total=1, matches=1))
        self.assertEqual(result['sampling_scores'], dict(scored=3, matches=3))
        self.assertTrue((self.out / 'cases/faults/audit/report.md').is_file())

    def test_checker_disagreement_is_not_rebaselined_and_cli_returns_one(self):
        case = self.case('healthy')
        case['expected']['behavior']['preflight'] = 'finding'
        self.save()
        self.assertEqual(self.cli(), 1)
        report = json.loads((self.out / 'benchmark-summary.json').read_text())
        self.assertEqual(report['cases'][0]['expected']['behavior']['preflight'], 'finding')
        self.assertEqual(report['cases'][0]['observed']['behavior']['preflight'], 'pass')
        self.assertEqual(report['summary']['mismatched'], 1)

    def test_tampered_record_rejected_other_cases_continue_and_cli_returns_two(self):
        case = self.case('healthy')
        self.case('missing')
        self.save()
        path = self.root / case['record']
        path.write_text(path.read_text() + '\n')
        self.assertEqual(self.cli(), 2)
        result = json.loads((self.out / 'benchmark-summary.json').read_text())
        self.assertEqual(result['summary'], dict(total=2, matched=1, mismatched=0, rejected=1))
        self.assertIsNone(result['cases'][0]['observed'])
        self.assertFalse(result['cases'][0]['rejection']['agent_fault_inferred'])

    def test_missing_log_is_pinned_absent_and_scored_unknown_not_agent_error(self):
        case = self.sampler(missing_log=True)
        result = self.evaluate()
        row = result['cases'][0]
        self.assertEqual(row['status'], 'matched')
        self.assertEqual(row['observed']['sampling']['summary']['finding'], 0)
        self.assertEqual(row['verified_absent_files'], 1)
        self.assertIsNone(case['files'][case['case_dir'] + '/observations/sampling/stdout.log'])

    def test_explicit_failed_exit_remains_finding_even_with_missing_log(self):
        self.sampler(missing_log=True, code=1)
        result = self.evaluate()
        self.assertEqual(result['cases'][0]['observed']['sampling']['events']['sample-test']['reasons'], ['nonzero_exit'])
        self.assertEqual(result['summary']['matched'], 1)

    def test_restored_absent_log_and_modified_sample_bytes_are_integrity_rejections(self):
        for missing_log in (True, False):
            with self.subTest(missing_log=missing_log):
                if self.cases:
                    shutil.rmtree(self.root / 'cases')
                    self.cases = []
                case = self.sampler(missing_log=missing_log)
                self.save()
                name = 'stdout.log' if missing_log else 'profile.xy'
                (self.root / case['case_dir'] / 'observations/sampling' / name).write_text('changed\n')
                result = run_benchmark(self.manifest, self.root, self.base / ('changed-' + str(missing_log)))
                self.assertEqual(result['summary']['rejected'], 1)
                self.assertIsNone(result['cases'][0]['observed'])

    def test_manifest_edit_and_explicit_trusted_pin_reject_before_output(self):
        self.case('healthy')
        self.save()
        original = sha(self.manifest)
        self.manifest.write_text(self.manifest.read_text() + '\n')
        self.assertEqual(self.cli(), 2)
        self.assertFalse(self.out.exists())
        self.repin_manifest()
        self.assertEqual(self.cli(['--manifest-sha256', original]), 2)
        self.assertFalse(self.out.exists())

    def test_unpinned_artifact_invalid_record_and_label_conflict_are_rejected(self):
        for scenario in ('unpinned', 'record', 'label'):
            with self.subTest(scenario=scenario):
                if self.cases:
                    shutil.rmtree(self.root / 'cases')
                    self.cases = []
                case = self.case('healthy')
                if scenario == 'unpinned':
                    case['files'].pop(case['case_dir'] + '/results.json')
                elif scenario == 'record':
                    (self.root / case['record']).write_text('{}')
                    self.pin_case_for_invalid(case)
                else:
                    path = self.root / case['record']
                    data = json.loads(path.read_text());data['synthetic'] = False
                    path.write_text(json.dumps(data));self.pin_case(case)
                self.save()
                result = run_benchmark(self.manifest, self.root, self.base / scenario)
                self.assertEqual(result['summary']['rejected'], 1)

    def pin_case_for_invalid(self, case):
        case['files'][case['record']] = sha(self.root / case['record'])

    def test_output_overlap_and_existing_output_are_refused_without_overwriting(self):
        case = self.case('healthy')
        self.save()
        for output in (self.catalogue / 'output', self.root / case['case_dir'] / 'output'):
            with self.assertRaises(RecordError):
                run_benchmark(self.manifest, self.root, output)
            self.assertFalse(output.exists())
        run_benchmark(self.manifest, self.root, self.out)
        previous = (self.out / 'benchmark-summary.json').read_bytes()
        with self.assertRaises(RecordError):
            run_benchmark(self.manifest, self.root, self.out)
        self.assertEqual(previous, (self.out / 'benchmark-summary.json').read_bytes())

    def test_traversal_and_external_symlink_are_refused(self):
        case = self.case('healthy')
        original = case['case_dir']
        case['case_dir'] = '../outside'
        self.save()
        self.assertEqual(self.cli(), 2)
        self.assertFalse(self.out.exists())
        case['case_dir'] = original
        self.save()
        path = self.root / case['record']
        external = self.base / 'external.json'
        external.write_bytes(path.read_bytes());path.unlink();path.symlink_to(external)
        self.assertEqual(self.cli(), 2)
        self.assertFalse(self.out.exists())

    def test_duplicate_manifest_keys_and_malformed_expectation_fail_closed(self):
        self.case('healthy')
        self.save()
        text = self.manifest.read_text().replace('"schema_version": "1.0",',
            '"schema_version": "1.0", "schema_version": "1.0",', 1)
        self.manifest.write_text(text);self.repin_manifest()
        self.assertEqual(self.cli(), 2)
        self.cases[0]['expected']['behavior'].pop('completion')
        self.save()
        self.assertEqual(self.cli(), 2)
        self.assertFalse(self.out.exists())

    def test_change_during_audit_is_rejected_instead_of_scored(self):
        case = self.case('healthy')
        self.save()
        from simclinic.checks import audit
        def mutating_audit(record):
            report = audit(record)
            path = self.root / case['case_dir'] / 'results.json'
            path.write_text('changed during observation')
            return report
        with patch('simclinic.evaluation.benchmark.audit', side_effect=mutating_audit):
            result = run_benchmark(self.manifest, self.root, self.out)
        self.assertEqual(result['summary']['rejected'], 1)
        self.assertIsNone(result['cases'][0]['observed'])


if __name__ == '__main__':
    unittest.main()
