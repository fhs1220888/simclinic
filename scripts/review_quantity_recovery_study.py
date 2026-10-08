"""Independent post-run interpretation of the frozen quantity-policy study.

No solver/model execution, reruns, rescoring, or edits to primary outcomes.
"""
import argparse
from datetime import datetime
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.domains.openfoam.acceptance import solver_measurements
from simclinic.domains.openfoam.reference_tasks import stationary_fields, reference_measurements, couette_inlet_order
from simclinic.domains.openfoam.quantity_inputs import compare_case, POLICY
from simclinic.evaluation.heldout import verify_hashes
from simclinic.evaluation.quantity_recovery_study import outcomes
from simclinic.checks.behavior import matches
from simclinic.domains.openfoam.completion import canonical_dictionary


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inlet_precision_review(actual, expected, spec):
    """Derived numeric evidence only; never change the frozen input decision."""
    number = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
    def vectors(path):
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.DOTALL)
        west = re.search(r'\bwest\s*\{([^{}]*)\}', text)
        if west is None:
            raise ValueError('West entry unsupported by derived reviewer')
        field = re.search(r'\bvalue\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\)\s*;', west[1], re.DOTALL)
        if field is None or int(field[1]) != spec['ny']:
            raise ValueError('West list count differs')
        rows = re.findall(r'\(\s*('+number+r')\s+('+number+r')\s+('+number+r')\s*\)', field[2])
        if len(rows) != spec['ny'] or re.sub(r'\(\s*'+number+r'\s+'+number+r'\s+'+number+r'\s*\)', '', field[2]).strip():
            raise ValueError('West vector literals unsupported')
        if any(Fraction(row[1]) != 0 or Fraction(row[2]) != 0 for row in rows):
            raise ValueError('Transverse velocity changed')
        return [Fraction(row[0]) for row in rows]
    found, wanted = vectors(actual), vectors(expected)
    exact = [Fraction(str(spec['wall_speed'])) * Fraction(2*i+1, 2*spec['ny']) for i in range(spec['ny'])]
    x, y = canonical_dictionary(actual), canonical_dictionary(expected)
    canonical_differences = []
    if len(x) == len(y):
        canonical_differences = [dict(token_index=i, actual=a, expected=b) for i,(a,b) in enumerate(zip(x,y)) if a!=b]
    return dict(actual_sha256=sha(actual), expected_sha256=sha(expected),
        exact_basis='wall_speed*(i+0.5)/ny computed as rational numbers',
        max_velocity_delta_m_per_s=float(max(abs(a-b) for a,b in zip(found,wanted))),
        max_actual_error_from_exact_m_per_s=float(max(abs(a-b) for a,b in zip(found,exact))),
        max_template_error_from_exact_m_per_s=float(max(abs(a-b) for a,b in zip(wanted,exact))),
        actual_at_least_as_close_to_exact_at_every_inlet_face=all(abs(a-e)<=abs(b-e) for a,b,e in zip(found,wanted,exact)),
        canonical_token_differences=canonical_differences,
        primary_score_changed=False, general_dictionary_equivalence_claimed=False)


