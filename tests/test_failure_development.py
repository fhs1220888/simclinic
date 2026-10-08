"""Synthetic injector/contract tests; no model or OpenFOAM calls."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.domains.openfoam import sampling_gate
from simclinic.domains.openfoam.sampling_gate import SamplingError
from simclinic.domains.openfoam.completion import task_definition
from simclinic.evaluation.failure_development import DevelopmentCompletion, contract_errors
from simclinic.session import SessionRecorder


class FailureDevelopmentTests(unittest.TestCase):
    def setup_case(self, root, scenario):
        root = Path(root)
        recorder = SessionRecorder(root / 'session', task_definition('Synthetic contract'), {}, {})
        recorder.data['synthetic'] = True
        work = root / 'work'
        (work / 'system').mkdir(parents=True)
        (work / 'system/sample').write_text('FoamFile { class dictionary; object sample; }\ntype sets;')
        context = dict(run_id='a'*32, input_version='sha256:synthetic')
        policy = DevelopmentCompletion(recorder, root / 'reference', 'synthetic-image', {}, scenario)
        return policy, work, context

    def test_fatal_uses_new_config_and_preserves_original_command_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, work, context = self.setup_case(temporary, 'fatal-sample')
            original_bytes = (work / 'system/sample').read_bytes()
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, b'FOAM FATAL IO ERROR\nEnd\n' if argv[1] == 'run' else b'', b'')
            marker = dict(context, input_files={})
            module = sampling_gate.subprocess
            with patch('simclinic.evaluation.failure_development.verify_workspace', return_value=marker), \
                 patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch.object(module, 'run', side_effect=run):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_fatal_log')
            self.assertIs(sampling_gate.subprocess, module)
            self.assertIn('-func simclinicFaultSample ', calls[0][-1])
            self.assertEqual((work / 'system/sample').read_bytes(), original_bytes)
            evidence = json.loads((policy.recorder.directory / 'observations/injection/intervention.json').read_text())
            self.assertEqual(evidence['actual_argv'], calls[0])
            self.assertTrue(evidence['original_reader_bytes_unchanged'])
            self.assertFalse(evidence['natural_failure'])
            self.assertEqual(policy.interventions, 1)

    def test_missing_removes_only_new_profile_and_archives_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, work, context = self.setup_case(temporary, 'missing-profile')
            def run(argv, **kwargs):
                if argv[1] == 'run':
                    output = work / 'postProcessing/sample/100'
                    output.mkdir(parents=True)
                    (output / 'profile.xy').write_bytes(b'fresh synthetic profile\n')
                    (output / 'pressure.xy').write_bytes(b'fresh synthetic pressure\n')
                return subprocess.CompletedProcess(argv, 0, b'End\n', b'')
            with patch('simclinic.evaluation.failure_development.verify_workspace', return_value=dict(context, input_files={})), \
                 patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch.object(sampling_gate.subprocess, 'run', side_effect=run):
                with self.assertRaises(SamplingError) as caught:
                    policy.sample(work, context)
            self.assertEqual(caught.exception.code, 'sampling_missing_output')
            self.assertEqual((policy.recorder.directory / 'observations/injection/removed-profile.xy').read_bytes(), b'fresh synthetic profile\n')
            self.assertTrue((work / 'postProcessing/sample/100/pressure.xy').is_file())
            self.assertFalse((work / 'postProcessing/sample/100/profile.xy').exists())
            self.assertFalse((policy.recorder.directory / 'observations/sampling/profile.xy').exists())

    def test_transport_exception_restores_module_and_retains_injection_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, work, context = self.setup_case(temporary, 'fatal-sample')
            module = sampling_gate.subprocess
            def run(argv, **kwargs):
                if argv[1] == 'run':
                    raise OSError('Synthetic unavailable runtime')
                return subprocess.CompletedProcess(argv, 0, b'', b'')
            with patch('simclinic.evaluation.failure_development.verify_workspace', return_value=dict(context, input_files={})), \
                 patch('simclinic.domains.openfoam.sampling_gate.verify_workspace', return_value=context), \
                 patch.object(module, 'run', side_effect=run):
                with self.assertRaises(SamplingError):
                    policy.sample(work, context)
            self.assertIs(sampling_gate.subprocess, module)
            evidence = json.loads((policy.recorder.directory / 'observations/injection/intervention.json').read_text())
            self.assertIn('actual_argv', evidence)
            self.assertNotIn('original_reader_bytes_unchanged', evidence)

    def failure_summary(self, scenario):
        category = 'sampling_fatal_log' if scenario == 'fatal-sample' else 'sampling_missing_output'
        return dict(failure=None, completed_graph=True, model_usage={'total_tokens': 100},
                    acceptance=dict(execution_end=True, steady_converged=True, local_continuity=1e-10,
                                    task_inputs_verified=True, rendered_velocity=True, profile_error=None, pressure_gradient_error=None),
                    coverage=dict(tools=True, decisions=True, references=False, acceptance=True),
                    sample_tool=dict(outcome='fail', error_category=category),
                    completion=dict(success=False, status='failed'), acceptance_error=dict(code=category), interventions=1)

    def test_honest_failure_is_protocol_pass_and_missing_remains_insufficient(self):
        for scenario, status in (('fatal-sample', 'finding'), ('missing-profile', 'insufficient_evidence')):
            summary = self.failure_summary(scenario)
            self.assertEqual(contract_errors(scenario, summary, dict(runs=[dict(status=status)])), [])
            if scenario == 'missing-profile':
                self.assertIn('Unexpected read-only diagnostic status', contract_errors(scenario, summary, dict(runs=[dict(status='finding')])))

    def test_wrong_success_unknown_usage_or_published_values_cannot_pass(self):
        scenario = 'fatal-sample'
        diagnostic = dict(runs=[dict(status='finding')])
        for key, value in (('completion', dict(success=True, status='completed')),
                           ('model_usage', dict(total_tokens=None)), ('interventions', 0)):
            summary = self.failure_summary(scenario)
            summary[key] = value
            self.assertTrue(contract_errors(scenario, summary, diagnostic))
        summary = self.failure_summary(scenario)
        summary['acceptance']['profile_error'] = 0.0
        self.assertIn('Unvalidated reference measurements published', contract_errors(scenario, summary, diagnostic))

    def test_normal_requires_original_acceptance_and_reference_evidence(self):
        summary = self.failure_summary('fatal-sample')
        summary.update(completion=dict(success=True), sample_tool=dict(outcome='pass'), interventions=0)
        summary['acceptance'].update(profile_error=0.01, pressure_gradient_error=0.02)
        summary['coverage']['references'] = True
        diagnostic = dict(runs=[dict(status='pass')])
        self.assertEqual(contract_errors('healthy', summary, diagnostic), [])
        summary['coverage']['references'] = False
        self.assertTrue(contract_errors('healthy', summary, diagnostic))

    def test_unknown_scenario_rejected(self):
        with self.assertRaises(ValueError):
            contract_errors('unknown', {}, {})


if __name__ == '__main__':
    unittest.main()
