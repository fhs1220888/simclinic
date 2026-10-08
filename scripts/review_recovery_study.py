"""Post-run interpretation; preserve the frozen primary score unchanged."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.domains.openfoam.acceptance import solver_measurements
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.domains.openfoam.reference_tasks import stationary_fields,reference_measurements,couette_inlet_order
from simclinic.checks.behavior import matches


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(batch):
    f,p=read(batch/'freeze.json'),read(batch/'progress.json')
    verified=read(batch/'archive-verification.json');digest=sha(batch/'freeze.json')
    if p['status']!='complete' or verified['freeze_sha256']!=digest or digest!=(batch/'freeze.sha256').read_text().strip():
        raise ValueError('Complete frozen batch and prior archive verification required')
    results=read(batch/'results.json');original_results_sha=sha(batch/'results.json')
    rows,differences,recovery_costs=[],[],[]
    for entry in f['entries']:
        out=batch/'runs'/entry['id'];record=load_record(out/'record.json',out);s=read(out/'summary.json')
        a=record.data['attempts'][-1];v=s['acceptance']['values']
        observed=solver_measurements((out/'attempts'/a['run_id']/'logs/log.simpleFoam').read_text(),a['execution']['exit_code'])
        if any(v[k]!=value for k,value in observed.items()):raise ValueError('Solver observation mismatch')
        stationary=stationary_fields(out/'case',entry['spec'],a)
        if stationary!=read(out/'observations/field-stationarity.json'):raise ValueError('Stationarity mismatch')
        couette_inlet_order(out/'case',entry['spec'])
        samples=[ev for ev in record.data['events'] if ev.get('operation')=='sample']
        if samples and samples[-1]['outcome']=='pass':
            folder=out/samples[-1].get('output_archive_root','observations/sampling')
            measured=reference_measurements(folder/'profile.xy',folder/'pressure.xy',entry['spec'])
            if any(abs(v[k]-value)>1e-12 for k,value in measured.items()):raise ValueError('Reference mismatch')
        elif v['profile_error'] is not None or v['pressure_gradient_error'] is not None:
            raise ValueError('Failed sampling published reference values')
        bad=[k for k,passed in s['acceptance']['input_checks'].items() if not passed]
        for name in bad:
            expected=batch/'definitions'/entry['id']/'reference'/name
            actual=out/a['input_snapshot']/name
            text=actual.read_text()
            # This only labels observed tokens. It does not change acceptance
            # or claim general semantic equivalence of OpenFOAM dictionaries.
            normalized,count=re.subn(r'\bnu\s+\[\s*0\s+2\s+-1\s+0\s+0\s+0\s+0\s*\]', 'nu', text)
            with tempfile.TemporaryDirectory() as tmp:
                copy=Path(tmp)/'dictionary';copy.write_text(normalized)
                only_added_dimensions=(name=='constant/physicalProperties' and count==1 and canonical_dictionary(copy)==canonical_dictionary(expected))
            differences.append(dict(id=entry['id'],arm=entry['arm'],scenario=entry['scenario'],path=name,
                generated_path=str(actual.relative_to(batch)),generated_sha256=sha(actual),
                reference_path=str(expected.relative_to(batch)),reference_sha256=sha(expected),
                only_added_nu_dimension_tokens=only_added_dimensions,
                frozen_input_check_pass=False,primary_score_changed=False))
        challenged=[ev for ev in record.data['events'] if ev.get('observation_type')=='explicit_sampling_challenge']
        if entry['scenario']=='transient_missing':
            if len(challenged)!=1 or not samples or samples[0]['outcome']!='fail' or samples[0]['error_category']!='sampling_missing_output':raise ValueError('Declared challenge not observed')
            if entry['arm']=='candidate':
                if len(samples)!=2 or samples[1]['retry_of']!=samples[0]['id']:raise ValueError('Recovery lineage mismatch')
                recovery_costs.append(samples[1]['elapsed_seconds'])
        criteria=s['acceptance']['criteria']
        rows.append(dict(id=entry['id'],stage=entry['stage'],arm=entry['arm'],scenario=entry['scenario'],
            quality_met=s['quality_met'],bad_input_paths=bad,
            numerical_met=all(matches(v[c['id']],c) is True for c in criteria if c['stage']=='numerical'),
            reference_measured=v['profile_error'] is not None,
            reference_met=all(matches(v[c['id']],c) is True for c in criteria if c['stage']=='reference'),
            sample_calls=len(samples),last_sampler_pass=bool(samples and samples[-1]['outcome']=='pass'),
            sampler_recovered=len(samples)==2 and samples[0]['outcome']=='fail' and samples[-1]['outcome']=='pass',
            false_completion=s['false_completion'],retry_finding=s['retry_finding'],
            metrics=s['metrics'],model_calls=s['model_usage']['total_calls'],model_tokens=s['model_tokens']))
    held=[r for r in rows if r['stage']=='heldout']
    groups=results['scoring']['groups']
    recovered=[r for r in held if r['scenario']=='transient_missing' and r['arm']=='candidate']
    elapsed=(datetime.fromisoformat(p['ended_at'])-datetime.fromisoformat(p['started_at'])).total_seconds()
    interpretation=dict(freeze_sha256=digest,analysis_script_sha256=sha(Path(__file__)),
        original_results_sha256=original_results_sha,primary_scoring=results['scoring'],
        rows=rows,input_contract_differences=differences,
        sampling_recovered=sum(r['sampler_recovered'] for r in recovered),sampling_recovery_attempted=len(recovered),
        extra_sampler_seconds=dict(min=min(recovery_costs),median=statistics.median(recovery_costs),max=max(recovery_costs)),
        total_wall_seconds=elapsed,totals=verified['totals'],model_cost=None,
        overall_optimization_proven=False,natural_failure_rate_estimated=False,
        primary_score_changed=False,new_model_calls=0,new_simulations=0,
        source_review=dict(version='Foundation 10',
            constant_viscosity_reader='https://cpp.openfoam.org/v10/constantViscosityModel_8C_source.html',
            dimensioned_value_reader='https://cpp.openfoam.org/v10/dimensionedType_8C_source.html',
            observed_correct_dimension_annotation_supported=True,
            general_dictionary_semantic_comparator_validated=False,model_motive_known=False),
        reason_hypothesis='Model motive is unknown. The fixed-version reader supports the observed optional correct dimensions, while our token comparison rejects them. Validate quantity-aware matching separately before any new freeze; do not regrade this study as success.')
    if sha(batch/'results.json')!=original_results_sha:raise ValueError('Primary score changed')
    write_json(batch/'interpretation.json',interpretation)
    lines=['# 完整模型恢复比较：结论与限制','',
        '4 条开发检查和 24 条保留运行按冻结顺序完成，无选择性重跑。原任务、阈值、两组策略及评分均保持。','',
        '| 保留场景 | 基线完整质量 | 候选完整质量 |','| --- | --- | --- |']
    for scenario,label in (('healthy','正常'),('transient_missing','第一次新采样输出丢失')):
        base,cand=groups[scenario+'/baseline'],groups[scenario+'/candidate']
        lines.append(f"| {label} | {base['quality_pass']}/{base['attempted']} | {cand['quality_pass']}/{cand['attempted']} |")
    lines+=['',f"候选实际采样恢复 {interpretation['sampling_recovered']}/{len(recovered)}；完整质量与采样恢复分别报告。正常质量门槛是否全部满足：{all(groups['healthy/'+arm]['quality_pass']==6 for arm in ('baseline','candidate'))}。预声明条件收益标记：{results['scoring']['conditional_recovery_benefit_observed']}。",'',
        '即使数值或后处理达标，只要输入契约检查未通过，原完整任务成绩仍为失败。不能把辅助分析当成重新评分。','',
        '| 输入不匹配运行 | 文件 | 唯一新增 nu 量纲 token |','| --- | --- | --- |']
    for d in differences:lines.append(f"| {d['id']} | {d['path']} | {d['only_added_nu_dimension_tokens']} |")
    lines+=['','已观测到的差异及路径/哈希保存在 interpretation.json。模型添加量纲的动机未知。', '',
        'Foundation 10 的 [constant viscosity 读取代码](https://cpp.openfoam.org/v10/constantViscosityModel_8C_source.html)为 nu 指定黏度量纲；[dimensioned value 初始化](https://cpp.openfoam.org/v10/dimensionedType_8C_source.html)在可选量纲出现时检查是否相符，再读取数值。这支持本批相同数值、正确显式量纲的兼容写法，与实际求解和参考观测一致。我们的逐 token 匹配拒绝了这一表示；尚未验证通用语义比较器，不能改写冻结评分。', '',
        f"额外采样进程耗时 min/median/max：{min(recovery_costs):.3f}/{statistics.median(recovery_costs):.3f}/{max(recovery_costs):.3f} 秒；复制和核验开销另包含在工作流耗时。数值、模型和耗时逐次保留；只有双方完整质量达标的配对才列耗时/token 差。没有稳定节省或总体优化结论。",'',
        f"全批实际 {verified['totals']['model_calls']} 次模型调用，{verified['totals']['model_tokens']:,} usage token；{verified['totals']['input_snapshots']} 份求解输入快照，{verified['totals']['artifacts']} 个注册证据核验。总运行 {elapsed/60:.1f} 分钟。费用未知。",'',
        f"保留运行错误完成声明：{sum(r['false_completion'] for r in held)}；无效重试检查发现：{sum(r['retry_finding'] for r in held)}。",'',
        '后续先用固定版本真实读取与正负测试验证量纲感知的输入核对，覆盖错误量纲和改变黏度等反例；必要修改双方共用验收适配后重新冻结新参数任务。本批 C1/C2 已转为开发证据，不能再次作为新独立保留集。候选仍只改后处理一次重试，不自动修改物理输入。', '',
        '范围：同一 Couette 机制、两个参数任务、每条件三次、明确注入；不估计自然故障率，不声称统计显著性或总体优化。', '',
        '[原始逐次报告](report.md)、[冻结协议](PROTOCOL.md)、[证据核验](archive-verification.json)、[辅助复核](interpretation.json)。']
    (batch/'INTERPRETATION.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:interpretation[k] for k in ('sampling_recovered','sampling_recovery_attempted','totals','total_wall_seconds')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--batch',type=Path,required=True)
    review(parser.parse_args().batch.resolve())
