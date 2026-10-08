"""Pinned offline diagnostic regressions; never execute archived commands."""
import hashlib
import json
import re
from pathlib import Path

from simclinic.advisor import candidates
from simclinic.checks import audit
from simclinic.checks.behavior import NAMES, STATUSES
from simclinic.core.record import RecordError, load_record, require
from simclinic.core.report import write_json, write_report
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling


KINDS = ('synthetic', 'real_normal', 'real_failure', 'injected_fault',
         'evidence_ablation', 'incomplete_run')
SAMPLING_KEYS = ('status', 'reasons', 'zero_exit_with_fatal_log', 'fatal_lines')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            value.update(chunk)
    return value.hexdigest()


def strict_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate manifest key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise RecordError('non-finite manifest constant: ' + value)

    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=pairs, parse_constant=constant)


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def source_path(root, name):
    require(isinstance(name, str) and name and '\\' not in name,
            'source path must be a nonempty relative POSIX path')
    path = Path(name)
    require(not path.is_absolute() and '..' not in path.parts and
            path.as_posix() == name and name != '.', 'unsafe source path: ' + name)
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root), 'source escapes root: ' + name)
    return resolved


def validate_manifest(manifest, root):
    require(isinstance(manifest, dict), 'benchmark manifest must be an object')
    require(manifest.get('schema_version') == '1.0', 'benchmark schema_version must be 1.0')
    require(isinstance(manifest.get('benchmark_id'), str) and manifest['benchmark_id'], 'missing benchmark_id')
    require(isinstance(manifest.get('version'), str) and
            re.fullmatch(r'\d+\.\d+\.\d+', manifest['version']), 'invalid benchmark version')
    cases = manifest.get('cases')
    require(isinstance(cases, list) and cases, 'benchmark cases must be nonempty')
    ids = set()
    for case in cases:
        require(isinstance(case, dict), 'case must be an object')
        identity = case.get('id')
        require(isinstance(identity, str) and re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', identity), 'invalid case id')
        require(identity not in ids, 'duplicate case id: ' + identity)
        ids.add(identity)
        require(case.get('source_kind') in KINDS, 'invalid source_kind: ' + identity)
        require(isinstance(case.get('basis'), str) and case['basis'], 'missing curation basis: ' + identity)
        for key in ('record', 'case_dir'):
            source_path(root, case.get(key))
        labels = case.get('record_labels', {})
        require(isinstance(labels, dict) and set(labels) == {'synthetic', 'fault_injection'}, 'invalid record label keys')
        require(type(labels.get('synthetic')) is bool and
                (labels['fault_injection'] is None or type(labels['fault_injection']) is bool), 'invalid record labels')
        require(labels['synthetic'] == (case['source_kind'] == 'synthetic'), 'source_kind disagrees with synthetic label')
        if case['source_kind'] == 'injected_fault':
            require(labels['fault_injection'] is True, 'injected fault must be explicitly labelled')
        if case['source_kind'] in ('real_normal', 'real_failure', 'incomplete_run'):
            require(labels['fault_injection'] is False, 'natural/source run must explicitly exclude injection')
        files = case.get('files')
        require(isinstance(files, dict) and case['record'] in files and valid_sha(files[case['record']]), 'record must have a pinned hash')
        for name, sha in files.items():
            source_path(root, name)
            require(sha is None or valid_sha(sha), 'invalid file hash: ' + name)
        provenance = case.get('provenance')
        require(isinstance(provenance, list) and provenance, 'missing provenance: ' + identity)
        for source in provenance:
            require(isinstance(source, dict) and source.get('path') in files and
                    valid_sha(files[source['path']]) and isinstance(source.get('role'), str) and source['role'], 'unpinned provenance')
        expected = case.get('expected', {})
        require(isinstance(expected, dict) and set(expected) == {'behavior', 'sampling'}, 'invalid expected sections')
        behavior = expected.get('behavior', {})
        require(isinstance(behavior, dict) and set(behavior) == set(NAMES) and
                all(v in STATUSES for v in behavior.values()), 'expected behavior must cover all five checks')
        sampling = expected.get('sampling', {})
        require(isinstance(sampling, dict) and set(sampling) == {'summary', 'events'}, 'invalid sampling sections')
        counts = sampling.get('summary', {})
        require(isinstance(counts, dict) and set(counts) == set(STATUSES) and
                all(type(n) is int and n >= 0 for n in counts.values()), 'invalid sampling summary')
        events = sampling.get('events')
        require(isinstance(events, dict), 'sampling events must be an object')
        for event_id, row in events.items():
            require(isinstance(event_id, str) and event_id and isinstance(row, dict) and
                    set(row) == set(SAMPLING_KEYS), 'invalid expected sampling event')
            require(row['status'] in STATUSES and type(row['zero_exit_with_fatal_log']) is bool and
                    isinstance(row['reasons'], list) and all(isinstance(r, str) for r in row['reasons']) and
                    isinstance(row['fatal_lines'], list) and all(type(n) is int and n > 0 for n in row['fatal_lines']), 'invalid sampling expectation')
        require(counts['not_applicable'] == int(not events) and
                all(counts[status] == sum(e['status'] == status for e in events.values()) for status in STATUSES[:-1]),
                'sampling summary disagrees with expected events')
    return manifest


def verify_files(case, root):
    for name, expected in case['files'].items():
        path = source_path(root, name)
        if expected is None:
            require(not path.exists() and not (root / name).is_symlink(), 'expected absent file appeared: ' + name)
        else:
            require(path.is_file(), 'pinned source missing: ' + name)
            require(digest(path) == expected, 'pinned source hash mismatch: ' + name)


def sampling_observed(result):
    return dict(summary=result['summary'], events={row['event_id']: dict(
        status=row['status'], reasons=row['reasons'], zero_exit_with_fatal_log=row['zero_exit_with_fatal_log'],
        fatal_lines=[e['line'] for e in row['fatal_errors']]) for row in result['runs']})


def evaluate_case(case, root):
    row = dict(id=case['id'], source_kind=case['source_kind'], basis=case['basis'],
               provenance=case['provenance'], record=case['record'], case_dir=case['case_dir'],
               expected=case['expected'], observed=None, differences=[], status='rejected')
    try:
        verify_files(case, root)
        record = load_record(source_path(root, case['record']), source_path(root, case['case_dir']))
        for key, expected in case['record_labels'].items():
            require(record.data.get(key) is expected, 'record label differs: ' + key)
        for artifact in record.data['artifacts']:
            name = (Path(case['case_dir']) / artifact['path']).as_posix()
            require(name in case['files'], 'registered artifact not pinned: ' + name)
        report = audit(record)
        report['candidates'] = candidates(report)
        sampling = diagnose_sampling(record)
        # Reject changes during observation rather than report them as Agent faults.
        verify_files(case, root)
        observed = dict(behavior={c['check_id']: c['status'] for c in report['checks']},
                        sampling=sampling_observed(sampling))
        for section in ('behavior', 'sampling'):
            for key in sorted(set(case['expected'][section]) | set(observed[section])):
                expected, actual = case['expected'][section].get(key), observed[section].get(key)
                if expected != actual:
                    row['differences'].append(dict(section=section, key=key, expected=expected, observed=actual))
        row.update(observed=observed, status='mismatched' if row['differences'] else 'matched',
                   verified_present_files=sum(sha is not None for sha in case['files'].values()),
                   verified_absent_files=sum(sha is None for sha in case['files'].values()))
        return row, report, sampling
    except (RecordError, OSError, ValueError) as exc:
        row['rejection'] = dict(type=type(exc).__name__, message=str(exc),
                                agent_fault_inferred=False)
        return row, None, None


def markdown(result):
    lines = ['# SimClinic 离线诊断基准', '',
             f"基准：`{result['benchmark_id']}`，版本 `{result['benchmark_version']}`；清单 SHA `{result['manifest_sha256']}`。", '',
             '只重新体检已有归档，不执行记录中的命令，不调用模型或求解器。预期判定由案例契约与来源说明固定；匹配不表示新的 CFD 验证或优化收益。', '',
             '| 案例 | 来源类型 | 基准结果 | 五项实际判定 | 采样实际汇总 |',
             '| --- | --- | --- | --- | --- |']
    for row in result['cases']:
        actual = row['observed']
        behavior = ', '.join(f'{k}={v}' for k, v in actual['behavior'].items()) if actual else '未评分'
        sampling = str(actual['sampling']['summary']) if actual else '未评分'
        lines.append(f"| [{row['id']}](cases/{row['id']}/result.json) | {row['source_kind']} | {row['status']} | {behavior} | {sampling} |")
        if row.get('rejection'):
            message = row['rejection']['message'].replace('\n', ' ').replace('|', '\\|')
            lines += ['', f"`{row['id']}` 拒绝：{message}。这是输入/完整性拒绝，不是 Agent 行为发现。", '']
        for difference in row['differences']:
            lines += ['', f"`{row['id']}` 的 `{difference['section']}/{difference['key']}` 与预期不符；详情见案例 result.json。", '']
    lines += ['', '## 汇总', '', json.dumps(result['summary'], ensure_ascii=False), '',
              '| 检查 | 匹配 / 已评分 |', '| --- | ---: |']
    for check_id, score in result['behavior_scores'].items():
        lines.append(f"| {NAMES[check_id]} | {score['matches']} / {score['scored']} |")
    lines += ['', f"采样案例预期匹配：{result['sampling_scores']['matches']} / {result['sampling_scores']['scored']}（包括明确不适用的案例）。", '',
              '合成、注入、缺失导出、自然故障和正常运行分组保留。发现问题是预期结果之一，不等同基准失败；证据不足不合并为通过或错误。', '',
              '匹配率是这些固定案例的回归指标，不估计自然错误率、独立保留成功率或总体优化。候选建议未应用、未复测。', '']
    return '\n'.join(lines)


def run_benchmark(manifest_path, root, out, manifest_sha256=None):
    root, manifest_path, out = Path(root).resolve(), Path(manifest_path).resolve(), Path(out).resolve()
    require(root.is_dir() and manifest_path.is_relative_to(root), 'manifest must be inside the selected source root')
    pin_path = manifest_path.with_suffix('.sha256')
    expected_sha = manifest_sha256 if manifest_sha256 is not None else pin_path.read_text().strip()
    require(valid_sha(expected_sha) and digest(manifest_path) == expected_sha, 'benchmark manifest hash mismatch')
    manifest = validate_manifest(strict_json(manifest_path), root)
    require(not out.exists(), 'benchmark output must be a fresh directory')
    require(not out.is_relative_to(manifest_path.parent), 'output must remain outside the benchmark catalogue')
    for case in manifest['cases']:
        directory = source_path(root, case['case_dir'])
        require(not out.is_relative_to(directory) and not directory.is_relative_to(out), 'output overlaps source case directory')
        for name in case['files']:
            require(not source_path(root, name).is_relative_to(out), 'output overlaps pinned source path')
    evaluations = [evaluate_case(case, root) for case in manifest['cases']]
    require(digest(manifest_path) == expected_sha, 'benchmark manifest changed during evaluation')
    rows = [row for row, _, _ in evaluations]
    summary = dict(total=len(rows), **{status: sum(r['status'] == status for r in rows)
                                     for status in ('matched', 'mismatched', 'rejected')})
    scores = {}
    for check_id in NAMES:
        evaluated = [r for r in rows if r['observed'] is not None]
        scores[check_id] = dict(scored=len(evaluated), matches=sum(
            r['expected']['behavior'][check_id] == r['observed']['behavior'][check_id] for r in evaluated),
            by_expected_state={state: dict(total=sum(r['expected']['behavior'][check_id] == state for r in evaluated),
                matches=sum(r['expected']['behavior'][check_id] == r['observed']['behavior'][check_id] == state for r in evaluated))
                for state in STATUSES})
    groups = {kind: dict(total=sum(r['source_kind'] == kind for r in rows),
        **{status: sum(r['source_kind'] == kind and r['status'] == status for r in rows)
           for status in ('matched', 'mismatched', 'rejected')}) for kind in KINDS}
    evaluated = [r for r in rows if r['observed'] is not None]
    sampling_scores = dict(scored=len(evaluated), matches=sum(
        r['expected']['sampling'] == r['observed']['sampling'] for r in evaluated))
    from simclinic import __version__
    checker_files = ('evaluation/benchmark.py', 'cli.py', 'checks/behavior.py', 'core/record.py',
                     'core/evidence.py', 'advisor/__init__.py', 'domains/openfoam/acceptance.py',
                     'domains/openfoam/sampling_diagnostics.py', 'domains/openfoam/logs.py', 'result_guard.py')
    package = Path(__file__).resolve().parents[1]
    result = dict(schema_version='1.0', benchmark_id=manifest['benchmark_id'], benchmark_version=manifest['version'],
        manifest_sha256=expected_sha, simclinic_version=__version__,
        checker_sha256={name: digest(package / name) for name in checker_files},
        source_root=str(root), source_records_read_only=True, new_model_calls=0, new_simulations=0,
        optimization_effect_proven=False, summary=summary, groups=groups, behavior_scores=scores,
        sampling_scores=sampling_scores, cases=rows,
        inference_scope='Pinned diagnostic regression only; no new CFD/reference validation or heldout effect estimate.')
    # Output is produced only after validation and all read-only evaluations.
    out.mkdir(parents=True)
    for row, report, sampling in evaluations:
        directory = out / 'cases' / row['id']
        write_json(directory / 'result.json', row)
        if report is not None:
            write_report(report, directory / 'audit')
            write_json(directory / 'sampling.json', sampling)
    write_json(out / 'benchmark-summary.json', result)
    (out / 'benchmark-report.md').write_text(markdown(result), encoding='utf-8')
    return result
