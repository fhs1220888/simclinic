"""Candidate registered completion policy; legacy and frozen policies stay intact."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

from .acceptance import profile_error, pressure_gradient_error, solver_measurements
from .completion import ChannelCompletion, canonical_dictionary
from .logs import parse_solver_log
from ...checks.behavior import matches
from ...core.report import write_json
from ...foam_runtime import current_record, verified_workspace, verify_workspace
from ...result_guard import ResultRejected


class SamplingError(ValueError):
    def __init__(self, code, message, assessment_artifact=None):
        self.code = code
        self.assessment_artifact = assessment_artifact
        prefix = code + ': '
        super().__init__(message if message.startswith(prefix) else prefix + message)


class GatedChannelCompletion(ChannelCompletion):
    """A separate policy: sample outcome reflects logs, outputs and provenance.

    Reference tolerances and task parameters come from the existing task.
    Failures stop completion; this policy does not invoke a repair model.
    """
    def measure_outputs(self, profile, pressure):
        return dict(profile_error=profile_error(profile), pressure_gradient_error=pressure_gradient_error(pressure))

    def sample(self, work, context):
        work = Path(work).resolve()
        directory = self.recorder.directory / 'observations/sampling'
        directory.mkdir(parents=True)
        started = time.monotonic()
        code, timed_out, launched = None, False, False
        stdout, stderr = b'', b''
        category, message, fatal, outputs = None, None, [], []
        values = None
        name = 'simclinic-assessment-' + uuid.uuid4().hex
        argv = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--init',
                '--platform', 'linux/amd64', '--network', 'none', '--cpus', '2', '--memory', '2g',
                '--read-only', '--tmpfs', '/tmp', '--user', 'root',
                '--mount', f'type=bind,src={work},dst=/case', '--workdir', '/case',
                '--entrypoint', '/bin/bash', self.image, '-c',
                'source /opt/openfoam10/etc/bashrc && simpleFoam -postProcess -func sample -latestTime']
        try:
            marker = verify_workspace(work)
            if any(marker.get(key) != context.get(key) for key in ('run_id', 'input_version')):
                raise ResultRejected('stale_sampling_context', 'Sampling context differs from verified workspace.')
            if (work / 'postProcessing').exists() or (work / 'postProcessing').is_symlink():
                raise SamplingError('sampling_preexisting_output', 'Fresh sampling workspace already has postProcessing outputs.')
            launched = True
            result = subprocess.run(argv, capture_output=True, timeout=120)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b'', exc.stderr or b''
            timed_out = True
            category, message = 'sampling_timeout', 'Sampling exceeded 120 seconds; exit status is unknown.'
        except (OSError, ResultRejected, SamplingError) as exc:
            category, message = getattr(exc, 'code', 'sampling_launch_error'), str(exc)
        finally:
            if launched:
                try:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=20)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # Keep the original process evidence; never replace its exit status.
        for filename, payload in (('stdout.log', stdout), ('stderr.log', stderr)):
            (directory / filename).write_bytes(payload)
        stdout_id = self.recorder.artifact(directory / 'stdout.log', context)
        stderr_id = self.recorder.artifact(directory / 'stderr.log', context)
        for filename, payload, artifact_id in (('stdout.log', stdout, stdout_id), ('stderr.log', stderr, stderr_id)):
            try:
                observed = parse_solver_log(payload.decode('utf-8'), 'observations/sampling/' + filename)
                fatal.extend(dict(item, artifact_id=artifact_id) for item in observed['fatal_errors'])
            except UnicodeError:
                if category is None:
                    category, message = 'sampling_log_encoding', 'Sampling log is not valid UTF-8: ' + filename
        if fatal:
            category = 'sampling_fatal_log'
            message = 'Fatal error in ' + ', '.join(f"{e['source']}:{e['line']}" for e in fatal)
        elif category is None and code != 0:
            category, message = 'sampling_process_exit', 'Sampling exited with status ' + str(code)
        if category is None:
            try:
                verify_workspace(work)
                files = []
                for filename in ('profile.xy', 'pressure.xy'):
                    found = sorted((work / 'postProcessing').rglob(filename))
                    if not found:
                        raise SamplingError('sampling_missing_output', 'Required output missing: postProcessing/**/' + filename)
                    if len(found) != 1:
                        raise SamplingError('sampling_ambiguous_output', 'Multiple outputs found for ' + filename)
                    path = found[0]
                    if path.is_symlink() or work not in path.resolve().parents or not path.is_file():
                        raise SamplingError('sampling_unsafe_output', 'Required output leaves the verified workspace: ' + filename)
                    payload = path.read_bytes()
                    if not payload.strip():
                        raise SamplingError('sampling_empty_output', 'Required output is empty: ' + filename)
                    files.append((filename, payload))
                for filename, payload in files:
                    target = directory / filename
                    target.write_bytes(payload)
                    artifact_id = self.recorder.artifact(target, context)
                    outputs.append(dict(path=str(target.relative_to(self.recorder.directory)),
                                        artifact_id=artifact_id, sha256=hashlib.sha256(payload).hexdigest()))
                # Read the exact archived sample bytes, not the mutable work files.
                values = self.measure_outputs(directory / 'profile.xy', directory / 'pressure.xy')
                verify_workspace(work)
            except (OSError, ResultRejected, ValueError) as exc:
                category = getattr(exc, 'code', 'sampling_invalid_output')
                message = str(exc)
        unknown = code is None or category in ('sampling_log_encoding', 'sampling_launch_error')
        outcome = 'pass' if category is None else 'fail' if fatal else 'unknown' if unknown else 'fail'
        if unknown:
            self.recorder.tools_complete = False
        assessment = dict(**{k: context.get(k) for k in ('run_id', 'input_version')},
                          policy='sampling-gate-v1', exit_code=code, execution_started=launched,
                          timed_out=timed_out, outcome=outcome, error_category=category, message=message,
                          stdout_artifact=stdout_id, stderr_artifact=stderr_id,
                          fatal_errors=fatal, outputs=outputs, measurements=values if category is None else None,
                          zero_exit_with_fatal_log=code == 0 and bool(fatal),
                          reference_tolerances_changed=False, automatic_repair=False)
        path = directory / 'gate-assessment.json'
        write_json(path, assessment)
        source = self.recorder.artifact(path, context)
        event = self.recorder.tool('sample', 'postprocess', code, outcome, context['input_version'],
                                   {'function': 'sample', 'time': 'current verified latest', 'policy': 'sampling-gate-v1'},
                                   elapsed_seconds=time.monotonic() - started, error_category=category,
                                   stdout_artifact=stdout_id, stderr_artifact=stderr_id,
                                   source_artifact=source, timed_out=timed_out)
        self.recorder.event('decision', context=context, handles=[event['id']], decision_type='sampling_gate',
                            action='continue' if outcome == 'pass' else 'stop', source_artifact=source)
        if category is not None:
            raise SamplingError(category, message, source)
        return values

    def __call__(self, state):
        # Keep the legacy policy available; register the new assessment once,
        # including typed errors, before emitting the completion statement.
        recorder = self.recorder
        context = state.get('simclinic_context') or {}
        case = Path(state.get('case_dir') or recorder.directory / 'missing-case')
        native = case / '.simclinic/native' / context.get('run_id', 'missing')
        logs = recorder.directory / 'attempts' / context.get('run_id', 'missing') / 'logs'
        execution = json.loads((native / 'execution.json').read_text()) if (native / 'execution.json').is_file() else {}
        log = (logs / 'log.simpleFoam').read_text() if (logs / 'log.simpleFoam').is_file() else ''
        values = solver_measurements(log, execution.get('exit_code'))
        values.update(profile_error=None, pressure_gradient_error=None, task_inputs_verified=False, rendered_velocity=False)
        checks, error = {}, None
        snapshot = native / 'input-snapshot'
        for folder in ('0', 'constant', 'system'):
            for path in (self.reference / folder).rglob('*'):
                if path.is_file():
                    relative = path.relative_to(self.reference)
                    candidate = snapshot / relative
                    checks[str(relative)] = candidate.is_file() and canonical_dictionary(candidate) == canonical_dictionary(path)
        values['task_inputs_verified'] = bool(checks) and all(checks.values())
        if not state.get('error_logs'):
            try:
                record = current_record(case, context)
                images = state.get('plot_outputs', [])
                image_artifacts = [a for a in record.data['artifacts'] if a.get('role') == 'visualization']
                values['rendered_velocity'] = bool(state.get('pyvista_visualization', {}).get('success')) and \
                    state.get('visualization_summary', {}).get('fields_visualized') == ['U'] and len(images) == 1 and any(
                        (case / a['path']).resolve() == Path(images[0]).resolve() and
                        hashlib.sha256(Path(images[0]).read_bytes()).hexdigest() == a['sha256'] for a in image_artifacts)
                with verified_workspace(case, context) as work:
                    measured = self.sample(work, context)
                current_record(case, context)
                values.update(measured)  # Publish measurements only after final identity verification.
            except (ResultRejected, OSError, ValueError) as exc:
                error = dict(type=type(exc).__name__, code=getattr(exc, 'code', 'sampling_assessment_error'),
                             message=str(exc), assessment_artifact=getattr(exc, 'assessment_artifact', None))
                recorder.references_complete = False
        accepted = error is None and all(matches(values[c['id']], c) is True for c in recorder.data['task']['acceptance'])
        assessment = dict(**{k: context.get(k) for k in ('run_id', 'input_version')}, values=values,
                          input_checks=checks, criteria=recorder.data['task']['acceptance'], accepted=accepted,
                          error=error, policy='sampling-gate-v1',
                          method='Same declared reference and tolerances; sampling gate before explicit completion.')
        path = recorder.directory / 'observations/task-assessment.json'
        write_json(path, assessment)
        source = recorder.artifact(path, context)
        for criterion in recorder.data['task']['acceptance']:
            recorder.event('measurement', context=context, criterion_id=criterion['id'],
                           value=values[criterion['id']], artifact_id=source)
        recorder.acceptance_complete = True
        claim = dict(**{k: context.get(k) for k in ('run_id', 'input_version')}, success=accepted,
                     status='completed' if accepted else 'failed' if state.get('error_logs') or error else 'acceptance_not_met',
                     emitter='Foam-Agent registered SimClinic channel completion node',
                     assessment_artifact=source, criteria_preserved=True)
        write_json(recorder.directory / 'observations/completion-claim.json', claim)
        return {'acceptance_report': assessment, 'completion_claim': claim}


class GatedHeldoutCompletion(GatedChannelCompletion):
    def __init__(self, recorder, reference, image, spec):
        super().__init__(recorder, reference, image)
        self.spec = spec

    def measure_outputs(self, profile, pressure):
        from ...evaluation.heldout import velocity_error, pressure_error
        return dict(profile_error=velocity_error(profile, self.spec), pressure_gradient_error=pressure_error(pressure, self.spec))
