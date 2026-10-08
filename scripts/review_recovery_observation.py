"""Read-only independent review of a sealed observation batch, using stdlib.

Load the readers from the execution freeze, never the changing checkout.  No
model, solver, sampling, render, or current-environment probe is performed.
Unsealed STARTED executions are salvaged as lower bounds, never as successes.
"""
import argparse
from collections import Counter
import hashlib
import importlib
import json
import math
from pathlib import Path
import platform
import struct
import sys


# This is a reproduction-equivalence rule, never an acceptance tolerance.
# Only the explicitly named derived fields may use it; all decisions remain
# exact, including each criterion's True/False/None result.  The 76-run offline
# inventory found a maximum of four steps from the 3.12 summation change;
# this bound is specific to the documented archived Couette workflow.
MAX_DERIVED_ULPS = 4
MEASUREMENT_FLOAT_PATHS = (('profile_error',), ('pressure_gradient_error',))
INLET_FLOAT_PATHS = (('west_face_y_centres', '*'), ('expected_inlet_x_velocity', '*'))


def ulp_distance(a, b):
    """Binary64 steps for finite, same-sign, nonzero normal Python floats."""
    if (type(a) is not float or type(b) is not float
            or not all(math.isfinite(v) and abs(v) >= sys.float_info.min for v in (a, b))
            or math.copysign(1, a) != math.copysign(1, b)
            or (sys.float_info.radix, sys.float_info.mant_dig, sys.float_info.max_exp) != (2, 53, 1024)):
        raise ValueError('ULP comparison requires same-sign nonzero normal binary64 floats')
    return abs(struct.unpack('>Q', struct.pack('>d', a))[0]
               - struct.unpack('>Q', struct.pack('>d', b))[0])


def require_derived_match(actual, archived, *, allowed_float_paths=(), label, differences):
    """Type/shape exact JSON tree, with a narrow audited derived-float rule.

    Wildcards match list indices only. There is no relative/absolute epsilon,
    zero floor, coercion, rounding or replacement of the archived value.
    """
    def walk(a, b, path):
        location = label + ''.join('/' + str(p) for p in path)
        if type(a) is not type(b):
            raise ValueError('Recomputed type differs: ' + location)
        if type(a) is dict:
            if a.keys() != b.keys():
                raise ValueError('Recomputed keys differ: ' + location)
            for key in a:
                walk(a[key], b[key], path + (key,))
        elif type(a) is list:
            if len(a) != len(b):
                raise ValueError('Recomputed list length differs: ' + location)
            for i, (av, bv) in enumerate(zip(a, b)):
                walk(av, bv, path + (i,))
        elif type(a) is float:
            if not math.isfinite(a) or not math.isfinite(b):
                raise ValueError('Nonfinite recomputed value: ' + location)
            if a.hex() == b.hex():
                return
            permitted = any(len(pattern) == len(path) and all(
                (type(found) is int if wanted == '*' else wanted == found)
                for wanted, found in zip(pattern, path)) for pattern in allowed_float_paths)
            if not permitted:
                raise ValueError('Exact float differs outside derived whitelist: ' + location)
            distance = ulp_distance(a, b)
            entry = dict(path=location, recomputed=a, archived=b, recomputed_hex=a.hex(),
                         archived_hex=b.hex(), absolute_difference=abs(a-b), ulp_distance=distance,
                         allowed_ulps=MAX_DERIVED_ULPS, within_limit=distance <= MAX_DERIVED_ULPS)
            differences.append(entry)
            if distance > MAX_DERIVED_ULPS:
                raise ValueError('Derived float exceeds ULP limit: ' + location)
        elif a != b:
            raise ValueError('Exact recomputed value differs: ' + location)
    walk(actual, archived, ())


def require_exact(actual, archived, label):
    require_derived_match(actual, archived, label=label, differences=[])


def require_same_decisions(observed, archived, criteria, checks):
    """A small float difference cannot cross even one frozen threshold."""
    decisions = {}
    for criterion in criteria:
        key = criterion['id']
        actual = checks.matches(observed.get(key), criterion)
        original = checks.matches(archived.get(key), criterion)
        if actual is not original:
            raise ValueError('Recomputed criterion decision differs: ' + key)
        decisions[key] = actual
    return decisions


