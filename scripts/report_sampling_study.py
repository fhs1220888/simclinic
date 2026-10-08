"""Report task quality and verified sampling diagnostics as separate outcomes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.evaluation.sampling_study import verify_sampling_freeze
from simclinic.result_guard import read_verified_json


def sampling_row(out, entry):
    summary = json.loads((out / 'summary.json').read_text())
    record = load_record(out / 'record.json', out)
    diagnosed = diagnose_sampling(record)
    events = {e['id']: e for e in record.data['events']}
    rows = []
    for observed in diagnosed['runs']:
        event = events[observed['event_id']]
        gate = None
        if event.get('source_artifact'):
            gate = read_verified_json(record, event['source_artifact'], observed['run_id'], observed['input_version'])
            if gate.get('policy') != 'sampling-gate-v1':
                raise ValueError('Unexpected gate assessment policy')
        rows.append(dict(event_id=event['id'], exit_code=observed['exit_code'], outcome=event['outcome'],
            error_category=event.get('error_category'), verified_fatal_errors=observed['fatal_errors'],
            fatal_failure_correctly_reported=event['outcome'] == 'fail' if observed['fatal_errors'] else None,
            output_byte_evidence=observed['outputs'], offline_evidence_status=observed['status'],
            candidate_node_invoked=gate is not None,
            candidate_failure_branch_invoked=gate is not None and gate['outcome'] != 'pass'))
    return dict(id=entry['id'], arm=entry['arm'], task=entry['task'], quality_met=summary['quality_met'],
                completed_graph=summary['completed_graph'], evidence_complete=summary['evidence_complete'],
                model_calls=summary['model_usage']['total_calls'], model_tokens=summary['model_usage']['total_tokens'],
                acceptance_error=summary.get('acceptance_error'), sampling=rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    frozen, digest = verify_sampling_freeze(batch)
    progress = json.loads((batch / 'progress.json').read_text())
    if progress['freeze_sha256'] != digest:
        raise ValueError('Progress belongs to another freeze')
    subprocess.run([sys.executable, str(ROOT / 'scripts/report_heldout.py'), '--batch', str(batch)], check=True)
    groups = {}
    for group in ('development', 'runs'):
        rows = []
        for entry in progress[group]:
            expected = batch / group / entry['id']
            if Path(entry['directory']).resolve() != expected.resolve():
                raise ValueError('Unexpected run directory')
            if entry['status'] != 'running' and (expected / 'summary.json').is_file():
                rows.append(sampling_row(expected, entry))
        groups[group] = rows
    held = [r for row in groups['runs'] for r in row['sampling']]
    output = dict(experiment=frozen['experiment'], status=progress['status'], freeze_sha256=digest,
                  development_excluded=True, development=groups['development'], held=groups['runs'],
                  held_verified_fatal_events=sum(bool(r['verified_fatal_errors']) for r in held),
                  held_candidate_node_invocations=sum(r['candidate_node_invoked'] for r in held),
                  held_candidate_failure_branches=sum(r['candidate_failure_branch_invoked'] for r in held),
                  optimization_effect_proven=False, new_model_calls=0, new_simulations=0,
                  interpretation='Candidate node invocation alone is not a diagnostic improvement. No natural failures means effect frequency remains undetermined.')
    write_json(batch / 'sampling-results.json', output)
    lines = ['# 采样策略冻结评估', '',
             f"状态：`{progress['status']}`；保留运行 {len(progress['runs'])}/{frozen['planned_runs']}，开发检查另列。", '',
             frozen['inference_scope'], '', '## 完整模型开发检查（不计入保留结果）', '',
             '| 运行 | 完整任务 | 证据完整 | 模型调用 | usage token | 采样工具状态 |',
             '| --- | --- | --- | ---: | ---: | --- |']
    for row in groups['development']:
        states = ', '.join(s['outcome'] for s in row['sampling']) or '未采样'
        lines.append(f"| {row['id']} | {row['quality_met']} | {row['evidence_complete']} | {row['model_calls']} | {row['model_tokens']} | {states} |")
    lines += ['', '## 保留采样指标', '',
              f"经核验的自然 fatal 采样事件 {output['held_verified_fatal_events']}；候选完成节点调用 {output['held_candidate_node_invocations']}，候选失败分支触发 {output['held_candidate_failure_branches']}。", '',
              '未运行的任务没有结果，未知日志/缺失归档输出不判作 Agent 错误。失败标签更准确不等于任务成功。没有自然采样故障时，无法估计该门禁的自然诊断收益。', '',
              '[任务质量与消耗](report.md)、[采样明细](sampling-results.json)、[冻结协议](freeze.json)和 [进度](progress.json)。', '',
              '开发与保留分两次显式 coordinator 调用；开发检查未通过、用量未知或达到预算边界时停止。两组原生源码相同、完成策略不同。基线是受控策略对照，双方保留旧/新失败和全部消耗。总体优化效果尚未证明。', '']
    (batch / 'sampling-report.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
