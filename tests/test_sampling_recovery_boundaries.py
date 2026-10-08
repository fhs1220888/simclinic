"""Synthetic sampler/fields with real run hashes and verified workspaces.

Only Docker execution is replaced. These tests exercise production recovery,
provenance and completion code; they are software checks, not CFD observations.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.core.report import write_json
from simclinic.domains.openfoam.completion import task_definition
from simclinic.domains.openfoam.sampling_gate import GatedChannelCompletion, SamplingError
from simclinic.domains.openfoam.sampling_recovery import BoundedSamplingRecovery
from simclinic.foam_runtime import begin_run, finish_run, current_record, record_image, verified_workspace
from simclinic.session import SessionRecorder
from tests import test_sampling_gate as gate_fixtures


class Candidate(BoundedSamplingRecovery, GatedChannelCompletion):
    pass


class SamplingRecoveryBoundaryTests(unittest.TestCase):
    def setup_case(self, root):
        root = Path(root)
        case, reference = root/'case', root/'reference'
        for folder in ('0', 'constant', 'system'):
            (case/folder).mkdir(parents=True)
        (case/'0/U').write_text('synthetic input U\n')
        (case/'0/p').write_text('synthetic input p\n')
        (case/'system/sample').write_text('synthetic fixed sampling specification\n')
        (case/'Allrun').write_text('synthetic solver, never executed\n')
        context = begin_run(case)
        (case/'1').mkdir()
        (case/'1/U').write_text('synthetic output U\n')
        (case/'1/p').write_text('synthetic output p\n')
        self.assertEqual(finish_run(case, context, []), [])
        native = case/'.simclinic/native'/context['run_id']
        for folder in ('0', 'constant', 'system'):
            shutil.copytree(case/folder, native/'input-snapshot'/folder)
            shutil.copytree(case/folder, reference/folder)
        write_json(native/'execution.json', {'exit_code': 0})
        recorder = self.recorder(root/'session', context)
        logs = recorder.directory/'attempts'/context['run_id']/'logs'
        logs.mkdir(parents=True)
        (logs/'log.simpleFoam').write_text(
            'Synthetic software fixture\nSIMPLE solution converged\n'
            'time step continuity errors : sum local = 0, global = 0\nEnd\n')
        with verified_workspace(case, context) as work:
            image = work/'U.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic software fixture')
            record_image(work, image)
        state = dict(case_dir=str(case), simclinic_context=context, error_logs=[],
                     plot_outputs=[str(image)], pyvista_visualization={'success': True},
                     visualization_summary={'fields_visualized': ['U']})
        return Candidate(recorder, reference, 'synthetic-image'), case, context, state

    def recorder(self, directory, context):
        recorder = SessionRecorder(directory, task_definition('Synthetic recovery boundary task'), {}, {})
        recorder.data['synthetic'] = True
        recorder.context = context
        recorder.data.update(context)
        return recorder

    def process(self, calls, second='valid', first_change=None):
        def run(argv, **kwargs):
            if argv[:2] == ['docker', 'rm']:
                return subprocess.CompletedProcess(argv, 0, b'', b'')
            self.assertEqual(argv[:2], ['docker', 'run'])
            self.assertTrue(argv[-1].endswith('simpleFoam -postProcess -func sample -latestTime'))
            mount = next(value for value in argv if value.startswith('type=bind,src='))
            work = Path(mount.split('src=', 1)[1].split(',dst=', 1)[0])
            calls.append(work)
            if len(calls) == 1:
                if first_change is not None:
                    first_change()
            elif second == 'valid':
                gate_fixtures.SamplingGateTests().outputs(work)
            elif second == 'fatal':
                return subprocess.CompletedProcess(argv, 0, b'FOAM FATAL IO ERROR\nEnd\n', b'')
            elif second == 'timeout':
                raise subprocess.TimeoutExpired(argv, 120, output=b'synthetic partial output\n')
            return subprocess.CompletedProcess(argv, 0, b'End\n', b'')
        return run

    def samples(self, policy):
        return [event for event in policy.recorder.data['events'] if event.get('operation') == 'sample']

    def assert_failed(self, result, code):
        assessment = result['acceptance_report']
        self.assertEqual(assessment['error']['code'], code)
        self.assertFalse(assessment['accepted'])
        self.assertFalse(result['completion_claim']['success'])
        self.assertEqual(result['completion_claim']['status'], 'failed')
        self.assertIsNone(assessment['values']['profile_error'])
        self.assertIsNone(assessment['values']['pressure_gradient_error'])

    def assert_archived(self, recorder):
        for artifact in recorder.data['artifacts']:
            path = recorder.directory/artifact['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact['sha256'])

    def test_unknown_expected_identity_stops_completion_before_sampler(self):
        for context in ({}, {'run_id': 'invalid', 'input_version': 'unknown'}):
            with self.subTest(context=context), tempfile.TemporaryDirectory() as root:
                policy, case, known, state = self.setup_case(root)
                state['simclinic_context'] = context
                with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run') as process:
                    result = policy(state)
                self.assert_failed(result, 'unknown_expected_identity')
                process.assert_not_called()
                self.assertFalse(hasattr(policy, 'recovery_attempts'))

    def test_current_run_or_input_change_after_first_process_prevents_retry(self):
        for mode, code in (('run', 'stale_run'), ('input', 'stale_input')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                policy, case, context, state = self.setup_case(root)
                def change():
                    if mode == 'run':
                        write_json(case/'.simclinic/native/current.json', dict(context, run_id='b'*32, status='complete'))
                    else:
                        (case/'0/U').write_text('changed current input\n')
                calls = []
                with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run',
                           side_effect=self.process(calls, first_change=change)):
                    result = policy(state)
                self.assert_failed(result, code)
                self.assertEqual(len(calls), 1)
                self.assertFalse(hasattr(policy, 'recovery_attempts'))
                self.assertEqual(self.samples(policy)[0]['error_category'], code)
                self.assert_archived(policy.recorder)

    def test_changed_source_output_hash_blocks_fresh_retry_before_process(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, state = self.setup_case(root)
            calls = []
            with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run',
                       side_effect=self.process(calls, first_change=lambda: (case/'1/U').write_text('changed source result\n'))):
                result = policy(state)
            self.assert_failed(result, 'digest_mismatch')
            self.assertEqual(len(calls), 1)
            self.assertEqual(policy.recovery_attempts, 1)
            self.assertEqual([event['error_category'] for event in self.samples(policy)], ['sampling_missing_output'])
            self.assert_archived(policy.recorder)

    def test_identity_change_after_successful_retry_cannot_publish_success(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, state = self.setup_case(root)
            calls, identity_checks = [], []
            def check_current(source, expected):
                identity_checks.append(expected)
                if len(identity_checks) == 2:
                    # The second gate already passed; transition immediately
                    # before recovery's final current-run verification.
                    (case/'0/U').write_text('changed after retry gate passed\n')
                return current_record(source, expected)
            with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(calls)), \
                 patch('simclinic.domains.openfoam.sampling_recovery.current_record', side_effect=check_current):
                result = policy(state)
            self.assert_failed(result, 'stale_input')
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0], calls[1])
            self.assertEqual([event['outcome'] for event in self.samples(policy)], ['fail', 'pass'])
            self.assertEqual(policy.recovery_attempts, 1)
            self.assertFalse(policy.recorder.references_complete)
            self.assert_archived(policy.recorder)

    def test_second_fatal_timeout_or_missing_output_terminates_with_first_failure_retained(self):
        for mode, code, outcome in (('fatal', 'sampling_fatal_log', 'fail'),
                                    ('timeout', 'sampling_timeout', 'unknown'),
                                    ('missing', 'sampling_missing_output', 'fail')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                policy, case, context, state = self.setup_case(root)
                original = {path: (case/path).read_bytes() for path in ('0/U', '0/p', '1/U', '1/p', 'Allrun')}
                calls = []
                with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(calls, second=mode)):
                    result = policy(state)
                self.assert_failed(result, code)
                self.assertEqual(len(calls), 2)
                self.assertNotEqual(calls[0], calls[1])
                self.assertEqual(policy.recovery_attempts, 1)
                first, second = self.samples(policy)
                self.assertEqual((first['exit_code'], first['outcome'], first['error_category']), (0, 'fail', 'sampling_missing_output'))
                self.assertEqual(second['retry_of'], first['id'])
                self.assertEqual((second['outcome'], second['error_category']), (outcome, code))
                self.assertTrue(all(event['elapsed_seconds'] >= 0 for event in (first, second)))
                first_assessment = json.loads((policy.recorder.directory/'observations/sampling/gate-assessment.json').read_text())
                self.assertEqual(first_assessment['error_category'], 'sampling_missing_output')
                self.assertEqual(first_assessment['measurements'], None)
                self.assertEqual(original, {path: (case/path).read_bytes() for path in original})
                if mode == 'timeout':
                    self.assertIsNone(second['exit_code'])
                    self.assertFalse(policy.recorder.tools_complete)
                self.assert_archived(policy.recorder)

    def test_reusing_instance_with_fresh_archive_cannot_reset_retry_budget(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, state = self.setup_case(root)
            calls = []
            with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(calls)):
                result = policy(state)
            self.assertTrue(result['completion_claim']['success'])
            self.assertEqual(policy.recovery_attempts, 1)
            self.assert_archived(policy.recorder)
            # Use a fresh recorder so directory collision does not mask the
            # actual per-instance recovery ceiling on a later invocation.
            policy.recorder = self.recorder(Path(root)/'later-session', context)
            later_calls = []
            with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(later_calls, second='missing')):
                with self.assertRaises(SamplingError) as caught:
                    with verified_workspace(case, context) as work:
                        policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_missing_output')
            self.assertEqual(len(later_calls), 1)
            self.assertEqual(policy.recovery_attempts, 1)
            self.assertFalse(any(event.get('decision_type') == 'bounded_postprocess_recovery' for event in policy.recorder.data['events']))
            self.assert_archived(policy.recorder)


if __name__ == '__main__':
    unittest.main()
