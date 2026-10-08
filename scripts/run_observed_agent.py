"""Real native workflow with scoped decisions, attempt lineage and completion."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_full_agent import REQUIREMENT,IMAGE,CODEX,CORPUS
from simclinic.session import SessionRecorder,plain
from simclinic.domains.openfoam.completion import ChannelCompletion,task_definition
from simclinic.domains.openfoam.sampling_gate import GatedChannelCompletion
from simclinic.backends.codex_cli import CodexCLI
from simclinic.retrieval import LexicalReferences
from simclinic.core.record import load_record
from simclinic.core.report import write_json,write_report
from simclinic.cli import get_report

NATIVE=ROOT/'integration/foam_agent_v210/native/content'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--entry',choices=('planner','local_runner'),default='local_runner')
    parser.add_argument('--fault',choices=('none','recoverable','persistent'),default='none')
    parser.add_argument('--model',default='gpt-6.1-sol')
    parser.add_argument('--codex',default=CODEX)
    parser.add_argument('--native',choices=('observed','content'),default='content',
                        help='默认使用正文修复版；旧源码对照可选 observed')
    parser.add_argument('--completion-policy',choices=('legacy','sampling'),default='sampling')
    args=parser.parse_args()
    native=NATIVE.parent/args.native
    if args.entry=='planner' and args.fault!='none':raise ValueError('Fault tests use the explicitly scoped local_runner entry.')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((native/'candidate-manifest.json').read_text())
    for name,sha in manifest['source_files'].items():
        if hashlib.sha256((native/name).read_bytes()).hexdigest()!=sha:raise ValueError('Native candidate changed: '+name)
    expected_content=manifest.get('runtime_requirements',{}).get('input_content_sha256')
    if expected_content and hashlib.sha256((ROOT/'simclinic/input_content.py').read_bytes()).hexdigest()!=expected_content:
        raise ValueError('Content helper differs from the native contract manifest')
    shutil.copytree(native,out/'source-package',ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0,str(out/'source-package/src'))
    from main import create_foam_agent_graph,initialize_state
    from config import Config
    from services import configure_llm
    from utils import configure_retriever,read_case_foamfiles,scan_case_directory
    from logger import close_logging
    environment=dict(distribution='OpenFOAM Foundation',version='10',solver='simpleFoam',image=IMAGE,
        image_id=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]['Id'],
        model=args.model,graph_entrypoint=args.entry,completion_policy=args.completion_policy,native_variant=args.native,
        native_manifest_sha256=hashlib.sha256((native/'candidate-manifest.json').read_bytes()).hexdigest())
    recorder=SessionRecorder(out,task_definition(REQUIREMENT),environment,
        dict(name='Foam-Agent with registered task gate',commit=manifest['base_commit'],full_agent_workflow=args.entry=='planner'))
    recorder.data['fault_injection']=args.fault!='none'
    backend=CodexCLI(out/'model-calls',args.model,executable=args.codex,max_calls=32 if args.entry=='planner' else 8,
                     observer=recorder.model)
    configure_llm(backend)
    references=LexicalReferences(CORPUS/'corpus.json')
    configure_retriever(references)
    os.environ['SIMCLINIC_DOCKER_IMAGE']=IMAGE
    os.environ['SIMCLINIC_OBSERVED_EXECUTION']='1'
    os.environ['LANGCHAIN_TRACING_V2']='false'
    config=Config(case_dir=str(out/'case'),database_path=str(CORPUS),max_loop=2,max_time_limit=180,
                  model_version=args.model,searchdocs=1,recursion_limit=30)
    config.visualization_max_loop=0
    reference=CORPUS/'reference-case'
    (out/'requirement.txt').write_text(REQUIREMENT)
    runtime_names=tuple(str(p.relative_to(ROOT)) for p in sorted((ROOT/'simclinic').rglob('*.py'))
                        if '__pycache__' not in p.parts)+('scripts/run_observed_agent.py',)
    runtime=out/'runtime-source'
    for name in runtime_names:
        path=runtime/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,path)
    write_json(out/'environment.json',dict(**environment,python=sys.version,cli_version=backend.version,
        source_manifest=manifest,corpus_sha256=references.sha256,
        runtime_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in runtime_names},
        requirements_sha256=hashlib.sha256((native.parent/'requirements.lock').read_bytes()).hexdigest(),
        fault=args.fault,review_limit=2,model_call_limit=backend.max_calls,model_render_fallback_limit=0))
    fixed_fault=None;injections=0
    if args.entry=='planner':
        initial=initialize_state(REQUIREMENT,config,llm_service=backend)
    else:
        case=out/'case';case.mkdir()
        for folder in ('0','constant','system'):shutil.copytree(reference/folder,case/folder)
        shutil.copy2(reference/'Allrun',case/'Allrun')
        if args.fault!='none':
            path=case/'system/fvSolution'
            text=path.read_text();fixed_fault=text.replace('solver GAMG;','solver SimClinicInvalidSolver;')
            assert fixed_fault!=text
            if args.fault=='recoverable':path.write_text(fixed_fault)
        initial=dict(case_dir=str(case),config=config,user_requirement=REQUIREMENT,llm_service=backend,
            tutorial_reference=json.loads((CORPUS/'corpus.json').read_text())['collections']['openfoam_tutorials_structure'][0]['full_content'],
            foamfiles=read_case_foamfiles(str(case)),dir_structure=scan_case_directory(str(case)),
            loop_count=0,input_writer_mode='initial',requires_hpc=False,requires_visualization=True,error_logs=[])
    def observe(stage,node,state):
        nonlocal injections
        if stage=='start' and node=='local_runner' and args.fault=='persistent':
            injections+=1
            path=Path(state['case_dir'])/'system/fvSolution'
            path.write_text(fixed_fault)
            evidence=out/'fault-injections'/('attempt-'+str(injections)+'.json')
            write_json(evidence,dict(fault_injection=True,actor='Explicit test injector',
                change='Restore the same invalid pressure solver token before this attempt.',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),changes_task_thresholds=False))
            recorder.event('observation',observation_type='external_fault_injection',source_artifact=recorder.artifact(evidence))
        recorder.observe(stage,node,state)
    result=None
    try:
        graph=create_foam_agent_graph(entrypoint=args.entry,observer=observe,
            completion_policy=(GatedChannelCompletion if args.completion_policy=='sampling' else ChannelCompletion)(recorder,reference,IMAGE)).compile()
        result=graph.invoke(initial,config={'recursion_limit':30})
        write_json(out/'graph-result.json',plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        recorder.finish(completed=False)
        write_json(out/'failure.json',dict(type=type(exc).__name__,message=str(exc)))
        raise
    finally:
        close_logging();write_json(out/'model-usage.json',backend.get_statistics())
    report=get_report(load_record(out/'record.json',out));write_report(report,out/'audit')
    summary=dict(synthetic=False,fault_injection=args.fault!='none',entrypoint=args.entry,fault=args.fault,
        attempts=len(recorder.attempts),completion=result['completion_claim'],coverage=recorder.data['coverage'],
        acceptance=result['acceptance_report']['values'],audit_summary=report['summary'],
        model_usage=backend.get_statistics(),optimization_effect_proven=False)
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    expected=args.fault!='persistent'
    if result['completion_claim']['success']!=expected:raise RuntimeError('Task result differs from the scenario expectation; evidence retained.')
    if not all(recorder.data['coverage'].values()):raise RuntimeError('Registered workflow coverage is incomplete; do not claim full observation.')
    if args.fault=='recoverable' and not (len(recorder.attempts)==2 and len(backend.calls)>=3):
        raise RuntimeError('Recovery did not exercise actual model review, rewriting and rerun.')


if __name__=='__main__':main()