def read(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(directory, relative):
    directory = Path(directory).resolve()
    rel = Path(relative)
    path = directory / rel
    if rel.is_absolute() or '..' in rel.parts or directory not in path.resolve().parents:
        raise ValueError('Evidence path leaves run archive')
    for item in (path,) + tuple(path.parents):
        if item == directory:
            break
        if item.is_symlink():
            raise ValueError('Symlink evidence')
    return path


def fresh_output(path, batch, freeze):
    path = Path(path).absolute()
    for parent in (path,) + tuple(path.parents):
        if parent.is_symlink() or (parent / 'freeze.json').exists() or (parent / 'freeze.sha256').exists():
            raise ValueError('Review output cannot enter a frozen or symlinked tree')
    if path.exists():
        raise ValueError('Review output must be fresh')
    for tree in (Path(batch).resolve(), Path(freeze).resolve()):
        if path.resolve() == tree or tree in path.resolve().parents:
            raise ValueError('Independent report must be outside batch and freeze')
    return path


def frozen_readers(freeze):
    runtime = Path(freeze).resolve() / 'common-runtime'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(runtime))
    names = ('simclinic.evaluation.recovery_execution_freeze',
             'simclinic.evaluation.recovery_observation_driver',
             'simclinic.evaluation.recovery_observation_score',
             'simclinic.domains.openfoam.acceptance',
             'simclinic.domains.openfoam.reference_tasks',
             'simclinic.domains.openfoam.input_contract', 'simclinic.domains.openfoam.logs',
             'simclinic.checks.behavior')
    modules = [importlib.import_module(name) for name in names]
    for module in modules:
        if runtime not in Path(module.__file__).resolve().parents:
            raise ValueError('Reader already loaded from outside freeze; use a fresh Python process')
    return dict(zip(('freeze', 'driver', 'score', 'acceptance', 'reference', 'inputs', 'logs', 'checks'), modules))


def issue(issues, code, detail=None, severity='error'):
    issues.append(dict(code=code, detail=detail, severity=severity))


