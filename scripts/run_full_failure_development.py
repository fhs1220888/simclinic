"""Freeze and execute three fresh full-model development paths; no heldout claims."""
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
from simclinic.evaluation.heldout import hashes, verify_hashes, task, DEVELOPMENT
from simclinic.evaluation.failure_development import SCENARIOS, DevelopmentCompletion, contract_errors
from simclinic.evaluation.idle_guard import start_idle_guard
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.domains.openfoam.acceptance import solver_measurements
from simclinic.evaluation.heldout import velocity_error, pressure_error
from simclinic.checks.behavior import matches
from simclinic.cli import get_report
from simclinic.session import SessionRecorder, plain
from simclinic.backends.codex_cli import CodexCLI
from simclinic.retrieval import LexicalReferences


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_freeze(batch):
    f = read(batch / 'freeze.json')
    digest = sha(batch / 'freeze.json')
    if digest != (batch / 'freeze.sha256').read_text().strip():
        raise ValueError('Freeze changed')
    for folder, key in (('common-runtime', 'common_runtime'), ('native', 'native'),
                        ('corpus', 'corpus'), ('task', 'task')):
        verify_hashes(batch / folder, f[key])
    if f['schedule'] != list(SCENARIOS) or f['heldout'] is not False:
        raise ValueError('Wrong development protocol')
    return f, digest


def freeze(batch):
    from scripts.run_full_agent import IMAGE, CODEX, CORPUS, REQUIREMENT
    if batch.exists():
        raise ValueError('Preserve existing freezes')
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
    shutil.copytree(CORPUS, batch / 'corpus', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(CORPUS / 'reference-case', batch / 'task/acceptance-reference')
    write_json(batch / 'task/spec.json', DEVELOPMENT)
    (batch / 'task/requirement.txt').write_text(REQUIREMENT)
    f = dict(schema_version='1.0', experiment='full-model-failure-development-v1',
             frozen_at=datetime.now(timezone.utc).isoformat(), heldout=False,
             synthetic=False, fresh_planner_inputs_solver_and_render=True,
             archived_reply_or_solver_output_reuse=False, optimization_effect_proven=False,
             schedule=list(SCENARIOS), model='gpt-6.1-sol', model_reasoning_effort='high',
             cli=CODEX, cli_sha256=sha(CODEX), cli_version=subprocess.check_output([CODEX, '--version'], text=True).strip(),
             image=IMAGE, image_id=image['Id'], python=sys.version, executable=sys.executable,
             installed_dependencies=subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines(),
             agent_commit=manifest['base_commit'], common_runtime=hashes(common), native=hashes(batch / 'native'),
             corpus=hashes(batch / 'corpus'), task=hashes(batch / 'task'),
             model_call_limit=32, review_limit=2, model_render_fallback_limit=0, solver_timeout_seconds=180,
             token_limit=1500000, reserve_per_run_model_tokens=450000, failed_usage_retained=True,
             idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256=sha('/usr/bin/caffeinate')),
             injection_scope='Declared sampler-only malformed extra file, or removal of newly generated profile; protected reader bytes and original case unchanged',
             primary_contract='Healthy passes original acceptance; injected paths reach honest explicit failure with typed evidence, unchanged upstream criteria and null reference measurements',
             diagnostic_contract='Fatal verified logs: finding; absent registered outputs alone: insufficient_evidence; gate provides observed typed error separately',
             stopping='Serial once only; stop unknown usage, insufficient budget, unexpected setup/collection/contract failure; preserve all observations, no selective rerun',
             limits='Three development paths, one channel, one run each; no natural failure rate, benefit magnitude, autonomous repair, independent heldout or new LLM explanation claim')
    write_json(batch / 'freeze.json', f)
    (batch / 'freeze.sha256').write_text(sha(batch / 'freeze.json') + '\n')
    print('Frozen; zero model/solver calls:', batch, flush=True)


