"""Shared, serial observation around the unchanged sampling and recovery gates.

No old source is changed. The observer sits inside BoundedSamplingRecovery's
first call, so first-failure eligibility is archived before its recovery catch.
Only the explicitly injected scenario can delete a newly qualified profile.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

from ..core.report import write_json
from ..domains.openfoam import sampling_gate
from ..domains.openfoam.input_contract import ContractReferenceCompletion
from ..domains.openfoam.logs import parse_solver_log
from ..domains.openfoam.sampling_gate import SamplingError
from ..domains.openfoam.sampling_recovery import BoundedSamplingRecovery
from ..foam_runtime import current_record, verify_workspace
from ..result_guard import ResultRejected, read_verified_result


SAMPLE_COMMAND = 'source /opt/openfoam10/etc/bashrc && simpleFoam -postProcess -func sample -latestTime'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _identity(context):
    return {key: context.get(key) for key in ('run_id', 'input_version')}


def _is_sampler(argv):
    return isinstance(argv, list) and argv[:2] == ['docker', 'run'] and argv[-1] == SAMPLE_COMMAND


class SharedSampleObserver:
    """The same first-call observer in both arms, before candidate recovery."""
    def initialize_observer(self, *, enabled, scenario, process_factory):
        if type(enabled) is not bool or scenario not in ('natural', 'transient_missing'):
            raise ValueError('Explicit bool and natural/transient_missing scenario required')
        self._observer_base = self.recorder
        self._recovery_enabled = enabled
        self._scenario = scenario
        self._process_factory = process_factory or subprocess.Popen
        self._observed_calls = 0
        self._launches = []
        self._branch_start = None
        self._summary = dict(schema_version='1.0', observer='bounded-recovery-shared-v1', scenario=scenario,
            candidate_enabled=enabled,
            first_sample=dict(function_called=False, launch_status='not_reached', launch_attempted=False,
                              missing_output_confirmed=False, eligible=False, reason_codes=['sample_not_reached'],
                              evidence_verified=True, eligibility_before_recovery=True),
            recovery=dict(launched=False, sample_pass=False, attempt_count=0, branch_entered=False, branch_elapsed_seconds=0,
                          extra_calls_elapsed_seconds=0, non_sampling_branch_elapsed_seconds=0, evidence_verified=True),
            injection=dict(requested=scenario == 'transient_missing', applied=False,
                           status='not_reached' if scenario == 'transient_missing' else 'disabled',
                           natural_failure=None, reason_codes=[]), observation_elapsed_seconds=0,
            policy_violation=False, identity_corrupt=False, collection_complete=True, collection_failure_reasons=[])

    def observation_summary(self):
        """Snapshot for the driver; absent external observer records stay unknown."""
        return deepcopy(self._summary)

    def _archive(self, name, payload, context, *, observation_type, handles=()):
        base = self._observer_base
        path = base.directory / 'observations/recovery-observer' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            raise ValueError('Observer evidence must be fresh: ' + str(path))
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            write_json(path, payload)
        artifact = base.artifact(path, context)
        event = base.event('observation', context=context, observation_type=observation_type,
                           source_artifact=artifact, handles=list(handles))
        return artifact, event['id']

    def _registered_payload(self, artifact_id, context):
        base = self._observer_base
        artifact = next(a for a in base.data['artifacts'] if a['id'] == artifact_id)
        if any(artifact.get(k) != context.get(k) for k in ('run_id', 'input_version')):
            raise ResultRejected('stale_observer_artifact', 'Gate evidence does not match caller identity')
        path = base.directory / artifact['path']
        if path.is_symlink() or base.directory not in path.resolve().parents or not path.is_file():
            raise ResultRejected('unsafe_observer_artifact', 'Unsafe registered observer evidence')
        for parent in path.parents:
            if parent == base.directory:
                break
            if parent.is_symlink():
                raise ResultRejected('unsafe_observer_artifact', 'Registered evidence uses a symlinked path')
        payload = path.read_bytes()
        if digest(payload) != artifact['sha256']:
            raise ResultRejected('observer_digest_mismatch', 'Registered gate evidence changed')
        return payload

    def _trusted_source(self, work, context):
        marker = verify_workspace(work)
        if any(marker.get(k) != context.get(k) for k in ('run_id', 'input_version')):
            raise ResultRejected('stale_sampling_context', 'Workspace differs from caller identity')
        record = current_record(marker['source_case'], context)
        if any(record.data.get(k) != context.get(k) for k in ('run_id', 'input_version')):
            raise ResultRejected('stale_native_record', 'Native manifest differs from caller identity')
        source_hashes = {}
        for artifact in record.data['artifacts']:
            if artifact.get('role') == 'visualization':
                continue
            payload = read_verified_result(record, artifact['id'], context['run_id'], context['input_version'])
            source_hashes[artifact['path']] = digest(payload)
        if marker.get('input_files') != source_hashes:
            raise ResultRejected('snapshot_manifest_mismatch', 'Workspace does not contain exactly trusted source bytes')
        return dict(workspace_marker=marker, source_manifest=record.data,
                    source_manifest_sha256=digest(Path(record.source).read_bytes()), verified_source_hashes=source_hashes)

    def _full_gate_qualification(self, work, context, process_result, launch):
        """Check every original gate predicate using its original log/line readers.

        This is pre-intervention qualification, not an extra sampler or a CFD
        acceptance pass. Physical reference tolerances are deliberately absent.
        """
        if launch['launch_status'] != 'started' or not launch.get('pid') or type(process_result.returncode) is not int or process_result.returncode != 0:
            raise SamplingError('injection_process_not_successful', 'A confirmed zero-exit process is required')
        if launch.get('postprocessing_present_before_launch') is not False:
            raise SamplingError('injection_workspace_not_fresh', 'Outputs must be newly generated')
        for name, payload in (('stdout.log', process_result.stdout), ('stderr.log', process_result.stderr)):
            parsed = parse_solver_log(payload.decode('utf-8'), name)
            if parsed['fatal_errors']:
                raise SamplingError('injection_fatal_log', 'Original gate would reject fatal logs')
        trusted = self._trusted_source(work, context)
        outputs = []
        for name in ('profile.xy', 'pressure.xy'):
            found = sorted((work / 'postProcessing').rglob(name))
            if len(found) != 1:
                raise SamplingError('injection_output_not_unique', 'One newly generated ' + name + ' required')
            path = found[0]
            if path.is_symlink() or work.resolve() not in path.resolve().parents or not path.is_file():
                raise SamplingError('injection_unsafe_output', 'Output leaves trusted workspace')
            # Reject symlinked parent directories, even when they resolve inside.
            if any(parent.is_symlink() for parent in path.parents if parent != work.parent):
                raise SamplingError('injection_unsafe_output', 'Output uses a symlinked path')
            payload = path.read_bytes()
            if not payload.strip():
                raise SamplingError('injection_empty_output', 'Required output is empty')
            outputs.append((path, payload))
        archived = []
        for path, payload in outputs:
            artifact, _ = self._archive('intervention/' + path.name, payload, context,
                                        observation_type='pre_intervention_output')
            archived.append((path, payload, artifact))
        archive_directory = self._observer_base.directory / 'observations/recovery-observer/intervention'
        # Like the original gate, parse the exact registered copies rather than
        # reopening mutable process outputs for the measurement reader.
        measurements = self.measure_outputs(archive_directory / 'profile.xy', archive_directory / 'pressure.xy')
        for path, payload, artifact in archived:
            if path.is_symlink() or path.read_bytes() != payload or self._registered_payload(artifact, context) != payload:
                raise SamplingError('injection_output_changed', 'Output changed during original reader qualification')
        self._trusted_source(work, context)  # Reader must not mutate trusted bytes.
        return dict(original_full_gate_conditions_verified=True, original_measurement_reader_used=True,
                    measurement_reader_source='exact_registered_output_bytes',
                    output_evidence=[dict(relative_path=str(path.relative_to(work)), sha256=digest(payload), artifact_id=artifact)
                                     for path, payload, artifact in archived],
                    measurements=measurements, reference_threshold_pass_required=False,
                    nonfatal_utf8_logs_verified=True, actual_argv=launch['argv'], actual_exit_code=0,
                    pid=launch['pid'], **trusted), archived

    def _inject_once(self, work, context, result, launch):
        try:
            qualification, outputs = self._full_gate_qualification(work, context, result, launch)
        except (OSError, ValueError, UnicodeError, KeyError) as exc:
            if getattr(exc, 'code', None) in ('stale_run', 'stale_input', 'digest_mismatch', 'snapshot_changed', 'snapshot_manifest_mismatch'):
                self._summary['identity_corrupt'] = True
            refusal = dict(**_identity(context), applied=False, status='injection_not_applied',
                           natural_failure=None, reason_codes=[getattr(exc, 'code', 'injection_original_gate_unverified')],
                           error_type=type(exc).__name__, message=str(exc), actual_argv=launch['argv'],
                           actual_exit_code=result.returncode, launch_artifact=launch['artifact_id'])
            artifact, _ = self._archive('intervention/refusal.json', refusal, context,
                                        observation_type='injection_not_applied')
            self._summary['injection'] = dict(refusal, requested=True, artifact_id=artifact)
            return
        qualified_artifact, _ = self._archive('intervention/full-gate-qualification.json', qualification, context,
                                              observation_type='pre_intervention_full_gate_qualification')
        evidence = []
        for path, payload, artifact in outputs:
            evidence.append(dict(relative_path=str(path.relative_to(work)), sha256=digest(payload), artifact_id=artifact))
        plan = dict(**_identity(context), status='ready_to_remove', natural_failure=False,
                    rule='Remove one qualified newly generated profile exactly once',
                    qualification_artifact=qualified_artifact, outputs=evidence,
                    launch_artifact=launch['artifact_id'], actual_argv=launch['argv'], actual_exit_code=0,
                    physics_numerics_acceptance_changed=False, solver_restarted=False)
        plan_artifact, _ = self._archive('intervention/plan.json', plan, context,
                                         observation_type='pre_intervention_plan')
        applied, error = False, None
        try:
            outputs[0][0].unlink()
            applied = True
            self._trusted_source(work, context)
        except (OSError, ValueError, KeyError) as exc:
            error = dict(type=type(exc).__name__, code=getattr(exc, 'code', 'injection_mutation_failure'), message=str(exc))
        outcome = dict(**_identity(context), requested=True, applied=applied and error is None,
                       profile_removed=applied, status='applied' if applied and error is None else 'injection_not_applied',
                       natural_failure=False if applied else None, plan_artifact=plan_artifact,
                       original_reader_bytes_unchanged=error is None, error=error,
                       reason_codes=[] if error is None else [error['code']])
        artifact, _ = self._archive('intervention/outcome.json', outcome, context,
                                    observation_type='explicit_sampling_intervention')
        self._summary['injection'] = dict(outcome, artifact_id=artifact)

    @contextmanager
    def _capture_sampler(self, work, context, number):
        original = sampling_gate.subprocess
        launch = dict(**_identity(context), sample_number=number, launch_attempted=False,
                      launch_status='not_attempted', pid=None, argv=None, timed_out=False,
                      observed_returncode=None, launcher='subprocess.Popen/communicate',
                      postprocessing_present_before_launch=None)
        self._launches.append(launch)

        def run(argv, **kwargs):
            if not _is_sampler(argv):
                return original.run(argv, **kwargs)
            if launch['launch_attempted']:
                raise ValueError('One actual sampler launch per sampling function call')
            if kwargs != dict(capture_output=True, timeout=120):
                raise ValueError('Frozen sampler subprocess options changed')
            launch.update(launch_attempted=True, argv=list(argv), started_at=now(),
                          postprocessing_present_before_launch=(work / 'postProcessing').exists())
            process = None
            def terminate_child():
                if process is None:
                    return None, None
                try:
                    process.kill()
                    captured = process.communicate(timeout=20)
                    launch['child_killed_and_reaped'] = True
                    launch['observed_returncode'] = process.returncode
                    return captured
                except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                    launch.update(child_killed_and_reaped=False, cleanup_error_type=type(cleanup_error).__name__,
                                  cleanup_error=str(cleanup_error), observed_returncode=process.returncode)
                    return None, None
            try:
                process = self._process_factory(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                pid = getattr(process, 'pid', None)
                launch.update(pid=pid, launch_status='started' if type(pid) is int and pid > 0 else 'unknown')
                stdout, stderr = process.communicate(timeout=120)
                launch.update(observed_returncode=process.returncode, returned_at=now(),
                              stdout_sha256=digest(stdout), stderr_sha256=digest(stderr))
                result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired as exc:
                launch.update(timed_out=True, returned_at=now(), exception_type=type(exc).__name__)
                if process is not None:
                    stdout, stderr = terminate_child()
                    exc.stdout = stdout or exc.stdout
                    exc.stderr = stderr or exc.stderr
                raise
            except OSError as exc:
                launch.update(launch_status='failed' if process is None else launch['launch_status'],
                              returned_at=now(), exception_type=type(exc).__name__, message=str(exc))
                terminate_child()
                raise
            except BaseException as exc:
                launch.update(returned_at=now(), exception_type=type(exc).__name__, message=str(exc))
                terminate_child()
                raise
            finally:
                artifact, event = self._archive('sample-' + str(number) + '/launch.json', dict(launch), context,
                                                observation_type='sampler_actual_launch')
                launch.update(artifact_id=artifact, event_id=event)
            if self._scenario == 'transient_missing' and number == 1:
                self._inject_once(work, context, result, launch)
            return result

        sampling_gate.subprocess = SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
        try:
            yield launch
        finally:
            sampling_gate.subprocess = original
            if not launch['launch_attempted']:
                artifact, event = self._archive('sample-' + str(number) + '/launch.json', dict(launch), context,
                                                observation_type='sampler_launch_not_attempted')
                launch.update(artifact_id=artifact, event_id=event)

    def _first_assessment(self, work, context, error, launch, events, call_artifact, call_event):
        reasons, evidence_verified, gate, gate_artifact, logs_verified = [], True, None, None, None
        sample = next((e for e in reversed(events) if e.get('operation') == 'sample'), None)
        missing, eligible, trust = None, None, None
        try:
            if sample is None:
                raise ValueError('First gate assessment was not registered')
            call = json.loads(self._registered_payload(call_artifact, context))
            actual_launch = json.loads(self._registered_payload(launch['artifact_id'], context))
            if any(call.get(k) != context.get(k) or actual_launch.get(k) != context.get(k) for k in ('run_id', 'input_version')):
                raise ResultRejected('stale_observer_artifact', 'Function entry or launch differs from caller identity')
            if actual_launch.get('launch_status') != launch['launch_status'] or actual_launch.get('pid') != launch.get('pid'):
                raise ValueError('Archived launch evidence differs from captured launch')
            gate_artifact = sample['source_artifact']
            gate = json.loads(self._registered_payload(gate_artifact, context))
            if any(gate.get(k) != context.get(k) or sample.get(k) != context.get(k) for k in ('run_id', 'input_version')):
                raise ResultRejected('stale_gate_identity', 'First gate differs from caller identity')
            if gate.get('exit_code') != sample.get('exit_code') or gate.get('error_category') != sample.get('error_category'):
                raise ValueError('First gate event and assessment differ')
            logs = [self._registered_payload(gate[key], context) for key in ('stdout_artifact', 'stderr_artifact')]
            try:
                logs_verified = not any(parse_solver_log(payload.decode('utf-8'), name)['fatal_errors']
                                        for name, payload in zip(('stdout.log', 'stderr.log'), logs))
            except UnicodeError:
                logs_verified = None
            category = gate.get('error_category')
            if category == 'sampling_missing_output':
                missing = isinstance(error, SamplingError) and error.code == category and error.assessment_artifact == gate_artifact \
                    and gate.get('outcome') == 'fail' and sample.get('outcome') == 'fail' \
                    and type(gate.get('exit_code')) is int and gate.get('exit_code') == 0 \
                    and actual_launch.get('observed_returncode') == 0 and logs_verified is True and launch['launch_status'] == 'started'
                if not missing:
                    missing = None
                    reasons.append('typed_missing_evidence_unverified')
            elif category in ('sampling_timeout', 'sampling_launch_error', 'sampling_fatal_log') or (type(gate.get('exit_code')) is int and gate['exit_code'] != 0):
                missing = False
                reasons.append(category or 'nonzero_exit')
            elif launch['launch_status'] == 'unknown' or logs_verified is None or (gate.get('exit_code') is None and launch['launch_status'] == 'started'):
                missing = None
                reasons.append('first_result_unknown')
            else:
                missing = False
                reasons.append(category or 'first_sample_passed')
            if missing is True:
                try:
                    trust = self._trusted_source(work, context)
                    eligible = True
                    reasons.append('current_run_input_source_and_workspace_verified')
                except ResultRejected as exc:
                    eligible = None if exc.code in ('unknown_expected_identity', 'missing_provenance', 'missing_artifact') else False
                    reasons.append(exc.code)
                except (OSError, ValueError, KeyError) as exc:
                    eligible = None
                    reasons.append(getattr(exc, 'code', 'eligibility_evidence_unavailable'))
            elif missing is False:
                eligible = False
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            evidence_verified = False
            reasons.append(getattr(exc, 'code', 'first_gate_evidence_unavailable'))
        if not evidence_verified:
            self._summary['collection_complete'] = False
            self._summary['collection_failure_reasons'].extend(reasons)
        corrupt_codes = {'stale_run', 'stale_input', 'digest_mismatch', 'snapshot_changed',
                         'snapshot_manifest_mismatch', 'stale_native_record', 'stale_gate_identity',
                         'stale_observer_artifact', 'observer_digest_mismatch', 'stale_sampling_context'}
        self._summary['identity_corrupt'] = self._summary['identity_corrupt'] or any(reason in corrupt_codes for reason in reasons)
        before = not any(e.get('decision_type') == 'bounded_postprocess_recovery' for e in self._observer_base.data['events'])
        if not before:
            eligible = None
            reasons.append('eligibility_recorded_after_recovery')
        if self._scenario == 'transient_missing' and self._summary['injection']['status'] == 'not_reached':
            refusal = dict(**_identity(context), requested=True, applied=False, status='injection_not_applied',
                           natural_failure=None, reason_codes=[gate.get('error_category') if gate else 'first_gate_unavailable'],
                           gate_artifact=gate_artifact, launch_artifact=launch.get('artifact_id'))
            artifact, _ = self._archive('intervention/refusal.json', refusal, context,
                                        observation_type='injection_not_applied')
            self._summary['injection'] = dict(refusal, artifact_id=artifact)
        assessment = dict(**_identity(context), function_called=True, launch_status=launch['launch_status'],
                          launch_attempted=launch['launch_attempted'], missing_output_confirmed=missing,
                          eligible=eligible, reason_codes=reasons, evidence_verified=evidence_verified,
                          eligibility_before_recovery=before, eligibility_evidence_verified=evidence_verified and trust is not None,
                          exit_code=gate.get('exit_code') if gate else None, nonfatal_logs_verified=logs_verified,
                          elapsed_seconds=sample.get('elapsed_seconds') if sample else None,
                          call_artifact=call_artifact, call_event_id=call_event,
                          launch_artifact=launch.get('artifact_id'), gate_artifact=gate_artifact,
                          sample_event_id=sample.get('id') if sample else None,
                          original_execution_started_is_launch_proof=False, trusted_source=trust)
        artifact, event = self._archive('first-eligibility.json', assessment, context,
                                        observation_type='shared_first_sample_eligibility',
                                        handles=[sample['id']] if sample else [])
        self._summary['first_sample'] = dict(assessment, eligibility_artifact=artifact, eligibility_event_id=event)
        return eligible, missing

    def sample(self, work, context):
        entered = time.monotonic()
        self._observed_calls += 1
        number = self._observed_calls
        if number > (2 if self._recovery_enabled else 1):
            self._summary['policy_violation'] = True
            raise ValueError('Fresh observer policy per run; sampling budget exhausted')
        work = Path(work).resolve()
        call_artifact, call_event = self._archive('sample-' + str(number) + '/call.json',
            dict(**_identity(context), sample_number=number, function_called=True, entered_at=now(),
                 workspace=str(work), execution_id=self._observer_base.data['environment'].get('execution_id')),
            context, observation_type='sampler_function_entry')
        offset = len(self._observer_base.data['events'])
        error, result = None, None
        launch = dict(launch_status='unknown', launch_attempted=None, artifact_id=None)
        sampler_started = time.monotonic()
        try:
            with self._capture_sampler(work, context, number) as launch:
                try:
                    result = super().sample(work, context)
                except BaseException as exc:
                    error = exc
        finally:
            sampler_elapsed = time.monotonic() - sampler_started
            events = self._observer_base.data['events'][offset:]
            if number == 1:
                eligible, missing = self._first_assessment(work, context, error, launch, events, call_artifact, call_event)
                self._summary['first_sample']['sample_function_elapsed_seconds'] = sampler_elapsed
                if error is not None:
                    self._branch_start = time.monotonic()
                if isinstance(error, SamplingError) and error.code == 'sampling_missing_output' and eligible is not True:
                    error = SamplingError('sampling_recovery_eligibility_unknown' if eligible is None else 'sampling_recovery_ineligible',
                                          'Shared first-failure observer did not establish recovery eligibility', error.assessment_artifact)
            self._summary['observation_elapsed_seconds'] += max(0, time.monotonic() - entered - sampler_elapsed)
        if error is not None:
            raise error
        return result


class RecoveryBranchTimer:
    def sample(self, work, context):
        try:
            return super().sample(work, context)
        except (ResultRejected, SamplingError) as exc:
            if exc.code in ('stale_run', 'stale_input', 'digest_mismatch', 'snapshot_changed', 'stale_sampling_context'):
                self._summary['identity_corrupt'] = True
            raise
        finally:
            base = self._observer_base
            extra = self._launches[1:]
            branch_attempted = getattr(self, 'recovery_attempts', 0) == 1
            if branch_attempted or (self._recovery_enabled and self._branch_start is not None and self._summary['first_sample']['eligible'] is True):
                elapsed = time.monotonic() - self._branch_start if self._branch_start is not None else None
                samples = [e for e in base.data['events'] if e.get('operation') == 'sample']
                launched = any(item['launch_status'] == 'started' for item in extra)
                if not launched and any(item['launch_status'] == 'unknown' for item in extra):
                    launched = None
                second = samples[1] if len(samples) > 1 else None
                timing = dict(**_identity(context), attempt_count=len(extra), launched=launched,
                              sample_pass=second.get('outcome') == 'pass' if second else False,
                              branch_elapsed_seconds=elapsed,
                              extra_calls_elapsed_seconds=sum(e['elapsed_seconds'] for e in samples[1:]) if all('elapsed_seconds' in e for e in samples[1:]) else None,
                              first_gate_elapsed_seconds=samples[0].get('elapsed_seconds') if samples else None,
                              includes_fresh_copy_and_final_identity_verification=True, evidence_verified=True,
                              extra_launch_artifacts=[item.get('artifact_id') for item in extra],
                              finished_at=now(), branch_entered=True, budget_reserved=branch_attempted)
                extra_elapsed = timing['extra_calls_elapsed_seconds']
                timing['non_sampling_branch_elapsed_seconds'] = max(0, elapsed - extra_elapsed) if elapsed is not None and extra_elapsed is not None else None
                timing['non_sampling_branch_elapsed_method'] = 'branch_wall_minus_registered_extra_sample_steps; includes copy, verification, observer and bookkeeping, not pure copy time'
                artifact, _ = self._archive('recovery-branch-timing.json', timing, context,
                                            observation_type='bounded_recovery_branch_timing')
                self._summary['recovery'] = dict(timing, timing_artifact=artifact)


class ObservedBaselineCompletion(RecoveryBranchTimer, SharedSampleObserver, ContractReferenceCompletion):
    pass


class ObservedCandidateCompletion(RecoveryBranchTimer, BoundedSamplingRecovery, SharedSampleObserver, ContractReferenceCompletion):
    pass


def observed_completion_policy(recorder, reference, image, spec, *, enabled=False, scenario='natural', process_factory=None):
    """Build a fresh registered node; construction executes no external process."""
    if type(enabled) is not bool:
        raise TypeError('enabled must be an explicit bool')
    cls = ObservedCandidateCompletion if enabled else ObservedBaselineCompletion
    policy = cls(recorder, reference, image, spec)
    policy.initialize_observer(enabled=enabled, scenario=scenario, process_factory=process_factory)
    return policy
