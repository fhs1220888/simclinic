"""Read-only diagnostics for the registered Foundation 10 channel sampler.

This supplements, rather than changes, the five agent checks or CFD acceptance.
Verified sample bytes do not prove a reference tolerance was met.
"""
from pathlib import Path, PurePosixPath

from simclinic.result_guard import ResultRejected, read_verified_result
from .logs import parse_solver_log


def diagnose_sampling(record):
    rows = []
    for event in record.data['events']:
        if event.get('kind') != 'tool' or event.get('operation') != 'sample' or event.get('phase') != 'postprocess':
            continue
        identity = (event.get('run_id'), event.get('input_version'))
        evidence, unknown, fatal, ends = [record.event_ref(event)], [], [], []
        verified_logs = 0
        for key in ('stdout_artifact', 'stderr_artifact'):
            artifact_id = event.get(key)
            try:
                payload = read_verified_result(record, artifact_id, *identity)
                artifact = record.artifact(artifact_id)
                observations = parse_solver_log(payload.decode('utf-8'), artifact['path'])
                verified_logs += 1
                for observation in observations['fatal_errors']:
                    fatal.append(dict(observation, artifact_id=artifact_id, event_id=event['id']))
                ends.extend(observations['end_evidence'])
                evidence.append(dict(artifact_id=artifact_id, path=artifact['path'], sha256=artifact['sha256']))
            except (ResultRejected, UnicodeError) as exc:
                unknown.append(dict(kind=key, code=getattr(exc, 'code', 'invalid_log_encoding'), message=str(exc)))
        outputs = []
        output_root, root_valid = 'observations/sampling', True
        declared_root = event.get('output_archive_root')
        if declared_root is not None:
            try:
                if not isinstance(declared_root, str) or '\\' in declared_root:
                    raise ResultRejected('invalid_output_archive_root', 'Output archive root must be a relative POSIX path')
                parsed_root = PurePosixPath(declared_root)
                if parsed_root.is_absolute() or '..' in parsed_root.parts or parsed_root.as_posix() != declared_root:
                    raise ResultRejected('invalid_output_archive_root', 'Unsafe output archive root')
                gate_id = event.get('source_artifact')
                read_verified_result(record, gate_id, *identity)
                gate = record.artifact(gate_id)
                if PurePosixPath(gate['path']).name != 'gate-assessment.json' or str(PurePosixPath(gate['path']).parent) != declared_root:
                    raise ResultRejected('invalid_output_archive_root', 'Root does not match verified gate assessment location')
                output_root = declared_root
                evidence.append(dict(artifact_id=gate_id,path=gate['path'],sha256=gate['sha256']))
            except ResultRejected as exc:
                root_valid = False
                unknown.append(dict(kind='output_archive_root',code=exc.code,message=str(exc)))
        for name in ('profile.xy', 'pressure.xy'):
            expected = str(Path(output_root) / name)
            candidates = [a for a in record.data['artifacts'] if a['path'] == expected
                          and (a.get('run_id'), a.get('input_version')) == identity]
            output = dict(path=expected, bytes_verified=False)
            if len(candidates) != 1:
                unknown.append(dict(kind='required_output', path=expected,
                                    code='missing_or_ambiguous_registered_output'))
            else:
                artifact = candidates[0]
                try:
                    payload = read_verified_result(record, artifact['id'], *identity)
                    if not payload.strip():
                        unknown.append(dict(kind='required_output', path=expected, code='empty_output'))
                    else:
                        output.update(bytes_verified=True, artifact_id=artifact['id'], sha256=artifact['sha256'])
                        evidence.append(dict(artifact_id=artifact['id'], path=expected, sha256=artifact['sha256']))
                except ResultRejected as exc:
                    unknown.append(dict(kind='required_output', path=expected, code=exc.code, message=str(exc)))
            outputs.append(output)
        code = event.get('exit_code')
        failed_exit = code is not None and code != 0
        status = ('finding' if failed_exit or fatal else
                  'pass' if code == 0 and root_valid and verified_logs == 2 and all(o['bytes_verified'] for o in outputs)
                  else 'insufficient_evidence')
        reasons = (['nonzero_exit'] if failed_exit else []) + (['fatal_error_in_verified_log'] if fatal else [])
        rows.append(dict(event_id=event['id'], run_id=identity[0], input_version=identity[1],
                         status=status, exit_code=code, recorded_tool_outcome=event.get('outcome'),
                         zero_exit_with_fatal_log=code == 0 and bool(fatal), reasons=reasons,
                         fatal_errors=fatal, end_evidence=ends, outputs=outputs,
                         unavailable_evidence=unknown, evidence=evidence))
    summary = {status: sum(row['status'] == status for row in rows)
               for status in ('finding', 'pass', 'insufficient_evidence')}
    summary['not_applicable'] = int(not rows)
    return dict(schema_version='1.0', diagnostic='openfoam-foundation-10-channel-sampling',
                synthetic=record.data['synthetic'], read_only=True, new_model_calls=0, new_simulations=0,
                summary=summary, runs=rows, primary_checks_and_acceptance_unchanged=True,
                reference_validation='not_assessed',
                limitation='限定已注册通道采样路径。pass 只表示退出、日志和所需输出的字节证据齐备，'
                           '不表示数值/参考验收通过。缺失或无法核验的日志不能当作 Agent 错误。')
