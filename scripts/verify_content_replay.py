"""Verify development replay bytes and write its scoped report, without rerunning."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import verify_hashes,velocity_error,pressure_error


def require(condition,message):
    if not condition:raise ValueError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    summaries = {};rows = [];total = 0;original_inputs = None
    for arm,name in (('baseline','baseline-executed'),('candidate','candidate')):
        out = directory/name
        summary = json.loads((out/'summary.json').read_text())
        record = load_record(out/'record.json',out).data
        env = json.loads((out/'environment.json').read_text())
        for artifact in record['artifacts']:
            require(hashlib.sha256((out/artifact['path']).read_bytes()).hexdigest()==artifact['sha256'], 'Evidence changed')
        for name,sha in env['source_manifest']['source_files'].items():
            require(hashlib.sha256((out/'source-package'/name).read_bytes()).hexdigest()==sha,'Native source changed')
        verify_hashes(out/'runtime-source',env['runtime_hashes'])
        provenance = json.loads((out/'replay/provenance.json').read_text())
        if original_inputs is None:original_inputs = provenance['before']
        require(provenance['before']==original_inputs,'Arms started with different input bytes')
        require(set(provenance['before'])==set(provenance['after']),'Input set changed')
        changed = [name for name in provenance['before'] if provenance['before'][name]!=provenance['after'][name]]
        require(changed==([] if arm=='baseline' else ['system/controlDict']),'An unrelated input changed')
        response = json.loads((out/'replay/response.json').read_text())['text']
        require(hashlib.sha256(response.encode()).hexdigest()==provenance['raw_text_sha256'],'Replay text changed')
        require(len(record['attempts'])==1 and not record['model_calls'],'Unexpected execution/model calls')
        attempt = record['attempts'][0]
        native = out/'attempts'/attempt['run_id']/'native'
        manifest = json.loads((native/'input-manifest.json').read_text())
        for name,sha in provenance['after'].items():
            require(manifest['files'][name]==sha,'Recorded input differs from actual executed snapshot')
            require(hashlib.sha256((native/'input-snapshot'/name).read_bytes()).hexdigest()==sha,'Input snapshot changed')
        content = (native/'input-snapshot/system/controlDict').read_text()
        require(content==(response if arm=='baseline' else json.loads(response)['content']), 'Unexpected dictionary rewrite')
        require(summary['heldout'] is False and summary['new_model_calls']==0,'Replay presented as held/model evidence')
        require(summary['completion']['success']==(arm=='candidate'),'Unexpected development outcome')
        if arm=='candidate':
            task = ROOT/'real-runs/heldout-field-selection-20261004/tasks/H2/spec.json'
            spec = json.loads(task.read_text());values = summary['acceptance'];samples = out/'observations/sampling'
            require(abs(velocity_error(samples/'profile.xy',spec)-values['profile_error'])<1e-12,'Velocity reference changed')
            require(abs(pressure_error(samples/'pressure.xy',spec)-values['pressure_gradient_error'])<1e-12,'Pressure reference changed')
            require(values['task_inputs_verified'] and values['rendered_velocity'],'Original task conditions not met')
        summaries[arm] = summary
        total += len(record['artifacts'])
        rows.append(dict(arm=arm,artifacts_verified=len(record['artifacts']),
            actual_Allrun_exit=attempt['execution']['exit_code'],quality_met=summary['completion']['success'],
            executed_solvers=sum(e['kind']=='tool' and e.get('phase')=='solve' for e in record['events'])))
    verification = dict(offline_verification=True,new_model_calls=0,new_simulations=0,
        archived_response_replay=True,development_regression=True,heldout=False,
        verified_artifacts=total,runs=rows,only_candidate_controlDict_changed=True,
        original_inner_dictionary_bytes_preserved=True,optimization_effect_proven=False,
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write_json(directory/'verification.json',verification)
    baseline,candidate = summaries['baseline'],summaries['candidate']
    lines = ['# 文件正文接口开发回归', '',
        '使用 H2-r3-candidate 已归档的真实模型回复，经过原生 initial_write 重放 controlDict；其余输入和 Allrun 来自同一次失败快照。没有新的模型生成或完整 planner 工作流，因此这是开发回归，不是新的保留评估。', '',
        '| 指标 | 原 observed 接口 | 内容接口候选 |','| --- | ---: | ---: |',
        '| Allrun 次数 | 1 | 1 |','| 实际 simpleFoam 次数 | 0 | 1 |',
        '| 首次 Allrun 退出码 | 1 | 0 |','| 完整原任务验收 | 未满足 | 满足 |',
        '| 新模型调用 / reviewer 改写 | 0 / 0 | 0 / 0 |', '',
        '双方 reviewer 上限均设为 0，以隔离第一次执行；这项开发协议不用于估计带 reviewer 的自然完整工作流失败率。', '',
        f"候选速度相对 L2={candidate['acceptance']['profile_error']:.12g}、压力梯度相对偏差={candidate['acceptance']['pressure_gradient_error']:.12g}、局部连续性={candidate['acceptance']['local_continuity']:.12g}，均保持原阈值；生成输入和当前 U PNG 也满足原任务要求。", '',
        '候选只改变 system/controlDict 的 JSON 外层封装；解包后的内层字典与原回复内容逐字一致，其余输入/Allrun 哈希不变。基线重现 file_name 的 blockMesh 解析错误并诚实报告失败。', '',
        '[基线结果](baseline-executed/summary.json) · [候选结果](candidate/summary.json) · [候选体检](candidate/audit/report.md) · [离线核验](verification.json)', '',
        f'已核验 {total} 个注册证据文件哈希、两次执行输入快照、两套源码与归档 runtime，并从真实采样重算任务参考误差。核验没有执行新仿真。', '',
        '这证明已知格式故障可由新初次生成接口处理，未证明自然故障率、耗时/成本或整体 Agent 优化。rewrite、reuse、Allrun 文本和任意 OpenFOAM 语法不在新增接口覆盖范围。', '']
    if (directory/'baseline/setup-failure.json').is_file():
        lines += ['首次尝试启动脚本时，本地沙箱无法访问 Docker socket，尚未运行 Agent、模型或容器；[设置失败记录](baseline/setup-failure.json) 保留，未计作 Agent 故障。随后在允许访问固定容器的环境执行了上述两次开发回归。', '']
    (directory/'report.md').write_text('\n'.join(lines))
    print(json.dumps(verification,indent=2))


if __name__=='__main__':main()
