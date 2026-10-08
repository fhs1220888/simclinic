"""Software-only observer tests: fake child processes, real run/hash manifests."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from simclinic.domains.openfoam.sampling_gate import SamplingError
from simclinic.evaluation.recovery_observer import observed_completion_policy
from simclinic.evaluation.recovery_observation_score import CONSUMPTION_FIELDS, score_observations
from simclinic.evaluation.recovery_observation_driver import stop_reason
from simclinic.foam_runtime import verified_workspace, current_record
from tests import test_sampling_recovery_boundaries as boundary_fixtures


class RecoveryObserverTests(unittest.TestCase):
    def setup_case(self, root, *, enabled=False, scenario='natural', modes=('valid',), change=None):
        legacy, case, context, state = boundary_fixtures.SamplingRecoveryBoundaryTests().setup_case(root)
        calls = []

        def factory(argv, **kwargs):
            index = len(calls)
            mode = modes[min(index, len(modes)-1)]
            mount = next(value for value in argv if value.startswith('type=bind,src='))
            work = Path(mount.split('src=', 1)[1].split(',dst=', 1)[0])
            calls.append(work)
            if mode == 'launch_failure':
                raise FileNotFoundError('Synthetic absent Docker executable')
            if mode not in ('missing', 'unknown_pid'):
                folder = work/'postProcessing/sample/100'
                folder.mkdir(parents=True)
                (folder/'profile.xy').write_text('# y p U_x U_y U_z\n' + '\n'.join(
                    f'{y:.12g} 0 {.02*y/.1:.12g} 0 0' for y in (.0025+i*.005 for i in range(20))) + '\n')
                (folder/'pressure.xy').write_text('# x p U_x U_y U_z\n' + '\n'.join(
                    f'{x:.12g} 0 .01 0 0' for x in (.1+i*.05 for i in range(17))) + '\n')
            if mode == 'missing_pressure':
                next((work/'postProcessing').rglob('pressure.xy')).unlink()
            if mode == 'invalid_pressure':
                next((work/'postProcessing').rglob('pressure.xy')).write_text('not a sampled line\n')
            if mode == 'empty_pressure':
                next((work/'postProcessing').rglob('pressure.xy')).write_bytes(b'')
            if mode == 'ambiguous_pressure':
                other = work/'postProcessing/other/pressure.xy'
                other.parent.mkdir()
                other.write_bytes(next((work/'postProcessing').rglob('pressure.xy')).read_bytes())
            if mode == 'unsafe_pressure':
                pressure = next((work/'postProcessing').rglob('pressure.xy'))
                outside = work.parent/'outside-pressure.xy'
                outside.write_bytes(pressure.read_bytes())
                pressure.unlink()
                pressure.symlink_to(outside)
            if change is not None:
                change(index, case, work)

            class FakeProcess:
                pid = None if mode == 'unknown_pid' else 4000 + index
                returncode = None
                def communicate(self, timeout=None):
                    if mode == 'timeout' and self.returncode is None and timeout is not None:
                        raise subprocess.TimeoutExpired(argv, timeout, output=b'Synthetic partial sampler output\n')
                    self.returncode = -9 if mode == 'timeout' else 4 if mode == 'exit_failure' else 0
                    if mode == 'fatal':
                        return b'FOAM FATAL IO ERROR\nEnd\n', b''
                    if mode == 'encoding':
                        return b'\xff', b''
                    return b'Synthetic software sampler\nEnd\n', b''
                def kill(self):
                    self.returncode = -9
            return FakeProcess()

        policy = observed_completion_policy(legacy.recorder, legacy.reference, legacy.image,
            dict(id='C0', kind='couette', wall_speed=.02, nx=40, ny=20),
            enabled=enabled, scenario=scenario, process_factory=factory)
        return policy, case, context, calls

    def execute(self, policy, case, context):
        result, error = None, None
        # The only delegated subprocess.run call is best-effort docker cleanup.
        with patch('simclinic.domains.openfoam.sampling_gate.subprocess.run',
                   return_value=subprocess.CompletedProcess(['synthetic-cleanup'], 0, b'', b'')):
            try:
                with verified_workspace(case, context) as work:
                    result = policy.sample(work, context)
            except (ValueError, OSError) as exc:
                error = exc
        return result, error, policy.observation_summary()

    def samples(self, policy):
        return [e for e in policy.recorder.data['events'] if e.get('operation') == 'sample']

    def assert_artifacts(self, policy):
        for artifact in policy.recorder.data['artifacts']:
            path = policy.recorder.directory/artifact['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact['sha256'])

    def test_construction_and_unreached_summary_do_not_start_a_sampler(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root)
            summary = policy.observation_summary()
            self.assertEqual(calls, [])
            self.assertFalse(summary['first_sample']['function_called'])
            self.assertEqual(summary['first_sample']['launch_status'], 'not_reached')
            self.assertFalse(summary['first_sample']['missing_output_confirmed'])
            self.assertFalse(summary['first_sample']['eligible'])

    def test_both_arms_use_same_eligibility_before_any_candidate_recovery(self):
        outcomes = []
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as root:
                policy, case, context, calls = self.setup_case(root, enabled=enabled, modes=('missing', 'valid'))
                result, error, summary = self.execute(policy, case, context)
                first = summary['first_sample']
                self.assertTrue(first['function_called'])
                self.assertEqual(first['launch_status'], 'started')
                self.assertTrue(first['missing_output_confirmed'])
                self.assertTrue(first['eligible'])
                self.assertTrue(first['nonfatal_logs_verified'])
                self.assertTrue(first['eligibility_evidence_verified'])
                self.assertTrue(first['eligibility_before_recovery'])
                self.assertEqual(first['run_id'], context['run_id'])
                self.assertEqual(first['input_version'], context['input_version'])
                events = policy.recorder.data['events']
                qualification_index = next(i for i, e in enumerate(events) if e.get('observation_type') == 'shared_first_sample_eligibility')
                decisions = [i for i, e in enumerate(events) if e.get('decision_type') == 'bounded_postprocess_recovery']
                self.assertTrue(all(qualification_index < index for index in decisions))
                self.assertEqual(len(calls), 2 if enabled else 1)
                self.assertEqual(self.samples(policy)[0]['error_category'], 'sampling_missing_output')
                if enabled:
                    self.assertIsNone(error)
                    self.assertTrue(summary['recovery']['launched'])
                    self.assertTrue(summary['recovery']['sample_pass'])
                    self.assertEqual(self.samples(policy)[1]['retry_of'], self.samples(policy)[0]['id'])
                else:
                    self.assertEqual(error.code, 'sampling_missing_output')
                    self.assertFalse(summary['recovery']['launched'])
                self.assert_artifacts(policy)
                outcomes.append({key: first[key] for key in ('function_called', 'launch_status', 'missing_output_confirmed', 'eligible', 'reason_codes')})
        self.assertEqual(outcomes[0], outcomes[1])

    def test_natural_success_never_injects_and_reports_one_first_call(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as root:
                policy, case, context, calls = self.setup_case(root, enabled=enabled)
                result, error, summary = self.execute(policy, case, context)
                self.assertIsNone(error)
                self.assertEqual(len(calls), 1)
                self.assertFalse(summary['first_sample']['missing_output_confirmed'])
                self.assertFalse(summary['first_sample']['eligible'])
                self.assertFalse(summary['recovery']['branch_entered'])
                self.assertEqual(summary['recovery']['branch_elapsed_seconds'], 0)
                self.assertEqual(summary['injection']['status'], 'disabled')
                self.assertFalse((policy.recorder.directory/'observations/recovery-observer/intervention').exists())
                self.assert_artifacts(policy)

    def test_execution_started_alone_never_confirms_launch_or_allows_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('unknown_pid',))
            result, error, summary = self.execute(policy, case, context)
            gate = json.loads((policy.recorder.directory/'observations/sampling/gate-assessment.json').read_text())
            self.assertTrue(gate['execution_started'])
            self.assertEqual(gate['error_category'], 'sampling_missing_output')
            self.assertEqual(summary['first_sample']['launch_status'], 'unknown')
            self.assertIsNone(summary['first_sample']['missing_output_confirmed'])
            self.assertIsNone(summary['first_sample']['eligible'])
            self.assertEqual(error.code, 'sampling_recovery_eligibility_unknown')
            self.assertEqual(len(calls), 1)
            self.assertFalse(summary['recovery']['launched'])

    def test_known_launch_failure_is_in_first_call_denominator(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('launch_failure',))
            result, error, summary = self.execute(policy, case, context)
            first = summary['first_sample']
            self.assertTrue(first['function_called'])
            self.assertTrue(first['launch_attempted'])
            self.assertEqual(first['launch_status'], 'failed')
            self.assertFalse(first['missing_output_confirmed'])
            self.assertFalse(first['eligible'])
            self.assertTrue(first['evidence_verified'])
            self.assertEqual(error.code, 'sampling_launch_error')
            self.assertEqual(len(calls), 1)
            self.assert_artifacts(policy)

    def test_timeout_encoding_fatal_and_nonzero_do_not_recover_or_erase_logs(self):
        for mode, code in (('timeout', 'sampling_timeout'), ('encoding', 'sampling_log_encoding'),
                           ('fatal', 'sampling_fatal_log'), ('exit_failure', 'sampling_process_exit')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                policy, case, context, calls = self.setup_case(root, enabled=True, modes=(mode,))
                result, error, summary = self.execute(policy, case, context)
                self.assertEqual(error.code, code)
                self.assertEqual(len(calls), 1)
                self.assertFalse(summary['recovery']['launched'])
                self.assertTrue(summary['first_sample']['evidence_verified'])
                if mode == 'encoding':
                    self.assertIsNone(summary['first_sample']['missing_output_confirmed'])
                else:
                    self.assertFalse(summary['first_sample']['missing_output_confirmed'])
                self.assert_artifacts(policy)

    def test_source_hash_change_makes_first_missing_ineligible_before_retry(self):
        with tempfile.TemporaryDirectory() as root:
            def change(index, case, work):
                (case/'1/U').write_text('Changed trusted source output\n')
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('missing',), change=change)
            result, error, summary = self.execute(policy, case, context)
            self.assertTrue(summary['first_sample']['missing_output_confirmed'])
            self.assertFalse(summary['first_sample']['eligible'])
            self.assertIn('digest_mismatch', summary['first_sample']['reason_codes'])
            self.assertEqual(error.code, 'sampling_recovery_ineligible')
            self.assertEqual(len(calls), 1)
            self.assertFalse(hasattr(policy, 'recovery_attempts'))
            self.assert_artifacts(policy)

    def test_injected_full_gate_qualification_archives_both_outputs_before_profile_removal(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as root:
                policy, case, context, calls = self.setup_case(root, enabled=enabled, scenario='transient_missing')
                result, error, summary = self.execute(policy, case, context)
                self.assertTrue(summary['injection']['applied'])
                self.assertFalse(summary['injection']['natural_failure'])
                folder = policy.recorder.directory/'observations/recovery-observer/intervention'
                qualification = json.loads((folder/'full-gate-qualification.json').read_text())
                self.assertTrue(qualification['original_full_gate_conditions_verified'])
                self.assertTrue(qualification['original_measurement_reader_used'])
                self.assertFalse(qualification['reference_threshold_pass_required'])
                self.assertTrue((folder/'profile.xy').is_file())
                self.assertTrue((folder/'pressure.xy').is_file())
                self.assertFalse(any((calls[0]/'postProcessing').rglob('profile.xy')))
                self.assertTrue(any((calls[0]/'postProcessing').rglob('pressure.xy')))
                self.assertTrue(summary['first_sample']['eligible'])
                self.assertEqual(self.samples(policy)[0]['error_category'], 'sampling_missing_output')
                self.assertEqual(len(calls), 2 if enabled else 1)
                self.assert_artifacts(policy)

    def test_injection_is_not_applied_if_either_output_or_original_gate_invalid(self):
        for mode in ('missing_pressure', 'invalid_pressure', 'empty_pressure', 'ambiguous_pressure', 'unsafe_pressure', 'fatal', 'encoding', 'exit_failure', 'missing', 'timeout', 'launch_failure'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                policy, case, context, calls = self.setup_case(root, enabled=False, scenario='transient_missing', modes=(mode,))
                result, error, summary = self.execute(policy, case, context)
                self.assertFalse(summary['injection']['applied'])
                self.assertEqual(summary['injection']['status'], 'injection_not_applied')
                self.assertTrue(summary['injection']['reason_codes'])
                self.assertEqual(len(calls), 1)
                self.assertFalse((policy.recorder.directory/'observations/recovery-observer/intervention/plan.json').exists())
                self.assert_artifacts(policy)

    def test_persistent_second_missing_stops_with_first_failure_and_timing_retained(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('missing', 'missing'))
            result, error, summary = self.execute(policy, case, context)
            self.assertEqual(error.code, 'sampling_missing_output')
            self.assertEqual(len(calls), 2)
            self.assertTrue(summary['recovery']['launched'])
            self.assertFalse(summary['recovery']['sample_pass'])
            self.assertEqual(summary['recovery']['attempt_count'], 1)
            self.assertEqual([e['outcome'] for e in self.samples(policy)], ['fail', 'fail'])
            self.assertGreater(summary['recovery']['branch_elapsed_seconds'], 0)
            self.assert_artifacts(policy)

    def score_row(self, entry, policy, context, error, summary):
        """Explicit software acceptance fixture; observer fields are untouched.

        These flags model a driver's separately verified completion contract.
        They are not an assertion of CFD validity for the synthetic fields.
        """
        samples = self.samples(policy)
        consumption = {field: 0 for field in CONSUMPTION_FIELDS}
        consumption.update(sample_calls=len(samples), sample_elapsed_seconds=sum(e['elapsed_seconds'] for e in samples),
                           evidence_count=len(policy.recorder.data['artifacts']),
                           recovery_branch_overhead_seconds=summary['recovery'].get('non_sampling_branch_elapsed_seconds', 0))
        success = error is None
        return dict(**entry, execution_id='software-' + entry['id'], **context,
                    quality_met=success, evidence_verified=summary['collection_complete'],
                    acceptance_verified=True, explicit_completion=success,
                    first_sample=summary['first_sample'], recovery=summary['recovery'], injection=summary['injection'],
                    identity_corrupt=summary['identity_corrupt'], policy_violation=summary['policy_violation'],
                    outcome_class='success' if success else 'honest_task_failure',
                    failure_category=getattr(error, 'code', None), false_completion=False,
                    consumption=consumption, consumption_known={})

    def test_actual_observer_summaries_feed_separate_natural_and_injected_scoring(self):
        entries, rows, ledger = [], [], []
        for stage in ('natural', 'injected'):
            for enabled in (False, True):
                with tempfile.TemporaryDirectory() as root:
                    item = dict(id=stage + ('-candidate' if enabled else '-baseline'), stage=stage,
                                arm='candidate' if enabled else 'baseline', spec_id='software-C0', repeat=1)
                    policy, case, context, calls = self.setup_case(root, enabled=enabled,
                        scenario='transient_missing' if stage == 'injected' else 'natural')
                    result, error, summary = self.execute(policy, case, context)
                    self.assert_artifacts(policy)
                    row = self.score_row(item, policy, context, error, summary)
                    entries.append(item)
                    rows.append(row)
                    ledger.append(dict(id=item['id'], execution_id=row['execution_id'], run_id=None, input_version=None))
        scored = score_observations(entries, rows, started=ledger)
        self.assertEqual(scored['comparisons']['natural']['recovery_benefit_status'], 'not_assessable')
        self.assertFalse(scored['natural_and_injected_pooled'])
        for arm in ('baseline', 'candidate'):
            self.assertEqual(scored['groups']['natural/' + arm]['counts']['E'], 0)
            self.assertEqual(scored['groups']['natural/' + arm]['counts']['S'], 1)
        candidate = scored['groups']['injected/candidate']
        self.assertEqual(tuple(candidate['counts'][key] for key in ('N', 'M', 'F', 'E', 'A', 'R_sample', 'R_full')), (1, 1, 1, 1, 1, 1, 1))
        self.assertEqual(scored['groups']['injected/baseline']['counts']['S'], 0)

    def test_complete_timeout_observer_is_known_nonrecoverable_and_driver_continues(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('timeout',))
            result, error, summary = self.execute(policy, case, context)
            item = dict(id='software-timeout', stage='natural', arm='candidate', spec_id='software-C0', repeat=1)
            row = self.score_row(item, policy, context, error, summary)
            self.assertFalse(policy.recorder.tools_complete)
            self.assertIsNone(stop_reason(row))
            scored = score_observations([item], [row], started=[dict(id=item['id'], execution_id=row['execution_id'])])
            counts = scored['groups']['natural/candidate']['counts']
            self.assertEqual(tuple(counts[key] for key in ('N', 'M', 'F', 'E', 'U_F', 'U_E', 'S')), (1, 1, 0, 0, 0, 0, 0))
            self.assertIsNone(summary['first_sample']['exit_code'])
            self.assertEqual(self.samples(policy)[0]['outcome'], 'unknown')
            self.assert_artifacts(policy)

    def test_identity_change_after_second_gate_pass_is_not_full_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('missing', 'valid'))
            checks = []
            def final_check(source, expected):
                checks.append(True)
                if len(checks) == 2:
                    (case/'0/U').write_text('changed immediately before final recovery check\n')
                return current_record(source, expected)
            with patch('simclinic.domains.openfoam.sampling_recovery.current_record', final_check):
                result, error, summary = self.execute(policy, case, context)
            self.assertEqual(error.code, 'stale_input')
            self.assertIsNone(result)
            self.assertTrue(summary['first_sample']['eligible'])
            self.assertTrue(summary['recovery']['sample_pass'])
            self.assertTrue(summary['identity_corrupt'])
            self.assertEqual(len(calls), 2)
            self.assert_artifacts(policy)

    def test_branch_wall_includes_copy_and_final_identity_checks(self):
        with tempfile.TemporaryDirectory() as root:
            policy, case, context, calls = self.setup_case(root, enabled=True, modes=('missing', 'valid'))
            @contextmanager
            def delayed_copy(source, expected):
                time.sleep(.02)
                with verified_workspace(source, expected) as work:
                    yield work
            def delayed_check(source, expected):
                time.sleep(.01)
                return current_record(source, expected)
            with patch('simclinic.domains.openfoam.sampling_recovery.verified_workspace', delayed_copy), \
                 patch('simclinic.domains.openfoam.sampling_recovery.current_record', delayed_check):
                result, error, summary = self.execute(policy, case, context)
            self.assertIsNone(error)
            recovery = summary['recovery']
            self.assertGreater(recovery['branch_elapsed_seconds'], recovery['extra_calls_elapsed_seconds'] + .03)
            self.assertTrue(recovery['includes_fresh_copy_and_final_identity_verification'])
            self.assertGreaterEqual(summary['observation_elapsed_seconds'], 0)
            self.assert_artifacts(policy)


if __name__ == '__main__':
    unittest.main()