def artifacts(directory, record):
    """Salvage all registered items individually, including failed collections."""
    by_id, inventory, issues = {}, [], []
    for item in record.get('artifacts', []):
        result = dict(id=item.get('id'), path=item.get('path'), expected_sha256=item.get('sha256'))
        try:
            if item['id'] in by_id:
                raise ValueError('Duplicate artifact id')
            path = safe(directory, item['path'])
            result['actual_sha256'] = sha(path)
            if result['actual_sha256'] != item['sha256']:
                raise ValueError('Registered bytes changed')
            result['verified'] = True
            by_id[item['id']] = dict(item, file=path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(verified=False, error=str(exc))
            issue(issues, 'registered_artifact_unverified', result)
        inventory.append(result)
    return by_id, inventory, issues


def payload(items, identifier, context=None):
    item = items[identifier]
    if context is not None and any(item.get(k) != context.get(k) for k in ('run_id', 'input_version')):
        raise ValueError('Registered artifact native identity differs')
    return read(item['file'])


def raw_models(directory):
    rows, known, unknown = [], 0, 0
    for folder in sorted((Path(directory) / 'model-calls').glob('*')):
        if not folder.is_dir():
            continue
        result = dict(path=str(folder.relative_to(directory)), total_tokens=None, verified_metadata=False)
        try:
            meta = safe(directory, str((folder / 'metadata.json').relative_to(directory)))
            data = read(meta)
            result.update(metadata_sha256=sha(meta), outcome=data.get('outcome'), total_tokens=data.get('total_tokens'),
                          verified_metadata=True, raw_metadata=data)
            value = data.get('total_tokens')
            if type(value) is not int or value < 0:
                unknown += 1
                result['total_tokens'] = None
            else:
                known += value
        except (OSError, ValueError, TypeError) as exc:
            unknown += 1
            result['error'] = str(exc)
        rows.append(result)
    return dict(calls=rows, observed_request_directories=len(rows), known_model_calls=sum(r['verified_metadata'] for r in rows),
                known_model_tokens_lower_bound=known, unknown_usage_calls=unknown,
                model_tokens=known if not unknown else None,
                exhaustive_total_proven=False, note='A missing FINAL never proves all requests were collected')


def raw_process_metadata(directory, registered, *, include_case=False):
    """Inventory even uncaptured child metadata without binding its identity.

    Original case metadata is allowed only as labelled raw salvage. It cannot
    fill any final run/input identity or turn an unsealed task into a success.
    Copies are listed individually and never summed as distinct subprocesses.
    """
    root = Path(directory)
    known = {item['path']: item['id'] for item in registered.values()}
    results = []
    names = {'metadata.json', 'started.json', 'execution.json', 'launch.json', 'call.json', 'gate-assessment.json'}
    for candidate in sorted(root.rglob('*.json')):
        relative = candidate.relative_to(root)
        if candidate.name not in names or 'source-package' in relative.parts or 'model-calls' in relative.parts:
            continue
        if relative.parts[0] == 'case' and not include_case:
            continue
        item = dict(path=str(relative), registered_artifact=known.get(str(relative)),
                    mutable_case_raw_salvage=bool(relative.parts and relative.parts[0] == 'case'),
                    native_identity_used_to_fill_final_record=False, counted_as_distinct_process=False)
        try:
            path = safe(root, relative)
            item.update(sha256=sha(path), raw_metadata=read(path), readable=True)
        except (OSError, ValueError, TypeError) as exc:
            item.update(readable=False, error=str(exc))
        results.append(item)
    return results


def native_source(items, trusted, context, native):
    """Use archived registered copies; never reopen source_case/workspace paths."""
    if not isinstance(trusted, dict):
        raise ValueError('No before-recovery trusted-source evidence')
    marker, source = trusted['workspace_marker'], trusted['source_manifest']
    if any(marker.get(k) != context.get(k) or source.get(k) != context.get(k) for k in ('run_id', 'input_version')):
        raise ValueError('Before-recovery source identity differs')
    entries = {a['path']: a for a in source['artifacts'] if a.get('role') != 'visualization'}
    old = {a['path']: a for a in native['artifacts'] if a.get('role') != 'visualization'}
    if {p: a['sha256'] for p, a in entries.items()} != {p: a['sha256'] for p, a in old.items()}:
        raise ValueError('Before-recovery source differs from archived native outputs')
    expected = {p: a['sha256'] for p, a in entries.items()}
    if marker.get('input_files') != expected or trusted.get('verified_source_hashes') != expected:
        raise ValueError('Before-recovery workspace exact source hash set differs')
    for path, digest in expected.items():
        rel = 'attempts/' + context['run_id'] + '/outputs/' + path
        found = [a for a in items.values() if a['path'] == rel]
        if len(found) != 1 or found[0]['sha256'] != digest:
            raise ValueError('Source evidence lacks a unique registered archived copy: ' + path)
    return dict(verified=True, source_artifact_count=len(expected), mutable_workspace_reopened=False)


def sample_review(row, record, items, readers, native):
    first, recovery, injection = row.get('first_sample', {}), row.get('recovery', {}), row.get('injection', {})
    events = record.get('events', [])
    positions = {e['id']: i for i, e in enumerate(events)}
    samples = [e for e in events if e.get('operation') == 'sample']
    decisions = [e for e in events if e.get('decision_type') == 'bounded_postprocess_recovery']
    context = {k: row.get(k) for k in ('run_id', 'input_version')}
    result = dict(first_function_called=first.get('function_called'), registered_sample_count=len(samples),
                  bounded_decision_count=len(decisions), errors=[], mutable_workspace_reopened=False,
                  numeric_differences=[])
    errors = result['errors']
    if row['arm'] == 'baseline' and (len(samples) > 1 or decisions or recovery.get('branch_entered') is True):
        errors.append('baseline_retried')
    if len(samples) > (2 if row['arm'] == 'candidate' else 1) or len(decisions) > 1:
        errors.append('sampling_or_branch_limit_exceeded')
    if row['stage'] != 'injected' and (injection.get('requested') is True or injection.get('status') == 'applied'
                                     or any(e.get('observation_type') == 'explicit_sampling_intervention' for e in events)):
        errors.append('injection_outside_injected_phase')
    if first.get('function_called') is not True:
        if samples or decisions:
            errors.append('sample_events_without_function_entry')
        result['status'] = 'pass' if not errors else 'fail'
        result['eligibility'] = 'not_reached' if first.get('function_called') is False else 'unknown'
        return result
    try:
        call = payload(items, first['call_artifact'], context)
        launch = payload(items, first['launch_artifact'], context)
        gate = payload(items, first['gate_artifact'], context)
        eligible = payload(items, first['eligibility_artifact'], context)
        if eligible != {k: v for k, v in first.items() if k not in
                        ('eligibility_artifact', 'eligibility_event_id', 'sample_function_elapsed_seconds')}:
            raise ValueError('First eligibility summary differs from registered assessment')
        if call.get('execution_id') != row['execution_id'] or call.get('sample_number') != 1:
            raise ValueError('First function entry execution identity/number differs')
        if not samples or samples[0]['id'] != first['sample_event_id'] or samples[0]['source_artifact'] != first['gate_artifact']:
            raise ValueError('First gate is not first registered sample event')
        order = [positions[first[k]] for k in ('call_event_id', 'sample_event_id', 'eligibility_event_id')]
        if order != sorted(order) or len(set(order)) != 3:
            raise ValueError('First entry/gate/eligibility event order differs')
        if decisions and order[-1] >= positions[decisions[0]['id']]:
            raise ValueError('Eligibility was not archived before recovery')
        if launch.get('launch_status') != first.get('launch_status') or launch.get('launch_attempted') != first.get('launch_attempted'):
            raise ValueError('Actual launch evidence differs from first summary')
        if launch.get('launch_status') == 'started' and (type(launch.get('pid')) is not int or launch['pid'] <= 0):
            raise ValueError('Sampler launch lacks a positive Popen PID')
        log_items = [items[gate[k]] for k in ('stdout_artifact', 'stderr_artifact')]
        if any(any(item.get(k) != context[k] for k in context) for item in log_items):
            raise ValueError('First sampler log artifact identity differs')
        try:
            logs = [item['file'].read_bytes().decode('utf-8') for item in log_items]
            nonfatal = not any(readers['logs'].parse_solver_log(log)['fatal_errors'] for log in logs)
        except UnicodeError:
            nonfatal = None
        confirmed = (gate.get('error_category') == 'sampling_missing_output' and gate.get('outcome') == 'fail'
                     and samples[0].get('outcome') == 'fail' and type(gate.get('exit_code')) is int
                     and gate['exit_code'] == 0 and launch.get('observed_returncode') == 0
                     and launch.get('launch_status') == 'started' and nonfatal is True)
        if first.get('missing_output_confirmed') is True and not confirmed:
            raise ValueError('Missing-output flag lacks zero-exit nonfatal actual first-launch evidence')
        result.update(first_launch_verified=True, first_gate_code=gate.get('error_category'),
                      missing_output_confirmed_from_raw=confirmed, before_recovery_verified=True)
        if first.get('eligible') is True:
            if not confirmed or first.get('eligibility_before_recovery') is not True:
                raise ValueError('Eligibility lacks confirmed missing output before recovery')
            result['trusted_source'] = native_source(items, first.get('trusted_source'), context, native)
        if decisions:
            if row['arm'] != 'candidate' or first.get('eligible') is not True or not confirmed:
                raise ValueError('Recovery decision without candidate eligible missing output')
            decision = decisions[0]
            if decision.get('retry_limit') != 1 or decision.get('handles') != [samples[0]['id']]:
                raise ValueError('Recovery decision limit or first-failure lineage differs')
            if any(e.get('phase') == 'solve' for e in events[positions[decision['id']] + 1:]):
                raise ValueError('Solver repeated after bounded recovery decision')
            if len(samples) == 2:
                if samples[1].get('retry_of') != samples[0]['id'] or samples[1].get('input_version') != context['input_version']:
                    raise ValueError('Second sample failure lineage/native input differs')
                second_call = next(a for a in items.values() if a['path'].endswith('/sample-2/call.json'))
                second_launch = next(a for a in items.values() if a['path'].endswith('/sample-2/launch.json'))
                call2, launch2 = payload(items, second_call['id'], context), payload(items, second_launch['id'], context)
                if call2.get('workspace') == call.get('workspace') or call2.get('sample_number') != 2:
                    raise ValueError('Recovery did not use a distinct fresh workspace')
                if launch2.get('launch_status') == 'started' and launch2.get('postprocessing_present_before_launch') is not False:
                    raise ValueError('Recovery started with old sampling outputs')
                result['fresh_second_workspace_verified'] = True
        if recovery.get('branch_entered') is True:
            timing = payload(items, recovery['timing_artifact'], context)
            if timing != {k: v for k, v in recovery.items() if k != 'timing_artifact'}:
                raise ValueError('Recovery timing differs from registered evidence')
            if timing.get('attempt_count', 99) > 1:
                raise ValueError('Recovery exceeded one extra sample attempt')
            result['recovery_branch_cost_verified'] = True
        if row['stage'] == 'injected' and injection.get('status') == 'applied':
            outcome = payload(items, injection['artifact_id'], context)
            if outcome != {k: v for k, v in injection.items() if k != 'artifact_id'}:
                raise ValueError('Intervention summary differs from registered outcome')
            plan = payload(items, outcome['plan_artifact'], context)
            qualified = payload(items, plan['qualification_artifact'], context)
            if (outcome.get('profile_removed') is not True or outcome.get('original_reader_bytes_unchanged') is not True
                    or plan.get('physics_numerics_acceptance_changed') is not False or plan.get('solver_restarted') is not False
                    or qualified.get('original_full_gate_conditions_verified') is not True
                    or qualified.get('actual_exit_code') != 0 or qualified.get('pid') != launch.get('pid')
                    or qualified.get('actual_argv') != launch.get('argv') or qualified.get('nonfatal_utf8_logs_verified') is not True
                    or qualified.get('measurement_reader_source') != 'exact_registered_output_bytes'
                    or plan.get('outputs') != qualified.get('output_evidence') or not confirmed):
                raise ValueError('Intervention lacks original full-gate qualification/deletion evidence')
            outputs = qualified['output_evidence']
            if len(outputs) != 2 or {Path(o['relative_path']).name for o in outputs} != {'profile.xy', 'pressure.xy'}:
                raise ValueError('Intervention did not archive exactly both original outputs')
            paths = {}
            for output in outputs:
                item = items[output['artifact_id']]
                if item['sha256'] != output['sha256'] or any(item.get(k) != context.get(k) for k in context):
                    raise ValueError('Qualified intervention output bytes/identity differ')
                paths[Path(output['relative_path']).name] = item['file']
            # The caller supplies the frozen spec in readers, not mutable case state.
            measured = readers['reference'].reference_measurements(paths['profile.xy'], paths['pressure.xy'], readers['spec'])
            require_derived_match(measured, qualified.get('measurements'),
                allowed_float_paths=MEASUREMENT_FLOAT_PATHS, label='injection_original_measurements',
                differences=result['numeric_differences'])
            criteria = readers['criteria'] if 'criteria' in readers else readers['reference'].task(readers['spec'])['acceptance']
            result['injection_original_criterion_decisions'] = require_same_decisions(
                measured, qualified['measurements'], [c for c in criteria if c['id'] in measured],
                readers.get('checks', readers['reference']))
            result['injection_original_measurements'] = measured
            result['injection_original_full_gate_verified'] = True
            result['injection_source'] = native_source(items, qualified, context, native)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, UnicodeError) as exc:
        errors.append(str(exc))
    result['status'] = 'pass' if not errors else 'fail'
    return result


