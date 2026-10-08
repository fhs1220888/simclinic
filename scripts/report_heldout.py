"""All-run descriptive results, with unknown values and failed tasks retained."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.checks.behavior import matches
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import verify_hashes


def distribution(values):
    known = [v for v in values if v is not None]
    return dict(known=len(known), missing=len(values)-len(known),
        mean=statistics.mean(known) if known else None, median=statistics.median(known) if known else None,
        minimum=min(known) if known else None, maximum=max(known) if known else None)


def attempt_metrics(record, failure):
    """No Allrun is distinct from a successful first Allrun; unknown is retained."""
    executions = [attempt['execution'] for attempt in record.get('attempts', [])]
    codes = [execution.get('exit_code') for execution in executions]
    return dict(execution_attempts=len(codes),
        first_execution_failed=codes[0] != 0 if codes and codes[0] is not None else None,
        failed_execution_attempts=sum(code != 0 for code in codes) if all(code is not None for code in codes) else None,
        input_content_rejected=(failure or {}).get('type') == 'InputContentError')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    frozen = json.loads((batch/'freeze.json').read_text())
    if hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest() != (batch/'freeze.sha256').read_text().strip():
        raise ValueError('Freeze changed')
    verify_hashes(batch/'common-runtime', frozen['common_runtime'])
    verify_hashes(batch/'corpus', frozen['corpus'])
    for arm in ('baseline','candidate'):
        verify_hashes(batch/'variants'/arm, frozen['variants'][arm])
    for task_id, expected in frozen['tasks'].items():
        verify_hashes(batch/'tasks'/task_id, expected)
    progress = json.loads((batch/'progress.json').read_text())
    entries = {e['id']:e for e in progress['runs']}
    rows = []
    for scheduled in frozen['schedule']:
        entry = entries.get(scheduled['id'])
        row = dict(scheduled, status=entry['status'] if entry else 'not_run', quality_met=None,
            cfd_met=None, evidence_complete=None, wrong_success_claim=None, invalid_retry=None,
            wrong_render_field=None, failed_criteria=[], metrics={}, artifact_hashes_verified=0,
            execution_attempts=None,first_execution_failed=None,failed_execution_attempts=None,input_content_rejected=None)
        if entry and (Path(entry['directory'])/'summary.json').is_file():
            out = Path(entry['directory'])
            summary = json.loads((out/'summary.json').read_text())
            record = load_record(out/'record.json', out).data
            for artifact in record['artifacts']:
                path = out/artifact['path']
                if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                    raise ValueError('Run evidence changed: '+str(path))
            criteria = record['task']['acceptance']
            values = summary['acceptance']
            cfd = [c for c in criteria if c['id'] not in ('task_inputs_verified','rendered_velocity')]
            cfd_met = all(matches(values.get(c['id']),c) is True for c in cfd)
            checks = summary['checks']
            complete = summary['evidence_complete']
            row.update(quality_met=summary['quality_met'], cfd_met=cfd_met, evidence_complete=complete,
                wrong_success_claim=checks['error_continuation']=='finding' or checks['completion']=='finding' if complete else None,
                invalid_retry=checks['retries']=='finding' if complete else None,
                wrong_render_field=values.get('rendered_velocity') is False if complete else None,
                failed_criteria=[c['id'] for c in criteria if matches(values.get(c['id']),c) is not True],
                metrics=summary['metrics'], actual_model_calls=summary['model_usage']['total_calls'],
                artifact_hashes_verified=len(record['artifacts']), audit_summary=summary['audit_summary'],
                failure=summary['failure'])
            row.update(attempt_metrics(record,summary['failure']))
        elif entry and entry['status'] != 'running':
            row.update(quality_met=False, cfd_met=False, evidence_complete=False)
        rows.append(row)
    groups = {}
    for arm in ('baseline','candidate'):
        observed = [r for r in rows if r['arm']==arm and r['status'] not in ('not_run','running')]
        planned = sum(r['arm']==arm for r in rows)
        groups[arm] = dict(planned=planned, executed=len(observed), not_executed=planned-len(observed),
            quality_successes=sum(r['quality_met'] is True for r in observed),
            task_failures=sum(r['quality_met'] is not True for r in observed),
            cfd_successes=sum(r['cfd_met'] is True for r in observed),
            evidence_missing=sum(r['evidence_complete'] is not True for r in observed),
            wrong_success_claims=sum(r['wrong_success_claim'] is True for r in observed),
            invalid_retry_runs=sum(r['invalid_retry'] is True for r in observed),
            wrong_render_field_runs=sum(r['wrong_render_field'] is True for r in observed),
            first_execution_failures=sum(r['first_execution_failed'] is True for r in observed),
            first_execution_status_unknown=sum(r['first_execution_failed'] is None for r in observed),
            execution_attempts=sum(r['execution_attempts'] for r in observed) if all(r['execution_attempts'] is not None for r in observed) else None,
            failed_execution_attempts=sum(r['failed_execution_attempts'] for r in observed) if all(r['failed_execution_attempts'] is not None for r in observed) else None,
            input_content_rejections=sum(r['input_content_rejected'] is True for r in observed),
            input_content_rejection_unknown=sum(r['input_content_rejected'] is None for r in observed),
            behavior_metric_unknown=sum(r['wrong_success_claim'] is None for r in observed),
            metrics={key:distribution([r['metrics'].get(key) for r in observed])
                     for key in ('wall_seconds','solver_seconds','model_tokens','model_cost')})
    eligible = []
    for task_id in sorted({r['task'] for r in rows}):
        for repeat in sorted({r['repeat'] for r in rows if r['task']==task_id}):
            pair = [r for r in rows if r['task']==task_id and r['repeat']==repeat]
            if len(pair) == 2 and all(r['quality_met'] is True and r['evidence_complete'] is True for r in pair):
                eligible.append(dict(task=task_id,repeat=repeat))
    scope = frozen.get('inference_scope', 'Only explicit U render-field selection on these declared channel tasks; all other policies shared.')
    planned_runs = len(rows)
    result = dict(experiment=frozen['experiment'], status=progress['status'], planned_runs=planned_runs,
        executed_runs=sum(g['executed'] for g in groups.values()), groups=groups, runs=rows,
        quality_and_evidence_eligible_pairs=eligible, total_model_tokens_including_development=progress['consumed_model_tokens'],
        development_excluded_from_held_results=True, freeze_integrity_verified=True,
        inference_scope=scope,
        optimization_effect_proven=False,
        limitations=['Exploratory sample: three parameter variations of one channel, three repeats per arm.',
            'No reliable model seed or provider snapshot; serial counterbalanced order reduces but does not eliminate time drift.',
            'The baseline is a controlled policy ablation, not unmodified upstream.',
            'Task failure and missing evidence retained; zero wrong success claims from shared gate is not candidate-specific improvement.',
            'All-run consumption distributions are descriptive; speed/cost improvement requires both-arm quality and complete evidence.',
            'Rendering fallback, natural stale-result failures, invalid retry improvement and all-patch aggregate effects are outside this study.'])
    write_json(batch/'results.json', result)
    lines = ['# 冻结版本的保留算例评估', '', f"状态：`{progress['status']}`；完成 {result['executed_runs']}/{planned_runs} 次保留运行。开发检查另列、不计入保留结果。", '',
        scope+' 两组其余源码、模型/语料、资源、观测与完成验收相同。基线为受控比较，不是未修改上游。', '',
        '| 组别 | 已运行/计划 | 任务质量通过 | CFD 条件通过 | 证据缺失 | 错误成功声明 | 重复重试发现 | U 图验收未满足 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm, g in groups.items():
        lines.append(f"| {arm} | {g['executed']}/{g['planned']} | {g['quality_successes']} | {g['cfd_successes']} | {g['evidence_missing']} | {g['wrong_success_claims']} | {g['invalid_retry_runs']} | {g['wrong_render_field_runs']} |")
    lines += ['', '## 所有执行尝试与初次格式拒绝', '',
        '| 组别 | Allrun 尝试 | 失败尝试 | 首次 Allrun 失败 | 首次状态未知/未执行 | InputContentError 终止 | 格式拒绝状态未知 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm, g in groups.items():
        lines.append(f"| {arm} | {g['execution_attempts']} | {g['failed_execution_attempts']} | {g['first_execution_failures']} | {g['first_execution_status_unknown']} | {g['input_content_rejections']} | {g['input_content_rejection_unknown']} |")
    lines += ['', '未执行 Allrun 不算首次执行成功；前置格式拒绝也不算完整任务成功。每次失败及恢复的消耗都包含在逐次数据中。', '']
    lines += ['', '行为指标未知数量：'+', '.join(f"{arm}={g['behavior_metric_unknown']}" for arm,g in groups.items())+'；未知不计为零错误。', '',
        '## 逐次结果', '', '| 运行 | 状态 | 任务通过 | 未满足/未知条件 | 模型 token |', '| --- | --- | --- | --- | ---: |']
    for r in rows:
        lines.append(f"| {r['id']} | {r['status']} | {r['quality_met']} | {', '.join(r['failed_criteria']) or '—'} | {r['metrics'].get('model_tokens')} |")
    lines += ['', '## 消耗（所有已运行样本的描述值）', '', '| 组别/指标 | 已知/缺失 | 均值 | 中位数 | 最小–最大 |', '| --- | --- | --- | --- | --- |']
    for arm, g in groups.items():
        for key, d in g['metrics'].items():
            lines.append(f"| {arm}/{key} | {d['known']}/{d['missing']} | {d['mean']} | {d['median']} | {d['minimum']}–{d['maximum']} |")
    lines += ['', f"同时质量通过且证据完整的配对数：{len(eligible)}。只有这些配对可用于质量达标前提下的消耗比较；当前不声明加速或成本下降。", '',
        f"包含开发检查的实际模型 usage：{progress['consumed_model_tokens']} token；成本未知保持 null。", '',
        '## 判断范围', '', '全部运行的记录与 Markdown/JSON 体检位于 `runs/<运行 ID>`；逐次状态和模型消耗在 progress.json。冻结依据在 freeze.json、freeze.sha256 和 arm-difference.patch。', '',
        scope+' 不能证明整体 Agent 优化、自然业务失败率下降或统计显著效果。共同门禁产生的诚实失败不归因于候选改动。', '',
        '没有可靠模型 seed/后端快照，存在时间漂移；三项任务共享同一通道几何，重复不等于独立领域覆盖。失败和缺失没有剔除。未启用模型渲染回退，未测试全部补丁的综合效应。', '']
    (batch/'report.md').write_text('\n'.join(lines))
    print('REPORT', result['executed_runs'], '/', planned_runs, 'runs;', json.dumps({a:g['quality_successes'] for a,g in groups.items()}), flush=True)


if __name__ == '__main__':
    main()
