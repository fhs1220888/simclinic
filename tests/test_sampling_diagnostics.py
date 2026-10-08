"""Synthetic byte/provenance fixtures; no model or solver calls."""
from contextlib import redirect_stderr
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from simclinic.cli import main
from simclinic.core.record import Record
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling


class SamplingDiagnosticTests(unittest.TestCase):
    def record(self, directory, stdout='End\n', outputs=True, code=0):
        root = Path(directory)
        event = dict(id='sample-event', kind='tool', operation='sample', phase='postprocess',
                     run_id='current-run', input_version='current-input', exit_code=code, outcome='pass',
                     stdout_artifact='stdout', stderr_artifact='stderr')
        artifacts = []
        files = [('stdout', 'stdout.log', stdout), ('stderr', 'stderr.log', '')]
        if outputs:
            files += [('profile', 'profile.xy', '0 0 0\n'), ('pressure', 'pressure.xy', '0 0 0\n')]
        for identity, name, text in files:
            path = root / 'observations/sampling' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            artifacts.append(dict(id=identity, path=str(path.relative_to(root)), run_id='current-run',
                                  input_version='current-input', sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        return Record(dict(synthetic=True, events=[event], artifacts=artifacts), 'synthetic-record.json', root)

    def test_zero_exit_and_end_do_not_hide_verified_fatal_error(self):
        with tempfile.TemporaryDirectory() as t:
            record = self.record(t, 'header\n--> FOAM FATAL IO ERROR:\nEnd\n', outputs=False)
            result = diagnose_sampling(record)
            row = result['runs'][0]
            self.assertEqual(row['status'], 'finding')
            self.assertTrue(row['zero_exit_with_fatal_log'])
            self.assertEqual(row['fatal_errors'][0]['line'], 2)
            self.assertEqual(row['fatal_errors'][0]['artifact_id'], 'stdout')
            self.assertTrue(row['end_evidence'])
            self.assertEqual(result['reference_validation'], 'not_assessed')

    def test_available_outputs_are_not_reference_acceptance(self):
        with tempfile.TemporaryDirectory() as t:
            result = diagnose_sampling(self.record(t))
            self.assertEqual(result['summary']['pass'], 1)
            self.assertEqual(result['reference_validation'], 'not_assessed')
            self.assertTrue(result['primary_checks_and_acceptance_unchanged'])

    def test_missing_logs_or_outputs_are_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as t:
            record = self.record(t, outputs=False)
            self.assertEqual(diagnose_sampling(record)['summary']['insufficient_evidence'], 1)
            record.data['artifacts'] = []
            self.assertEqual(diagnose_sampling(record)['summary']['finding'], 0)

    def test_tampered_or_old_log_cannot_prove_current_failure(self):
        with tempfile.TemporaryDirectory() as t:
            record = self.record(t)
            (Path(t) / 'observations/sampling/stdout.log').write_text('FOAM FATAL ERROR\n')
            row = diagnose_sampling(record)['runs'][0]
            self.assertEqual(row['status'], 'insufficient_evidence')
            self.assertEqual(row['fatal_errors'], [])
            self.assertEqual(row['unavailable_evidence'][0]['code'], 'digest_mismatch')
            record = self.record(t, 'FOAM FATAL ERROR\n')
            record.data['artifacts'][0]['run_id'] = 'old-run'
            self.assertEqual(diagnose_sampling(record)['runs'][0]['fatal_errors'], [])

    def test_nonzero_exit_is_evidence_even_when_log_missing(self):
        with tempfile.TemporaryDirectory() as t:
            record = self.record(t, code=1)
            record.data['artifacts'] = []
            row = diagnose_sampling(record)['runs'][0]
            self.assertEqual(row['status'], 'finding')
            self.assertEqual(row['reasons'], ['nonzero_exit'])
            self.assertTrue(row['unavailable_evidence'])

    def test_no_sampler_is_not_applicable_and_query_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            record = self.record(t)
            before = {p: p.read_bytes() for p in Path(t).rglob('*') if p.is_file()}
            diagnose_sampling(record)
            self.assertEqual(before, {p: p.read_bytes() for p in Path(t).rglob('*') if p.is_file()})
            record.data['events'] = []
            self.assertEqual(diagnose_sampling(record)['summary']['not_applicable'], 1)

    def test_cli_refuses_output_inside_source_collection(self):
        with tempfile.TemporaryDirectory() as t:
            with redirect_stderr(io.StringIO()):
                result = main(['diagnose-sampling', str(Path(t) / 'record.json'), '--case', t,
                               '--out', str(Path(t) / 'diagnostic.json')])
            self.assertEqual(result, 2)
            self.assertFalse((Path(t) / 'diagnostic.json').exists())


if __name__ == '__main__':
    unittest.main()