def acceptance_review(directory, row, record, items, readers, definition):
    """Recompute from registered archived fields/lines and frozen task, no case."""
    result = dict(status='unknown', values={}, full_quality=None, mutable_case_reopened=False,
                  numeric_differences=[])
    candidates = [a for a in items.values() if a['path'] == 'observations/task-assessment.json']
    if not candidates:
        result['reason'] = 'task_assessment_not_reached'
        if row.get('quality_met') is True:
            result.update(status='fail', reason='success_without_archived_task_assessment')
        return result
    try:
        assessment = payload(items, candidates[0]['id'], row)
        require_exact(assessment.get('criteria'), definition['task']['acceptance'], 'acceptance_criteria')
        values = assessment['values']
        attempts = record.get('attempts', [])
        attempt = attempts[-1] if attempts else None
        observed = {c['id']: None for c in definition['task']['acceptance']}
        observed.update(task_inputs_verified=False, rendered_velocity=False)
        if attempt:
            context = {k: attempt[k] for k in ('run_id', 'input_version')}
            if any(context[k] != row.get(k) for k in context):
                raise ValueError('Final attempt identity differs from final row')
            native = read(safe(directory, 'attempts/' + context['run_id'] + '/native/record.json'))
            log_path = 'attempts/' + context['run_id'] + '/logs/log.simpleFoam'
            if log_path in row.get('files', {}):
                log_file = safe(directory, log_path)
                if sha(log_file) != row['files'][log_path]:
                    raise ValueError('Sealed original solver log changed')
                observed.update(readers['acceptance'].solver_measurements(log_file.read_text(), attempt['execution']['exit_code']))
                result['solver_log'] = dict(path=log_path, sha256=sha(log_file), seal='FINAL row file inventory')
            checks, detail = readers['inputs'].compare_case(safe(directory, attempt['input_snapshot']), definition['reference'], definition['spec'])
            require_exact(checks, assessment.get('input_checks'), 'nine_file_input_decisions')
            observed['task_inputs_verified'] = bool(checks) and all(checks.values())
            if checks and not any(v is False for v in checks.values()) and any(v is None for v in checks.values()):
                observed['task_inputs_verified'] = None
            result['input_comparison'] = detail
            stability = dict(field_stationary=None, velocity_change_scaled=None, pressure_change_scaled=None, compared_times=[])
            times = native.get('output_times', [])
            if (any(type(t) is not str or not math.isfinite(float(t)) or float(t) <= 0 for t in times)
                    or times != sorted(set(times), key=float)):
                raise ValueError('Archived positive-time field ordering is invalid')
            if len(times) >= 2:
                scales = definition['task']['reference_contract']
                changed = {}
                for field, components, scale in (('U', 3, scales['velocity_scale']), ('p', 1, scales['kinematic_pressure_scale'])):
                    snapshots = []
                    for t in times[-2:]:
                        name = 'attempts/' + context['run_id'] + '/outputs/' + t + '/' + field
                        artifact = next(a for a in items.values() if a['path'] == name)
                        if any(artifact.get(k) != context[k] for k in context):
                            raise ValueError('Field stationarity artifact identity differs')
                        snapshots.append(readers['reference'].field_values(artifact['file'].read_bytes(), definition['spec']['nx'] * definition['spec']['ny'], components))
                    changed[field] = max(abs(a-b) for a, b in zip(*snapshots)) / scale
                stability = dict(field_stationary=all(v <= scales['field_change_tolerance'] for v in changed.values()),
                                 velocity_change_scaled=changed['U'], pressure_change_scaled=changed['p'], compared_times=times[-2:])
            stable_items = [a for a in items.values() if a['path'] == 'observations/field-stationarity.json']
            if stable_items:
                archived_stability = payload(items, stable_items[0]['id'], context)
                require_exact(stability, archived_stability, 'stationarity')
                result['stationarity_criterion_decisions'] = require_same_decisions(
                    stability, archived_stability,
                    [dict(id=k, operator='le', target=definition['task']['reference_contract']['field_change_tolerance'])
                     for k in ('velocity_change_scaled', 'pressure_change_scaled')], readers['checks'])
            inlet_items = [a for a in items.values() if a['path'] == 'observations/inlet-face-order.json']
            if inlet_items:
                # This is the immutable registered output archive, never the
                # case/source_case path or a later mutable workspace.
                inlet = readers['reference'].couette_inlet_order(safe(directory, 'attempts/' + context['run_id'] + '/outputs'), definition['spec'])
                require_derived_match(inlet, payload(items, inlet_items[0]['id'], context),
                    allowed_float_paths=INLET_FLOAT_PATHS, label='inlet_face_order',
                    differences=result['numeric_differences'])
                result['inlet_face_order_verified'] = True
                result['inlet_face_order'] = inlet
            observed['steady_evidence'] = bool(observed.get('steady_converged')) or bool(stable_items and stability['field_stationary'] is True)
            result['stationarity'] = stability
            visuals = []
            for artifact in items.values():
                if not artifact['path'].startswith('observations/node-'):
                    continue
                node = read(artifact['file'])
                if node.get('stage') == 'end' and node.get('node') == 'visualization':
                    visuals.append(node['payload'])
            if visuals:
                state = visuals[-1]
                images = state.get('plot_outputs', [])
                if state.get('pyvista_visualization', {}).get('success') and state.get('visualization_summary', {}).get('fields_visualized') == ['U'] and len(images) == 1:
                    relative = str(Path(images[0]).relative_to(Path(state['case_dir'])))
                    expected = 'attempts/' + context['run_id'] + '/outputs/' + relative
                    png = [a for a in items.values() if a['path'] == expected and all(a.get(k) == context[k] for k in context)]
                    observed['rendered_velocity'] = len(png) == 1 and png[0]['file'].read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            samples = [e for e in record['events'] if e.get('operation') == 'sample']
            if samples and samples[-1].get('outcome') == 'pass':
                gate = payload(items, samples[-1]['source_artifact'], context)
                output_items = {Path(o['path']).name: items[o['artifact_id']] for o in gate['outputs']}
                measured = readers['reference'].reference_measurements(output_items['profile.xy']['file'], output_items['pressure.xy']['file'], definition['spec'])
                require_derived_match(measured, gate.get('measurements'),
                    allowed_float_paths=MEASUREMENT_FLOAT_PATHS, label='final_sample_measurements',
                    differences=result['numeric_differences'])
                require_same_decisions(measured, gate['measurements'],
                    [c for c in definition['task']['acceptance'] if c['id'] in measured], readers['checks'])
                observed.update(measured)
            elif values.get('profile_error') is not None or values.get('pressure_gradient_error') is not None:
                raise ValueError('Failed sampling published successful reference measurements')
        for criterion in definition['task']['acceptance']:
            key = criterion['id']
            # Early rejected/native failures can skip later checks. They cannot
            # claim success; do not manufacture a positive value for that path.
            if values.get(key) is not None:
                require_derived_match({key: observed.get(key)}, {key: values[key]},
                    allowed_float_paths=MEASUREMENT_FLOAT_PATHS, label='acceptance_values',
                    differences=result['numeric_differences'])
        result['criterion_decisions'] = require_same_decisions(
            observed, values, definition['task']['acceptance'], readers['checks'])
        claims = [e for e in record['events'] if e.get('kind') == 'claim' and e.get('claim_type') == 'completion']
        accepted = assessment.get('error') is None and all(readers['checks'].matches(observed.get(c['id']), c) is True for c in definition['task']['acceptance'])
        quality = accepted and bool(claims and claims[-1].get('success') is True) and record.get('collection_status') == 'complete'
        if quality != row.get('quality_met') or accepted != row.get('acceptance_verified'):
            raise ValueError('Raw acceptance/full-quality result differs from sealed row')
        result.update(status='pass', values=observed, accepted=accepted, full_quality=quality)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, UnicodeError) as exc:
        result.update(status='fail', reason=str(exc))
    return result


