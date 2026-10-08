"""Synthetic process and provenance contracts; these do not run CFD."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.core.record import load_record
from simclinic.domains.openfoam.completion import task_definition
from simclinic.domains.openfoam.sampling_gate import GatedChannelCompletion, SamplingError
from simclinic.result_guard import ResultRejected
from simclinic.session import SessionRecorder


class SamplingGateTests(unittest.TestCase):
    def setup_case(self, temporary):
        root = Path(temporary)
        recorder = SessionRecorder(root / 'session', task_definition('Synthetic task'), {}, {})
        recorder.data['synthetic'] = True
        context = dict(run_id='a' * 32, input_version='sha256:synthetic-input')
        recorder.context = context
        recorder.data.update(context)
        work = root / 'work'
        work.mkdir()
        return GatedChannelCompletion(recorder, root / 'reference', 'synthetic-image'), work, context

    def outputs(self, work, mode='valid'):
        target = work / 'postProcessing/sample/100'
        target.mkdir(parents=True)
        (target / 'profile.xy').write_text('\n'.join(
            f'{y:.12g} {6*.01*(y/.1)*(1-y/.1):.12g} 0 0'
            for y in (.0025+i*.005 for i in range(20))) + '\n')
        (target / 'pressure.xy').write_text('\n'.join(
            f'{x:.12g} {.012*(1-x):.12g}' for x in (.1+i*.05 for i in range(17))) + '\n')
        if mode == 'empty':
            (target / 'profile.xy').write_text('')
        elif mode == 'ambiguous':
            other = work / 'postProcessing/sample/200'
            other.mkdir()
            (other / 'profile.xy').write_text((target / 'profile.xy').read_text())
        elif mode == 'invalid':
            (target / 'profile.xy').write_text('0 nan 0 0\n1 0 0 0\n')
        elif mode == 'unsafe':
            (target / 'profile.xy').unlink()
            outside = work.parent / 'outside.xy'
            outside.write_text('Do not read this as sample evidence')
            (target / 'profile.xy').symlink_to(outside)

    def process(self, work, mode=None, stdout=b'End\n', code=0):
        def run(argv, **kwargs):
            if argv[1] == 'rm':
                return subprocess.CompletedProcess(argv, 0, b'', b'')
            if mode:
                self.outputs(work, mode)
            return subprocess.CompletedProcess(argv, code, stdout, b'')
        return run

    def event(self, policy):
        return next(e for e in policy.recorder.data['events'] if e.get('operation') == 'sample')

    def test_success_preserves_reference_and_registers_continue(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work, 'valid')):
                result = policy.sample(work, context)
            self.assertLess(result['profile_error'], 1e-10)
            self.assertLess(result['pressure_gradient_error'], 1e-10)
            self.assertEqual(self.event(policy)['outcome'], 'pass')
            decision = policy.recorder.data['events'][-1]
            self.assertEqual(decision['action'], 'continue')
            self.assertEqual(decision['handles'], [self.event(policy)['id']])
            data = load_record(policy.recorder.directory / 'record.json', policy.recorder.directory).data
            for artifact in data['artifacts']:
                self.assertEqual(hashlib.sha256((policy.recorder.directory / artifact['path']).read_bytes()).hexdigest(), artifact['sha256'])

    def test_zero_exit_and_end_with_fatal_log_stops(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work, stdout=b'header\nFOAM FATAL IO ERROR\nEnd\n')):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_fatal_log')
            event = self.event(policy)
            self.assertEqual((event['exit_code'], event['outcome'], event['error_category']), (0, 'fail', 'sampling_fatal_log'))
            assessment = json.loads((policy.recorder.directory / 'observations/sampling/gate-assessment.json').read_text())
            self.assertEqual(assessment['fatal_errors'][0]['line'], 2)
            self.assertTrue(assessment['zero_exit_with_fatal_log'])
            self.assertEqual(policy.recorder.data['events'][-1]['action'], 'stop')

    def test_missing_output_names_path_instead_of_stopiteration(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work)):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_missing_output')
            self.assertIn('profile.xy', str(caught.exception))
            self.assertEqual(str(caught.exception).count('sampling_missing_output:'), 1)
            self.assertEqual(self.event(policy)['outcome'], 'fail')

    def test_empty_ambiguous_invalid_or_unsafe_samples_stop(self):
        for mode, expected in (('empty', 'sampling_empty_output'), ('ambiguous', 'sampling_ambiguous_output'),
                               ('invalid', 'sampling_invalid_output'), ('unsafe', 'sampling_unsafe_output')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as t:
                policy, work, context = self.setup_case(t)
                with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                     patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work, mode)):
                    with self.assertRaises(SamplingError) as caught:
                        policy.sample(work, context)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(self.event(policy)['outcome'], 'fail')

    def test_timeout_keeps_unknown_exit_and_incomplete_tool_coverage(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            def run(argv, **kwargs):
                if argv[1] == 'rm':
                    return subprocess.CompletedProcess(argv, 0, b'', b'')
                raise subprocess.TimeoutExpired(argv, 120, output=b'partial\n')
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=run):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_timeout')
            self.assertIsNone(self.event(policy)['exit_code'])
            self.assertEqual(self.event(policy)['outcome'], 'unknown')
            self.assertFalse(policy.recorder.tools_complete)

    def test_nonzero_exit_preserved_with_specific_category(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work, code=3)):
                with self.assertRaises(SamplingError):
                    policy.sample(work, context)
            self.assertEqual(self.event(policy)['exit_code'], 3)
            self.assertEqual(self.event(policy)['error_category'], 'sampling_process_exit')

    def test_preexisting_output_or_old_context_never_launches_process(self):
        for mode in ('preexisting', 'old-context'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as t:
                policy, work, context = self.setup_case(t)
                marker = dict(context)
                if mode == 'preexisting':
                    (work / 'postProcessing').mkdir()
                else:
                    marker['run_id'] = 'b' * 32
                with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=marker), \
                     patch('simclinic.domains.openfoam.sampling_gate.subprocess.run') as run:
                    with self.assertRaises(SamplingError):
                        policy.sample(work, context)
                run.assert_not_called()
                self.assertIsNone(self.event(policy)['exit_code'])

    def test_snapshot_change_after_sampling_stops_before_output_registration(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', side_effect=[context, ResultRejected('snapshot_changed', 'Synthetic change')]), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work, 'valid')):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'snapshot_changed')
            self.assertFalse((policy.recorder.directory / 'observations/sampling/profile.xy').exists())

    def test_unknown_log_encoding_cannot_pass(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch('simclinic.domains.openfoam.sampling_gate.subprocess.run', side_effect=self.process(work, 'valid', stdout=b'\xff')):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_log_encoding')
            self.assertEqual(self.event(policy)['outcome'], 'unknown')

    def test_identity_failure_after_sample_cannot_publish_measurements_or_success(self):
        with tempfile.TemporaryDirectory() as t:
            policy, work, context = self.setup_case(t)
            case = Path(t) / 'case'
            native = case / '.simclinic/native' / context['run_id']
            (native / 'input-snapshot/system').mkdir(parents=True)
            policy.reference.mkdir()
            (policy.reference / 'system').mkdir()
            text = 'FoamFile { object sample; }\n'
            (native / 'input-snapshot/system/sample').write_text(text)
            (policy.reference / 'system/sample').write_text(text)
            (native / 'execution.json').write_text('{"exit_code":0}')
            logs = policy.recorder.directory / 'attempts' / context['run_id'] / 'logs'
            logs.mkdir(parents=True)
            (logs / 'log.simpleFoam').write_text('SIMPLE solution converged\ntime step continuity errors : sum local = 1e-12, global = 0\nEnd\n')
            image = case / 'U.png'
            image.write_bytes(b'Synthetic image; not a CFD render')
            from simclinic.core.record import Record
            record = Record(dict(artifacts=[dict(path='U.png', role='visualization', sha256=hashlib.sha256(image.read_bytes()).hexdigest())]), 'synthetic')
            @contextmanager
            def workspace(*args):
                yield work
                raise ResultRejected('snapshot_changed', 'Synthetic identity change at context exit')
            state = dict(case_dir=str(case), simclinic_context=context, plot_outputs=[str(image)],
                         pyvista_visualization={'success': True}, visualization_summary={'fields_visualized': ['U']})
            with patch('simclinic.domains.openfoam.sampling_gate.current_record', return_value=record), \
                 patch('simclinic.domains.openfoam.sampling_gate.verified_workspace', workspace), \
                 patch.object(policy, 'sample', return_value=dict(profile_error=0, pressure_gradient_error=0)):
                result = policy(state)
            self.assertFalse(result['completion_claim']['success'])
            self.assertEqual(result['acceptance_report']['error']['code'], 'snapshot_changed')
            self.assertIsNone(result['acceptance_report']['values']['profile_error'])
            self.assertFalse(policy.recorder.references_complete)


if __name__ == '__main__':
    unittest.main()
