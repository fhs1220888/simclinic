"""Conservative scoring of normalized, independently verified observations.

This scorer does not verify archive bytes itself. A caller must bind the STARTED
ledger and verified observer/acceptance evidence before setting verification
flags. Unknown evidence never becomes a successful task or a zero cost.
"""
from collections import Counter, defaultdict
import math
import statistics


STAGES = ('development', 'natural', 'injected')
ARMS = ('baseline', 'candidate')
CONSUMPTION_FIELDS = ('model_tokens', 'model_calls', 'solver_calls',
                      'solver_elapsed_seconds', 'sample_calls', 'sample_elapsed_seconds',
                      'workflow_elapsed_seconds', 'agent_workflow_elapsed_seconds', 'recovery_branch_overhead_seconds',
                      'evidence_count', 'billed_cost')
COUNT_FIELDS = {'model_tokens', 'model_calls', 'solver_calls', 'sample_calls', 'evidence_count'}
LAUNCH_STATES = {'started', 'failed', 'unknown', 'not_reached', 'not_attempted'}
AUDIT_CHECKS = ('preflight', 'error_continuation', 'retries', 'stale_results', 'completion')


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def number(value, field=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    if field in COUNT_FIELDS and type(value) is not int:
        return None
    return value


def boolean(value):
    return value if type(value) is bool else None


def consumption_item(row, field):
    consumption = row.get('consumption') if isinstance(row.get('consumption'), dict) else {}
    lower_bounds = row.get('consumption_known') if isinstance(row.get('consumption_known'), dict) else {}
    total = number(consumption.get(field), field)
    known = number(lower_bounds.get(field), field)
    if total is not None:
        return total, total
    return None, known if known is not None else 0


def summarized(values, successes, started):
    known = [item['value'] for item in values if item['value'] is not None]
    lower = sum(item['known_lower_bound'] for item in values)
    unknown = len(values) - len(known)
    total = sum(known) if not unknown else None
    return dict(total=total, known_lower_bound=lower, unknown_tasks=unknown,
                per_started=ratio(total, started) if total is not None else None,
                per_full_success=ratio(total, successes) if total is not None else None,
                distribution=dict(known_count=len(known), unknown_count=unknown,
                                  minimum=min(known) if known else None,
                                  maximum=max(known) if known else None,
                                  mean=statistics.mean(known) if known else None,
                                  median=statistics.median(known) if known else None,
                                  scope='known_values_only'),
                all_runs=values)


def consumption_summary(rows, successes):
    result = {}
    for field in CONSUMPTION_FIELDS:
        values = []
        for row in rows:
            total, lower = consumption_item(row, field)
            values.append(dict(id=row['id'], execution_id=row['execution_id'],
                               value=total, known_lower_bound=lower))
        result[field] = summarized(values, successes, len(rows))
    return result


def same_native_identity(row, first):
    """A pre-sampling failure needs no native identity; a sampled run does."""
    run_id, input_version = row.get('run_id'), row.get('input_version')
    if not isinstance(run_id, str) or not run_id or not isinstance(input_version, str) or not input_version:
        return False
    return first.get('run_id') == run_id and first.get('input_version') == input_version


def normalize(start, rows, entry, duplicated_task=False):
    identifier, execution_id = start['id'], start['execution_id']
    issues = []
    if entry is None:
        issues.append('unplanned_started_task')
    if duplicated_task:
        issues.append('task_started_more_than_once')
    row = dict(rows[0]) if len(rows) == 1 else {}
    if not rows:
        issues.append('started_without_terminal_record')
    elif len(rows) > 1:
        issues.append('duplicate_terminal_records')
    row['id'], row['execution_id'] = identifier, execution_id
    if rows and any(r.get('id') != identifier or r.get('execution_id') != execution_id for r in rows):
        issues.append('execution_identity_mismatch')
    for key in ('run_id', 'input_version'):
        expected = start.get(key)
        if expected is not None and row.get(key) != expected:
            issues.append('native_identity_mismatch:' + key)
    if entry is not None:
        for key in ('stage', 'arm', 'spec_id'):
            if key in row and row[key] != entry.get(key):
                issues.append('protocol_identity_mismatch:' + key)
            row[key] = entry.get(key)
    else:
        row.update(stage='unassigned', arm='unknown', spec_id=None)
    intervention = row.get('injection')
    if isinstance(intervention, dict):
        status = intervention.get('status')
        trusted = row.get('evidence_verified') is True and intervention.get('evidence_verified') is not False
        row['injection_applied'] = (status == 'applied' if status in
                                    ('applied', 'not_applied', 'not_requested', 'injection_not_applied',
                                     'disabled', 'not_reached') and trusted else None)
    else:
        row['injection_applied'] = boolean(row.get('injection_applied'))
    invalid_intervention = row['injection_applied'] is True and row['stage'] != 'injected'
    if invalid_intervention:
        issues.append('injection_outside_injected_phase')
    elif row['stage'] == 'injected' and row['injection_applied'] is not True:
        issues.append('noncomparable_intervention')

    # Duplicate snapshots can describe the same accumulated cost. Sum neither
    # twice nor zero: the largest supported cumulative value is a lower bound.
    if len(rows) > 1:
        row['consumption'] = {field: None for field in CONSUMPTION_FIELDS}
        row['consumption_known'] = {
            field: max((consumption_item(record, field)[1] for record in rows), default=0)
            for field in CONSUMPTION_FIELDS}
    elif not rows:
        row['consumption'] = start.get('consumption', {})
        row['consumption_known'] = start.get('consumption_known', {})
    else:
        row.setdefault('consumption', {})
        row.setdefault('consumption_known', {})
    for name in ('consumption', 'consumption_known'):
        if not isinstance(row[name], dict):
            issues.append('invalid_' + name + '_shape')
            row[name] = {}
        else:
            row[name] = dict(row[name])
    for field in CONSUMPTION_FIELDS:
        raw = row['consumption'].get(field)
        if raw is not None and number(raw, field) is None:
            issues.append('invalid_consumption:' + field)
        known = row['consumption_known'].get(field)
        if known is not None and number(known, field) is None:
            issues.append('invalid_consumption_lower_bound:' + field)
        if number(raw, field) is not None and number(known, field) is not None and known > raw:
            issues.append('consumption_lower_bound_exceeds_total:' + field)
            row['consumption'][field] = None

    identity_bad = any('identity_mismatch' in issue or issue in
                       ('duplicate_terminal_records', 'task_started_more_than_once', 'unplanned_started_task')
                       for issue in issues)
    first = row.get('first_sample') if isinstance(row.get('first_sample'), dict) else {}
    called = boolean(first.get('function_called'))
    launch = first.get('launch_status')
    launch = launch if launch in LAUNCH_STATES else 'unknown'
    missing, eligible = boolean(first.get('missing_output_confirmed')), boolean(first.get('eligible'))
    if called is False:
        if launch not in ('not_reached', 'unknown'):
            issues.append('launch_without_function_call')
        launch, missing, eligible = 'not_reached', False, False
    elif called is True:
        if row.get('evidence_verified') is not True:
            issues.append('unverified_first_function_call_record')
        if not same_native_identity(row, first):
            issues.append('first_sample_identity_mismatch')
            missing, eligible = None, None
        if first.get('evidence_verified') is not True or row.get('evidence_verified') is not True:
            missing, eligible = None, None
        if missing is True:
            if launch != 'started':
                issues.append('missing_output_without_verified_process_launch')
                missing, eligible = None, None
            elif (first.get('exit_code') != 0 or type(first.get('exit_code')) is not int or
                  first.get('nonfatal_logs_verified') is not True):
                issues.append('missing_output_without_exit_zero_nonfatal_logs')
                missing, eligible = None, None
            elif first.get('eligibility_before_recovery') is not True or first.get('eligibility_evidence_verified') is not True:
                eligible = None
        elif eligible is True:
            issues.append('eligible_without_confirmed_missing_output')
            missing, eligible = None, None
        if launch == 'not_reached':
            issues.append('called_sample_marked_not_reached')
            launch, missing, eligible = 'unknown', None, None
    else:
        launch, missing, eligible = 'unknown', None, None
    if identity_bad:
        # Function-entry evidence may still count M, but mismatched result
        # evidence cannot establish a first outcome or recovery eligibility.
        missing, eligible = (False, False) if called is False else (None, None)
    if invalid_intervention:
        missing, eligible = (False, False) if called is False else (None, None)

    recovery = row.get('recovery') if isinstance(row.get('recovery'), dict) else {}
    launched, sample_pass = boolean(recovery.get('launched')), boolean(recovery.get('sample_pass'))
    branch_entered = boolean(recovery.get('branch_entered'))
    if launched is True:
        if branch_entered is False:
            issues.append('sampler_launch_without_branch_entry')
        # An actual extra sampler launch implies its containing branch started.
        branch_entered = True
    if identity_bad or row.get('evidence_verified') is not True or recovery.get('evidence_verified') is not True:
        sample_pass = None
    if launched is not True:
        sample_pass = False if launched is False else None
    if launched is True and row['arm'] == 'baseline':
        issues.append('baseline_recovery_launched')
    if branch_entered is True and row['arm'] == 'baseline':
        issues.append('baseline_recovery_branch_entered')
    if launched is True and eligible is not True:
        issues.append('recovery_launched_without_confirmed_eligibility')
    extra_calls = recovery.get('extra_calls', recovery.get('attempt_count'))
    if extra_calls is not None and (type(extra_calls) is not int or extra_calls not in (0, 1)):
        issues.append('extra_sampler_limit_violated')
    safety_bad = any(issue in ('baseline_recovery_launched', 'recovery_launched_without_confirmed_eligibility',
                              'extra_sampler_limit_violated', 'sampler_launch_without_branch_entry',
                              'baseline_recovery_branch_entered') for issue in issues)
    if safety_bad:
        sample_pass = None

    quality = boolean(row.get('quality_met'))
    if quality is True and not (row.get('evidence_verified') is True and row.get('acceptance_verified') is True and
                                row.get('explicit_completion') is True and isinstance(row.get('run_id'), str) and
                                row.get('run_id') and isinstance(row.get('input_version'), str) and row.get('input_version')):
        issues.append('success_without_complete_verified_acceptance')
        quality = None
    if quality is True and (called is not True or launch != 'started'):
        issues.append('success_without_first_sampler_launch')
        quality = None
    if quality is True and (first.get('evidence_verified') is not True or missing is None):
        issues.append('success_without_verified_first_sample_outcome')
        quality = None
    if quality is True and missing is False and (type(first.get('exit_code')) is not int or
                                                 first.get('exit_code') != 0 or
                                                 first.get('nonfatal_logs_verified') is not True):
        issues.append('success_without_exit_zero_nonfatal_sample')
        quality = None
    if quality is True and missing is True and not (launched is True and sample_pass is True):
        issues.append('success_without_verified_recovery_of_first_failure')
        quality = None
    if identity_bad or safety_bad or invalid_intervention or any('sample_identity_mismatch' in issue for issue in issues):
        quality = None
    row.update(quality_met=quality,
               first_sample=dict(first, function_called=called, launch_status=launch,
                                 missing_output_confirmed=missing, eligible=eligible),
               recovery=dict(recovery, launched=launched, branch_entered=branch_entered, sample_pass=sample_pass), issues=issues)
    return row


def consumption_stratum(rows):
    successes = sum(row['quality_met'] is True for row in rows)
    return dict(N=len(rows), S=successes, consumption=consumption_summary(rows, successes))


def recovery_stratum(row):
    first = row['first_sample']
    if first['function_called'] is False:
        return 'first_sample_not_reached'
    if first['function_called'] is None or first['missing_output_confirmed'] is None:
        return 'eligibility_unknown'
    if first['missing_output_confirmed'] is False:
        return 'no_confirmed_missing_output'
    if first['eligible'] is None:
        return 'eligibility_unknown'
    if first['eligible'] is False:
        return 'confirmed_ineligible_missing_output'
    if row['quality_met'] is None:
        return 'eligible_quality_unknown'
    return 'eligible_recovered' if row['quality_met'] is True else 'eligible_unrecovered'


def group_score(rows, planned, stage, arm):
    counts = Counter(N=len(rows), M=0, F=0, E=0, S=0, B=0, A=0, R_sample=0, R_full=0, U_F=0, U_E=0)
    failures, outcomes, launches, audits = Counter(), Counter(), Counter(), defaultdict(Counter)
    for row in rows:
        quality = row['quality_met']
        counts['S'] += quality is True
        counts['quality_false'] += quality is False
        counts['quality_unknown'] += quality is None
        first, recovery = row['first_sample'], row['recovery']
        called, missing, eligible = first['function_called'], first['missing_output_confirmed'], first['eligible']
        if called is True:
            counts['M'] += 1
            launches[first['launch_status']] += 1
            if missing is True:
                counts['F'] += 1
                if eligible is True:
                    counts['E'] += 1
                    counts['R_full'] += arm == 'candidate' and quality is True
                elif eligible is None:
                    counts['U_E'] += 1
                else:
                    counts['confirmed_ineligible_missing_output'] += 1
            elif missing is None:
                counts['U_F'] += 1
            else:
                counts['confirmed_other_first_outcome'] += 1
        elif called is False:
            counts['not_reached_first_sample'] += 1
        else:
            counts['first_sample_reach_unknown'] += 1
        if arm == 'candidate':
            counts['B'] += recovery['branch_entered'] is True
            counts['recovery_branch_entry_unknown'] += recovery['branch_entered'] is None
            counts['A'] += recovery['launched'] is True
            counts['recovery_launch_unknown'] += recovery['launched'] is None
            counts['R_sample'] += recovery['launched'] is True and recovery['sample_pass'] is True
            counts['extra_sample_result_unknown'] += recovery['launched'] is True and recovery['sample_pass'] is None
        if quality is not True:
            failures[str(row.get('failure_category') or 'unknown')] += 1
        outcomes[str(row.get('outcome_class') or 'unknown')] += 1
        results = row.get('audits') if isinstance(row.get('audits'), dict) else {}
        for check in sorted(set(AUDIT_CHECKS) | set(results)):
            result = results.get(check)
            if isinstance(result, dict):
                result = result.get('status')
            audits[check][str(result) if result in ('finding', 'pass', 'insufficient_evidence', 'not_applicable') else 'unknown'] += 1
    n, m, f, e, uf, ue = (counts[key] for key in ('N', 'M', 'F', 'E', 'U_F', 'U_E'))
    reach_unknown = counts['first_sample_reach_unknown']
    frequencies = dict(F_over_M=ratio(f, m), E_over_M=ratio(e, m), E_over_N=ratio(e, n),
                       F_over_known_first_outcomes=ratio(f, m - uf),
                       first_missing_range=[ratio(f, m), ratio(f + uf, m)],
                       eligibility_range_over_M=[ratio(e, m), ratio(e + uf + ue, m)],
                       eligibility_range_over_N=[ratio(e, n), ratio(e + uf + ue, n)],
                       eligibility_range_over_N_including_unknown_reach=[ratio(e, n), ratio(e + uf + ue + reach_unknown, n)],
                       call_denominator_complete=reach_unknown == 0,
                       first_missing_range_including_unknown_reach=[ratio(f, m + reach_unknown),
                                                                   ratio(f + uf + reach_unknown, m + reach_unknown)],
                       eligibility_range_including_unknown_reach=[ratio(e, m + reach_unknown),
                                                                  ratio(e + uf + ue + reach_unknown, m + reach_unknown)],
                       confidence_interval=False)
    recovery_rates = dict(A_over_E=ratio(counts['A'], e),
                          R_sample_over_A=ratio(counts['R_sample'], counts['A']),
                          R_full_over_E=ratio(counts['R_full'], e)) if arm == 'candidate' else None
    recovery_costs = {}
    for key, scope, field in (('first_failure_elapsed_seconds', 'F', 'elapsed_seconds'),
                              ('recovery_branch_elapsed_seconds', 'B', 'branch_elapsed_seconds'),
                              ('launched_branch_elapsed_seconds', 'A', 'branch_elapsed_seconds')):
        if scope == 'F':
            selected = [row for row in rows if row['first_sample']['missing_output_confirmed'] is True]
            uncertain = [row for row in rows if row['first_sample']['function_called'] is None or
                         (row['first_sample']['function_called'] is True and row['first_sample']['missing_output_confirmed'] is None)]
        else:
            membership = 'branch_entered' if scope == 'B' else 'launched'
            selected = [row for row in rows if row['recovery'][membership] is True]
            uncertain = [row for row in rows if arm == 'candidate' and row['recovery'][membership] is None]
        values = []
        for row in selected:
            source = row['first_sample'] if scope == 'F' else row['recovery']
            value = number(source.get(field))
            lower = number(source.get(field + '_known'))
            values.append(dict(id=row['id'], execution_id=row['execution_id'], value=value,
                               known_lower_bound=value if value is not None else lower or 0))
        for row in uncertain:
            values.append(dict(id=row['id'], execution_id=row['execution_id'], value=None,
                               known_lower_bound=0, scope_membership_unknown=True))
        recovery_costs[key] = summarized(values, counts['R_full'], len(selected))
        recovery_costs[key]['scope'] = scope
        recovery_costs[key]['confirmed_scope_tasks'] = len(selected)
        recovery_costs[key]['scope_membership_unknown'] = len(uncertain)
    applied = sum(row.get('injection_applied') is True for row in rows)
    injection_unknown = sum(boolean(row.get('injection_applied')) is None for row in rows)
    quality_subsets, recovery_subsets, failure_subsets = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        quality_subsets['success' if row['quality_met'] is True else 'failure' if row['quality_met'] is False else 'unknown'].append(row)
        recovery_subsets[recovery_stratum(row)].append(row)
        if row['quality_met'] is not True:
            failure_subsets[str(row.get('failure_category') or 'unknown')].append(row)
    return dict(stage=stage, arm=arm, planned=planned, counts=dict(counts),
                complete_task_success_rate=ratio(counts['S'], n), frequencies=frequencies,
                recovery_rates=recovery_rates, recovery_costs=recovery_costs,
                launch_status=dict(launches), failure_categories=dict(failures), outcome_classes=dict(outcomes),
                audits={key: dict(value) for key, value in audits.items()},
                consumption=consumption_summary(rows, counts['S']),
                consumption_by_quality={key: consumption_stratum(quality_subsets[key]) for key in ('success', 'failure', 'unknown')},
                consumption_by_recovery={key: consumption_stratum(value) for key, value in recovery_subsets.items()},
                consumption_by_failure_category={key: consumption_stratum(value) for key, value in failure_subsets.items()},
                intervention=dict(applied=applied, not_applied=n - applied - injection_unknown,
                                  unknown=injection_unknown, coverage=ratio(applied, n)) if stage == 'injected' else None,
                issues=[dict(id=row['id'], execution_id=row['execution_id'], reasons=row['issues'])
                        for row in rows if row['issues']],
                all_runs=rows)


def score_observations(entries, records, *, started=None):
    """Score every STARTED execution, including interrupted/missing final rows.

    ``entries`` is the frozen protocol matrix. ``started`` is the independently
    verified STARTED ledger with id + execution_id (native run/input may be
    absent before solving). ``records`` contains normalized final/partial rows.
    Supplying no STARTED ledger is rejected, never inferred from successes.
    """
    if started is None:
        raise ValueError('An independently verified STARTED ledger is required')
    entries, records = list(entries), list(records)
    if any(not isinstance(entry, dict) for entry in entries) or any(not isinstance(record, dict) for record in records):
        raise ValueError('Protocol entries and observation records must be objects')
    plan = {}
    for entry in entries:
        if not isinstance(entry.get('id'), str) or entry['id'] in plan:
            raise ValueError('Protocol entries need unique string IDs')
        if entry.get('stage') not in STAGES or entry.get('arm') not in ARMS:
            raise ValueError('Unknown protocol stage or arm')
        plan[entry['id']] = entry
    starts = list(started)
    if any(not isinstance(start, dict) for start in starts):
        raise ValueError('Every STARTED entry must be an object')
    executions = set()
    for start in starts:
        if not isinstance(start.get('id'), str) or not isinstance(start.get('execution_id'), str) or not start['execution_id']:
            raise ValueError('Every STARTED entry needs task and execution identity')
        if start['execution_id'] in executions:
            raise ValueError('Duplicate STARTED execution identity leaves the denominator unknown')
        executions.add(start['execution_id'])
    by_execution = defaultdict(list)
    unmatched = []
    for record in records:
        if record.get('execution_id') not in executions:
            unmatched.append(record)
        else:
            by_execution[record['execution_id']].append(record)
    task_counts = Counter(start['id'] for start in starts)
    normalized = [normalize(start, by_execution[start['execution_id']], plan.get(start['id']),
                            task_counts[start['id']] > 1) for start in starts]
    groups = {}
    for stage in STAGES:
        for arm in ARMS:
            selected = [row for row in normalized if row['stage'] == stage and row['arm'] == arm]
            expected = sum(entry['stage'] == stage and entry['arm'] == arm for entry in entries)
            groups[stage + '/' + arm] = group_score(selected, expected, stage, arm)
    unassigned = [row for row in normalized if row['stage'] == 'unassigned']
    comparisons = {}
    for stage in STAGES:
        baseline, candidate = (groups[stage + '/' + arm] for arm in ARMS)
        b, c = baseline['complete_task_success_rate'], candidate['complete_task_success_rate']
        complete = not unmatched and not unassigned and all(group['counts']['N'] == group['planned'] and not group['issues']
                       and group['counts'].get('quality_unknown', 0) == 0 for group in (baseline, candidate))
        e = baseline['counts']['E'] + candidate['counts']['E']
        opportunity = 'not_assessable' if not e else 'descriptive_pilot_only'
        comparisons[stage] = dict(candidate_minus_baseline_percentage_points=(c - b) * 100 if b is not None and c is not None else None,
                                  complete_predeclared_comparison=complete,
                                  recovery_benefit_status=opportunity,
                                  recovery_benefit_reason='no_confirmed_recoverable_opportunity' if not e else 'no_counterfactual_or_statistical_inference',
                                  natural_recovery_benefit_not_assessable=stage == 'natural' and not e,
                                  natural_evidence=stage == 'natural',
                                  injected_evidence_cannot_establish_natural_benefit=stage == 'injected')
    strata = {}
    for stage in STAGES:
        for arm in ARMS:
            task_ids = sorted({row['spec_id'] for row in normalized if row['stage'] == stage and row['arm'] == arm and row['spec_id']})
            for task_id in task_ids:
                subset = [row for row in normalized if row['stage'] == stage and row['arm'] == arm and row['spec_id'] == task_id]
                strata[stage + '/' + arm + '/' + task_id] = dict(N=len(subset), S=sum(row['quality_met'] is True for row in subset),
                    consumption=consumption_summary(subset, sum(row['quality_met'] is True for row in subset)))
    paired = []
    by_pair = defaultdict(dict)
    for row in normalized:
        entry = plan.get(row['id'], {})
        key = (row['stage'], row['spec_id'], entry.get('repeat'))
        if row['quality_met'] is True:
            by_pair[key][row['arm']] = row
    for (stage, task, repeat), arms in sorted(by_pair.items(), key=lambda item: repr(item[0])):
        if all(arm in arms for arm in ARMS):
            paired.append(dict(stage=stage, spec_id=task, repeat=repeat,
                               baseline={field: consumption_item(arms['baseline'], field)[0] for field in CONSUMPTION_FIELDS},
                               candidate={field: consumption_item(arms['candidate'], field)[0] for field in CONSUMPTION_FIELDS}))
    return dict(schema='simclinic-recovery-observation-score-v1', planned=len(entries), started=len(starts),
                not_started=[entry['id'] for entry in entries if not task_counts[entry['id']]],
                groups=groups, comparisons=comparisons, task_strata=strata,
                all_started_consumption=consumption_summary(normalized, sum(row['quality_met'] is True for row in normalized)),
                unassigned_started=unassigned,
                unmatched_records=unmatched,
                archive_record_set_consistent=not unmatched and not unassigned and all(not row['issues'] for row in normalized),
                both_successful_pairs=dict(rows=paired, conditional_secondary_only=True,
                                           limitation='Conditioning on both successes introduces selection bias; this is not stable saving evidence.'),
                consumption_semantics=dict(workflow_elapsed_seconds='Entire coordinator task wall including setup, child work, archival and parent verification; batch overhead is reported separately.',
                                           agent_workflow_elapsed_seconds='Inner Agent/recorder wall; included in workflow total, never add it again.',
                                           sample_elapsed_seconds='Sampling function wall including its verification, subprocess, cleanup and evidence handling; not process CPU time.',
                                           recovery_branch_overhead_seconds='Recovery branch wall minus extra sampling step wall; includes copy, verification, observer and bookkeeping, not pure copying time.',
                                           unknown_totals='Known amounts remain lower bounds; total and unit cost are null when any required amount is unknown.'),
                natural_and_injected_pooled=False, statistical_significance_claimed=False,
                stable_savings_or_overall_optimization_proven=False)


# Short alias for callers that treat all offline scorers alike.
score = score_observations