def review_run(directory, row, entry, readers, bundle, sealed):
    result = dict(id=entry['id'], execution_id=row.get('execution_id'), sealed_final=sealed, issues=[])
    result['raw_models'] = raw_models(directory)
    try:
        if not sealed and not (Path(directory) / 'record.json').is_file():
            result.update(status='incomplete', registered_artifacts=[], registered_artifact_count=0,
                          raw_process_metadata=raw_process_metadata(directory, {}, include_case=True),
                          success_inferred=False, reason='STARTED without collected record or durable FINAL')
            return result
        record = read(safe(directory, 'record.json'))
        items, inventory, problems = artifacts(directory, record)
        result.update(registered_artifacts=inventory, registered_artifact_count=len(inventory))
        result['raw_process_metadata'] = raw_process_metadata(directory, items, include_case=not sealed)
        result['issues'].extend(problems)
        if record.get('environment', {}).get('execution_id') != row.get('execution_id'):
            raise ValueError('Raw record execution identity differs from STARTED')
        events = record.get('events', [])
        solvers = [e for e in events if e.get('phase') == 'solve' and e.get('kind') == 'tool']
        samples = [e for e in events if e.get('operation') == 'sample']
        for event in solvers + samples:
            source = payload(items, event['source_artifact'], event)
            if source.get('exit_code') != event.get('exit_code') or source.get('outcome') != event.get('outcome'):
                raise ValueError('Raw solver/sample metadata differs from event')
        result['tool_counts'] = dict(registered_solver_calls=len(solvers), registered_sample_calls=len(samples),
                                    solver_seconds=sum(e['elapsed_seconds'] for e in solvers if type(e.get('elapsed_seconds')) in (int, float)),
                                    sample_seconds=sum(e['elapsed_seconds'] for e in samples if type(e.get('elapsed_seconds')) in (int, float)))
        result['native_attempt_count'] = len(record.get('attempts', []))
        if not sealed:
            result.update(status='incomplete', success_inferred=False, final_consumption_total=None,
                          reason='STARTED without durable FINAL; raw evidence only supplies known lower bounds')
            return result
        costs, raw = row['consumption'], result['raw_models']
        if costs.get('model_calls') != raw['observed_request_directories'] or costs.get('model_tokens') != raw['model_tokens']:
            raise ValueError('Sealed model costs differ from all raw request directories')
        for name, events_of_kind in (('solver', solvers), ('sample', samples)):
            if costs.get(name + '_calls') != len(events_of_kind):
                raise ValueError('Sealed ' + name + ' call count differs')
            elapsed = [e.get('elapsed_seconds') for e in events_of_kind]
            total = sum(elapsed) if all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in elapsed) else None
            if costs.get(name + '_elapsed_seconds') != total:
                raise ValueError('Sealed ' + name + ' elapsed cost differs')
        if costs.get('evidence_count') != len(inventory):
            raise ValueError('Sealed evidence count differs')
        definition_dir = bundle / 'definitions' / entry['spec_id']
        definition = dict(spec=read(definition_dir / 'spec.json'), task=read(definition_dir / 'task.json'), reference=definition_dir / 'reference')
        local_readers = dict(readers, spec=definition['spec'], criteria=definition['task']['acceptance'])
        attempts = record.get('attempts', [])
        native = read(safe(directory, 'attempts/' + attempts[-1]['run_id'] + '/native/record.json')) if attempts else {}
        result['sampling_policy'] = sample_review(row, record, items, local_readers, native)
        result['acceptance'] = acceptance_review(directory, row, record, items, local_readers, definition)
        for name in ('sampling_policy', 'acceptance'):
            if result[name]['status'] == 'fail':
                issue(result['issues'], name + '_review_failed', result[name])
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        issue(result['issues'], 'raw_run_review_failed', str(exc))
    result['status'] = 'fail' if result['issues'] else 'pass' if sealed else 'incomplete'
    return result


