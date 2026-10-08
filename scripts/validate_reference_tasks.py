"""Frozen, zero-model validation of two mechanisms on two mesh resolutions."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.report import write_json, write_report
from simclinic.core.record import load_record
from simclinic.cli import get_report
from simclinic.session import SessionRecorder, plain
from simclinic.evaluation.heldout import hashes, verify_hashes
from simclinic.evaluation.idle_guard import start_idle_guard
from simclinic.domains.openfoam.reference_tasks import (
    DEVELOPMENT_TASKS, create_reference, task, ReferenceTaskCompletion,
    reference_measurements, couette_inlet_order, stationary_fields)
from simclinic.checks.behavior import matches
from simclinic.domains.openfoam.sampling_recovery import RecoveringReferenceCompletion
from simclinic.evaluation.sampling_challenge import sampling_challenge
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.domains.openfoam.acceptance import solver_measurements


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_freeze(batch):
    f = read(batch / 'freeze.json')
    digest = sha(batch / 'freeze.json')
    if digest != (batch / 'freeze.sha256').read_text().strip():
        raise ValueError('Freeze changed')
    for folder, key in (('common-runtime', 'common_runtime'), ('native', 'native'), ('definitions', 'definitions')):
        verify_hashes(batch / folder, f[key])
    return f, digest


def freeze(batch):
    from scripts.run_full_agent import IMAGE, CORPUS
    if batch.exists():
        raise ValueError('Preserve prior batches')
    native = ROOT / 'integration/foam_agent_v210/native/content'
    manifest = read(native / 'candidate-manifest.json')
    for name, expected in manifest['source_files'].items():
        if sha(native / name) != expected:
            raise ValueError('Native source changed: ' + name)
    if sha(ROOT / 'simclinic/input_content.py') != manifest['runtime_requirements']['input_content_sha256']:
        raise ValueError('Content helper changed')
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]
    batch.mkdir(parents=True)
    common = batch / 'common-runtime'
    shutil.copytree(ROOT / 'simclinic', common / 'simclinic', ignore=shutil.ignore_patterns('__pycache__'))
    (common / 'scripts').mkdir()
    shutil.copy2(Path(__file__), common / 'scripts' / Path(__file__).name)
    shutil.copytree(native, batch / 'native', ignore=shutil.ignore_patterns('__pycache__'))
    schedule = []
    for base in DEVELOPMENT_TASKS:
        for factor, label in ((1, 'coarse'), (2, 'fine')):
            spec = dict(base, nx=base['nx']*factor, ny=base['ny']*factor)
            run_id = base['id'] + '-' + label
            folder = batch / 'definitions' / run_id
            create_reference(CORPUS / 'reference-case', folder / 'reference', spec)
            write_json(folder / 'spec.json', spec)
            write_json(folder / 'task.json', task(spec))
            schedule.append(run_id)
    predecessor = ROOT / 'real-runs/new-reference-development-20261005'
    previous = None
    if predecessor.exists():
        old = read(predecessor / 'progress.json')
        if old['status'] != 'reference_contract_failed':
            raise ValueError('Expected preserved failed predecessor')
        previous = dict(directory=str(predecessor), freeze_sha256=sha(predecessor/'freeze.json'),
            status=old['status'], reason='Accurate zero-gradient Couette result lacked the required SIMPLE marker; preserve failure, add explicit new-task full-field stationarity criterion and output cadence')
    f = dict(experiment='new-analytic-reference-development-v2', frozen_at=datetime.now(timezone.utc).isoformat(),
        schedule=schedule, heldout=False, full_agent_workflow=False, graph_entrypoint='local_runner',
        new_model_calls=0, archived_output_or_reply_reuse=False, synthetic=False,
        image=IMAGE, image_id=image['Id'], python=sys.version, executable=sys.executable,
        installed_dependencies=subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines(),
        common_runtime=hashes(common), native=hashes(batch / 'native'), definitions=hashes(batch / 'definitions'),
        predecessor=previous, original_solver_and_residual_controls_preserved=True,
        output_cadence_change=dict(previous=2000, current=100, purpose='Two independently registered positive-time full-field states'),
        numerical_contract_change='New tasks use SIMPLE marker OR full-field U/p stationarity <=1e-7; original marker stays a separate observed value; original old task contracts unchanged',
        resources=dict(cpus=2, memory='2g', platform='linux/amd64', network='none'),
        solver_timeout_seconds=180, sampling_timeout_seconds=120, review_limit=0, model_render_fallback_limit=0,
        idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256=sha('/usr/bin/caffeinate')),
        reference_basis=[dict(url='https://www.mech.kth.se/~henning/CFD/CFD_main.pdf',
            title='Henningson and Berggren: Fluid Dynamics, Theory and Computation', page_index=123,
            fact='Plane Couette u=U*y/h, v=w=0, zero pressure gradient'),
            dict(url='https://doc.cfd.direct/openfoam/user-guide-v10/boundaries',
                 fact='Foundation 10 fixedValue, zeroGradient and empty boundary definitions')],
        uniform_reference_derivation='Constant vector U and constant kinematic p make divergence, convection, pressure gradient and viscous Laplacian zero; direct substitution, not tabulated CFD truth',
        acceptance_predeclared=True, pressure_reference_zero=True,
        stopping='Serial once-only four runs; stop any failed task/setup, preserve partial outcomes; no auto tuning or restart',
        scope='Trusted-input native solve/U rendering/registered completion on two new mechanisms, two meshes; neither new planner compatibility nor optimization proven',
        optimization_effect_proven=False)
    write_json(batch / 'freeze.json', f)
    (batch / 'freeze.sha256').write_text(sha(batch / 'freeze.json') + '\n')
    print('Frozen four zero-model reference runs:', batch, flush=True)


def run(batch, run_id):
    f, digest = verify_freeze(batch)
    if ROOT != batch / 'common-runtime' or run_id not in f['schedule']:
        raise ValueError('Use frozen driver and known run')
    p = read(batch / 'progress.json')
    if p['status'] != 'running' or p['freeze_sha256'] != digest or p['runs'][-1]['id'] != run_id or p['runs'][-1]['status'] != 'running':
        raise ValueError('Run must be registered by coordinator')
    if sys.version != f['python'] or subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines() != f['installed_dependencies']:
        raise ValueError('Python/runtime changed')
    if json.loads(subprocess.check_output(['docker', 'image', 'inspect', f['image']], text=True))[0]['Id'] != f['image_id']:
        raise ValueError('Container changed')
    definition = batch / 'definitions' / run_id
    spec, declared_task = read(definition / 'spec.json'), read(definition / 'task.json')
    out = batch / 'runs' / run_id
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(batch / 'native', out / 'source-package')
    shutil.copytree(definition / 'reference', out / 'case')
    sys.path.insert(0, str(out / 'source-package/src'))
    from main import create_foam_agent_graph
    from config import Config
    from services import configure_llm
    from utils import read_case_foamfiles, scan_case_directory
    from logger import close_logging
    class ForbiddenModel:
        def invoke(self, *args, **kwargs):
            raise AssertionError('Reference validation forbids model calls')
    backend = ForbiddenModel()
    configure_llm(backend)
    environment = dict(freeze_sha256=digest, image=f['image'], image_id=f['image_id'],
        distribution='OpenFOAM Foundation', version='10', solver='simpleFoam', graph_entrypoint='local_runner',
        resources=f['resources'], reference_contract=declared_task['reference_contract'])
    write_json(out / 'environment.json', environment)
    recorder = SessionRecorder(out, declared_task, environment,
        dict(name='Foam-Agent analytic reference development', commit='bfb7f791f77438ff90c1a39ff398739ee97bcc67', full_agent_workflow=False))
    entry = next((e for e in f.get('entries', []) if e['id'] == run_id), {})
    scenario, arm = entry.get('scenario','healthy'), entry.get('arm','baseline')
    recorder.data.update(heldout=False, fault_injection=scenario!='healthy', development_regression=True, new_model_calls=0,
                         authored_inputs=True, archived_solver_output_reuse=False)
    os.environ.update(SIMCLINIC_DOCKER_IMAGE=f['image'], SIMCLINIC_OBSERVED_EXECUTION='1', LANGCHAIN_TRACING_V2='false')
    config = Config(case_dir=str(out / 'case'), max_loop=0, max_time_limit=180, recursion_limit=30)
    config.visualization_max_loop = 0
    initial = dict(case_dir=str(out / 'case'), config=config, user_requirement=declared_task['objective'],
        llm_service=backend, foamfiles=read_case_foamfiles(str(out / 'case')),
        dir_structure=scan_case_directory(str(out / 'case')), loop_count=0, input_writer_mode='initial',
        requires_hpc=False, requires_visualization=True, error_logs=[])
    result, failure, completion = {}, None, None
    try:
        policy = RecoveringReferenceCompletion if arm=='candidate' else ReferenceTaskCompletion
        completion = policy(recorder, definition / 'reference', f['image'], spec)
        graph = create_foam_agent_graph(entrypoint='local_runner', observer=recorder.observe, completion_policy=completion).compile()
        with sampling_challenge(recorder,scenario):
            result = graph.invoke(initial, config={'recursion_limit': 30})
        write_json(out / 'graph-result.json', plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        failure = dict(type=type(exc).__name__, message=str(exc))
        write_json(out / 'failure.json', failure)
        recorder.finish(completed=False)
    finally:
        close_logging()
    record = load_record(out / 'record.json', out)
    write_report(get_report(record), out / 'audit')
    write_json(out / 'sampling-diagnostic.json', diagnose_sampling(record))
    summary = dict(run_id=run_id, spec=spec, new_model_calls=0, heldout=False, synthetic=False,
        full_agent_workflow=False, fault_injection=scenario!='healthy', arm=arm, scenario=scenario,
        recovery_attempts=getattr(completion,'recovery_attempts',0), acceptance=result.get('acceptance_report', {}),
        completion=result.get('completion_claim'), coverage=recorder.data['coverage'],
        attempts=len(recorder.attempts), metrics=recorder.data['metrics'], failure=failure,
        optimization_effect_proven=False)
    summary['reference_validated'] = not failure and bool((summary['completion'] or {}).get('success')) and all(summary['coverage'].values())
    summary['development_contract_met'] = summary['reference_validated']
    if f.get('recovery_development'):
        expected = scenario=='healthy' or (scenario=='transient_missing' and arm=='candidate')
        values=summary['acceptance'].get('values',{})
        sample_events=[e for e in recorder.data['events'] if e.get('operation')=='sample']
        expected_samples=2 if arm=='candidate' and scenario!='healthy' else 1
        upstream=all(values.get(k) is True for k in ('execution_end','steady_evidence','task_inputs_verified','rendered_velocity')) and matches(values.get('local_continuity'),dict(operator='le',target=1e-7)) is True
        honest_failure=summary['completion'] and summary['completion']['success'] is False and summary['completion']['status']=='failed' and (summary['acceptance'].get('error') or {}).get('code')=='sampling_missing_output' and values.get('profile_error') is None and values.get('pressure_gradient_error') is None
        summary['development_contract_met'] = not failure and upstream and len(sample_events)==expected_samples and \
            all(summary['coverage'][k] for k in ('tools','decisions','acceptance')) and \
            (summary['reference_validated'] if expected else bool(honest_failure))
        summary['sample_tools']=sample_events
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary['development_contract_met'] else 2


def report(batch):
    f, digest = verify_freeze(batch)
    p = read(batch / 'progress.json')
    rows = [read(batch / 'runs' / r['id'] / 'summary.json') for r in p['runs'] if (batch / 'runs' / r['id'] / 'summary.json').exists()]
    lines = ['# 新参考机制先行验证', '', '真实 native local_runner → 求解 → U 图 → 注册采样验收；零模型调用。开发数据，尚未验证 planner 或优化效果。', '',
        '| 算例 | 网格 | 任务质量 | 开发契约 | 速度误差 | 压强梯度归一化误差 | 求解秒 |', '| --- | --- | --- | --- | --- | --- | --- |']
    for r in rows:
        v = r['acceptance'].get('values', {})
        lines.append(f"| {r['run_id']} | {r['spec']['nx']}×{r['spec']['ny']} | {r['reference_validated']} | {r.get('development_contract_met')} | {v.get('profile_error')} | {v.get('pressure_gradient_error')} | {r['metrics']['solver_seconds']} |")
    lines += ['', '状态：' + p['status'], '', '两条固定采样线同时检查全部 U 分量及 x/y 压强梯度。零参考梯度使用预声明的非零特征尺度，不计算零分母相对误差。Couette 另核验真实 inlet face 顺序。', '',
        '本批次是新任务 v2 契约：稳态证据为原 SIMPLE 标记或两份正时间全场 U/p 变化均 <=1e-7；原标记真/假单列在逐次 JSON。输出每100步保存，原求解/残差控制不变；v1 缺少标记的失败批次原样保留。', '',
        '不放宽阈值或自动调整参数；两个网格均需达标。两网格检查不等于证明普遍网格收敛阶；均匀流较简单，不能代表分离或复杂流动。', '',
        '固定环境、完整证据、模型拒绝器和开发标识见 freeze.json 及各 runs/<算例>/。新保留任务应使用未执行的参数组合，当前 C0/F0 只能作为开发参考。']
    (batch / 'report.md').write_text('\n'.join(lines) + '\n')
    write_json(batch / 'results.json', dict(freeze_sha256=digest, status=p['status'], rows=rows, new_model_calls=0, optimization_effect_proven=False))


def coordinate(batch):
    f, digest = verify_freeze(batch)
    if ROOT != batch / 'common-runtime' or (batch / 'progress.json').exists():
        raise ValueError('Fresh frozen coordinator only')
    guard = start_idle_guard(batch, f, 'reference')
    write_json(batch / 'idle-assertion-active.json', dict(guard_pid=guard.pid,
        assertions=subprocess.check_output(['/usr/bin/pmset', '-g', 'assertions'], text=True)))
    p = dict(status='running', freeze_sha256=digest, runs=[], new_model_calls=0)
    path = batch / 'progress.json'
    write_json(path, p)
    for run_id in f['schedule']:
        row = dict(id=run_id, status='running', started_at=datetime.now(timezone.utc).isoformat())
        p['runs'].append(row)
        write_json(path, p)
        print('START', run_id, flush=True)
        log = batch / 'execution-logs' / (run_id + '.log')
        log.parent.mkdir(exist_ok=True)
        with log.open('x') as stream:
            proc = subprocess.run([sys.executable, str(Path(__file__)), 'run', '--batch', str(batch), '--run', run_id], stdout=stream, stderr=subprocess.STDOUT)
        row.update(status='finished', exit_code=proc.returncode, ended_at=datetime.now(timezone.utc).isoformat())
        write_json(path, p)
        report(batch)
        print('END', run_id, proc.returncode, flush=True)
        if proc.returncode:
            p['status'] = 'reference_contract_failed'
            break
    else:
        p['status'] = 'complete'
    write_json(path, p)
    report(batch)
    return 0 if p['status'] == 'complete' else 2


def verify(batch):
    f, digest = verify_freeze(batch)
    p = read(batch / 'progress.json')
    if p['status'] != 'complete' or [r['id'] for r in p['runs']] != f['schedule']:
        raise ValueError('Complete frozen schedule required')
    totals = dict(artifacts=0, input_snapshots=0, solver_runs=0, model_calls=0)
    rows, identities = [], set()
    for run_id in f['schedule']:
        out = batch / 'runs' / run_id
        s = read(out / 'summary.json')
        record = load_record(out / 'record.json', out)
        if not s['reference_validated'] or record.data['model_calls'] or record.data['metrics']['model_tokens'] != 0 or s['attempts'] != 1:
            raise ValueError('Unexpected quality/model/retry state')
        verify_hashes(out / 'source-package', f['native'])
        for a in record.data['artifacts']:
            if sha(out / a['path']) != a['sha256']:
                raise ValueError('Evidence changed')
        totals['artifacts'] += len(record.data['artifacts'])
        a = record.data['attempts'][0]
        if a['run_id'] in identities:
            raise ValueError('Duplicate result identity')
        identities.add(a['run_id'])
        native = out / 'attempts' / a['run_id'] / 'native'
        manifest = read(native / 'input-manifest.json')
        if hashes(native / 'input-snapshot') != manifest['files']:
            raise ValueError('Input snapshot changed')
        version = 'sha256:' + hashlib.sha256(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if version != a['input_version'] or manifest['input_version'] != version:
            raise ValueError('Wrong input identity')
        totals['input_snapshots'] += 1
        totals['solver_runs'] += 1
        spec = read(batch / 'definitions' / run_id / 'spec.json')
        declared_task = read(batch / 'definitions' / run_id / 'task.json')
        if record.data['task'] != declared_task or s['acceptance']['criteria'] != declared_task['acceptance']:
            raise ValueError('Task contract changed')
        reference = batch / 'definitions' / run_id / 'reference'
        snapshot = out / a['input_snapshot']
        for name in hashes(reference):
            if Path(name).parts[0] in ('0', 'constant', 'system') and canonical_dictionary(reference / name) != canonical_dictionary(snapshot / name):
                raise ValueError('Wrong original task input')
        measured = reference_measurements(out / 'observations/sampling/profile.xy', out / 'observations/sampling/pressure.xy', spec)
        measured.update(solver_measurements((out / 'attempts' / a['run_id'] / 'logs/log.simpleFoam').read_text(), a['execution']['exit_code']))
        if any(abs(s['acceptance']['values'][k]-v) > 1e-12 for k,v in measured.items()):
            raise ValueError('Reference/solver recomputation differs')
        stability = stationary_fields(out / 'case', spec, a)
        if stability != read(out / 'observations/field-stationarity.json'):
            raise ValueError('Full-field stationarity differs')
        expected_steady = measured['steady_converged'] or stability['field_stationary'] is True
        if s['acceptance']['values']['steady_evidence'] != expected_steady:
            raise ValueError('Wrong steady evidence')
        if not all(matches(s['acceptance']['values'][c['id']],c) is True for c in declared_task['acceptance']):
            raise ValueError('Reference task did not meet original v2 criteria')
        if spec['kind'] == 'couette' and couette_inlet_order(out / 'case', spec) != read(out / 'observations/inlet-face-order.json'):
            raise ValueError('Inlet face order differs')
        if diagnose_sampling(record) != read(out / 'sampling-diagnostic.json'):
            raise ValueError('Sampling diagnostics differ')
        rows.append(dict(id=run_id, reference_validated=True, original_inputs_recomputed=True,
                         reference_and_solver_recomputed=True, registered_artifacts_verified=len(record.data['artifacts'])))
    write_json(batch / 'archive-verification.json', dict(freeze_sha256=digest, totals=totals, rows=rows,
        offline_verification=True, new_model_calls=0, new_simulations=0, optimization_effect_proven=False))
    print(json.dumps(totals), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('freeze', 'batch', 'run', 'report', 'verify'))
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--run')
    args = parser.parse_args()
    if args.action == 'run':
        return run(args.batch.resolve(), args.run)
    return dict(freeze=freeze, batch=coordinate, report=report, verify=verify)[args.action](args.batch.resolve())


if __name__ == '__main__':
    sys.exit(main())
