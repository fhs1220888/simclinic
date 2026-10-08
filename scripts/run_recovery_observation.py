"""Offline-check, freeze and explicitly dispatch the serial observation pilot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from simclinic.evaluation.recovery_observation_driver import (
    BudgetBoundary, Ledger, budget_reason, derive_quality, digest, now,
    offline_contract_checks, read, stop_reason, verify_batch,
    verify_registered_evidence, writable_new, write_new)


def freeze_data(bundle, *, current=False):
    from simclinic.evaluation.recovery_execution_freeze import verify_freeze
    check = verify_freeze(bundle, check_current_environment=current)
    frozen = read(bundle / 'freeze.json')
    if current and check.get('current_environment_verification', {}).get('status') != 'pass':
        raise ValueError('Actual frozen environment must match before any dispatch')
    if current and (sys.version != frozen['environment']['python']['version']
                    or sys.prefix != frozen['environment']['python']['prefix']
                    or Path(sys.executable).resolve() != Path(frozen['python_executable']).resolve()):
        raise ValueError('The executing Python interpreter must match the freeze')
    return frozen, hashlib.sha256((bundle / 'freeze.json').read_bytes()).hexdigest(), check


def config_for(bundle):
    return read(bundle / 'protocol-design/protocol.json')


def environment_for(bundle):
    return read(bundle / 'environment.json')


def report(batch, bundle):
    from simclinic.evaluation.recovery_observation_score import score_observations
    _, freeze_sha, _ = freeze_data(bundle)
    config = config_for(bundle)
    verified = verify_batch(batch, config, freeze_sha, bundle=bundle)
    scored = score_observations(config['entries'], verified['rows'], started=verified['started'])
    result = dict(execution_freeze_sha256=freeze_sha, design_freeze_sha256=read(bundle / 'freeze.json')['design_freeze_sha256'],
                  status=verified['status'], integrity=verified, scoring=scored,
                  batch_consumption={k: read(batch / 'progress.json').get(k) for k in
                      ('elapsed_total_seconds', 'coordinator_overhead_seconds')},
                  new_execution_authorization_implied=False)
    # Mutable current batch reports are permitted; frozen sources remain readonly.
    from simclinic.core.report import write_json
    write_json(batch / 'results.json', result)
    (batch / 'report.md').write_text('# 自然与注入观察结果\n\n'
        '全部 STARTED 任务进入各情景分母，未启动任务单列。自然与注入不合并。\n\n'
        '自然没有符合恢复资格的故障时，自然恢复收益仍为无法判断；注入成功不能替代。\n\n'
        '```json\n' + json.dumps(scored, ensure_ascii=False, indent=2) + '\n```\n')
    return result


def guarded_backend(base, check_budget):
    """Before/after each model call, preserving the exact frozen backend."""
    class Backend(base):
        def invoke(self, *args, **kwargs):
            if len(self.calls) >= self.max_calls:
                raise BudgetBoundary('Model call limit reached; no automatic retry')
            reason = check_budget(self.calls)
            if reason:
                raise BudgetBoundary(reason)
            result = super().invoke(*args, **kwargs)
            reason = check_budget(self.calls)
            if reason:
                raise BudgetBoundary(reason)
            return result
    return Backend


def run_one(batch, bundle, rid):
    """Only a registered child of the frozen coordinator may enter this path."""
    child_began = time.monotonic()
    frozen, freeze_sha, _ = freeze_data(bundle, current=True)
    config = config_for(bundle)
    ledger = Ledger(batch / 'ledger.jsonl', config, freeze_sha)
    start = ledger.events[-1] if ledger.events else {}
    if (ROOT != bundle / 'common-runtime' or start.get('kind') != 'STARTED'
            or start['entry']['id'] != rid or read(batch / 'progress.json')['status'] != 'running'):
        raise ValueError('Fresh registered frozen coordinator child required')
    authorization = read(batch / 'authorization.json')
    if authorization.get('explicit_cli_authorization') is not True or authorization.get('freeze_sha256') != freeze_sha:
        raise ValueError('Child has no matching explicit batch authorization')
    entry = start['entry']
    progress = read(batch / 'progress.json')
    prior = verify_batch(batch, config, freeze_sha, bundle=bundle)['rows'][:-1]
    if progress['rows'] != prior:
        raise ValueError('Progress usage differs from independently sealed FINAL rows')
    settings = environment_for(bundle)
    configured = settings['configured']
    limits = config['budgets']
    out = batch / 'runs' / rid
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(bundle / 'native', out / 'source-package', ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0, str(out / 'source-package/src'))
    from simclinic.session import SessionRecorder, plain
    from simclinic.backends.codex_cli import CodexCLI
    from simclinic.retrieval import LexicalReferences
    from simclinic.evaluation.recovery_observer import observed_completion_policy
    from simclinic.core.record import load_record
    from simclinic.cli import get_report
    from simclinic.core.report import write_json, write_report
    from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
    spec_dir = bundle / 'definitions' / entry['spec_id']
    spec, declared = read(spec_dir / 'spec.json'), read(spec_dir / 'task.json')
    env = dict(execution_id=start['execution_id'], freeze_sha256=freeze_sha,
               model=configured['model'], image=configured['image'], resources=configured['resources'],
               arm=entry['arm'], stage=entry['stage'], distribution='OpenFOAM Foundation', version='10', solver='simpleFoam')
    recorder = SessionRecorder(out, declared, env, dict(name='Foam-Agent bounded recovery observation',
                    commit=configured['agent_commit'], full_agent_workflow=True))
    recorder.data.update(heldout=entry['stage'] != 'development', fault_injection=entry['stage'] == 'injected')
    recorder.save()
    policy = observed_completion_policy(recorder, spec_dir / 'reference', configured['image'], spec,
                    enabled=entry['arm'] == 'candidate', scenario='transient_missing' if entry['stage'] == 'injected' else 'natural')
    backend = None
    result = {}
    failure = None
    close_logging = lambda: None
    dispatch_lag = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(start['at'])).total_seconds()
                       - (time.monotonic() - child_began))
    prior_rows = prior
    # Coordinator wall includes serial dispatch/validation/report overhead.
    prior_total_wall = start['elapsed_total_seconds']
    prior_stage_wall = start['elapsed_stage_seconds']

    def call_budget(calls):
        try:
            os.kill(progress['idle_guard_pid'], 0)
        except (OSError, KeyError):
            raise RuntimeError('Frozen idle guard unavailable; no further model call')
        tokens = [call.get('total_tokens') for call in calls]
        usage = sum(tokens) if all(t is not None for t in tokens) else None
        row = dict(stage=entry['stage'], consumption=dict(model_tokens=usage),
                   consumption_known=dict(model_tokens=sum(t for t in tokens if t is not None)))
        elapsed = dispatch_lag + time.monotonic() - child_began
        return budget_reason(config, prior_rows + [row], entry['stage'],
                             prior_total_wall + elapsed, prior_stage_wall + elapsed, before_run=False)

    try:
        from main import create_foam_agent_graph, initialize_state
        from config import Config
        from services import configure_llm
        from utils import configure_retriever
        from logger import close_logging
        Backend = guarded_backend(CodexCLI, call_budget)
        backend = Backend(out / 'model-calls', configured['model'], executable=configured['codex_executable'],
                          timeout=limits['model_timeout_seconds'], max_calls=limits['model_calls_per_run'], observer=recorder.model)
        configure_llm(backend)
        configure_retriever(LexicalReferences(bundle / 'corpus/corpus.json'))
        os.environ.update(SIMCLINIC_DOCKER_IMAGE=configured['image'], SIMCLINIC_OBSERVED_EXECUTION='1', LANGCHAIN_TRACING_V2='false')
        native_config = Config(case_dir=str(out / 'case'), database_path=str(bundle / 'corpus'),
                              max_loop=limits['shared_reviewer_limit'], max_time_limit=limits['solver_timeout_seconds'],
                              model_version=configured['model'], searchdocs=1, recursion_limit=30)
        native_config.visualization_max_loop = limits['model_render_fallback_limit']
        write_new(out / 'environment.json', dict(**env, acceptance_reference_in_model_context=False))
        (out / 'requirement.txt').write_text(declared['objective'])
        graph = create_foam_agent_graph(observer=recorder.observe, completion_policy=policy).compile()
        result = graph.invoke(initialize_state(declared['objective'], native_config, llm_service=backend),
                              config={'recursion_limit': 30})
        write_new(out / 'graph-result.json', plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        failure = dict(type=type(exc).__name__, message=str(exc), budget_interrupted=isinstance(exc, BudgetBoundary))
        write_new(out / 'failure.json', failure)
        recorder.finish(completed=False)
    finally:
        close_logging()
    calls = [read(p) for p in sorted((out / 'model-calls').glob('*/metadata.json'))]
    usage = backend.get_statistics() if backend is not None else dict(total_calls=0, total_tokens=0, model_cost=None)
    write_new(out / 'model-usage.json', usage)
    observation = policy.observation_summary()
    write_new(out / 'observation-summary.json', observation)
    # Register the machine observer summary in the same evidence inventory.
    recorder.artifact(out / 'observation-summary.json')
    recorder.save()
    record = load_record(out / 'record.json', out)
    write_report(get_report(record), out / 'audit')
    write_new(out / 'sampling-diagnostic.json', diagnose_sampling(record))
    count = verify_registered_evidence(out, recorder.data)
    derived = derive_quality(out, recorder.data)
    acceptance_error = (derived['assessment'] or {}).get('error') or {}
    from simclinic.checks.behavior import retries
    tokens = [c.get('total_tokens') for c in calls]
    tools = [e for e in recorder.data['events'] if e.get('kind') == 'tool']
    samples = [e for e in tools if e.get('operation') == 'sample']
    solvers = [e for e in tools if e.get('phase') == 'solve']

    def total_time(events):
        values = [e.get('elapsed_seconds') for e in events]
        return sum(values) if all(v is not None for v in values) else None

    row = dict(**entry, execution_id=start['execution_id'], run_id=recorder.data.get('run_id'),
               input_version=recorder.data.get('input_version'), evidence_verified=observation.get('collection_complete') is True,
               **{k: derived[k] for k in ('quality_met', 'acceptance_verified', 'explicit_completion', 'false_completion')},
               **{k: observation[k] for k in ('first_sample', 'recovery', 'injection')},
               consumption=dict(model_tokens=sum(tokens) if all(t is not None for t in tokens) else None,
                                model_calls=len(calls), solver_calls=len(solvers), solver_elapsed_seconds=total_time(solvers),
                                sample_calls=len(samples), sample_elapsed_seconds=total_time(samples),
                                workflow_elapsed_seconds=time.monotonic() - recorder.started,
                                agent_workflow_elapsed_seconds=time.monotonic() - recorder.started,
                                recovery_branch_overhead_seconds=observation['recovery'].get('non_sampling_branch_elapsed_seconds', 0)
                                    if observation['recovery'].get('launched') is False else
                                    observation['recovery'].get('non_sampling_branch_elapsed_seconds'),
                                evidence_count=count, billed_cost=None),
               consumption_known=dict(model_tokens=sum(t for t in tokens if t is not None)),
               backend_failure=any(c.get('outcome') != 'pass' for c in calls),
               infrastructure_failure=bool(failure and not failure['budget_interrupted']
                                           and not any(c.get('outcome') != 'pass' for c in calls)),
               budget_interrupted=bool(failure and failure['budget_interrupted']),
               policy_violation=observation.get('policy_violation', False),
               identity_corrupt=observation.get('identity_corrupt', False) or acceptance_error.get('type') == 'ResultRejected', failure=failure,
               identity_unverified=(observation['first_sample'].get('missing_output_confirmed') is True
                                    and observation['first_sample'].get('eligible') is not True),
               coverage=recorder.data['coverage'], optimization_effect_proven=False)
    row['collection_failure_reasons'] = observation.get('collection_failure_reasons', [])
    row['outcome_class'] = ('backend_failure' if row['backend_failure'] else 'infrastructure_failure'
                            if row['infrastructure_failure'] else 'budget_interrupted' if row['budget_interrupted']
                            else 'complete_success' if row['quality_met'] else 'honest_task_failure')
    row['failure_category'] = (derived['assessment'].get('error') or {}).get('code') if derived['assessment'] else None
    row['audits'] = {item['check_id']: item['status'] for item in get_report(record)['checks']}
    row['retry_finding'] = retries(record)['status'] == 'finding'
    row['files'] = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(out.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    write_new(out / 'summary.json', row)
    # Honest failed/unknown task returns normally; coordinator classifies stop rules.
    return 0


def coordinate(batch, bundle, *, authorized=False):
    if authorized is not True:
        raise ValueError('Separate explicit execution authorization is required')
    frozen, freeze_sha, _ = freeze_data(bundle, current=True)
    if ROOT != bundle / 'common-runtime':
        raise ValueError('Use the sealed common-runtime script for actual execution')
    config = config_for(bundle)
    batch = writable_new(batch)
    batch.mkdir(parents=True)
    write_new(batch / 'authorization.json', dict(explicit_cli_authorization=True, at=now(),
                freeze_sha256=freeze_sha, budget=config['budgets'], scope=config['counts']))
    ledger = Ledger(batch / 'ledger.jsonl', config, freeze_sha, create=True)
    from simclinic.core.report import write_json
    progress = dict(status='running', freeze_sha256=freeze_sha, rows=[], elapsed_total_seconds=0,
                    elapsed_stage_seconds=0, started_at=now())
    write_json(batch / 'progress.json', progress)
    began = time.monotonic()
    stage = None
    stage_began = began
    guard = None
    try:
        from simclinic.evaluation.idle_guard import start_idle_guard
        guard = start_idle_guard(batch, frozen, 'observation')
        progress['idle_guard_pid'] = guard.pid
        write_json(batch / 'progress.json', progress)
        for entry in config['entries']:
            if guard.poll() is not None:
                progress['status'] = 'idle_guard_failure'
                break
            if stage != entry['stage']:
                stage = entry['stage']
                stage_began = time.monotonic()
            total_elapsed, stage_elapsed = time.monotonic() - began, time.monotonic() - stage_began
            reason = budget_reason(config, progress['rows'], stage, total_elapsed, stage_elapsed)
            if reason:
                progress['status'] = reason
                break
            progress.update(elapsed_total_seconds=total_elapsed, elapsed_stage_seconds=stage_elapsed)
            write_json(batch / 'progress.json', progress)
            task_began = time.monotonic()
            ledger.start(entry, elapsed_total_seconds=total_elapsed, elapsed_stage_seconds=stage_elapsed)
            log = batch / 'execution-logs' / (entry['id'] + '.log')
            log.parent.mkdir(exist_ok=True)
            with log.open('x') as stream:
                proc = subprocess.run([sys.executable, str(Path(__file__)), 'run', '--freeze', str(bundle),
                                       '--batch', str(batch), '--id', entry['id']], stdout=stream, stderr=subprocess.STDOUT)
            summary = batch / 'runs' / entry['id'] / 'summary.json'
            if not summary.is_file():
                progress['status'] = 'collection_failure'
                break
            from simclinic.evaluation.recovery_observation_driver import verify_run_files
            row = read(summary)
            verify_run_files(summary.parent, row)
            # A nonzero child with a fully sealed honest failure still counts.
            # Unexpected nonzero dispatch status is retained independently.
            row['child_exit_code'] = proc.returncode
            row['unexpected_child_exit'] = bool(proc.returncode and row.get('outcome_class') not in
                ('honest_task_failure', 'backend_failure', 'budget_interrupted'))
            row['infrastructure_failure'] |= row['unexpected_child_exit']
            row['child_workflow_elapsed_seconds'] = row['consumption']['workflow_elapsed_seconds']
            row['consumption']['workflow_elapsed_seconds'] = time.monotonic() - task_began
            row['workflow_wall_scope'] = 'STARTED dispatch through child archive and parent verification; report overhead in batch wall'
            write_json(summary, row)
            ledger.finish(row)
            progress['rows'].append(row)
            progress['status'] = (stop_reason(row) or budget_reason(config, progress['rows'], stage,
                                  time.monotonic() - began, time.monotonic() - stage_began, before_run=False) or 'running')
            write_json(batch / 'progress.json', progress)
            report(batch, bundle)
            if progress['status'] != 'running':
                break
        else:
            progress['status'] = 'complete'
    except BaseException as exc:
        progress['status'] = 'collection_failure' if ledger.events and ledger.events[-1]['kind'] == 'STARTED' else 'infrastructure_failure'
        write_json(batch / 'coordinator-failure.json', dict(type=type(exc).__name__, message=str(exc), at=now()))
    finally:
        if guard is not None:
            if guard.poll() is None:
                guard.terminate()
                try:
                    guard.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    guard.kill()
                    guard.wait(timeout=10)
            write_json(batch / 'idle-guard-released.json', dict(pid=guard.pid, released=guard.poll() is not None, at=now()))
        progress.update(ended_at=now(), elapsed_total_seconds=time.monotonic() - began)
        if ledger.events and ledger.events[-1]['kind'] == 'STARTED':
            progress['unfinished_task_wall'] = dict(id=ledger.events[-1]['entry']['id'],
                execution_id=ledger.events[-1]['execution_id'], known_lower_bound_seconds=max(0, time.monotonic() - task_began))
        # This includes failures, observer/archive work, dispatch, reports and cleanup.
        task_wall = sum(r['consumption']['workflow_elapsed_seconds'] for r in progress['rows'])
        progress['coordinator_overhead_seconds'] = max(0, progress['elapsed_total_seconds'] - task_wall)
        progress['coordinator_overhead_scope'] = 'Residual batch wall outside sealed FINAL task wall, including any unfinished task; not pure coordinator CPU'
        write_json(batch / 'progress.json', progress)
        try:
            report(batch, bundle)
        except Exception as exc:
            write_json(batch / 'report-failure.json', dict(type=type(exc).__name__, message=str(exc),
                        started=ledger.starts(), successful_tasks_not_inferred=True))
    return 0 if progress['status'] == 'complete' else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('offline-check', 'freeze', 'check-freeze', 'batch', 'run', 'report', 'verify'))
    parser.add_argument('--protocol', type=Path, default=ROOT / 'protocols/bounded-recovery-observation-v1')
    parser.add_argument('--freeze', type=Path)
    parser.add_argument('--batch', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--id')
    parser.add_argument('--authorize-execution', action='store_true')
    parser.add_argument('--current-environment', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'offline-check':
        from scripts.check_recovery_observation_protocol import verify_design
        checked = dict(design=verify_design(args.protocol), driver=offline_contract_checks(read(args.protocol / 'protocol.json')))
        if args.out:
            write_new(args.out, checked)
        print(json.dumps(checked, ensure_ascii=False, indent=2))
        return 0
    if args.action == 'freeze':
        if args.out is None:
            parser.error('freeze requires a new --out directory')
        from simclinic.evaluation.recovery_execution_freeze import build_freeze
        print(json.dumps(build_freeze(ROOT, args.out, protocol_directory=args.protocol), ensure_ascii=False, indent=2))
        return 0
    if args.freeze is None:
        parser.error('--freeze is required')
    bundle = args.freeze.resolve()
    if args.action == 'check-freeze':
        _, _, checked = freeze_data(bundle, current=args.current_environment)
        print(json.dumps(checked, ensure_ascii=False, indent=2))
        return 0
    if args.batch is None:
        parser.error('--batch is required')
    batch = args.batch.resolve()
    if args.action == 'batch':
        return coordinate(batch, bundle, authorized=args.authorize_execution)
    if args.action == 'run':
        if args.id is None:
            parser.error('registered run requires --id')
        return run_one(batch, bundle, args.id)
    if args.action == 'report':
        print(json.dumps(report(batch, bundle), ensure_ascii=False, indent=2))
    else:
        _, freeze_sha, _ = freeze_data(bundle)
        print(json.dumps(verify_batch(batch, config_for(bundle), freeze_sha, bundle=bundle), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
