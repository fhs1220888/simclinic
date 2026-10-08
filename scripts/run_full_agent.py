"""Run the real planner-to-render native graph with explicit backend adapters."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NATIVE = ROOT/'integration/foam_agent_v210/native/full'
CORPUS = ROOT/'integration/foam_agent_v210/reference-corpus-v1'
IMAGE = 'openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d'
CODEX = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'

REQUIREMENT = '''Generate and run an OpenFOAM Foundation 10 simpleFoam case locally.
Steady incompressible single-phase laminar Poiseuille channel: length 1 m,
height 0.1 m, thickness 0.01 m; mesh 40 x 20 x 1, uniform grading, empty
front/back. Use nu=0.001 m^2/s and streamwise mean velocity 0.01 m/s.
Inlet fixedValue nonuniform List<vector> of 20 values at y=(i+0.5)*0.005:
Ux=6*0.01*(y/0.1)*(1-y/0.1), Uy=Uz=0. Walls no-slip, outlet U zeroGradient
and kinematic p fixedValue 0; inlet/walls p zeroGradient. Use Foundation 10
constant/physicalProperties (viscosityModel constant; nu 0.001;) and
constant/momentumTransport (simulationType laminar;), never turbulence.
Use the retrieved reference numerical dictionaries and boundary names exactly.
SIMPLE residualControl p/U 1e-7, endTime 2000, deltaT 1, writeInterval 2000,
writePrecision 12, ASCII uncompressed fields. Generate all nine reference files,
including system/sample with the same 20 y-profile and 17 x-pressure points.
Do not put function objects/postprocessing in controlDict or Allrun.
Allrun must contain exactly these executable lines (environment is supplied):
set -eu
blockMesh > log.blockMesh 2>&1
checkMesh > log.checkMesh 2>&1
simpleFoam > log.simpleFoam 2>&1
No cd, source, RunFunctions, external mesh, HPC or additional shell commands.
After solving, explicitly render the velocity U field to a PNG using PyVista.
Acceptance is independently checked: successful solver exit and End, SIMPLE
convergence, local continuity <=1e-7, velocity profile relative L2 error <=0.02,
kinematic pressure-gradient relative error <=0.03. Preserve every threshold and
physical parameter. Rendering or solver execution alone is not CFD acceptance.
'''


def jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    if isinstance(value, dict):
        return {k:jsonable(v) for k,v in value.items() if k not in ('config','llm_service')}
    if isinstance(value, (tuple,list)):
        return [jsonable(v) for v in value]
    return value


def canonical_dictionary(path):
    text=path.read_text()
    text=re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.DOTALL)
    text=re.sub(r'FoamFile\s*\{[^{}]*\}', '', text)
    tokens=re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[^\s;(){}\[\]]+|[;(){}\[\]]',text)
    return [round(float(token),12) if re.fullmatch(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?',token) else token for token in tokens]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--codex', default=CODEX)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    from simclinic.core.report import write_json, write_report
    from simclinic.core.record import load_record, validate
    from simclinic.cli import get_report
    from simclinic.backends.codex_cli import CodexCLI
    from simclinic.retrieval import LexicalReferences
    from simclinic.domains.openfoam.acceptance import channel_criteria, solver_measurements, profile_error, pressure_gradient_error
    from simclinic.checks.behavior import matches
    manifest = json.loads((NATIVE/'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        if hashlib.sha256((NATIVE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Native source differs from pinned candidate: '+name)
    archived=out/'source-package'
    shutil.copytree(NATIVE,archived,ignore=shutil.ignore_patterns('__pycache__'))
    image = json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    sys.path.insert(0, str(archived/'src'))
    from main import create_foam_agent_graph, initialize_state
    from config import Config
    from services import configure_llm
    from utils import configure_retriever
    from logger import close_logging
    trace = []
    def observe(category, name, payload):
        trace.append(dict(sequence=len(trace)+1, elapsed_seconds=time.monotonic()-started,
                          category=category,name=name,payload=jsonable(payload)))
        write_json(out/'trace.json', trace)
    backend = CodexCLI(out/'model-calls', args.model, executable=args.codex,
                       observer=lambda metadata,folder:observe('model',metadata['id'],metadata))
    references = LexicalReferences(CORPUS/'corpus.json', observer=lambda name,query,hits:
        observe('retrieval', name, dict(query=query, hits=[dict(id=h['id'],score=h['score']) for h in hits])))
    configure_llm(backend)
    configure_retriever(references)
    os.environ['SIMCLINIC_DOCKER_IMAGE'] = IMAGE
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    config = Config(case_dir=str(out/'case'),database_path=str(CORPUS),max_loop=2,
                    max_time_limit=180,model_version=args.model,searchdocs=1,recursion_limit=30)
    config.visualization_max_loop = 0
    (out/'requirement.txt').write_text(REQUIREMENT)
    runtime_files = ('simclinic/foam_execution.py','simclinic/foam_runtime.py','simclinic/backends/codex_cli.py',
                     'simclinic/retrieval.py','simclinic/rendering_request.py',
                     'integration/foam_agent_v210/native/foam_tool.py', 'scripts/run_full_agent.py')
    write_json(out/'environment.json', dict(python=sys.version,executable=sys.executable,image=IMAGE,
        image_id=image['Id'],model=args.model,cli_version=backend.version,source_manifest=manifest,
        corpus_sha256=references.sha256,retrieval_method='lexical-overlap-v1',reference_cases=1,
        runtime_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in runtime_files},
        requirements_sha256=hashlib.sha256((NATIVE.parent/'requirements.lock').read_bytes()).hexdigest(),
        model_call_limit=32,solver_timeout_seconds=180,review_limit=2,model_render_fallback_limit=0))
    result = None
    try:
        initial = initialize_state(REQUIREMENT, config, llm_service=backend)
        graph = create_foam_agent_graph(observer=observe).compile()
        result = graph.invoke(initial, config={'recursion_limit':config.recursion_limit})
        write_json(out/'graph-result.json',jsonable(result))
    except Exception as exc:
        write_json(out/'failure.json',dict(type=type(exc).__name__,message=str(exc),
                                         model_usage=backend.get_statistics()))
        raise
    finally:
        close_logging()
        write_json(out/'model-usage.json', backend.get_statistics())

    case = Path(result['case_dir'])
    context = result.get('simclinic_context')
    if not context:
        raise ValueError('Graph did not reach a recorded solver execution.')
    native_dir = case/'.simclinic/native'/context['run_id']
    execution = json.loads((native_dir/'execution.json').read_text())
    values = solver_measurements((case/'log.simpleFoam').read_text() if (case/'log.simpleFoam').is_file() else '', execution['exit_code'])
    values.update(profile_error=None, pressure_gradient_error=None)
    sampling = dict(exit_code=None,elapsed_seconds=None)
    if not result.get('error_logs'):
        argv=['docker','run','--rm','--pull','never','--platform','linux/amd64','--network','none','--cpus','2','--memory','2g',
              '--user','root','--mount',f'type=bind,src={case},dst=/case','--workdir','/case','--entrypoint','/bin/bash',IMAGE,
              '-c','source /opt/openfoam10/etc/bashrc && simpleFoam -postProcess -func sample -latestTime']
        sample_started=time.monotonic()
        sample=subprocess.run(argv,capture_output=True,timeout=120)
        (out/'sampling.stdout.log').write_bytes(sample.stdout)
        (out/'sampling.stderr.log').write_bytes(sample.stderr)
        sampling=dict(exit_code=sample.returncode,elapsed_seconds=time.monotonic()-sample_started)
        if sample.returncode==0:
            try:
                values['profile_error']=profile_error(next((case/'postProcessing').rglob('profile.xy')))
                values['pressure_gradient_error']=pressure_gradient_error(next((case/'postProcessing').rglob('pressure.xy')))
            except (ValueError,StopIteration) as exc:
                sampling['error']=str(exc)
    # Direct input verification is reported separately from the output comparisons.
    # A successful comparison is limited to the specified benchmark and one run.
    input_checks={str(p.relative_to(CORPUS/'reference-case')):
        (case/p.relative_to(CORPUS/'reference-case')).is_file() and
        canonical_dictionary(case/p.relative_to(CORPUS/'reference-case'))==canonical_dictionary(p)
        for folder in ('0','constant','system') for p in (CORPUS/'reference-case'/folder).rglob('*') if p.is_file()}
    task_inputs_verified=bool(input_checks) and all(input_checks.values())
    accepted=task_inputs_verified and all(matches(values[c['id']],c) is True for c in channel_criteria())
    write_json(out/'acceptance.json', dict(values=values,criteria=channel_criteria(),accepted=accepted,sampling=sampling,
        task_inputs_verified=task_inputs_verified,input_checks=input_checks,
        input_check_method='Conservative token equality against specified reference dictionaries; header/comments/number formatting ignored.'))

    data=json.loads((native_dir/'record.json').read_text())
    for artifact in data['artifacts']:
        artifact['path']='case/'+artifact['path']
    data['task']=dict(id='poiseuille-channel',objective=REQUIREMENT,required_preflight=['checkMesh'],acceptance=channel_criteria())
    data['agent'].update(full_agent_workflow=True,model=args.model,retrieval_method='lexical-overlap-v1')
    data['environment'].update(model=args.model,openfoam_image=IMAGE,native_candidate_manifest_sha256=
        hashlib.sha256((NATIVE/'candidate-manifest.json').read_bytes()).hexdigest())
    def artifact(path, role=None):
        entry=dict(id='full-a'+str(len(data['artifacts'])+1),path=str(path.relative_to(out)),
                   run_id=context['run_id'],input_version=context['input_version'],sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if role:entry['role']=role
        data['artifacts'].append(entry)
        return entry['id']
    def event(kind, **fields):
        entry=dict(id='full-e'+str(len(data['events'])+1),kind=kind,run_id=context['run_id'],input_version=context['input_version'])
        entry.update(fields)
        data['events'].append(entry)
        return entry
    # Prepend the actual three solver process calls before native Allrun/render events.
    previous_events=data['events'];data['events']=[]
    calls=[json.loads(p.read_text()) for p in (native_dir/'command-calls').glob('*/metadata.json')]
    calls.sort(key=lambda call:call['started'])
    for call in calls:
        command_dir=next(p.parent for p in (native_dir/'command-calls').glob('*/metadata.json')
                         if json.loads(p.read_text())==call)
        event('tool',operation=call['operation'],phase=call['phase'],exit_code=call['exit_code'],outcome=call['outcome'],
              input_version=call['input_version'],arguments={'argv':call['argv']},input_fingerprint=call['input_version'],
              error_category=None if call['exit_code']==0 else 'process_exit',elapsed_seconds=call['elapsed_seconds'],
              metadata_artifact=artifact(command_dir/'metadata.json'),stdout_artifact=artifact(command_dir/'stdout.log'),
              stderr_artifact=artifact(command_dir/'stderr.log'))
    data['events'].extend(previous_events)
    acceptance_artifact=artifact(out/'acceptance.json')
    for criterion in channel_criteria():
        event('measurement',criterion_id=criterion['id'],value=values[criterion['id']],artifact_id=acceptance_artifact)
    artifact(out/'trace.json')
    for metadata in backend.calls:
        event('observation',observation_type='model_call',metadata=metadata,
              artifact_id=artifact(out/'model-calls'/metadata['id']/'metadata.json'),run_id=None,input_version=None)
    data['coverage']=dict(tools=len(calls)==3 and execution['exit_code']==0,decisions=False,references=True,acceptance=True)
    data['collection_status']='complete'
    data['collection_scope']={'tools':'Three policy-validated Allrun commands and native renderer; model metadata saved separately.',
        'decisions':'Node boundaries and model outputs collected; free-text claims and all internal decisions not normalized.',
        'acceptance':'Independent fixed benchmark measurements; no Agent completion claim synthesized.'}
    data['metrics']=dict(wall_seconds=time.monotonic()-started,solver_seconds=sum(c['elapsed_seconds'] for c in calls if c['phase']=='solve'),
                         model_tokens=backend.get_statistics()['total_tokens'],model_cost=None)
    write_json(out/'record.json',validate(data))
    write_report(get_report(load_record(out/'record.json',out)),out/'audit')
    summary=dict(synthetic=False,full_model_driven_workflow=True,graph_entrypoint='planner',
                 nodes_exercised=[t['name'] for t in trace if t['category']=='end'],run_context=context,
                 visualization_success=result.get('pyvista_visualization',{}).get('success',False),
                 requested_field_verified=result.get('visualization_summary',{}).get('fields_visualized')==['U'],
                 images=result.get('plot_outputs',[]),acceptance=values,benchmark_accepted=accepted,
                 model_usage=backend.get_statistics(),optimization_effect_proven=False)
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if not summary['visualization_success'] or not summary['requested_field_verified'] or not accepted:
        raise RuntimeError('Evidence saved, but full workflow did not meet the benchmark or rendering requirement.')


if __name__ == '__main__':
    main()
