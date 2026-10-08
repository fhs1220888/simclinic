"""Synthetic normalized rows test arithmetic; none are natural CFD evidence."""
import copy
import unittest

from simclinic.evaluation.recovery_observation_score import CONSUMPTION_FIELDS, score_observations


def entry(identifier, stage='natural', arm='candidate', repeat=1):
    return dict(id=identifier, stage=stage, arm=arm, spec_id='C7', repeat=repeat)


def observation(item, **changes):
    row = dict(id=item['id'], execution_id='execution-' + item['id'], stage=item['stage'],
               arm=item['arm'], spec_id=item['spec_id'], run_id='native-' + item['id'], input_version='input-' + item['id'],
               quality_met=True, evidence_verified=True, acceptance_verified=True, explicit_completion=True,
               outcome_class='success', failure_category=None,
               first_sample=dict(function_called=True, launch_status='started', missing_output_confirmed=False,
                   eligible=False, evidence_verified=True, run_id='native-' + item['id'], input_version='input-' + item['id'],
                   exit_code=0, nonfatal_logs_verified=True, eligibility_before_recovery=True,
                   eligibility_evidence_verified=True, elapsed_seconds=3),
               recovery=dict(launched=False, sample_pass=False, branch_elapsed_seconds=0,
                             attempt_count=0, evidence_verified=True),
               consumption={field: 0 for field in CONSUMPTION_FIELDS}, consumption_known={},
               audits=dict(false_completion='pass', ineffective_retry='pass'))
    row['consumption'].update(model_tokens=100, model_calls=1, solver_calls=1, solver_elapsed_seconds=4,
                              sample_calls=1, sample_elapsed_seconds=3, workflow_elapsed_seconds=10, agent_workflow_elapsed_seconds=10,
                              recovery_branch_overhead_seconds=0, evidence_count=8, billed_cost=.25)
    row.update(changes)
    return row


def started(rows):
    return [dict(id=row['id'], execution_id=row['execution_id'], run_id=None, input_version=None) for row in rows]


def missing(row, eligible=True):
    row['first_sample'].update(missing_output_confirmed=True, eligible=eligible)
    row.update(quality_met=False, explicit_completion=False, outcome_class='honest_task_failure',
               failure_category='sampling_missing_output')
    return row