def review(batch, freeze):
    batch, freeze = Path(batch).resolve(), Path(freeze).resolve()
    readers = frozen_readers(freeze)
    result = dict(schema_version='1.1', reviewer='independent-archived-observation-v2',
                  review_script_sha256=sha(Path(__file__)), batch=str(batch), execution_freeze=str(freeze),
                  new_model_calls=0, new_simulations=0, new_sampling_calls=0, primary_score_changed=False,
                  mutable_case_reopened=False, issues=[], runs=[])
    result['python_runtime'] = dict(implementation=platform.python_implementation(), version=platform.python_version(),
                                  executable=sys.executable, platform=platform.platform(),
                                  binary64=(sys.float_info.radix, sys.float_info.mant_dig, sys.float_info.max_exp) == (2, 53, 1024))
    result['numeric_comparison_rule'] = dict(id='derived-binary64-ulp-v1', maximum_ulps=MAX_DERIVED_ULPS,
        float_paths=dict(measurements=MEASUREMENT_FLOAT_PATHS, inlet=INLET_FLOAT_PATHS),
        exact_structure_types_and_nonwhitelisted_values=True, exact_each_criterion_decision=True,
        exact_original_score=True, original_thresholds_changed=False)
    watched = {name: sha(batch / name) for name in ('ledger.jsonl', 'progress.json', 'results.json') if (batch / name).is_file()}
    result['primary_files_sha256'] = watched
    try:
        result['freeze_integrity'] = readers['freeze'].verify_freeze(freeze)
        freeze_sha = sha(freeze / 'freeze.json')
        config = read(freeze / 'protocol-design/protocol.json')
        ledger = readers['driver'].Ledger(batch / 'ledger.jsonl', config, freeze_sha)
        finals = {e['execution_id'] for e in ledger.events if e['kind'] == 'FINAL'}
        result['all_started'] = len(ledger.starts())
        verified = readers['driver'].verify_batch(batch, config, freeze_sha, bundle=freeze)
        scored = readers['score'].score_observations(config['entries'], verified['rows'], started=verified['started'])
        original = read(batch / 'results.json') if (batch / 'results.json').is_file() else None
        aligned = False
        if original is not None:
            try:
                require_exact(original.get('scoring'), scored, 'original_score')
                aligned = True
            except ValueError as exc:
                result['score_comparison_error'] = str(exc)
        result['ledger_and_original_score'] = dict(status='pass' if aligned else 'fail', all_started=verified['all_started'],
            sealed_finals=len(finals), unsealed_started=len(verified['gaps']), original_scoring_matches=aligned,
            recomputed_scoring=scored, original_score_sha256=watched.get('results.json'),
            identity_and_input_snapshots_verified=True, budget_and_stopping_prefix_verified=True)
        if not aligned:
            issue(result['issues'], 'primary_scoring_missing_or_different')
        expected = Counter((e['entry']['stage'], e['entry']['arm']) for e in ledger.events if e['kind'] == 'STARTED')
        for key, group in scored['groups'].items():
            if group['counts']['N'] != expected[tuple(key.split('/'))]:
                issue(result['issues'], 'all_started_denominator_mismatch', key)
        natural_e = sum(scored['groups']['natural/' + arm]['counts']['E'] for arm in ('baseline', 'candidate'))
        natural = scored['comparisons']['natural']
        if not natural_e and (natural.get('recovery_benefit_status') != 'not_assessable' or natural.get('natural_recovery_benefit_not_assessable') is not True):
            issue(result['issues'], 'zero_natural_opportunity_misinterpreted')
        result['natural_E0_boundary_verified'] = bool(natural_e or natural.get('recovery_benefit_status') == 'not_assessable')
        result['batch_status'] = verified['status']
        required_stop = readers['driver'].stop_reason(verified['rows'][-1]) if verified['rows'] else None
        result['stopping'] = dict(last_started_required_stop=required_stop, actual_status=verified['status'],
                                 next_task_after_mandatory_stop=False,
                                 missing_FINAL_requires_stop=bool(verified['gaps']))
        if required_stop and verified['status'] in ('running', 'complete'):
            issue(result['issues'], 'mandatory_last_stop_ignored', required_stop)
        if verified['gaps'] and verified['status'] == 'complete':
            issue(result['issues'], 'unsealed_started_claimed_complete')
        plan = {e['id']: e for e in config['entries']}
        for row in verified['rows']:
            sealed = row['execution_id'] in finals
            reviewed = review_run(batch / 'runs' / row['id'], row, plan[row['id']], readers, freeze, sealed)
            result['runs'].append(reviewed)
            if reviewed['status'] == 'fail':
                issue(result['issues'], 'independent_run_failed', reviewed['id'])
        result['salvage'] = [r for r in result['runs'] if not r['sealed_final']]
        result['status'] = 'fail' if result['issues'] else 'incomplete' if verified['gaps'] or verified['status'] == 'running' else 'pass'
        result['complete_planned_comparison'] = verified['status'] == 'complete' and not verified['gaps']
        result['review_scope'] = 'Type-exact sealed scoring; bounded derived-float equivalence with exact frozen criterion decisions; partial STARTED costs are lower bounds only'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issue(result['issues'], 'freeze_or_batch_integrity_failed', str(exc))
        result['status'] = 'fail'
        # Even a broken primary seal must not erase collected raw costs.  These
        # rows are explicitly untrusted salvage, regardless of a FINAL claim.
        if 'ledger' in locals():
            already = {r['execution_id'] for r in result['runs']}
            for event in ledger.events:
                if event['kind'] != 'STARTED' or event['execution_id'] in already:
                    continue
                row = dict(event['entry'], execution_id=event['execution_id'])
                result['runs'].append(review_run(batch / 'runs' / row['id'], row, event['entry'], readers, freeze, False))
            result['salvage'] = [r for r in result['runs'] if not r['sealed_final']]
    for name, digest in watched.items():
        if sha(batch / name) != digest:
            issue(result['issues'], 'primary_changed_during_review', name)
            result['status'] = 'fail'
    result['unsealed_case_metadata_salvaged'] = any(
        item['mutable_case_raw_salvage'] for run in result['runs'] for item in run.get('raw_process_metadata', []))
    result['later_directory_state_used_to_repair_identity'] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    output = fresh_output(args.out, args.batch, args.freeze)
    result = review(args.batch, args.freeze)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(status=result['status'], all_started=result.get('all_started'), out=str(output)), ensure_ascii=False))
    return 1 if result['status'] == 'fail' else 0


if __name__ == '__main__':
    raise SystemExit(main())
