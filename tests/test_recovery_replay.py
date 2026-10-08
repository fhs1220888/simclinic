"""Synthetic archive safety fixtures; no models or CFD execution."""
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from simclinic.core.record import Record
from simclinic.evaluation.recovery_replay import (
    LATEST_FREEZE_SHA256, _json, recovery_evidence, replay,
    verify_artifacts, verify_inputs, verify_tree,
)
from scripts.replay_contract_recovery import main


class RecoveryReplayTests(unittest.TestCase):
    def fixture(self, directory, second_outcome='pass', second_outputs=True):
        root = Path(directory)
        artifacts = []

        def artifact(artifact_id, path, payload):
            path = root / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            artifacts.append(dict(id=artifact_id, path=path.relative_to(root).as_posix(),
                                  run_id='run-1', input_version='input-1',
                                  sha256=hashlib.sha256(payload).hexdigest()))

        samples = []
        for number, (outcome, archive) in enumerate((('fail', 'observations/sampling'),
                (second_outcome, 'observations/sampling-recovery/attempt-2/observations/sampling')), 1):
            event = dict(id='sample-' + str(number), kind='tool', operation='sample', phase='postprocess',
                         run_id='run-1', input_version='input-1', input_fingerprint='input-1',
                         exit_code=0, timed_out=False, outcome=outcome,
                         error_category='sampling_missing_output' if outcome == 'fail' else None,
                         stdout_artifact='stdout-' + str(number), stderr_artifact='stderr-' + str(number),
                         source_artifact='gate-' + str(number), elapsed_seconds=2.0,
                         retry_of='sample-1' if number == 2 else None)
            if number == 2:
                event['output_archive_root'] = archive
            for stream in ('stdout', 'stderr'):
                artifact(stream + '-' + str(number), archive + '/' + stream + '.log',
                         b'End\n' if stream == 'stdout' else b'')
            if number == 2 and second_outputs:
                artifact('profile', archive + '/profile.xy', b'0 0 0\n')
                artifact('pressure', archive + '/pressure.xy', b'0 0\n')
            gate = dict(run_id='run-1', input_version='input-1', exit_code=0, timed_out=False,
                        execution_started=True, outcome=outcome, error_category=event['error_category'],
                        stdout_artifact=event['stdout_artifact'], stderr_artifact=event['stderr_artifact'],
                        fatal_errors=[], zero_exit_with_fatal_log=False, reference_tolerances_changed=False,
                        measurements=None)
            artifact(event['source_artifact'], archive + '/gate-assessment.json', json.dumps(gate).encode())
            samples.append(event)
        decision = dict(id='decision', kind='decision', decision_type='bounded_postprocess_recovery',
                        run_id='run-1', input_version='input-1', handles=['sample-1'],
                        retry_limit=1, action='retry', source_artifact='gate-1')
        record = Record(dict(synthetic=True, run_id='run-1', input_version='input-1',
                             events=[samples[0], decision, samples[1]], artifacts=artifacts), 'synthetic-record.json', root)
        summary = dict(quality_met=True, metrics=dict(model_cost=None, wall_seconds=12),
                       model_usage=dict(total_calls=1, total_tokens=10, model_cost=None))
        return record, summary

    def replace_gate(self, record, artifact_id, **updates):
        artifact = record.artifact(artifact_id)
        path = record.case_dir / artifact['path']
        data = json.loads(path.read_text())
        data.update(updates)
        path.write_text(json.dumps(data))
        artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def test_verified_failure_recovery_keeps_diagnostic_and_consumption_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            record, summary = self.fixture(directory)
            before = {path: path.read_bytes() for path in Path(directory).rglob('*') if path.is_file()}
            self.assertEqual(verify_artifacts(record), 8)
            result = recovery_evidence(record, summary)
            self.assertTrue(result['sampling_recovered'])
            self.assertEqual(result['recovery_status'], 'recovered')
            self.assertEqual(result['first_failure']['diagnostic_status'], 'insufficient_evidence')
            self.assertEqual(result['retry']['diagnostic_status'], 'pass')
            self.assertEqual(result['extra_sampler_calls'], 1)
            self.assertEqual(result['extra_sampler_seconds'], 2.0)
            self.assertEqual(result['total_sampler_seconds'], 4.0)
            self.assertIsNone(result['consumption']['model_cost'])
            self.assertFalse(result['original_score_changed'])
            self.assertEqual(before, {path: path.read_bytes() for path in Path(directory).rglob('*') if path.is_file()})

    def test_second_failure_honestly_terminates_and_false_completion_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            record, summary = self.fixture(directory, second_outcome='fail', second_outputs=False)
            result = recovery_evidence(record, summary)
            self.assertFalse(result['sampling_recovered'])
            self.assertEqual(result['recovery_status'], 'terminated_after_retry')
            record.data['events'].append(dict(id='claim', claim_type='completion', success=True))
            with self.assertRaisesRegex(ValueError, 'successful completion'):
                recovery_evidence(record, summary)

    def test_old_or_unknown_sampler_identity_rejected(self):
        for key, value in (('run_id', 'old-run'), ('input_version', 'old-input'), ('run_id', None)):
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                record, summary = self.fixture(directory)
                record.data['events'][0][key] = value
                with self.assertRaisesRegex(ValueError, 'sampler identity'):
                    recovery_evidence(record, summary)

    def test_fatal_or_timeout_is_not_missing_output_recovery(self):
        for updates in (dict(fatal_errors=[dict(message='fatal')]), dict(timed_out=True)):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as directory:
                record, summary = self.fixture(directory)
                self.replace_gate(record, 'gate-1', **updates)
                if 'timed_out' in updates:
                    record.data['events'][0]['timed_out'] = True
                with self.assertRaisesRegex(ValueError, 'nonfatal missing output'):
                    recovery_evidence(record, summary)

    def test_unverifiable_logs_and_missing_hashes_stay_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            record, summary = self.fixture(directory)
            record.artifact('stdout-1')['sha256'] = None
            with self.assertRaisesRegex(ValueError, 'Unknown artifact digest'):
                verify_artifacts(record)
            with self.assertRaisesRegex(ValueError, 'unverifiable first sampling logs'):
                recovery_evidence(record, summary)

    def test_success_with_missing_retry_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            record, summary = self.fixture(directory, second_outputs=False)
            with self.assertRaisesRegex(ValueError, 'lacks verified'):
                recovery_evidence(record, summary)

    def test_retry_limit_solver_rerun_and_shared_archive_rejected(self):
        for change in ('limit', 'extra_sample', 'solve', 'shared_root'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                record, summary = self.fixture(directory)
                if change == 'limit':
                    record.data['events'][1]['retry_limit'] = 2
                elif change == 'extra_sample':
                    record.data['events'].append(dict(record.data['events'][0], id='sample-3'))
                elif change == 'solve':
                    record.data['events'].insert(2, dict(id='solve-again', phase='solve'))
                else:
                    record.data['events'][-1]['output_archive_root'] = 'observations/sampling'
                with self.assertRaises(ValueError):
                    recovery_evidence(record, summary)

    def test_input_bytes_and_version_verified_with_current_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / 'attempts/run/native/input-snapshot'
            snapshot.mkdir(parents=True)
            (snapshot / 'Allrun').write_text('synthetic; never execute')
            files = {'Allrun': hashlib.sha256((snapshot / 'Allrun').read_bytes()).hexdigest()}
            version = 'sha256:' + hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            (snapshot.parent / 'input-manifest.json').write_text(json.dumps(dict(run_id='run', input_version=version, files=files)))
            attempt = dict(run_id='run', input_version=version, input_snapshot=snapshot.relative_to(root).as_posix())
            record = Record(dict(run_id='run', input_version=version, attempts=[attempt]), 'synthetic-record', root)
            self.assertEqual(verify_inputs(record), [('run', version)])
            record.data['run_id'] = 'old-run'
            with self.assertRaisesRegex(ValueError, 'Current record identity'):
                verify_inputs(record)
            record.data['run_id'] = 'run'
            (snapshot / 'Allrun').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'Frozen bytes'):
                verify_inputs(record)

    def test_tree_extra_bytes_missing_digest_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'file').write_bytes(b'one')
            expected = dict(file=hashlib.sha256(b'one').hexdigest())
            verify_tree(root, expected)
            with self.assertRaisesRegex(ValueError, 'invalid frozen digest'):
                verify_tree(root, dict(file=None))
            (root / 'extra').write_bytes(b'two')
            with self.assertRaisesRegex(ValueError, 'file set changed'):
                verify_tree(root, expected)
            (root / 'extra').unlink()
            (root / 'link').symlink_to(root / 'file')
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                verify_tree(root, expected)

    def test_duplicate_keys_and_nonfinite_json_rejected(self):
        for payload in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _json(payload)

    def test_existing_or_source_output_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / 'batch'
            batch.mkdir()
            for out in (batch / 'inside', batch, root):
                with self.subTest(out=out), self.assertRaisesRegex(ValueError, 'new output directory'):
                    replay(batch, out)
            self.assertEqual(list(batch.iterdir()), [])
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--batch', str(batch), '--out', str(root)]), 2)

    def test_self_updated_sidecar_cannot_replace_explicit_trusted_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / 'batch'
            batch.mkdir()
            frozen = b'{}'
            (batch / 'freeze.json').write_bytes(frozen)
            (batch / 'freeze.sha256').write_text(hashlib.sha256(frozen).hexdigest())
            (batch / 'results.json').write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'trusted SHA-256'):
                replay(batch, root / 'out')
            self.assertNotEqual(hashlib.sha256(frozen).hexdigest(), LATEST_FREEZE_SHA256)
            self.assertFalse((root / 'out').exists())

    def test_output_inside_another_frozen_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch, older = root / 'batch', root / 'older-archive'
            batch.mkdir()
            older.mkdir()
            (older / 'freeze.json').write_text('{}')
            (older / 'freeze.sha256').write_text('unknown-test-fixture')
            with self.assertRaisesRegex(ValueError, 'any existing frozen archive'):
                replay(batch, older / 'new-output')
            self.assertFalse((older / 'new-output').exists())


if __name__ == '__main__':
    unittest.main()
