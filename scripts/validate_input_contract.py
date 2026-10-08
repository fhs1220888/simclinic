"""Frozen zero-model nine-file literal contract development controls."""
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
from simclinic.domains.openfoam.input_contract import (POLICY, SOURCES, FILES, SIGNIFICANT_DIGITS, compare_case, contract_task, ContractReferenceCompletion, RecoveringContractReferenceCompletion)
from simclinic.evaluation.input_contract_validation import schedule, apply_positive, apply_negative
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
    entries = schedule()
    for entry in entries:
        spec, run_id = entry['spec'], entry['id']
        definition = batch/'definitions'/run_id
        reference = create_reference(CORPUS/'reference-case', definition/'reference', spec)
        shutil.copytree(reference, definition/'inputs')
        if entry['variant']=='equivalent_high_precision':
            apply_positive(definition/'inputs', spec, high_precision=True)
        elif entry['variant']=='changed_literal':
            apply_negative(definition/'inputs', entry['changed_file'])
        elif entry['variant']=='wrong_dimensions':
            p = definition/'inputs/constant/physicalProperties'
            p.write_text(p.read_text().replace('nu 0.001;', 'nu [0 2 -2 0 0 0 0] 0.001;'))
        write_json(definition/'spec.json', spec)
        write_json(definition/'task.json', contract_task(spec))
        checks, evidence = compare_case(definition/'inputs', reference, spec)
        if all(v is True for v in checks.values()) is not entry['expected_input_match']:
            raise ValueError('Authored control does not satisfy predeclared input expectation')
        if len(checks)!=9 or any(v is None for v in checks.values()):
            raise ValueError('Authored controls must contain nine understood literal files')
        write_json(definition/'expected-input-review.json', evidence)
    histories = {}
    for name in ('full-model-recovery-study-20261005', 'full-model-quantity-recovery-study-20261006', 'quantity-input-development-20261006'):
        prior = ROOT/'real-runs'/name
        old = read(prior/'freeze.json')
        if sha(prior/'freeze.json')!=(prior/'freeze.sha256').read_text().strip():
            raise ValueError('Historical freeze changed')
        for folder,key in (('common-runtime','common_runtime'),('native','native'),('definitions','definitions')):
            verify_hashes(prior/folder, old[key])
        histories[name] = dict(freeze_sha256=sha(prior/'freeze.json'), results_sha256=sha(prior/'results.json'))
    delivered = read(ROOT/'patches/simclinic-sampling-gate/manifest.json')['files']
    for name,value in delivered.items():
        if sha(ROOT/name)!=value['delivered_sha256']: raise ValueError('Protected delivery changed')
    f = dict(experiment='foundation10-nine-file-input-development-v3',
        frozen_at=datetime.now(timezone.utc).isoformat(), entries=entries, schedule=[e['id'] for e in entries],
        heldout=False, full_agent_workflow=False, graph_entrypoint='local_runner',
        new_model_calls=0, archived_output_or_reply_reuse=False, synthetic=False,
        image=IMAGE, image_id=image['Id'], python=sys.version, executable=sys.executable,
        installed_dependencies=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines(),
        common_runtime=hashes(common), native=hashes(batch/'native'), definitions=hashes(batch/'definitions'),
        resources=dict(cpus=2,memory='2g',platform='linux/amd64',network='none'),
        solver_timeout_seconds=180, sampling_timeout_seconds=120, review_limit=0, model_render_fallback_limit=0,
        idle_sleep_guard=dict(command='/usr/bin/caffeinate',sha256=sha('/usr/bin/caffeinate')),
        input_matching_policy=POLICY, inlet_significant_digits=SIGNIFICANT_DIGITS,
        serialization_bound='half_unit_at_12_significant_digits_of_exact_value_inclusive',
        source_basis=SOURCES, acceptance_predeclared=True,
        negative_controls='One deliberate literal change per file plus wrong nu dimensions; honest task rejection required',
        stopping='Four positive and ten negative serial once-only controls; stop setup or unexpected outcome; no model/review/restart',
        original_quality_thresholds_preserved=True, historical_integrity=histories,
        protected_delivery={name:value['delivered_sha256'] for name,value in delivered.items()},
        optimization_effect_proven=False)
    write_json(batch / 'freeze.json', f)
    (batch / 'freeze.sha256').write_text(sha(batch / 'freeze.json') + '\n')
    print('Frozen fourteen zero-model nine-file controls:', batch, flush=True)


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
    shutil.copytree(definition / 'inputs', out / 'case')
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
        policy = RecoveringContractReferenceCompletion if arm=='candidate' else ContractReferenceCompletion
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
    values = summary['acceptance'].get('values', {})
    context = recorder.data['attempts'][-1]
    checks, evidence = compare_case(out/context['input_snapshot'], definition/'reference', spec)
    summary['quantity_comparison'] = evidence
    summary['variant'] = entry['variant']
    actual_inputs = all(v is True for v in checks.values())
    expected = entry['expected_quality']
    honest = bool(summary['completion']) and summary['completion']['success'] is expected
    solver_log = (out/'attempts'/context['run_id']/'logs/log.simpleFoam').read_text()
    observed_expected = values.get('execution_end') is entry['expected_solver_end']
    if not entry['expected_solver_end']:
        observed_expected = observed_expected and context['execution']['exit_code']!=0 and 'FOAM FATAL' in solver_log and values.get('profile_error') is None
    if entry['variant']=='wrong_dimensions':
        observed_expected = observed_expected and 'dimensions' in solver_log
    if entry['expected_quality']:
        observed_expected = observed_expected and all(matches(values.get(c['id']),c) is True for c in declared_task['acceptance'])
    changed = entry.get('changed_file')
    input_decisions = all(v is True for k,v in checks.items() if k!=changed)
    if changed: input_decisions = input_decisions and checks[changed] is False
    summary['development_contract_met'] = (failure is None and honest and observed_expected and input_decisions and
        actual_inputs is entry['expected_input_match'] and values.get('task_inputs_verified') is entry['expected_input_match'] and
        summary['reference_validated'] is expected and not record.data['model_calls'])
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary['development_contract_met'] else 2