class RecoveryObservationScoreTests(unittest.TestCase):
    def test_zero_natural_opportunities_is_not_substituted_by_injected_success(self):
        plan = [entry('natural-b', arm='baseline'), entry('natural-c'),
                entry('injected-b', stage='injected', arm='baseline'), entry('injected-c', stage='injected')]
        rows = [observation(item) for item in plan]
        missing(rows[2]); missing(rows[3])
        for row in rows[2:]: row['injection_applied'] = True
        rows[3].update(quality_met=True, explicit_completion=True, outcome_class='success')
        rows[3]['recovery'].update(launched=True, sample_pass=True, attempt_count=1, branch_elapsed_seconds=5)
        rows[3]['consumption'].update(sample_calls=2, sample_elapsed_seconds=6, workflow_elapsed_seconds=15)
        report = score_observations(plan, rows, started=started(rows))
        self.assertEqual(report['comparisons']['natural']['recovery_benefit_status'], 'not_assessable')
        self.assertTrue(report['comparisons']['natural']['natural_recovery_benefit_not_assessable'])
        self.assertEqual(report['comparisons']['injected']['candidate_minus_baseline_percentage_points'], 100)
        self.assertFalse(report['natural_and_injected_pooled'])
        natural = report['groups']['natural/candidate']
        self.assertEqual((natural['counts']['N'], natural['counts']['M'], natural['counts']['E']), (1, 1, 0))
        self.assertIsNone(natural['recovery_rates']['A_over_E'])
        injected = report['groups']['injected/candidate']
        self.assertEqual((injected['counts']['A'], injected['counts']['R_sample'], injected['counts']['R_full']), (1, 1, 1))
        self.assertEqual(injected['intervention']['coverage'], 1)

    def test_planning_failure_counts_task_denominator_but_not_sample_denominator(self):
        item = entry('planning-failure')
        row = observation(item, quality_met=False, run_id=None, input_version=None, explicit_completion=False,
                          outcome_class='honest_task_failure', failure_category='input_generation_failure')
        row['first_sample'] = dict(function_called=False, launch_status='not_reached')
        row['consumption'].update(solver_calls=0, sample_calls=0)
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['N'], group['counts']['M'], group['counts']['S']), (1, 0, 0))
        self.assertEqual(group['counts']['not_reached_first_sample'], 1)
        self.assertEqual(group['frequencies']['first_missing_range'], [None, None])
        self.assertIsNone(group['consumption']['model_tokens']['per_full_success'])

    def test_first_call_launch_failure_and_prelaunch_rejection_both_count_M(self):
        plan = [entry('launch-failure'), entry('gate-rejection')]
        rows = [observation(item, quality_met=False, explicit_completion=False) for item in plan]
        rows[0]['first_sample'].update(launch_status='failed', exit_code=None)
        rows[1]['first_sample'].update(launch_status='not_attempted', exit_code=None)
        group = score_observations(plan, rows, started=started(rows))['groups']['natural/candidate']
        self.assertEqual(group['counts']['M'], 2)
        self.assertEqual(group['counts']['F'], 0)
        self.assertEqual(group['launch_status'], {'failed': 1, 'not_attempted': 1})

    def test_timeout_is_known_nonrecoverable_and_unknown_classes_are_disjoint(self):
        plan = [entry('timeout'), entry('unknown-first'), entry('unknown-eligible')]
        rows = [observation(item, quality_met=False, explicit_completion=False, outcome_class='honest_task_failure') for item in plan]
        rows[0]['first_sample'].update(exit_code=None, missing_output_confirmed=False, eligible=False)
        rows[0]['failure_category'] = 'sampling_timeout'
        rows[1]['first_sample'].update(exit_code=None, missing_output_confirmed=None, eligible=None)
        missing(rows[2], eligible=None)
        group = score_observations(plan, rows, started=started(rows))['groups']['natural/candidate']
        self.assertEqual((group['counts']['M'], group['counts']['F'], group['counts']['E'],
                          group['counts']['U_F'], group['counts']['U_E']), (3, 1, 0, 1, 1))
        self.assertEqual(group['frequencies']['first_missing_range'], [1/3, 2/3])
        self.assertEqual(group['frequencies']['eligibility_range_over_M'], [0, 2/3])
        self.assertEqual(group['failure_categories']['sampling_timeout'], 1)

    def test_unknown_total_preserves_known_lower_bound_and_never_zeroes_cost(self):
        plan = [entry('known'), entry('usage-unknown')]
        rows = [observation(item) for item in plan]
        rows[1]['consumption']['model_tokens'] = None
        rows[1]['consumption_known']['model_tokens'] = 40
        rows[1]['consumption']['billed_cost'] = None
        group = score_observations(plan, rows, started=started(rows))['groups']['natural/candidate']
        tokens = group['consumption']['model_tokens']
        self.assertIsNone(tokens['total'])
        self.assertEqual(tokens['known_lower_bound'], 140)
        self.assertEqual(tokens['unknown_tasks'], 1)
        self.assertIsNone(tokens['per_started'])
        self.assertIsNone(tokens['per_full_success'])
        self.assertEqual(group['consumption']['billed_cost']['known_lower_bound'], .25)
        self.assertEqual(tokens['distribution']['scope'], 'known_values_only')

    def test_failed_additional_sample_keeps_first_failure_and_all_extra_consumption(self):
        item = entry('second-fails')
        row = missing(observation(item))
        row['recovery'].update(launched=True, sample_pass=False, branch_elapsed_seconds=7, attempt_count=1)
        row['consumption'].update(sample_calls=2, sample_elapsed_seconds=9, workflow_elapsed_seconds=18)
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['A'], group['counts']['R_sample'], group['counts']['R_full']), (1, 0, 0))
        self.assertEqual(group['consumption']['sample_calls']['total'], 2)
        self.assertEqual(group['consumption']['solver_calls']['total'], 1)
        self.assertEqual(group['recovery_costs']['first_failure_elapsed_seconds']['total'], 3)
        self.assertEqual(group['recovery_costs']['recovery_branch_elapsed_seconds']['total'], 7)
        self.assertIsNone(group['consumption']['workflow_elapsed_seconds']['per_full_success'])

    def test_started_then_interrupted_counts_N_and_unstarted_is_not_a_failure(self):
        plan = [entry('interrupted'), entry('never-started')]
        ledger = [dict(id='interrupted', execution_id='execution-interrupted',
                       consumption_known=dict(model_tokens=30))]
        report = score_observations(plan, [], started=ledger)
        group = report['groups']['natural/candidate']
        self.assertEqual((group['counts']['N'], group['counts']['M'], group['counts']['S']), (1, 0, 0))
        self.assertEqual(group['counts']['first_sample_reach_unknown'], 1)
        self.assertEqual(group['frequencies']['first_missing_range_including_unknown_reach'], [0, 1])
        self.assertEqual(report['not_started'], ['never-started'])
        self.assertEqual(group['consumption']['model_tokens']['known_lower_bound'], 30)
        self.assertIsNone(group['consumption']['model_tokens']['total'])
        self.assertFalse(report['comparisons']['natural']['complete_predeclared_comparison'])

    def test_duplicate_or_wrong_identity_records_do_not_promote_success(self):
        item = entry('identity')
        good = observation(item)
        duplicate = copy.deepcopy(good)
        duplicate['consumption']['model_tokens'] = 200
        report = score_observations([item], [good, duplicate], started=started([good]))
        group = report['groups']['natural/candidate']
        self.assertEqual(group['counts']['S'], 0)
        self.assertEqual(group['consumption']['model_tokens']['known_lower_bound'], 200)
        self.assertIsNone(group['consumption']['model_tokens']['total'])
        wrong = copy.deepcopy(good)
        wrong['first_sample']['run_id'] = 'old-run'
        group = score_observations([item], [wrong], started=started([good]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['N'], group['counts']['M'], group['counts']['S'], group['counts']['U_F']), (1, 1, 0, 1))
        wrong = copy.deepcopy(good)
        wrong['execution_id'] = 'other-execution'
        report = score_observations([item], [wrong], started=started([good]))
        self.assertEqual(report['groups']['natural/candidate']['counts']['S'], 0)
        self.assertEqual(len(report['unmatched_records']), 1)
        self.assertFalse(report['archive_record_set_consistent'])

    def test_confirmed_missing_requires_real_launch_exit_zero_logs_and_prior_eligibility(self):
        item = entry('fatal')
        row = missing(observation(item))
        row['first_sample']['nonfatal_logs_verified'] = False
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['F'], group['counts']['E'], group['counts']['U_F']), (0, 0, 1))
        row = missing(observation(item))
        row['first_sample']['eligibility_before_recovery'] = False
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['F'], group['counts']['E'], group['counts']['U_E']), (1, 0, 1))

    def test_claimed_quality_without_explicit_completion_and_bounded_retry_is_unknown(self):
        item = entry('claim')
        row = observation(item, explicit_completion=False)
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual(group['counts']['S'], 0)
        row = missing(observation(item))
        row.update(quality_met=True, explicit_completion=True)
        row['recovery'].update(launched=True, sample_pass=True, attempt_count=2)
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual(group['counts']['S'], 0)
        self.assertIn('extra_sampler_limit_violated', group['issues'][0]['reasons'])

    def test_unverified_first_success_malformed_cost_and_audit_states_stay_unknown(self):
        item = entry('malformed')
        row = observation(item)
        row['first_sample']['evidence_verified'] = False
        row['consumption'] = None
        row['audits'] = dict(completion_evidence='insufficient_evidence', stale_reference='not_applicable')
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual(group['counts']['S'], 0)
        self.assertIsNone(group['consumption']['model_tokens']['total'])
        self.assertEqual(group['audits']['completion_evidence'], {'insufficient_evidence': 1})
        self.assertEqual(group['audits']['stale_reference'], {'not_applicable': 1})

    def test_verified_nested_intervention_is_distinct_from_natural_observation(self):
        item = entry('injection', stage='injected')
        row = missing(observation(item))
        row['injection'] = dict(status='applied')
        group = score_observations([item], [row], started=started([row]))['groups']['injected/candidate']
        self.assertEqual(group['intervention'], dict(applied=1, not_applied=0, unknown=0, coverage=1))
        row['injection']['evidence_verified'] = False
        group = score_observations([item], [row], started=started([row]))['groups']['injected/candidate']
        self.assertEqual(group['intervention']['unknown'], 1)
        self.assertIn('noncomparable_intervention', group['issues'][0]['reasons'])
        natural = entry('natural-with-intervention')
        row = missing(observation(natural))
        row['injection'] = dict(status='applied', evidence_verified=True)
        group = score_observations([natural], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['F'], group['counts']['E'], group['counts']['U_F']), (0, 0, 1))
        self.assertIn('injection_outside_injected_phase', group['issues'][0]['reasons'])

    def test_quality_and_recovery_consumption_strata_include_failure_and_unknown(self):
        plan = [entry('recovered'), entry('unrecovered'), entry('eligibility-unknown')]
        rows = [missing(observation(item)) for item in plan]
        rows[0].update(quality_met=True, explicit_completion=True)
        rows[0]['recovery'].update(launched=True, sample_pass=True, attempt_count=1)
        rows[1]['consumption']['model_tokens'] = 200
        rows[2]['first_sample']['eligible'] = None
        rows[2]['quality_met'] = None
        rows[2]['consumption']['model_tokens'] = None
        rows[2]['consumption_known']['model_tokens'] = 50
        group = score_observations(plan, rows, started=started(rows))['groups']['natural/candidate']
        self.assertEqual({key: value['N'] for key, value in group['consumption_by_quality'].items()},
                         dict(success=1, failure=1, unknown=1))
        strata = group['consumption_by_recovery']
        self.assertEqual({key: value['N'] for key, value in strata.items()},
                         dict(eligible_recovered=1, eligible_unrecovered=1, eligibility_unknown=1))
        self.assertEqual(strata['eligible_unrecovered']['consumption']['model_tokens']['total'], 200)
        self.assertEqual(strata['eligibility_unknown']['consumption']['model_tokens']['known_lower_bound'], 50)
        self.assertIsNone(strata['eligibility_unknown']['consumption']['model_tokens']['total'])

    def test_explicit_negative_interventions_and_injected_presample_failure_are_known(self):
        for status in ('injection_not_applied', 'disabled', 'not_reached', 'not_applied', 'not_requested'):
            with self.subTest(status=status):
                item = entry('prefix', stage='injected')
                row = observation(item, run_id=None, input_version=None, quality_met=False, explicit_completion=False,
                                  outcome_class='honest_task_failure', failure_category='input_generation_failure')
                row['first_sample'] = dict(function_called=False, launch_status='not_reached')
                row['injection'] = dict(status=status)
                group = score_observations([item], [row], started=started([row]))['groups']['injected/candidate']
                self.assertEqual(group['intervention'], dict(applied=0, not_applied=1, unknown=0, coverage=0))
                self.assertEqual((group['counts']['N'], group['counts']['M'], group['counts']['S']), (1, 0, 0))
                self.assertIn('noncomparable_intervention', group['issues'][0]['reasons'])

    def test_entered_recovery_branch_cost_is_preserved_when_copy_fails_before_sampler(self):
        item = entry('copy-failure')
        row = missing(observation(item))
        row['recovery'].update(branch_entered=True, launched=False, sample_pass=False,
                               branch_elapsed_seconds=4, non_sampling_branch_elapsed_seconds=4, attempt_count=0)
        row['consumption'].update(recovery_branch_overhead_seconds=4, sample_calls=1, workflow_elapsed_seconds=14)
        group = score_observations([item], [row], started=started([row]))['groups']['natural/candidate']
        self.assertEqual((group['counts']['B'], group['counts']['A'], group['counts']['R_full']), (1, 0, 0))
        branch = group['recovery_costs']['recovery_branch_elapsed_seconds']
        self.assertEqual((branch['scope'], branch['total']), ('B', 4))
        self.assertEqual(group['recovery_costs']['launched_branch_elapsed_seconds']['total'], 0)
        self.assertEqual(group['consumption']['recovery_branch_overhead_seconds']['total'], 4)
        self.assertEqual(group['consumption']['sample_calls']['total'], 1)

    def test_ledger_is_required_and_duplicate_execution_cannot_define_denominator(self):
        item = entry('run')
        row = observation(item)
        with self.assertRaisesRegex(ValueError, 'STARTED ledger'):
            score_observations([item], [row])
        ledger = started([row])
        with self.assertRaisesRegex(ValueError, 'Duplicate STARTED'):
            score_observations([item], [row], started=ledger + ledger)


if __name__ == '__main__':
    unittest.main()
