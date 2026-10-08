"""One full real Agent run, using only frozen common runtime and arm source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.evaluation.heldout import verify_hashes, task
from simclinic.evaluation.sampling_study import completion_class, verify_sampling_freeze
from simclinic.session import SessionRecorder, plain
from simclinic.backends.codex_cli import CodexCLI
from simclinic.retrieval import LexicalReferences
from simclinic.core.record import load_record
from simclinic.core.report import write_json, write_report
from simclinic.cli import get_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--arm', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    frozen, _ = verify_sampling_freeze(batch)
    if args.task not in frozen['tasks']:
        raise ValueError('Unknown frozen task')
    expected_out = batch/('development' if args.task == 'D0' else 'runs')/(('D0-' + args.arm) if args.task == 'D0' else args.out.name)
    if args.out.resolve() != expected_out.resolve():
        raise ValueError('Output must belong to this frozen batch')
    progress = json.loads((batch/'progress.json').read_text())
    group = 'development' if args.task == 'D0' else 'runs'
    active = [e for e in progress['development'] + progress['runs'] if e['status'] == 'running']
    if len(active) != 1 or active[0]['id'] != args.out.name or active[0]['arm'] != args.arm or \
            active[0]['task'] != args.task or Path(active[0]['directory']).resolve() != args.out.resolve():
        raise ValueError('Only the coordinator registered active run may execute')
    expected_stage = 'development_running' if group == 'development' else 'running'
    if progress['status'] != expected_stage or progress['freeze_sha256'] != (batch/'freeze.sha256').read_text().strip():
        raise ValueError('Run is outside the registered stage')
    if hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest() != (batch/'freeze.sha256').read_text().strip():
        raise ValueError('Freeze manifest changed')
    if ROOT != batch/'common-runtime':
        raise ValueError('Execute the archived driver, never mutable workspace code')
    verify_hashes(ROOT, frozen['common_runtime'])
    verify_hashes(batch/'corpus', frozen['corpus'])
    verify_hashes(batch/'variants'/args.arm, frozen['variants'][args.arm])
    definition = batch/'tasks'/args.task
    verify_hashes(definition, frozen['tasks'][args.task])
    if hashlib.sha256(Path(frozen['cli']).read_bytes()).hexdigest() != frozen['cli_sha256']:
        raise ValueError('CLI binary changed since freeze')
    dependencies = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines()
    if dependencies != frozen['installed_dependencies'] or sys.version != frozen['python']:
        raise ValueError('Python/dependencies changed since freeze')
    image = json.loads(subprocess.check_output(['docker','image','inspect',frozen['image']], text=True))[0]
    if image['Id'] != frozen['image_id']:
        raise ValueError('Container image differs from freeze')
    spec = json.loads((definition/'spec.json').read_text())
    objective = (definition/'requirement.txt').read_text()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(batch/'variants'/args.arm, out/'source-package', ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0, str(out/'source-package/src'))
    from main import create_foam_agent_graph, initialize_state
    from config import Config
    from services import configure_llm
    from utils import configure_retriever
    from logger import close_logging
    environment = dict(runtime_digest=hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest(),
        distribution='OpenFOAM Foundation', version='10', solver='simpleFoam', image=frozen['image'],
        model=frozen['model'], graph_entrypoint='planner', completion_policy=frozen['completion_policies'][args.arm], resources=dict(cpus=2, memory='2g', platform='linux/amd64'))
    recorder = SessionRecorder(out, task(spec, objective), environment,
        dict(name=frozen.get('agent_name', 'Controlled Foam-Agent field-selection ablation'), commit=frozen['agent_commit'],
             full_agent_workflow=True, evaluation_arm=args.arm))
    recorder.data.update(fault_injection=False, heldout=args.task != 'D0')
    backend = CodexCLI(out/'model-calls', frozen['model'], executable=frozen['cli'],
                       max_calls=frozen['model_call_limit'], observer=recorder.model)
    configure_llm(backend)
    configure_retriever(LexicalReferences(batch/'corpus/corpus.json'))
    os.environ['SIMCLINIC_DOCKER_IMAGE'] = frozen['image']
    os.environ['SIMCLINIC_OBSERVED_EXECUTION'] = '1'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    config = Config(case_dir=str(out/'case'), database_path=str(batch/'corpus'), max_loop=frozen['review_limit'],
                    max_time_limit=frozen['solver_timeout_seconds'], model_version=frozen['model'], searchdocs=1, recursion_limit=30)
    config.visualization_max_loop = frozen['model_render_fallback_limit']
    (out/'requirement.txt').write_text(objective)
    write_json(out/'environment.json', dict(**environment, arm=args.arm, task=spec,
        freeze_sha256=environment['runtime_digest'], freeze=str(batch/'freeze.json'),
        source_hashes=frozen['variants'][args.arm], common_runtime_hashes=frozen['common_runtime'],
        fault_injection=False, acceptance_reference_in_model_context=False))
    result = None
    failure = None
    try:
        initial = initialize_state(objective, config, llm_service=backend)
        graph = create_foam_agent_graph(observer=recorder.observe,
            completion_policy=completion_class(args.arm)(recorder, definition/'acceptance-reference', frozen['image'], spec)).compile()
        result = graph.invoke(initial, config={'recursion_limit':30})
        write_json(out/'graph-result.json', plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        failure = dict(type=type(exc).__name__, message=str(exc))
        write_json(out/'failure.json', failure)
        recorder.finish(completed=False)
    finally:
        close_logging()
        write_json(out/'model-usage.json', backend.get_statistics())
    report = get_report(load_record(out/'record.json', out))
    write_report(report, out/'audit')
    completion = result.get('completion_claim') if result else None
    acceptance = result.get('acceptance_report',{}).get('values',{}) if result else {}
    sample_events = [e for e in recorder.data['events'] if e.get('operation') == 'sample']
    summary = dict(completion_policy=frozen['completion_policies'][args.arm],
        acceptance_error=result.get('acceptance_report', {}).get('error') if result else None,
        sample_tool=sample_events[-1] if sample_events else None, task_id=args.task, arm=args.arm, synthetic=False, fault_injection=False, heldout=args.task != 'D0',
        completed_graph=bool(completion), completion=completion, acceptance=acceptance,
        quality_met=bool(completion and completion['success']), coverage=recorder.data['coverage'],
        evidence_complete=all(recorder.data['coverage'].values()), attempts=len(recorder.attempts),
        audit_summary=report['summary'], checks={c['check_id']:c['status'] for c in report['checks']},
        metrics=recorder.data['metrics'], model_usage=backend.get_statistics(), failure=failure,
        optimization_effect_proven=False)
    write_json(out/'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    # Failed tasks are valid study observations. A setup/collector failure is flagged to coordinator.
    return 0 if completion and all(recorder.data['coverage'].values()) else 2


if __name__ == '__main__':
    sys.exit(main())
