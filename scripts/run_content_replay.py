"""Real development CFD regression from a historical model reply, not a new eval.

Only controlDict goes through initial_write. Other input bytes and Allrun come
from the same failed attempt. No new model, planner, reviewer or prompt tuning.
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
sys.path.insert(0,str(ROOT))
from scripts.run_full_agent import IMAGE
from simclinic.evaluation.heldout import hashes, verify_hashes, HeldoutCompletion, task
from simclinic.session import SessionRecorder, plain
from simclinic.core.record import load_record
from simclinic.core.report import write_json, write_report
from simclinic.cli import get_report

BATCH = ROOT/'real-runs/heldout-field-selection-20261004'
SOURCE = BATCH/'runs/H2-r3-candidate'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arm',choices=('baseline','candidate'),required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    frozen = json.loads((BATCH/'freeze.json').read_text())
    verify_hashes(BATCH/'common-runtime',frozen['common_runtime'])
    definition = BATCH/'tasks/H2'
    verify_hashes(definition,frozen['tasks']['H2'])
    original = load_record(SOURCE/'record.json',SOURCE).data
    for artifact in original['artifacts']:
        if hashlib.sha256((SOURCE/artifact['path']).read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('Historical evidence changed')
    attempt = original['attempts'][0]
    snapshot = SOURCE/attempt['input_snapshot']
    response_file = SOURCE/'model-calls/call-008/response.json'
    response = json.loads(response_file.read_text())['text']
    if response.encode() != (snapshot/'system/controlDict').read_bytes():
        raise ValueError('Response and failed-file bytes differ')
    native = ROOT/'integration/foam_agent_v210/native'/('observed' if args.arm=='baseline' else 'content')
    manifest = json.loads((native/'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        if hashlib.sha256((native/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Native candidate changed')
    if args.arm=='candidate':
        expected = manifest['runtime_requirements']['input_content_sha256']
        if hashlib.sha256((ROOT/'simclinic/input_content.py').read_bytes()).hexdigest() != expected:
            raise ValueError('Content interface changed since build')
    image_id = json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]['Id']
    out = args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    shutil.copytree(native,out/'source-package',ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0,str(out/'source-package/src'))
    from config import Config
    from main import create_foam_agent_graph
    from services import configure_llm
    from services.input_writer import initial_write
    from utils import read_case_foamfiles,scan_case_directory
    from logger import close_logging
    requests = []
    class RecordedReply:
        def invoke(self,user,system=None,pydantic_obj=None,**kwargs):
            if pydantic_obj or requests or '<file_name>controlDict</file_name>' not in (system or ''):
                raise AssertionError('Replay permits exactly one archived controlDict response, no new model calls')
            requests.append(dict(user=user,system=system,replay=True,new_model_call=False))
            return response
    backend = RecordedReply()
    configure_llm(backend)
    environment = dict(distribution='OpenFOAM Foundation',version='10',solver='simpleFoam',image=IMAGE,
        image_id=image_id,
        graph_entrypoint='local_runner',response_source='Historical real model reply replayed without a new model',
        resources=dict(cpus=2,memory='2g',network='none',platform='linux/amd64'))
    spec = json.loads((definition/'spec.json').read_text())
    objective = (definition/'requirement.txt').read_text()
    recorder = SessionRecorder(out,task(spec,objective),environment,
        dict(name='Foam-Agent content contract development replay',commit=manifest['base_commit'],
             full_agent_workflow=False,evaluation_arm=args.arm))
    recorder.data.update(fault_injection=False,heldout=False,development_regression=True,
                         archived_response_replay=True,new_model_calls=0)
    raw = out/'replay/response.json';raw.parent.mkdir()
    shutil.copy2(response_file,raw)
    recorder.artifact(raw)
    case = out/'case'
    for folder in ('0','constant','system'):shutil.copytree(snapshot/folder,case/folder)
    shutil.copy2(snapshot/'Allrun',case/'Allrun')
    before = hashes(case)
    initial_write(str(case),[dict(file_name='controlDict',folder_name='system')],objective,
                  'Archived reply replay; no retrieval or model generation','simpleFoam')
    after = hashes(case)
    changed = [name for name in before if before[name]!=after[name]]
    if changed != ([] if args.arm=='baseline' else ['system/controlDict']):
        raise ValueError('Replay changed another input')
    write_json(out/'replay/request.json',requests[0]);recorder.artifact(out/'replay/request.json')
    write_json(out/'replay/provenance.json',dict(source_record=str(SOURCE/'record.json'),
        source_run_id=attempt['run_id'],source_input_version=attempt['input_version'],
        archived_response_sha256=hashlib.sha256(response_file.read_bytes()).hexdigest(),
        raw_text_sha256=hashlib.sha256(response.encode()).hexdigest(),before=before,after=after,
        changed_inputs=changed,new_model_calls=0,heldout=False,development_regression=True))
    recorder.artifact(out/'replay/provenance.json')
    runtime = out/'runtime-source'
    shutil.copytree(ROOT/'simclinic',runtime/'simclinic',ignore=shutil.ignore_patterns('__pycache__'))
    (runtime/'scripts').mkdir();shutil.copy2(Path(__file__),runtime/'scripts'/Path(__file__).name)
    write_json(out/'environment.json',dict(**environment,python=sys.version,source_manifest=manifest,
        runtime_hashes=hashes(runtime),archived_response_replay=True,new_model_calls=0,
        review_limit=0,review_disabled_for_paired_first_attempt_regression=True,model_render_fallback_limit=0))
    os.environ['SIMCLINIC_DOCKER_IMAGE']=IMAGE
    os.environ['SIMCLINIC_OBSERVED_EXECUTION']='1'
    os.environ['LANGCHAIN_TRACING_V2']='false'
    config = Config(case_dir=str(case),max_loop=0,max_time_limit=180,recursion_limit=30)
    config.visualization_max_loop=0
    initial = dict(case_dir=str(case),config=config,user_requirement=objective,llm_service=backend,
        foamfiles=read_case_foamfiles(str(case)),dir_structure=scan_case_directory(str(case)),
        loop_count=0,input_writer_mode='initial',requires_hpc=False,requires_visualization=True,error_logs=[])
    try:
        graph = create_foam_agent_graph(entrypoint='local_runner',observer=recorder.observe,
            completion_policy=HeldoutCompletion(recorder,definition/'acceptance-reference',IMAGE,spec)).compile()
        result = graph.invoke(initial,config={'recursion_limit':30})
        write_json(out/'graph-result.json',plain(result))
        recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        write_json(out/'failure.json',dict(type=type(exc).__name__,message=str(exc)))
        recorder.finish(completed=False)
        raise
    finally:
        close_logging()
    report = get_report(load_record(out/'record.json',out));write_report(report,out/'audit')
    summary = dict(synthetic=False,new_model_calls=0,archived_response_replay=True,
        heldout=False,development_regression=True,full_agent_workflow=False,arm=args.arm,
        attempts=len(recorder.attempts),changed_inputs=changed,
        completion=result['completion_claim'],acceptance=result['acceptance_report']['values'],
        audit_summary=report['summary'],coverage=recorder.data['coverage'],optimization_effect_proven=False)
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,indent=2),flush=True)
    if result['completion_claim']['success'] != (args.arm=='candidate'):
        raise RuntimeError('Replay differs from predeclared regression expectation; retain evidence')


if __name__=='__main__':main()
