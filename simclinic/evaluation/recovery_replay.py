"""Offline replay of the frozen nine-file recovery archive; never execute commands.

The archive's recorded provenance is checked against its bytes and pinned primary
results. This is an archive regression, not another independent CFD experiment.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

from simclinic.checks import audit
from simclinic.core.record import load_record
from simclinic.core.report import write_json, write_report
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.result_guard import read_verified_json
from .contract_recovery_study import outcomes


LATEST_FREEZE_SHA256 = '5fe1c82fe06adde350c10362dbe7af080cff010327a0752b33803858217bd3f8'
LATEST_RESULTS_SHA256 = '0360fdedeaa42575922b2d6f97bce4a2298b552d19ad80f84ecdb48f8019560d'


def _json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError('Non-finite JSON value: ' + value)
    return json.loads(payload.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _path(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValueError('Expected relative POSIX archive path')
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or '..' in parsed.parts or parsed.as_posix() != relative:
        raise ValueError('Unsafe archive path: ' + relative)
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('Archive path escaped its root: ' + relative)
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError('Symlink archive evidence rejected: ' + relative)
        current = current.parent
    return path


def _read(root, relative):
    return _path(root, relative).read_bytes()


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def verify_tree(root, expected):
    """Exact file-set and digest check, without importing archived Python code."""
    root = Path(root).resolve()
    if not root.is_dir() or not isinstance(expected, dict):
        raise ValueError('Missing frozen tree or hash manifest: ' + str(root))
    actual = {}
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        _path(root, relative)
        if path.is_file():
            actual[relative] = _sha(path.read_bytes())
    for name, digest in expected.items():
        _path(root, name)
        if not _digest(digest):
            raise ValueError('Missing or invalid frozen digest: ' + name)
    if actual != expected:
        raise ValueError('Frozen bytes/file set changed: ' + str(root))


def verify_artifacts(record):
    """Every registered byte must be present; a missing digest is rejected."""
    for artifact in record.data['artifacts']:
        if not _digest(artifact.get('sha256')):
            raise ValueError('Unknown artifact digest: ' + artifact['id'])
        if _sha(_read(record.case_dir, artifact['path'])) != artifact['sha256']:
            raise ValueError('Artifact bytes changed: ' + artifact['id'])
    return len(record.data['artifacts'])


def verify_inputs(record):
    identities = []
    for attempt in record.data.get('attempts', []):
        identity = (attempt.get('run_id'), attempt.get('input_version'))
        if any(not isinstance(value, str) or not value for value in identity) or identity in identities:
            raise ValueError('Unknown or repeated attempt identity')
        identities.append(identity)
        snapshot = _path(record.case_dir, attempt['input_snapshot'])
        manifest = _json(_read(snapshot.parent, 'input-manifest.json'))
        verify_tree(snapshot, manifest['files'])
        version = 'sha256:' + _sha(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode())
        if identity != (manifest.get('run_id'), manifest.get('input_version')) or identity[1] != version:
            raise ValueError('Attempt/input manifest identity differs')
    if not identities or identities[-1] != (record.data['run_id'], record.data['input_version']):
        raise ValueError('Current record identity differs from its last verified input snapshot')
    return identities


def _seconds(events):
    values = [event.get('elapsed_seconds') for event in events]
    known = all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values)
    return sum(values) if known else None


def recovery_evidence(record, summary):
    """Locate failure/retry bytes while refusing stale or inconsistent identities."""
    current = (record.data['run_id'], record.data['input_version'])
    if any(not isinstance(value, str) or not value for value in current):
        raise ValueError('Unknown current run/input identity')
    samples = [event for event in record.data['events'] if event.get('operation') == 'sample']
    decisions = [event for event in record.data['events'] if event.get('decision_type') == 'bounded_postprocess_recovery']
    if len(samples) > 2 or len(decisions) > 1:
        raise ValueError('Bounded sampling retry limit exceeded')
    diagnostics = diagnose_sampling(record)
    diagnostic_by_id = {row['event_id']: row for row in diagnostics['runs']}
    located = []
    for event in samples:
        if (event.get('run_id'), event.get('input_version')) != current or event.get('input_fingerprint') != current[1]:
            raise ValueError('Stale/unknown sampler identity')
        gate = read_verified_json(record, event.get('source_artifact'), *current)
        if (gate.get('run_id'), gate.get('input_version')) != current:
            raise ValueError('Gate payload identity differs')
        for key in ('exit_code', 'timed_out', 'outcome', 'error_category', 'stdout_artifact', 'stderr_artifact'):
            if gate.get(key) != event.get(key):
                raise ValueError('Gate/event evidence differs: ' + key)
        if gate.get('reference_tolerances_changed') is not False:
            raise ValueError('Unknown or changed reference tolerances')
        row = diagnostic_by_id.get(event['id'])
        if row is None:
            raise ValueError('Sampler lacks supported postprocess diagnostic')
        located.append(dict(event_id=event['id'], record_pointer=record.event_ref(event)['pointer'],
                            retry_of=event.get('retry_of'), outcome=event['outcome'],
                            error_category=event.get('error_category'), exit_code=event.get('exit_code'),
                            timed_out=event.get('timed_out'), elapsed_seconds=event.get('elapsed_seconds'),
                            assessment=record.artifact(event['source_artifact']),
                            diagnostic_status=row['status'], evidence=row['evidence'], gate=gate))
    recovered = False
    if len(samples) == 2 or decisions:
        if len(samples) != 2 or len(decisions) != 1:
            raise ValueError('Incomplete failure/decision/retry lineage')
        first, second = samples
        decision = decisions[0]
        initial = located[0]['gate']
        if (first.get('error_category') != 'sampling_missing_output' or first['outcome'] != 'fail'
                or first['exit_code'] != 0 or first.get('timed_out') is not False
                or initial.get('execution_started') is not True or initial.get('fatal_errors') != []
                or initial.get('zero_exit_with_fatal_log') is not False):
            raise ValueError('Recovery trigger is not an explicit nonfatal missing output')
        first_diagnostic = diagnostic_by_id[first['id']]
        log_failures = [item for item in first_diagnostic['unavailable_evidence']
                        if item.get('kind') in ('stdout_artifact', 'stderr_artifact')]
        if first_diagnostic['fatal_errors'] or log_failures:
            raise ValueError('Fatal or unverifiable first sampling logs cannot justify recovery')
        if all(output['bytes_verified'] for output in first_diagnostic['outputs']) or initial.get('measurements') is not None:
            raise ValueError('Missing-output trigger conflicts with archived output/measurement evidence')
        if ((decision.get('run_id'), decision.get('input_version')) != current or decision.get('retry_limit') != 1
                or decision.get('handles') != [first['id']] or decision.get('action') != 'retry'
                or decision.get('source_artifact') != first['source_artifact'] or second.get('retry_of') != first['id']):
            raise ValueError('Recovery decision/identity/lineage differs')
        events = record.data['events']
        first_index, second_index = events.index(first), events.index(second)
        if not first_index < events.index(decision) < second_index:
            raise ValueError('Recovery decision is outside failure/retry order')
        if any(event.get('phase') == 'solve' for event in events[first_index + 1:second_index]):
            raise ValueError('A solver event occurred during sampling recovery')
        root = second.get('output_archive_root')
        if not isinstance(root, str) or not root.startswith('observations/sampling-recovery/attempt-2/'):
            raise ValueError('Retry archive is not the separate attempt-2 root')
        recovered = (second['outcome'] == 'pass' and second['exit_code'] == 0
                     and second.get('timed_out') is False and diagnostic_by_id[second['id']]['status'] == 'pass'
                     and located[1]['gate'].get('fatal_errors') == [])
        if second['outcome'] == 'pass' and not recovered:
            raise ValueError('Recorded recovery success lacks verified successful sampling evidence')
    challenges = []
    for event in record.data['events']:
        if event.get('observation_type') == 'explicit_sampling_challenge':
            if (event.get('run_id'), event.get('input_version')) != current:
                raise ValueError('Challenge identity differs')
            payload = read_verified_json(record, event['source_artifact'], *current)
            challenges.append(dict(event_id=event['id'], artifact=record.artifact(event['source_artifact']),
                                   natural_failure=payload.get('natural_failure'),
                                   scenario=payload.get('scenario'), removed_sha256=payload.get('removed_sha256'),
                                   payload_artifact=record.artifact(payload.get('payload_artifact'))))
    claims = [event for event in record.data['events'] if event.get('claim_type') == 'completion']
    if samples and samples[-1]['outcome'] != 'pass' and any(event.get('success') is True for event in claims):
        raise ValueError('Failed final sampling was followed by a successful completion claim')
    return dict(schema_version='1.0', run_id=current[0], input_version=current[1],
                archive=str(record.case_dir), sampling_recovered=recovered,
                recovery_status='recovered' if recovered else 'terminated_after_retry' if len(samples) == 2 else 'not_attempted',
                original_quality_met=summary.get('quality_met'), original_score_changed=False,
                sample_calls=len(samples), extra_sampler_calls=max(0, len(samples) - 1),
                total_sampler_seconds=_seconds(samples), extra_sampler_seconds=_seconds(samples[1:]),
                first_failure=located[0] if located and samples[0]['outcome'] == 'fail' else None,
                retry=located[1] if len(located) == 2 else None, samples=located,
                recovery_decision=decisions[0] if decisions else None, challenges=challenges,
                workspace_freshness='Frozen runtime plus recorded decision; temporary workspace is not re-created offline',
                consumption=summary.get('metrics'), model_usage=summary.get('model_usage'),
                diagnostic=diagnostics, new_model_calls=0, new_simulations=0,
                limitation='已知归档回归；sampling_recovered 不等于完整 CFD 验收，也不证明自然故障收益。')


def replay(batch, out, run_ids=None, freeze_sha256=LATEST_FREEZE_SHA256, results_sha256=LATEST_RESULTS_SHA256):
    batch, out = Path(batch).resolve(), Path(out).resolve()
    if out.exists() or out.is_relative_to(batch) or batch.is_relative_to(out):
        raise ValueError('Replay requires a new output directory outside the source batch')
    if any((ancestor / 'freeze.json').is_file() and (ancestor / 'freeze.sha256').is_file() for ancestor in out.parents):
        raise ValueError('Replay output must not enter any existing frozen archive')
    for value in (freeze_sha256, results_sha256):
        if not _digest(value):
            raise ValueError('Explicit trusted freeze and primary results SHA-256 required')
    frozen_bytes, primary_bytes = _read(batch, 'freeze.json'), _read(batch, 'results.json')
    if _sha(frozen_bytes) != freeze_sha256 or _read(batch, 'freeze.sha256').decode().strip() != freeze_sha256:
        raise ValueError('Frozen manifest differs from trusted SHA-256')
    if _sha(primary_bytes) != results_sha256:
        raise ValueError('Primary results differ from trusted SHA-256')
    frozen, primary = _json(frozen_bytes), _json(primary_bytes)
    if frozen.get('experiment') != 'full-model-nine-file-policy-recovery-study-v3':
        raise ValueError('Unsupported frozen study format')
    for folder, key in (('common-runtime', 'common_runtime'), ('native', 'native'), ('corpus', 'corpus'), ('definitions', 'definitions')):
        verify_tree(_path(batch, folder), frozen[key])
    entries = frozen['entries']
    ids = [entry['id'] for entry in entries]
    if len(set(ids)) != len(ids) or frozen['schedule'] != ids or [row['id'] for row in primary['rows']] != ids:
        raise ValueError('Frozen and primary schedules differ')
    selected = set(ids if run_ids is None else run_ids)
    if not selected or not selected.issubset(ids):
        raise ValueError('Select at least one known frozen run ID')
    references = {}
    for name in ('archive-verification.json', 'interpretation.json', 'readiness.json', 'delivery-verification.json', 'historical-integrity.json', 'progress.json'):
        payload = _read(batch, name)
        data = _json(payload)
        if data.get('freeze_sha256') != freeze_sha256:
            raise ValueError('Related summary belongs to another freeze: ' + name)
        if 'primary_results_sha256' in data and data['primary_results_sha256'] != results_sha256:
            raise ValueError('Related summary cites another primary result: ' + name)
        references[name] = dict(path=str(batch / name), sha256=_sha(payload), data=data)
    if primary.get('status') != 'complete' or references['progress.json']['data'].get('status') != 'complete':
        raise ValueError('A complete frozen archive is required')
    if [row['id'] for row in references['progress.json']['data']['runs']] != ids:
        raise ValueError('Progress differs from frozen schedule')
    totals = dict(artifacts=0, input_snapshots=0, model_calls=0, model_tokens=0, solver_runs=0, sampler_calls=0)
    imported, rows, summaries, identities = [], [], [], set()
    for entry, original in zip(entries, primary['rows']):
        run = _path(batch, 'runs/' + entry['id'])
        record = load_record(_path(run, 'record.json'), run)
        summary = _json(_read(run, 'summary.json'))
        if summary != original or any(summary.get(key) != value for key, value in entry.items()):
            raise ValueError('Run summary differs from primary/frozen entry: ' + entry['id'])
        if record.data['task'] != _json(_read(batch, 'definitions/' + entry['id'] + '/task.json')):
            raise ValueError('Task/acceptance changed: ' + entry['id'])
        verify_tree(_path(run, 'source-package'), frozen['native'])
        totals['artifacts'] += verify_artifacts(record)
        attempt_ids = verify_inputs(record)
        current = (record.data['run_id'], record.data['input_version'])
        completion = summary['completion']
        if (completion.get('run_id'), completion.get('input_version')) != current:
            raise ValueError('Completion summary identity differs')
        if read_verified_json(record, completion.get('assessment_artifact'), *current) != summary['acceptance']:
            raise ValueError('Primary acceptance summary differs from registered assessment bytes')
        for event in record.data['events']:
            if event.get('claim_type') == 'completion':
                node = read_verified_json(record, event.get('source_artifact'), *current)
                if ((event.get('run_id'), event.get('input_version')) != current
                        or node.get('stage') != 'end' or node.get('node') != 'completion'
                        or node.get('payload', {}).get('completion_claim') != completion
                        or event.get('success') != completion.get('success')):
                    raise ValueError('Completion claim differs from primary summary/registered bytes')
        for identity in attempt_ids:
            if identity[0] in identities:
                raise ValueError('Reused run identity across archive')
            identities.add(identity[0])
        totals['input_snapshots'] += len(attempt_ids)
        totals['solver_runs'] += len(attempt_ids)
        metadata_paths = sorted(_path(run, 'model-calls').glob('*/metadata.json'))
        metadata = [_json(_read(run, path.relative_to(run).as_posix())) for path in metadata_paths]
        if metadata != record.data.get('model_calls'):
            raise ValueError('Model calls differ from registered metadata')
        token_values = [call.get('total_tokens') for call in metadata]
        if any(type(value) is not int or value < 0 for value in token_values):
            raise ValueError('Unknown model usage; cannot reproduce aggregate consumption')
        tokens = sum(token_values)
        if (tokens != summary.get('model_tokens') or tokens != record.data['metrics']['model_tokens']
                or len(metadata) != summary['model_usage']['total_calls']):
            raise ValueError('Recorded model consumption differs')
        registered_metadata = {artifact['path'] for artifact in record.data['artifacts'] if artifact['path'].endswith('/metadata.json')}
        if not all(path.relative_to(run).as_posix() in registered_metadata for path in metadata_paths):
            raise ValueError('Unregistered model usage metadata')
        totals['model_calls'] += len(metadata)
        totals['model_tokens'] += tokens
        evidence = recovery_evidence(record, summary)
        totals['sampler_calls'] += evidence['sample_calls']
        report = audit(record)
        previous = _json(_read(run, 'audit/report.json'))
        if report['summary'] != previous['summary'] or [(c['check_id'], c['status']) for c in report['checks']] != [(c['check_id'], c['status']) for c in previous['checks']]:
            raise ValueError('Current audit differs from archived check outcomes')
        row = dict(id=entry['id'], arm=entry['arm'], stage=entry['stage'], scenario=entry['scenario'],
                   synthetic=record.data['synthetic'], source_nature='known_archive_regression',
                   original_quality_met=summary['quality_met'], sampling_recovered=evidence['sampling_recovered'],
                   sample_calls=evidence['sample_calls'], audit_summary=report['summary'],
                   first_failure_event=evidence['first_failure']['event_id'] if evidence['first_failure'] else None,
                   retry_event=evidence['retry']['event_id'] if evidence['retry'] else None,
                   exported=entry['id'] in selected, archive=str(run), record_sha256=_sha(_read(run, 'record.json')))
        rows.append(row)
        summaries.append(summary)
        if entry['id'] in selected:
            imported.append((entry['id'], record, report, evidence))
    scoring = outcomes(summaries, True)
    if (scoring != primary['scoring'] or scoring != references['archive-verification.json']['data']['scoring']
            or scoring != references['interpretation.json']['data']['primary_scoring']
            or totals != references['archive-verification.json']['data']['totals']
            or totals != references['interpretation.json']['data']['totals']):
        raise ValueError('Replayed totals/scoring differ from archived summaries')
    result = dict(schema_version='1.0', batch=str(batch), freeze_sha256=freeze_sha256,
                  primary_results_sha256=results_sha256, read_only_source=True, archive_verified=True,
                  new_model_calls=0, new_simulations=0, primary_results_changed=False,
                  totals=totals, original_scoring=scoring, verified_runs=len(rows), exported_runs=len(imported),
                  sampling_recovered=sum(row['sampling_recovered'] for row in rows), rows=rows,
                  related_summaries=references,
                  limitation='全批注册字节、冻结源码和输入快照核验；五项体检与采样诊断是已知归档回归。'
                             '原质量评分仅交叉核对，不重新求解或独立复测 CFD；导入 provenance 本身不能被认证。')
    out.mkdir(parents=True)
    for run_id, record, report, evidence in imported:
        destination = out / 'runs' / run_id
        write_json(destination / 'imported-record.json', record.data)
        write_report(report, destination / 'audit')
        write_json(destination / 'sampling-diagnostic.json', evidence['diagnostic'])
        write_json(destination / 'recovery-evidence.json', evidence)
    write_json(out / 'replay.json', result)
    lines = ['# 有限采样恢复离线复现', '', f'源归档：`{batch}`', '',
             f'冻结 SHA-256：`{freeze_sha256}`', f'原结果 SHA-256：`{results_sha256}`', '',
             f'只读核验 {len(rows)} 次运行；导出 {len(imported)} 次；采样恢复 {result["sampling_recovered"]} 次。',
             '新增模型调用 0，新增仿真 0。', '', result['limitation'], '',
             '| 运行 | 原质量 | 首次失败 | 重采样 | 采样恢复 | 导出 |', '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        lines.append(f'| {row["id"]} | {row["original_quality_met"]} | {row["first_failure_event"]} | {row["retry_event"]} | {row["sampling_recovered"]} | {row["exported"]} |')
    lines += ['', '[完整核验与消耗](replay.json)。导出的每次运行位于 `runs/<id>/`：'
              '`imported-record.json`、`audit/report.md`、`sampling-diagnostic.json`、`recovery-evidence.json`。', '',
              '再次调用 audit 或 diagnose-sampling 时，`--case` 必须指向原 `runs/<id>` 归档根；'
              '导入记录未复制原始证据，不能把输出目录当作 evidence 根。']
    (out / 'replay.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result