def review(batch):
    f, p = read(batch/'freeze.json'), read(batch/'progress.json')
    verified = read(batch/'archive-verification.json')
    digest, primary_sha = sha(batch/'freeze.json'), sha(batch/'results.json')
    if p['status'] != 'complete' or verified['freeze_sha256'] != digest or digest != (batch/'freeze.sha256').read_text().strip() or f['input_matching_policy'] != POLICY:
        raise ValueError('Complete frozen comparison and prior archive verification required')
    for folder, key in (('common-runtime','common_runtime'), ('native','native'), ('corpus','corpus'), ('definitions','definitions')):
        verify_hashes(batch/folder, f[key])
    # Root analysis must use exactly the reader/matcher validated and frozen.
    for name in ('simclinic/domains/openfoam/reference_tasks.py', 'simclinic/domains/openfoam/quantity_inputs.py', 'simclinic/evaluation/quantity_recovery_study.py'):
        if sha(ROOT/name) != sha(batch/'common-runtime'/name):
            raise ValueError('Analysis policy differs from frozen runtime')
    results = read(batch/'results.json')
    summaries, rows, retry_seconds, precision_reviews = [], [], [], []
    for e in f['entries']:
        out = batch/'runs'/e['id']
        record, s = load_record(out/'record.json', out), read(out/'summary.json')
        summaries.append(s)
        a, v = record.data['attempts'][-1], s['acceptance']['values']
        observed = solver_measurements((out/'attempts'/a['run_id']/'logs/log.simpleFoam').read_text(), a['execution']['exit_code'])
        if any(v[k] != value for k, value in observed.items()):
            raise ValueError('Solver observations differ')
        stable = stationary_fields(out/'case', e['spec'], a)
        if stable != read(out/'observations/field-stationarity.json') or v['steady_evidence'] != (observed['steady_converged'] or stable['field_stationary'] is True):
            raise ValueError('Steady evidence differs')
        couette_inlet_order(out/'case', e['spec'])
        checks, detail = compare_case(out/a['input_snapshot'], batch/'definitions'/e['id']/'reference')
        if checks != s['acceptance']['input_checks']:
            raise ValueError('Quantity input decisions differ')
        if checks.get('0/U') is False:
            diagnostic = inlet_precision_review(out/a['input_snapshot']/'0/U', batch/'definitions'/e['id']/'reference/0/U', e['spec'])
            precision_reviews.append(dict(id=e['id'], arm=e['arm'], scenario=e['scenario'], **diagnostic))
        samples = [ev for ev in record.data['events'] if ev.get('operation') == 'sample']
        if samples and samples[-1]['outcome'] == 'pass':
            folder = out/samples[-1].get('output_archive_root', 'observations/sampling')
            measured = reference_measurements(folder/'profile.xy', folder/'pressure.xy', e['spec'])
            if any(abs(v[k]-value) > 1e-12 for k, value in measured.items()):
                raise ValueError('Reference observations differ')
        elif v['profile_error'] is not None or v['pressure_gradient_error'] is not None:
            raise ValueError('Failed sampling published reference values')
        if e['scenario'] == 'transient_missing':
            challenged = [ev for ev in record.data['events'] if ev.get('observation_type') == 'explicit_sampling_challenge']
            if len(challenged) != 1 or not samples or samples[0]['outcome'] != 'fail' or samples[0]['exit_code'] != 0 or samples[0]['error_category'] != 'sampling_missing_output':
                raise ValueError('Predeclared missing-output intervention not observed')
            if e['arm'] == 'candidate':
                if len(samples) != 2 or samples[1]['retry_of'] != samples[0]['id']:
                    raise ValueError('Recovery lineage differs')
                retry_seconds.append(samples[1]['elapsed_seconds'])
        criteria = s['acceptance']['criteria']
        rows.append(dict(id=e['id'], stage=e['stage'], arm=e['arm'], scenario=e['scenario'],
            quality_met=s['quality_met'], numerical_met=all(matches(v[c['id']], c) is True for c in criteria if c['stage']=='numerical'),
            reference_met=all(matches(v[c['id']], c) is True for c in criteria if c['stage']=='reference'),
            input_check_values=checks, input_comparison=detail,
            explicit_nu_dimensions=detail['files']['constant/physicalProperties'].get('explicit_dimensions'),
            sample_calls=len(samples), sampling_recovered=len(samples)==2 and samples[0]['outcome']=='fail' and samples[-1]['outcome']=='pass',
            false_completion=s['false_completion'], retry_finding=s['retry_finding'], metrics=s['metrics'],
            model_calls=s['model_usage']['total_calls'], model_tokens=s['model_tokens']))
    scoring = outcomes(summaries, True)
    if scoring != results['scoring'] or scoring != verified['scoring']:
        raise ValueError('Frozen primary outcome differs')
    held = [r for r in rows if r['stage']=='heldout']
    recoveries = [r for r in held if r['arm']=='candidate' and r['scenario']=='transient_missing']
    pairs = [pair for pair in results['pairs'] if pair['both_quality_pass']]
    differences = {k:[pair['differences_candidate_minus_baseline'][k] for pair in pairs] for k in ('wall_seconds','solver_seconds','model_tokens')}
    distribution = {k:dict(n=len(values), min=min(values), median=statistics.median(values), max=max(values), less=sum(x<0 for x in values), equal=sum(x==0 for x in values), more=sum(x>0 for x in values)) for k, values in differences.items() if values}
    elapsed = (datetime.fromisoformat(p['ended_at'])-datetime.fromisoformat(p['started_at'])).total_seconds()
    interpretation = dict(freeze_sha256=digest, analysis_script_sha256=sha(Path(__file__)), primary_results_sha256=primary_sha,
        primary_scoring=scoring, rows=rows, totals=verified['totals'], total_wall_seconds=elapsed,
        inlet_precision_reviews=precision_reviews,
        sampling_recovered=sum(r['sampling_recovered'] for r in recoveries), sampling_recovery_attempted=len(recoveries),
        extra_sampler_seconds=dict(min=min(retry_seconds), median=statistics.median(retry_seconds), max=max(retry_seconds)) if retry_seconds else None,
        both_quality_pass_pair_count=len(pairs), paired_differences_candidate_minus_baseline=distribution,
        new_model_calls=0, new_simulations=0, primary_score_changed=False, model_cost=None,
        natural_failure_rate_estimated=False, overall_optimization_proven=False, statistical_significance_claimed=False)
    if sha(batch/'results.json') != primary_sha:
        raise ValueError('Primary results changed')
    write_json(batch/'interpretation.json', interpretation)
    lines = ['# 新量纲核对下的完整模型恢复比较', '',
        '四条开发检查及 24 条保留运行按原冻结顺序完成；没有续跑、选择性重跑或修改物理输入/质量阈值。两组共用新输入核对，唯一候选差异为一次有限重采样。', '',
        '| 保留情景 | 基线完整质量 | 候选完整质量 |', '| --- | --- | --- |']
    for scenario, label in (('healthy', '正常'), ('transient_missing', '首次新采样输出丢失')):
        b, c = scoring['groups'][scenario+'/baseline'], scoring['groups'][scenario+'/candidate']
        lines.append(f"| {label} | {b['quality_pass']}/{b['attempted']} | {c['quality_pass']}/{c['attempted']} |")
    lines += ['', f"预声明条件恢复收益标记：{scoring['conditional_recovery_benefit_observed']}。候选采样恢复 {interpretation['sampling_recovered']}/{len(recoveries)}；采样成功与完整质量分别报告。", '',
        f"错误完成声明 {sum(r['false_completion'] for r in held)}；无效重试发现 {sum(r['retry_finding'] for r in held)}。全部模型与失败消耗保留，费用未知。", '',
        f"共 {verified['totals']['model_calls']} 次模型调用、{verified['totals']['model_tokens']:,} usage token、{verified['totals']['input_snapshots']} 份求解输入快照、{verified['totals']['artifacts']} 个注册证据；实际 {elapsed/60:.1f} 分钟。", '']
    if retry_seconds:
        lines += [f"额外采样进程秒 min/median/max：{min(retry_seconds):.3f}/{statistics.median(retry_seconds):.3f}/{max(retry_seconds):.3f}。复制与身份核验开销另计入工作流时间。", '']
    lines += [f"仅双方完整质量达标的 {len(pairs)} 对用于耗时/token 差；注入失败的基线没有达标完成耗时，不能用于配对加速结论。", '',
        '| 候选减基线 | 对数 | min | median | max | 更少/相同/更多 |', '| --- | --- | --- | --- | --- | --- |']
    for k, value in distribution.items():
        lines.append(f"| {k} | {value['n']} | {value['min']} | {value['median']} | {value['max']} | {value['less']}/{value['equal']}/{value['more']} |")
    if not distribution:
        lines = lines[:-2]
        lines += ['完整质量达标配对为零，不计算配对耗时或 token 差。']
    if precision_reviews:
        lines += ['', '入口数值表示的辅助复核：原模板以 12 位有效数字保存速度，原比较器分别取整到 12 位小数；更高精度的同一公式数值可能跨过取整边界。下表以精确有理数公式独立核对入口速度，原任务失败成绩保持，不把辅助分析当作重新评分。', '',
            '| 运行 | 最大输入速度差 m/s | 实际值相对公式最大误差 m/s | 模板相对公式最大误差 m/s | 实际每个值至少一样接近公式 |', '| --- | --- | --- | --- | --- |']
        for item in precision_reviews:
            lines.append(f"| {item['id']} | {item['max_velocity_delta_m_per_s']:.3e} | {item['max_actual_error_from_exact_m_per_s']:.3e} | {item['max_template_error_from_exact_m_per_s']:.3e} | {item['actual_at_least_as_close_to_exact_at_every_inlet_face']} |")
    lines += ['', '两个 Couette 参数任务、每条件三次、明确输出丢失注入。本批结论仅限这种故障与已固定工作流；不估计自然故障率，不声称统计显著、普遍 CFD 改善、稳定消耗节省或总体优化。新量纲核对是双方共用评估器修复，不计为候选 Agent 的差异。历史批次主评分原样保留。', '',
        '后续可先整理这一有限恢复策略的可应用补丁、证据与复测边界；进一步验证应另冻结未使用任务、自然失败观测及其他可恢复故障，C3/C4 此后只能作为开发数据。', '',
        '[逐次报告](report.md)、[冻结协议](PROTOCOL.md)、[证据核验](archive-verification.json)、[独立复核](interpretation.json)。']
    (batch/'INTERPRETATION.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:interpretation[k] for k in ('sampling_recovered','sampling_recovery_attempted','totals','total_wall_seconds')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    review(parser.parse_args().batch.resolve())