def report(batch):
    f, digest = verify_freeze(batch)
    p = read(batch/'progress.json')
    rows = [read(batch/'runs'/r['id']/'summary.json') for r in p['runs'] if (batch/'runs'/r['id']/'summary.json').exists()]
    lines = ['# Nine-file input contract: zero-model development', '',
             'Real pinned Foundation 10 solver/render/sampling. Four authored positive and ten negative controls, not heldout optimization evidence.', '',
             '| Input | Full quality | Input pass | Solver ended | Expected control met |', '| --- | --- | --- | --- | --- |']
    for r in rows:
        v = r['acceptance'].get('values', {})
        lines.append(f"| {r['run_id']} | {r['reference_validated']} | {v.get('task_inputs_verified')} | {v.get('execution_end')} | {r['development_contract_met']} |")
    lines += ['', 'Status: '+p['status'], '',
              'C3/C4 are known development data. Template precision and high precision must pass. Each deliberate file change and wrong dimensions must fail honestly. Unknown expressions remain insufficient evidence.',
              'No model calls; raw input identities and historical scores are preserved. Numerical and reference thresholds remain unchanged. No optimization benefit is established.']
    (batch/'report.md').write_text('\n'.join(lines)+'\n')
    write_json(batch/'results.json', dict(freeze_sha256=digest,status=p['status'],rows=rows,new_model_calls=0,optimization_effect_proven=False))


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
    p = read(batch/'progress.json')
    if p['status']!='complete' or [r['id'] for r in p['runs']]!=f['schedule'] or any(r['exit_code']!=0 for r in p['runs']):
        raise ValueError('Complete expected-control schedule required')
    totals = dict(artifacts=0,input_snapshots=0,solver_runs=0,model_calls=0,sampler_calls=0)
    identities = set()
    for entry in f['entries']:
        out = batch/'runs'/entry['id']; s = read(out/'summary.json'); record = load_record(out/'record.json',out)
        if not s['development_contract_met'] or record.data['model_calls'] or record.data['metrics']['model_tokens']!=0 or s['attempts']!=1:
            raise ValueError('Unexpected development/model/retry state')
        verify_hashes(out/'source-package',f['native'])
        for a in record.data['artifacts']:
            if sha(out/a['path'])!=a['sha256']: raise ValueError('Evidence changed')
        totals['artifacts'] += len(record.data['artifacts'])
        totals['sampler_calls'] += sum(e.get('operation')=='sample' for e in record.data['events'])
        a = record.data['attempts'][0]; native = out/'attempts'/a['run_id']/'native'
        if a['run_id'] in identities: raise ValueError('Duplicate identity')
        identities.add(a['run_id'])
        m = read(native/'input-manifest.json')
        if hashes(native/'input-snapshot')!=m['files']: raise ValueError('Snapshot changed')
        version = 'sha256:'+hashlib.sha256(json.dumps(m['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if version!=a['input_version'] or version!=m['input_version']: raise ValueError('Input identity differs')
        totals['input_snapshots'] += 1; totals['solver_runs'] += 1
        definition = batch/'definitions'/entry['id']; declared = read(definition/'task.json')
        assessment = s['acceptance']; values = assessment['values']
        if record.data['task']!=declared or assessment['criteria']!=declared['acceptance']: raise ValueError('Contract changed')
        checks, evidence = compare_case(out/a['input_snapshot'],definition/'reference',entry['spec'])
        source_snapshot=out/'case/.simclinic/native'/a['run_id']/'input-snapshot'
        verify_hashes(source_snapshot,m['files'])
        source_evidence=json.loads(json.dumps(evidence))
        for name, detail in source_evidence['files'].items(): detail['actual']=str(source_snapshot/name)
        if checks!=assessment['input_checks'] or source_evidence!=assessment['input_comparison'] or evidence!=s['quantity_comparison']:
            raise ValueError('Input comparison evidence differs')
        if hashes(out/a['input_snapshot'])!=hashes(definition/'inputs'):
            raise ValueError('Authored negative/positive input differs')
        accepted = assessment['error'] is None and all(matches(values[c['id']],c) is True for c in declared['acceptance'])
        if accepted!=entry['expected_quality'] or accepted!=assessment['accepted'] or s['completion']['success']!=accepted:
            raise ValueError('Quality/expected failure differs')
        solver = solver_measurements((out/'attempts'/a['run_id']/'logs/log.simpleFoam').read_text(),a['execution']['exit_code'])
        if any(values[k]!=v for k,v in solver.items()): raise ValueError('Solver observations differ')
        if solver['execution_end'] is not entry['expected_solver_end']: raise ValueError('Reader behavior differs')
        if entry['expected_solver_end']:
            try:
                stability = stationary_fields(out/'case',entry['spec'],a)
            except ValueError:
                if entry['changed_file']!='system/blockMeshDict' or assessment['error'] is None:
                    raise
                if values['profile_error'] is not None or values['field_stationary'] is not None:
                    raise ValueError('Invalid mesh must not have manufactured reference/stability evidence')
            else:
                if stability!=read(out/'observations/field-stationarity.json'): raise ValueError('Stationarity differs')
                if values['steady_evidence']!=(solver['steady_converged'] or stability['field_stationary'] is True): raise ValueError('Steady evidence differs')
                if couette_inlet_order(out/'case',entry['spec'])!=read(out/'observations/inlet-face-order.json'): raise ValueError('Face order differs')
                try:
                    measured = reference_measurements(out/'observations/sampling/profile.xy',out/'observations/sampling/pressure.xy',entry['spec'])
                except ValueError:
                    if entry['changed_file']!='system/sample' or assessment['error'] is None or values['profile_error'] is not None:
                        raise
                else:
                    if any(abs(values[k]-v)>1e-12 for k,v in measured.items()): raise ValueError('Reference differs')
        if len(checks)!=9 or any(v is not True for k,v in checks.items() if k!=entry.get('changed_file')):
            raise ValueError('Unchanged files were not accepted')
        if entry.get('changed_file') and checks[entry['changed_file']] is not False:
            raise ValueError('Changed file was not rejected')
        if diagnose_sampling(record)!=read(out/'sampling-diagnostic.json'): raise ValueError('Diagnostics differ')
        claims = [(i,e) for i,e in enumerate(record.data['events']) if e.get('claim_type')=='completion']
        measures = [(i,e) for i,e in enumerate(record.data['events']) if e['kind']=='measurement']
        if len(claims)!=1 or len(measures)!=len(declared['acceptance']) or any(i>=claims[0][0] or e['value']!=values[e['criterion_id']] for i,e in measures): raise ValueError('Measurement claim order differs')
    original_root = batch.parents[1]
    for name,expected in f['historical_integrity'].items():
        prior = original_root/'real-runs'/name
        old = read(prior/'freeze.json')
        if sha(prior/'freeze.json')!=expected['freeze_sha256'] or sha(prior/'results.json')!=expected['results_sha256']:
            raise ValueError('Historical score/freeze changed')
        for folder,key in (('common-runtime','common_runtime'),('native','native'),('definitions','definitions')):
            verify_hashes(prior/folder,old[key])
    for name,expected in f['protected_delivery'].items():
        if sha(original_root/name)!=expected: raise ValueError('Protected delivery changed')
    write_json(batch/'archive-verification.json',dict(freeze_sha256=digest,totals=totals,offline_verification=True,new_model_calls=0,new_simulations=0,all_expected_controls_verified=True,verification_driver_sha256=sha(Path(__file__)),execution_runtime_preserved=True,optimization_effect_proven=False))
    print(json.dumps(totals))


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
