"""Supplement final workflow outcomes with every execution attempt and model call."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.report import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    progress = json.loads((batch/'progress.json').read_text())
    rows = []
    for entry in progress['runs']:
        if entry['status']=='running':
            continue
        out = Path(entry['directory'])
        if not (out/'record.json').is_file():
            rows.append(dict(id=entry['id'], arm=entry['arm'], attempts=None, failed_attempts=None,
                             solver_calls=None, failed_solver_calls=None, model_calls=None, missing=True))
            continue
        record = json.loads((out/'record.json').read_text())
        tools = [e for e in record['events'] if e['kind']=='tool']
        executions = [a['execution'] for a in record['attempts']]
        failed = [e for e in tools if e.get('exit_code') not in (None,0)]
        solvers = [e for e in tools if e.get('phase')=='solve']
        rows.append(dict(id=entry['id'], arm=entry['arm'], attempts=len(executions),
            failed_attempts=sum(e['exit_code'] not in (None,0) for e in executions),
            unknown_attempt_status=sum(e['exit_code'] is None for e in executions),
            solver_calls=len(solvers), failed_solver_calls=sum(e['exit_code'] not in (None,0) for e in solvers),
            model_calls=len(record['model_calls']), missing=False,
            failed_tools=[dict(id=e['id'], operation=e['operation'], exit_code=e['exit_code'],
                               run_id=e['run_id'], input_version=e['input_version']) for e in failed]))
    groups = {}
    keys = ('attempts','failed_attempts','solver_calls','failed_solver_calls','model_calls')
    for arm in ('baseline','candidate'):
        subset = [r for r in rows if r['arm']==arm]
        groups[arm] = dict(executed_workflows=len(subset), missing_attempt_records=sum(r['missing'] for r in subset))
        for key in keys:
            groups[arm][key] = sum(r[key] for r in subset) if all(r[key] is not None for r in subset) else None
    result = dict(supplement_to_predeclared_final_workflow_outcomes=True, primary_analysis_unchanged=True,
        development_excluded=True, groups=groups, runs=rows,
        interpretation='A recovered workflow remains one planned sample. Its failed attempt and extra consumption are retained; recovery uses the shared policy and is reported separately from the changed interface or field rule.')
    write_json(batch/'attempts.json',result)
    lines = ['# 全部执行尝试与恢复记录', '',
        '这是对最终工作流结果的补充，主评估源码和统计规则未改；开发检查不计入以下数字。', '',
        '| 组别 | 工作流 | Allrun 执行尝试 | 失败执行尝试 | simpleFoam 调用 | 失败 simpleFoam | 模型调用 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm, g in groups.items():
        lines.append(f"| {arm} | {g['executed_workflows']} | {g['attempts']} | {g['failed_attempts']} | {g['solver_calls']} | {g['failed_solver_calls']} | {g['model_calls']} |")
    lines += ['', '| 运行 | 执行尝试 | 失败尝试 | 失败工具 | 模型调用 |', '| --- | ---: | ---: | --- | ---: |']
    for r in rows:
        failures = ', '.join(f"{e['operation']} exit {e['exit_code']}" for e in r.get('failed_tools',[]))
        lines.append(f"| {r['id']} | {r['attempts']} | {r['failed_attempts']} | {failures or '—'} | {r['model_calls']} |")
    lines += ['', '成功恢复的工作流仍是一个预定样本；首次失败不丢弃，额外求解/模型耗时和 token 已包含在主报告的逐次消耗。blockMesh 失败发生在 simpleFoam 之前，不能将其写成求解器已经运行。', '']
    for entry in progress['runs']:
        path = Path(entry['directory'])/'record.json'
        if entry['status']=='running' or not path.is_file():
            continue
        record = json.loads(path.read_text())
        for attempt in record['attempts']:
            path = Path(entry['directory'])/'attempts'/attempt['run_id']/'native/input-snapshot/system/controlDict'
            if attempt['execution']['exit_code'] == 0 or not path.is_file():
                continue
            try:
                envelope = json.loads(path.read_text())
            except ValueError:
                continue
            if isinstance(envelope,dict) and {'file_name','folder_name','content'} <= set(envelope):
                lines += [f"`{entry['id']}` 的失败尝试 `{attempt['run_id']}` 中，controlDict 实际为 JSON 包裹文本，非预期 OpenFOAM 字典。原始证据位于该输入快照及 log.blockMesh；重试处理计划和最终验收位于同一运行目录。", '']
    lines += ['本表按工具退出状态统计执行尝试，不能替代任务验收；退出 0 仍可能伴随致命日志或缺少参考输出。失败原因另见原始输入快照、日志及任务测量。双方共用的恢复机制不是候选修改本身，必须结合冻结的单一差异解释结果。', '']
    (batch/'attempts.md').write_text('\n'.join(lines))
    print(json.dumps(groups,indent=2))


if __name__=='__main__':
    main()
