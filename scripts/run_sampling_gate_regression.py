"""Real, model-free development runs of frozen normal/failed inputs.

Replay means archived inputs, never archived solver outputs. Every graph starts
at local_runner and solves/renders/samples afresh; this is not a heldout study.
"""
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
from simclinic.cli import get_report
from simclinic.core.record import load_record
from simclinic.core.report import write_json, write_report
from simclinic.domains.openfoam.sampling_gate import GatedHeldoutCompletion
from simclinic.evaluation.heldout import HeldoutCompletion, hashes, task, verify_hashes
from simclinic.session import SessionRecorder, plain

IMAGE = 'openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d'


def prepare(out, scenarios=('healthy', 'archived-fatal')):
    source_batch = ROOT / 'real-runs/content-interface-heldout-20261004'
    frozen = json.loads((source_batch / 'freeze.json').read_text())
    verify_hashes(source_batch / 'tasks/I1', frozen['tasks']['I1'])
    native = ROOT / 'integration/foam_agent_v210/native/content'
    manifest = json.loads((native / 'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        if hashlib.sha256((native / name).read_bytes()).hexdigest() != sha:
            raise ValueError('Native source changed: ' + name)
    if hashlib.sha256((ROOT / 'simclinic/input_content.py').read_bytes()).hexdigest() != manifest['runtime_requirements']['input_content_sha256']:
        raise ValueError('Content helper changed')
    image_id = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]['Id']
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(native, out / 'native', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(source_batch / 'tasks/I1', out / 'task')
    sources = {}
    source_runs = {'healthy': 'I1-r1-candidate', 'archived-fatal': 'I1-r2-baseline',
                   'missing-profile': 'I1-r1-candidate'}
    for scenario in scenarios:
        run = source_runs[scenario]
        original = source_batch / 'runs' / run
        record = load_record(original / 'record.json', original).data
        for artifact in record['artifacts']:
            if hashlib.sha256((original / artifact['path']).read_bytes()).hexdigest() != artifact['sha256']:
                raise ValueError('Historical artifact changed: ' + artifact['path'])
        attempt = record['attempts'][0]
        inputs = original / attempt['input_snapshot']
        shutil.copytree(inputs, out / 'inputs' / scenario)
        injection = None
        if scenario == 'missing-profile':
            path = out / 'inputs' / scenario / 'system/sample'
            text = path.read_text()
            import re
            changed, count = re.subn(r'\bprofile(?=\s*\{)', 'alternateProfile', text)
            if count != 1:
                raise ValueError('Expected exactly one profile sampling set to rename')
            path.write_text(changed)
            injection = dict(actor='Explicit development test injector',
                             change='Rename profile set to alternateProfile; retain coordinates, fields and physics.',
                             modified_file='system/sample', task_thresholds_changed=False)
        sources[scenario] = dict(record=str(original / 'record.json'), source_run_id=attempt['run_id'],
                                 source_input_version=attempt['input_version'], original_input_hashes=hashes(inputs),
                                 input_hashes=hashes(out / 'inputs' / scenario), fault_injection=injection is not None,
                                 injection=injection)
    runtime = out / 'common-runtime'
    shutil.copytree(ROOT / 'simclinic', runtime / 'simclinic', ignore=shutil.ignore_patterns('__pycache__'))
    (runtime / 'scripts').mkdir()
    shutil.copy2(Path(__file__), runtime / 'scripts' / Path(__file__).name)
    protocol = dict(development_regression=True, heldout=False, archived_input_replay=True,
                    archived_solver_output_reuse=False, full_agent_workflow=False, new_model_calls=0,
                    scope='Fresh native local_runner -> U render -> registered completion; no planner or LLM.',
                    image=IMAGE, image_id=image_id, python=sys.version,
                    installed_dependencies=subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines(),
                    resources=dict(cpus=2, memory='2g', network='none', platform='linux/amd64'),
                    sources=sources, native=hashes(out / 'native'), task=hashes(out / 'task'),
                    common_runtime=hashes(runtime), review_limit=0, model_render_fallback_limit=0,
                    difference='Registered completion policy only; same native content snapshot and task per scenario.',
                    schedule=[dict(id=scenario + '-' + policy, scenario=scenario, policy=policy)
                              for i, scenario in enumerate(scenarios)
                              for policy in (('legacy', 'sampling') if i % 2 == 0 else ('sampling', 'legacy'))],
                    optimization_effect_proven=False)
    write_json(out / 'protocol.json', protocol)
    (out / 'protocol.sha256').write_text(hashlib.sha256((out / 'protocol.json').read_bytes()).hexdigest() + '\n')
    print('Prepared frozen development regression:', out)


def run(batch, run_id):
    protocol = json.loads((batch / 'protocol.json').read_text())
    digest = hashlib.sha256((batch / 'protocol.json').read_bytes()).hexdigest()
    if digest != (batch / 'protocol.sha256').read_text().strip() or ROOT != batch / 'common-runtime':
        raise ValueError('Use the frozen development driver and intact protocol')
    for directory, key in (('common-runtime', 'common_runtime'), ('native', 'native'), ('task', 'task')):
        verify_hashes(batch / directory, protocol[key])
    entries = protocol['schedule']
    index = next(i for i, item in enumerate(entries) if item['id'] == run_id)
    for previous in entries[:index]:
        if not (batch / 'runs' / previous['id'] / 'summary.json').is_file():
            raise ValueError('Run the predeclared serial order')
    entry = entries[index]
    scenario, policy = entry['scenario'], entry['policy']
    verify_hashes(batch / 'inputs' / scenario, protocol['sources'][scenario]['input_hashes'])
    if sys.version != protocol['python'] or subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines() != protocol['installed_dependencies']:
        raise ValueError('Python/dependencies changed')
    if json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]['Id'] != protocol['image_id']:
        raise ValueError('Image changed')
    out = batch / 'runs' / run_id
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(batch / 'native', out / 'source-package', ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0, str(out / 'source-package/src'))
    from config import Config
    from main import create_foam_agent_graph
    from services import configure_llm
    from utils import read_case_foamfiles, scan_case_directory
    from logger import close_logging
    class ForbiddenModel:
        def invoke(self, *args, **kwargs):
            raise AssertionError('This development regression permits zero model calls')
    backend = ForbiddenModel()
    configure_llm(backend)
    environment = dict(distribution='OpenFOAM Foundation', version='10', solver='simpleFoam',
                       image=IMAGE, image_id=protocol['image_id'], completion_policy=policy,
                       protocol_sha256=digest, graph_entrypoint='local_runner', resources=protocol['resources'])
    spec = json.loads((batch / 'task/spec.json').read_text())
    objective = (batch / 'task/requirement.txt').read_text()
    recorder = SessionRecorder(out, task(spec, objective), environment,
                               dict(name='Foam-Agent sampling gate development regression',
                                    commit='bfb7f791f77438ff90c1a39ff398739ee97bcc67', full_agent_workflow=False))
    recorder.data.update(heldout=False, fault_injection=protocol['sources'][scenario].get('fault_injection',False), development_regression=True,
                         archived_input_replay=True, archived_solver_output_reuse=False, new_model_calls=0)
    case = out / 'case'
    shutil.copytree(batch / 'inputs' / scenario, case)
    write_json(out / 'environment.json', dict(**environment, python=sys.version,
        common_runtime_hashes=protocol['common_runtime'], source_hashes=protocol['native'],
        source_input=protocol['sources'][scenario], new_model_calls=0, review_limit=0, model_render_fallback_limit=0))
    os.environ['SIMCLINIC_DOCKER_IMAGE'] = IMAGE
    os.environ['SIMCLINIC_OBSERVED_EXECUTION'] = '1'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    config = Config(case_dir=str(case), max_loop=0, max_time_limit=180, recursion_limit=30)
    config.visualization_max_loop = 0
    initial = dict(case_dir=str(case), config=config, user_requirement=objective, llm_service=backend,
                   foamfiles=read_case_foamfiles(str(case)), dir_structure=scan_case_directory(str(case)),
                   loop_count=0, input_writer_mode='initial', requires_hpc=False,
                   requires_visualization=True, error_logs=[])
    try:
        completion = (GatedHeldoutCompletion if policy == 'sampling' else HeldoutCompletion)(
            recorder, batch / 'task/acceptance-reference', IMAGE, spec)
        graph = create_foam_agent_graph(entrypoint='local_runner', observer=recorder.observe,
                                        completion_policy=completion).compile()
        result = graph.invoke(initial, config={'recursion_limit': 30})
        write_json(out / 'graph-result.json', plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        write_json(out / 'failure.json', dict(type=type(exc).__name__, message=str(exc)))
        recorder.finish(completed=False)
        raise
    finally:
        close_logging()
    report = get_report(load_record(out / 'record.json', out))
    write_report(report, out / 'audit')
    sample = next(e for e in recorder.data['events'] if e.get('operation') == 'sample')
    summary = dict(synthetic=False, development_regression=True, heldout=False,
        archived_input_replay=True, archived_solver_output_reuse=False, full_agent_workflow=False,
        new_model_calls=0, scenario=scenario, fault_injection=recorder.data['fault_injection'],
        completion_policy=policy, attempts=len(recorder.attempts),
        completion=result['completion_claim'], acceptance=result['acceptance_report']['values'],
        acceptance_error=result['acceptance_report']['error'], coverage=recorder.data['coverage'],
        sample_tool={k: sample.get(k) for k in ('id', 'exit_code', 'outcome', 'error_category')},
        audit_summary=report['summary'], metrics=recorder.data['metrics'], optimization_effect_proven=False)
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if result['completion_claim']['success'] != (scenario == 'healthy'):
        raise RuntimeError('Unexpected task result; evidence retained')
    if policy == 'sampling' and scenario == 'archived-fatal' and sample['error_category'] != 'sampling_fatal_log':
        raise RuntimeError('Expected typed sampling failure was not observed')
    if policy == 'sampling' and scenario == 'missing-profile' and sample['error_category'] != 'sampling_missing_output':
        raise RuntimeError('Expected missing-output failure was not observed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--batch', type=Path)
    parser.add_argument('--run')
    parser.add_argument('--scenarios', nargs='+', choices=('healthy','archived-fatal','missing-profile'),
                        default=['healthy','archived-fatal'])
    args = parser.parse_args()
    if args.prepare and args.out:
        if len(set(args.scenarios)) != len(args.scenarios):
            parser.error('Scenarios must be unique')
        prepare(args.out.resolve(), args.scenarios)
    elif args.batch and args.run:
        run(args.batch.resolve(), args.run)
    else:
        parser.error('Use --prepare --out or --batch --run')


if __name__ == '__main__':
    main()