def runtime_check(f):
    if sys.version != f['python'] or subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines() != f['installed_dependencies']:
        raise ValueError('Runtime changed')
    if sha(f['cli']) != f['cli_sha256']:
        raise ValueError('Backend binary changed')
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', f['image']], text=True))[0]
    if image['Id'] != f['image_id']:
        raise ValueError('Image changed')


def run(batch, scenario):
    f, digest = verify_freeze(batch)
    if ROOT != batch / 'common-runtime':
        raise ValueError('Use archived driver')
    p = read(batch / 'progress.json')
    active = [row for row in p['runs'] if row['status'] == 'running']
    if p['status'] != 'running' or p['freeze_sha256'] != digest or len(active) != 1 or active[0]['id'] != scenario:
        raise ValueError('Only coordinator registered run may execute')
    runtime_check(f)
    out = batch / 'runs' / scenario
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(batch / 'native', out / 'source-package')
    sys.path.insert(0, str(out / 'source-package/src'))
    from main import create_foam_agent_graph, initialize_state
    from config import Config
    from services import configure_llm
    from utils import configure_retriever
    from logger import close_logging
    objective = (batch / 'task/requirement.txt').read_text()
    spec = read(batch / 'task/spec.json')
    injected = scenario != 'healthy'
    environment = dict(freeze_sha256=digest, image=f['image'], model=f['model'],
                       distribution='OpenFOAM Foundation', version='10', solver='simpleFoam',
                       graph_entrypoint='planner', completion_policy='sampling-gate-v1',
                       resources=dict(cpus=2, memory='2g', network='none', platform='linux/amd64'))
    recorder = SessionRecorder(out, task(spec, objective), environment,
        dict(name='Foam-Agent full-model failure development', commit=f['agent_commit'], full_agent_workflow=True))
    recorder.data.update(heldout=False, fault_injection=injected, development_regression=True,
                         injection_scope=f['injection_scope'])
    backend = CodexCLI(out / 'model-calls', f['model'], executable=f['cli'], max_calls=f['model_call_limit'], observer=recorder.model)
    configure_llm(backend)
    configure_retriever(LexicalReferences(batch / 'corpus/corpus.json'))
    os.environ.update(SIMCLINIC_DOCKER_IMAGE=f['image'], SIMCLINIC_OBSERVED_EXECUTION='1', LANGCHAIN_TRACING_V2='false')
    config = Config(case_dir=str(out / 'case'), database_path=str(batch / 'corpus'), max_loop=f['review_limit'],
                    max_time_limit=f['solver_timeout_seconds'], model_version=f['model'], searchdocs=1, recursion_limit=30)
    config.visualization_max_loop = f['model_render_fallback_limit']
    write_json(out / 'environment.json', dict(**environment, fault_injection=injected, acceptance_reference_in_model_context=False))
    (out / 'requirement.txt').write_text(objective)
    policy = DevelopmentCompletion(recorder, batch / 'task/acceptance-reference', f['image'], spec, scenario)
    result, failure = {}, None
    try:
        initial = initialize_state(objective, config, llm_service=backend)
        graph = create_foam_agent_graph(observer=recorder.observe, completion_policy=policy).compile()
        result = graph.invoke(initial, config={'recursion_limit': 30})
        write_json(out / 'graph-result.json', plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        failure = dict(type=type(exc).__name__, message=str(exc))
        write_json(out / 'failure.json', failure)
        recorder.finish(completed=False)
    finally:
        close_logging()
        write_json(out / 'model-usage.json', backend.get_statistics())
    record = load_record(out / 'record.json', out)
    report = get_report(record)
    write_report(report, out / 'audit')
    diagnostic = diagnose_sampling(record)
    write_json(out / 'sampling-diagnostic.json', diagnostic)
    samples = [e for e in recorder.data['events'] if e.get('operation') == 'sample']
    summary = dict(scenario=scenario, synthetic=False, heldout=False, fault_injection=injected,
                   completed_graph='completion_claim' in result, completion=result.get('completion_claim'),
                   acceptance=result.get('acceptance_report', {}).get('values', {}),
                   acceptance_error=result.get('acceptance_report', {}).get('error'),
                   sample_tool=samples[-1] if samples else None, interventions=policy.interventions,
                   coverage=recorder.data['coverage'], attempts=len(recorder.attempts),
                   audit_summary=report['summary'], metrics=recorder.data['metrics'],
                   model_usage=backend.get_statistics(), failure=failure, optimization_effect_proven=False)
    summary['contract_errors'] = contract_errors(scenario, summary, diagnostic)
    summary['contract_met'] = not summary['contract_errors']
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary['contract_met'] else 2


def report(batch):
    f, digest = verify_freeze(batch)
    p = read(batch / 'progress.json')
    rows = []
    lines = ['# 完整模型失败路径开发验证', '',
             '开发验证：每条路径从新模型规划开始，重新生成输入、求解和 U 图；故障注入单列。', '',
             '| 路径 | 契约 | 完成声明 | 采样类别 | 诊断 | 模型调用 | token |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for entry in p['runs']:
        out = batch / 'runs' / entry['id']
        if not (out / 'summary.json').is_file():
            continue
        s = read(out / 'summary.json')
        d = read(out / 'sampling-diagnostic.json')
        row = dict(id=entry['id'], contract_met=s['contract_met'], claim=s['completion'],
                   fault_injection=s['fault_injection'], error=s['acceptance_error'],
                   diagnostic=d['summary'], model_usage=s['model_usage'], metrics=s['metrics'])
        rows.append(row)
        status = d['runs'][0]['status'] if d['runs'] else 'not_applicable'
        lines.append(f"| {entry['id']} | {s['contract_met']} | {(s['completion'] or {}).get('success')} | {(s['sample_tool'] or {}).get('error_category')} | {status} | {s['model_usage']['total_calls']} | {s['model_usage']['total_tokens']} |")
    lines += ['', f"批次状态：{p['status']}；已知 token 合计：{p['known_model_tokens']}；完整合计：{p['model_tokens']}；费用未知。", '',
              '缺失归档输出的只读诊断仍为证据不足；本次运行门禁观测到的具体缺失错误单独保存，不能因此判定 Agent 错误。', '',
              '注入只改变隔离后处理故障条件，原求解输入、结果身份及任务阈值保持；失败路径不发布参考偏差值。', '',
              '每个 runs/<路径>/ 目录含完整记录、原始日志、注入证据、门禁评估、Markdown/JSON 体检、逐次模型元数据。', '',
              '局限：三条开发路径、一个通道、每条一次。未评估自然故障率、自动恢复、额外 LLM 故障解释或总体优化收益。']
    write_json(batch / 'results.json', dict(freeze_sha256=digest, status=p['status'], development=True,
               heldout=False, rows=rows, known_model_tokens=p['known_model_tokens'], model_tokens=p['model_tokens'],
               model_cost=None, optimization_effect_proven=False))
    (batch / 'report.md').write_text('\n'.join(lines) + '\n')


def coordinate(batch):
    f, digest = verify_freeze(batch)
    if ROOT != batch / 'common-runtime' or (batch / 'progress.json').exists():
        raise ValueError('Use fresh archived coordinator; no resume/selective retry')
    runtime_check(f)
    guard = start_idle_guard(batch, f, 'development')
    write_json(batch / 'idle-assertion-active.json', dict(coordinator_pid=os.getpid(), guard_pid=guard.pid,
        assertions=subprocess.check_output(['/usr/bin/pmset', '-g', 'assertions'], text=True)))
    p = dict(status='running', freeze_sha256=digest, started_at=datetime.now(timezone.utc).isoformat(),
             runs=[], known_model_tokens=0, model_tokens=0)
    path = batch / 'progress.json'
    write_json(path, p)
    for scenario in f['schedule']:
        if p['known_model_tokens'] + f['reserve_per_run_model_tokens'] > f['token_limit']:
            p['status'] = 'budget_boundary'
            break
        entry = dict(id=scenario, status='running', started_at=datetime.now(timezone.utc).isoformat())
        p['runs'].append(entry)
        write_json(path, p)
        log = batch / 'execution-logs' / (scenario + '.log')
        log.parent.mkdir(exist_ok=True)
        print('START', scenario, flush=True)
        with log.open('x') as stream:
            proc = subprocess.run([sys.executable, str(Path(__file__)), 'run', '--batch', str(batch), '--scenario', scenario],
                                  stdout=stream, stderr=subprocess.STDOUT)
        out = batch / 'runs' / scenario
        s = read(out / 'summary.json') if (out / 'summary.json').is_file() else {}
        metadata = [read(x) for x in sorted((out / 'model-calls').glob('*/metadata.json'))]
        known = sum(x['total_tokens'] for x in metadata if x.get('total_tokens') is not None)
        tokens = s.get('model_usage', {}).get('total_tokens')
        entry.update(status='finished', ended_at=datetime.now(timezone.utc).isoformat(), exit_code=proc.returncode,
                     contract_met=s.get('contract_met', False), known_model_tokens=known, model_tokens=tokens)
        p['known_model_tokens'] += known
        p['model_tokens'] = p['known_model_tokens'] if tokens is not None else None
        print('END', scenario, json.dumps(entry), flush=True)
        if tokens is None:
            p['status'] = 'usage_unknown'
        elif proc.returncode or not entry['contract_met']:
            p['status'] = 'development_contract_failed'
        elif p['known_model_tokens'] > f['token_limit']:
            p['status'] = 'budget_boundary'
        write_json(path, p)
        report(batch)
        if p['status'] != 'running':
            break
    else:
        p['status'] = 'complete'
    p['ended_at'] = datetime.now(timezone.utc).isoformat()
    write_json(path, p)
    report(batch)
    print('STATUS', p['status'], 'TOKENS', p['model_tokens'], flush=True)
    return 0 if p['status'] == 'complete' else 2


def verify(batch):
    f, digest = verify_freeze(batch)
    p = read(batch / 'progress.json')
    if p['freeze_sha256'] != digest or p['status'] != 'complete' or [r['id'] for r in p['runs']] != f['schedule']:
        raise ValueError('Complete predeclared schedule required for full verification')
    totals = dict(artifacts=0, input_snapshots=0, model_calls=0, model_tokens=0)
    identities, rows, last_end = set(), [], None
    for entry in p['runs']:
        if last_end is not None and entry['started_at'] < last_end:
            raise ValueError('Overlapping runs')
        last_end = entry['ended_at']
        out = batch / 'runs' / entry['id']
        s = read(out / 'summary.json')
        record = load_record(out / 'record.json', out)
        if record.data.get('heldout') is not False or record.data.get('fault_injection') != (entry['id'] != 'healthy'):
            raise ValueError('Incorrect development label')
        if read(out / 'environment.json')['freeze_sha256'] != digest:
            raise ValueError('Wrong runtime')
        verify_hashes(out / 'source-package', f['native'])
        for artifact in record.data['artifacts']:
            if sha(out / artifact['path']) != artifact['sha256']:
                raise ValueError('Registered evidence changed: ' + artifact['path'])
        totals['artifacts'] += len(record.data['artifacts'])
        for attempt in record.data['attempts']:
            if attempt['run_id'] in identities:
                raise ValueError('Repeated run identity')
            identities.add(attempt['run_id'])
            native = out / 'attempts' / attempt['run_id'] / 'native'
            manifest = read(native / 'input-manifest.json')
            if hashes(native / 'input-snapshot') != manifest['files']:
                raise ValueError('Input snapshot changed')
            version = 'sha256:' + hashlib.sha256(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if version != attempt['input_version'] or version != manifest['input_version']:
                raise ValueError('Input identity changed')
            totals['input_snapshots'] += 1
        last = record.data['attempts'][-1]
        snapshot = out / last['input_snapshot']
        reference = batch / 'task/acceptance-reference'
        if not all(canonical_dictionary(snapshot / name) == canonical_dictionary(reference / name)
                   for name in hashes(reference) if Path(name).parts[0] in ('0', 'constant', 'system')):
            raise ValueError('Original task inputs differ')
        log = (out / 'attempts' / last['run_id'] / 'logs/log.simpleFoam').read_text()
        measured = solver_measurements(log, last['execution']['exit_code'])
        if any(s['acceptance'][k] != v for k, v in measured.items()):
            raise ValueError('Solver observations differ')
        meta = [read(x) for x in sorted((out / 'model-calls').glob('*/metadata.json'))]
        tokens = sum(x['total_tokens'] for x in meta)
        if len(meta) != s['model_usage']['total_calls'] or tokens != s['model_usage']['total_tokens'] or tokens != record.data['metrics']['model_tokens']:
            raise ValueError('Usage mismatch')
        totals['model_calls'] += len(meta)
        totals['model_tokens'] += tokens
        diagnostic = diagnose_sampling(record)
        if diagnostic != read(out / 'sampling-diagnostic.json') or contract_errors(entry['id'], s, diagnostic):
            raise ValueError('Diagnostic/contract mismatch')
        assessment = read(out / 'observations/task-assessment.json')
        if assessment['criteria'] != record.data['task']['acceptance'] or assessment['values'] != s['acceptance']:
            raise ValueError('Task assessment mismatch')
        if assessment['accepted'] != all(matches(assessment['values'][c['id']], c) is True for c in assessment['criteria']):
            raise ValueError('Acceptance mismatch')
        gate = read(out / 'observations/sampling/gate-assessment.json')
        if gate['error_category'] != s['sample_tool']['error_category'] or gate['exit_code'] != s['sample_tool']['exit_code']:
            raise ValueError('Gate/tool mismatch')
        events = record.data['events']
        sample_index = next(i for i, e in enumerate(events) if e.get('operation') == 'sample')
        decisions = [e for e in events[sample_index+1:] if e.get('decision_type') == 'sampling_gate']
        if len(decisions) != 1 or decisions[0]['handles'] != [s['sample_tool']['id']] or decisions[0]['action'] != ('continue' if entry['id'] == 'healthy' else 'stop'):
            raise ValueError('Sampler decision not linked')
        if entry['id'] == 'healthy':
            if abs(velocity_error(out / 'observations/sampling/profile.xy', DEVELOPMENT) - s['acceptance']['profile_error']) > 1e-12 or abs(pressure_error(out / 'observations/sampling/pressure.xy', DEVELOPMENT) - s['acceptance']['pressure_gradient_error']) > 1e-12:
                raise ValueError('Reference recomputation differs')
        else:
            injection = read(out / 'observations/injection/intervention.json')
            if injection.get('original_reader_bytes_unchanged') is not True or injection['natural_failure'] is not False:
                raise ValueError('Injection integrity missing')
            for name, expected in injection['original_reader_manifest'].items():
                # Compare copied solver/reader bytes with original native evidence.
                if sha(out / 'case' / name) != expected:
                    raise ValueError('Original case changed by injector')
            if gate['measurements'] is not None or gate['outputs']:
                raise ValueError('Failure published reference outputs')
        rows.append(dict(id=entry['id'], contract_met=True, artifacts=len(record.data['artifacts']),
                         model_calls=len(meta), model_tokens=tokens, original_inputs_verified=True,
                         sampling_decision_linked=True, diagnostic_recomputed=True))
    if totals['model_tokens'] != p['model_tokens'] or totals['model_tokens'] != p['known_model_tokens']:
        raise ValueError('Batch usage mismatch')
    result = dict(freeze_sha256=digest, offline_verification=True, new_model_calls=0, new_simulations=0,
                  totals=totals, rows=rows, heldout=False, optimization_effect_proven=False)
    write_json(batch / 'archive-verification.json', result)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('freeze', 'batch', 'run', 'report', 'verify'))
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--scenario', choices=SCENARIOS)
    args = parser.parse_args()
    batch = args.batch.resolve()
    if args.action == 'run':
        return run(batch, args.scenario)
    return dict(freeze=freeze, batch=coordinate, report=report, verify=verify)[args.action](batch)


if __name__ == '__main__':
    sys.exit(main())
